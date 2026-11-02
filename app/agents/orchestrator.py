"""Agent orchestrator: the single entry point for every channel.

InboundMessage (unified event model)
  -> tenant / agent / conversation / contact resolution
  -> (human owns the conversation? store + notify, no AI reply)
  -> quota check -> hybrid NLU (rules -> LLM)
  -> dialogue manager (guardrails, handover rules, state machine flows)
  -> LLM tool-calling loop for open questions (with Redis FAQ cache)
  -> output guardrails -> persist -> events
= AgentReply
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import nlu as nlu_module
from app.agents.dialogue import Decision, DialogueManager
from app.agents.guardrails import filter_output
from app.agents.llm.base import LLMResponse
from app.agents.llm.router import LLMRouter, get_llm_router
from app.agents.loop import LoopResult, run_agent_loop
from app.agents.prompts import build_system_prompt
from app.agents.tools import ToolContext, tool_schemas
from app.channels.schema import AgentReply, InboundMessage
from app.core.config import settings
from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.pii import Redaction
from app.core.redis import get_redis
from app.core.tenancy import set_session_tenant
from app.models import Agent, ConversationStatus, MessageRole, Tenant
from app.rag import answer as rag_answer
from app.rag import service as rag_service
from app.rag.service import SearchResult
from app.services import agent_service, booking_service, conversation_service, usage_service
from app.workers.events import emit
from app.workers.workflow import get_workflow_engine

log = get_logger(__name__)

HANDOVER_MESSAGE = "I'm connecting you with a member of our team. They'll reply here shortly."
LLM_FALLBACK = (
    "Sorry, I'm having trouble answering right now. Would you like me to connect you with our team?"
)
FAQ_INTENTS = {"question", "pricing", "services_info", "timings", "billing"}
_CONTEXTUAL = re.compile(r"\b(it|that|this|those|these|they|them|he|she|above|previous|same)\b", re.I)
LOCK_TTL_SECONDS = 30
LOCK_WAIT_SECONDS = 10.0


@contextlib.asynccontextmanager
async def conversation_lock(key: str) -> AsyncIterator[None]:
    """Serialise turns of one conversation (protects the dialogue state machine).

    Best effort: if Redis is unavailable or the lock can't be taken in time we
    proceed rather than drop the customer's message.
    """
    redis = get_redis()
    token = secrets.token_hex(8)
    lock_key = f"lock:conv:{key}"
    acquired = False
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    try:
        while time.monotonic() < deadline:
            if await redis.set(lock_key, token, nx=True, ex=LOCK_TTL_SECONDS):
                acquired = True
                break
            await asyncio.sleep(0.05)
    except Exception as exc:
        log.warning("conversation lock unavailable: %s", type(exc).__name__)
    try:
        yield
    finally:
        if acquired:
            try:
                if await redis.get(lock_key) == token:
                    await redis.delete(lock_key)
            except Exception:
                log.debug("conversation lock release failed")


def _normalise_question(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


async def _faq_cache_key(tenant: Tenant, agent: Agent, text: str) -> str:
    try:
        kb_version = await get_redis().get(f"kb:ver:{tenant.id}") or "0"
    except Exception:
        kb_version = "0"
    raw = f"{tenant.id}|{agent.id}|{agent.updated_at.isoformat()}|{kb_version}|{_normalise_question(text)}"
    return "faq:" + hashlib.sha256(raw.encode()).hexdigest()


class AgentOrchestrator:
    def __init__(self, router: LLMRouter | None = None) -> None:
        self._router = router

    @property
    def router(self) -> LLMRouter:
        return self._router or get_llm_router()

    async def _resolve_agent(self, db: AsyncSession, tenant: Tenant, agent_id: uuid.UUID | None) -> Agent:
        if agent_id is not None:
            agent = await agent_service.get_agent(db, tenant.id, agent_id)
            if not agent.is_active:
                raise NotFoundError("Agent is not active")
            return agent
        agent = await agent_service.get_default_agent(db, tenant.id)
        if agent is None:
            raise AppError("No active agent is configured for this tenant", status_code=409)
        return agent

    async def handle(self, db: AsyncSession, inbound: InboundMessage) -> AgentReply:
        started = time.perf_counter()
        tenant = await db.get(Tenant, inbound.tenant_id)
        if tenant is None or not tenant.is_active:
            raise ForbiddenError("Tenant is not active")
        set_session_tenant(db, tenant.id)
        agent = await self._resolve_agent(db, tenant, inbound.agent_id)

        lock_key = f"{tenant.id}:{inbound.channel.value}:{inbound.session_id}"
        async with conversation_lock(lock_key):
            return await self._handle_locked(db, tenant, agent, inbound, started)

    async def _handle_locked(
        self, db: AsyncSession, tenant: Tenant, agent: Agent, inbound: InboundMessage, started: float
    ) -> AgentReply:
        text = inbound.text.strip()
        conversation, contact = await conversation_service.get_or_create_conversation(
            db, tenant, agent, inbound
        )
        history = await conversation_service.recent_history(db, conversation.id)
        user_msg = conversation_service.add_message(
            db,
            conversation,
            MessageRole.USER,
            text,
            attachments=[a.model_dump() for a in inbound.attachments],
            meta={"provider_message_id": inbound.metadata.provider_message_id}
            if inbound.metadata.provider_message_id
            else {},
        )
        await db.flush()

        # A human owns this conversation: store the message, notify the inbox, no AI reply.
        if conversation.status is ConversationStatus.HANDED_OVER:
            await db.commit()
            await conversation_service.append_history(conversation.id, "user", text)
            await emit(
                "message.received",
                tenant.id,
                {
                    "message_id": str(user_msg.id),
                    "channel": conversation.channel.value,
                    "text": text,
                    "role": "user",
                    "handed_over": True,
                },
                conversation_id=conversation.id,
            )
            return AgentReply(
                conversation_id=conversation.id,
                message_id=None,
                text=None,
                handed_over=True,
                status=conversation.status.value,
            )

        quota = await usage_service.quota_status(db, tenant)
        router: LLMRouter | None = self.router if self.router.available and not quota.exceeded else None
        if quota.exceeded:
            log.warning("monthly token quota exhausted; rules-only mode", extra={"used": quota.used})

        tz = booking_service.tenant_tz(tenant)
        today = datetime.now(tz).date()
        redaction = Redaction()
        last_bot = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
        nlu = await nlu_module.analyze(
            text,
            template_intents=agent.intents or [],
            business=tenant.name,
            scope=(agent.flow or {}).get("scope", "products and services"),
            today=today,
            router=router,
            last_bot_message=last_bot,
            redact=redaction.protect,
            restore=redaction.restore,
        )
        if nlu.llm_response is not None:
            await usage_service.record(
                db,
                tenant_id=tenant.id,
                purpose="nlu",
                response=nlu.llm_response,
                conversation_id=conversation.id,
            )
        user_msg.intent, user_msg.confidence, user_msg.sentiment = (
            nlu.intent,
            round(nlu.confidence, 3),
            nlu.sentiment,
        )
        user_msg.entities = {
            k: v for k, v in nlu.entities.items() if k not in ("email", "phone")
        }  # no PII copies

        ctx = ToolContext(
            db=db, tenant=tenant, agent=agent, conversation=conversation, contact=contact, redaction=redaction
        )
        state: dict[str, Any] = dict(conversation.dialogue_state or {})
        decision = await DialogueManager(ctx, today, llm_available=router is not None).decide(
            state, nlu, text
        )

        reply_text: str | None = decision.text
        loop: LoopResult | None = None
        cache_key: str | None = None
        cache_hit = False
        rag_outcome: str | None = None
        rag: SearchResult | None = None
        if decision.kind == "llm" and "knowledge_search" in (agent.enabled_tools or []):
            # Retrieval runs locally (embeddings never leave the platform), so the raw text is fine here.
            rag = await rag_service.search(db, tenant.id, text)
            rag_outcome = "confident" if rag.confident else ("low_confidence" if rag.chunks else "empty_kb")

        if (
            decision.kind == "llm"
            and rag_outcome == "low_confidence"
            and nlu.intent in FAQ_INTENTS
            and decision.pending_question is None
            and not ({"phone", "email", "name"} & nlu.entities.keys())  # sharing details = lead, not FAQ
        ):
            # Grounding rule: a knowledge question we can't support with sources gets a scripted reply.
            reply_text = rag_answer.DONT_KNOW
            decision.quick_replies = ["Talk to a human"]
        elif decision.kind == "llm" and router is not None:
            cacheable = (
                decision.pending_question is None
                and not state.get("flow")
                and nlu.intent in FAQ_INTENTS
                and not redaction.mapping
                and not _CONTEXTUAL.search(text)
                and len(text) >= 8
            )
            if cacheable:
                cache_key = await _faq_cache_key(tenant, agent, text)
                try:
                    cached = await get_redis().get(cache_key)
                except Exception:
                    cached = None
                if cached:
                    payload = json.loads(cached)
                    reply_text, cache_hit = payload["text"], True
                    ctx.citations.extend(payload.get("citations", []))
                    await usage_service.record(
                        db,
                        tenant_id=tenant.id,
                        purpose="cache",
                        provider="cache",
                        model="faq-cache",
                        cached=True,
                        conversation_id=conversation.id,
                    )
            if not cache_hit:
                extra_context = None
                if rag is not None and rag.confident:
                    ctx.citations.extend(rag_answer.citations(rag))
                    extra_context = rag_answer.context_block(rag, redaction.protect)
                loop = await self._run_llm(
                    db, ctx, router, history, text, decision.pending_question, today, tz.key, extra_context
                )
                reply_text = redaction.restore(loop.text) if loop.text else None
                if reply_text and ctx.citations:
                    ctx.citations[:] = rag_answer.cited_only(reply_text, ctx.citations)
                if reply_text is None:
                    reply_text = LLM_FALLBACK
                    decision.quick_replies = ["Talk to a human"]
                if ctx.handover_reason:
                    decision = Decision("handover", reason=ctx.handover_reason, text=reply_text)
        elif decision.kind == "llm" and rag is not None and rag.confident:
            # No LLM available (no keys / quota exhausted): answer extractively from the best source.
            ctx.citations.extend(rag_answer.citations(rag)[:1])
            reply_text = rag_answer.extractive_answer(text, rag)
        elif decision.kind == "llm":
            reply_text = LLM_FALLBACK
            decision.quick_replies = ["Talk to a human"]

        if decision.kind == "handover":
            conversation.status = ConversationStatus.HANDED_OVER
            conversation.handover_reason = (decision.reason or "unspecified")[:60]
            conversation.handed_over_at = datetime.now(UTC)
            state.pop("flow", None)
            reply_text = decision.text or HANDOVER_MESSAGE
            decision.quick_replies = []

        check = filter_output(reply_text, (tenant.settings or {}).get("blocked_terms") or [])
        reply_text = check.text
        latency_ms = int((time.perf_counter() - started) * 1000)

        last = loop.last if loop else None
        assistant_msg = conversation_service.add_message(
            db,
            conversation,
            MessageRole.ASSISTANT,
            reply_text,
            intent=nlu.intent,
            tokens_in=loop.tokens_in if loop else 0,
            tokens_out=loop.tokens_out if loop else 0,
            latency_ms=latency_ms,
            llm_provider=last.provider if last else ("cache" if cache_hit else None),
            llm_model=last.model if last else None,
            tool_calls=[
                {k: v for k, v in c.items() if k != "arguments"} for c in (loop.tool_calls if loop else [])
            ],
            citations=ctx.citations,
            meta={
                "decision": decision.kind,
                "nlu_source": nlu.source,
                "guardrail": check.reason,
                "flow_event": decision.flow_event,
                "rag": rag_outcome,
                "quick_replies": decision.quick_replies,
            },
        )
        conversation.dialogue_state = state
        conversation.last_intent = nlu.intent
        conversation.sentiment = (
            nlu.sentiment
            if conversation.sentiment is None
            else round(0.6 * conversation.sentiment + 0.4 * nlu.sentiment, 3)
        )
        if decision.resolved and conversation.status is ConversationStatus.ACTIVE:
            conversation.resolved_by_ai = True
        if conversation.first_response_ms is None:
            conversation.first_response_ms = latency_ms
        await db.commit()

        get_workflow_engine().wake()
        await conversation_service.append_history(conversation.id, "user", text)
        await conversation_service.append_history(conversation.id, "assistant", reply_text)
        if (
            cache_key
            and not cache_hit
            and loop is not None
            and loop.text
            and not check.blocked
            and decision.kind == "llm"
            and all(c["name"] == "knowledge_search" for c in loop.tool_calls)
        ):
            try:
                await get_redis().set(
                    cache_key,
                    json.dumps({"text": reply_text, "citations": ctx.citations}),
                    ex=settings.faq_cache_ttl_seconds,
                )
            except Exception:
                log.debug("faq cache write skipped")
        await self._publish(tenant, conversation, user_msg, assistant_msg, nlu, decision, ctx, latency_ms)

        return AgentReply(
            conversation_id=conversation.id,
            message_id=assistant_msg.id,
            text=reply_text,
            handed_over=conversation.status is ConversationStatus.HANDED_OVER,
            status=conversation.status.value,
            intent=nlu.intent,
            confidence=round(nlu.confidence, 3),
            quick_replies=decision.quick_replies,
            citations=ctx.citations,
            latency_ms=latency_ms,
            debug={
                "decision": decision.kind,
                "nlu_source": nlu.source,
                "sentiment": nlu.sentiment,
                "entities": {k: v for k, v in nlu.entities.items() if k not in ("email", "phone")},
                "tools": loop.tool_calls if loop else [],
                "tokens": (loop.tokens_in + loop.tokens_out) if loop else 0,
                "cache_hit": cache_hit,
                "flow": state.get("flow"),
                "guardrail": check.reason,
                "rag": rag_outcome,
                "rag_confidence": round(rag.confidence, 3) if rag else None,
            },
        )

    async def _run_llm(
        self,
        db: AsyncSession,
        ctx: ToolContext,
        router: LLMRouter,
        history: list[dict[str, str]],
        text: str,
        pending_question: str | None,
        today: Any,
        tz_name: str,
        extra_context: str | None = None,
    ) -> LoopResult:
        async def on_usage(response: LLMResponse) -> None:
            await usage_service.record(
                db,
                tenant_id=ctx.tenant.id,
                purpose="chat",
                response=response,
                conversation_id=ctx.conversation.id,
            )

        system_prompt = build_system_prompt(
            tenant=ctx.tenant,
            agent=ctx.agent,
            today=today.strftime("%A %d %B %Y"),
            timezone=tz_name,
            pending_question=pending_question,
            extra_context=extra_context,
        )
        model = ctx.agent.llm_model
        return await run_agent_loop(
            router,
            system_prompt=system_prompt,
            history=history,
            user_text=text,
            tools=tool_schemas(list(ctx.agent.enabled_tools or [])),
            ctx=ctx,
            on_usage=on_usage,
            model=model,
            temperature=ctx.agent.temperature,
            max_iterations=settings.llm_max_tool_iterations,
        )

    async def _publish(
        self,
        tenant: Tenant,
        conversation: Any,
        user_msg: Any,
        assistant_msg: Any,
        nlu: Any,
        decision: Decision,
        ctx: ToolContext,
        latency_ms: int,
    ) -> None:
        cid = conversation.id
        await emit(
            "message.received",
            tenant.id,
            {
                "message_id": str(user_msg.id),
                "channel": conversation.channel.value,
                "intent": nlu.intent,
                "confidence": nlu.confidence,
                "sentiment": nlu.sentiment,
                "role": "user",
                "text": user_msg.content,
            },
            conversation_id=cid,
        )
        await emit(
            "message.sent",
            tenant.id,
            {
                "message_id": str(assistant_msg.id),
                "channel": conversation.channel.value,
                "latency_ms": latency_ms,
                "decision": decision.kind,
                "role": "assistant",
                "text": assistant_msg.content,
                "tokens": assistant_msg.tokens_in + assistant_msg.tokens_out,
            },
            conversation_id=cid,
        )
        for event_type, payload in ctx.events:
            await emit(event_type, tenant.id, payload, conversation_id=cid)
        if decision.kind == "handover":
            await emit(
                "handover.requested",
                tenant.id,
                {
                    "reason": conversation.handover_reason,
                    "channel": conversation.channel.value,
                },
                conversation_id=cid,
            )


_orchestrator: AgentOrchestrator | None = None


def get_orchestrator() -> AgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator()
    return _orchestrator
