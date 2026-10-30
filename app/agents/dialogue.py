"""Dialogue manager: deterministic state machine + routing to the LLM.

Structured flows (lead qualification, booking, cancellation) are driven by a
state machine stored on `conversation.dialogue_state`, so they are
predictable, cheap and work without an LLM. Open questions are routed to the
LLM + tools (+ RAG). Guardrails, handover rules and low-confidence
clarification are applied first.

State shape:
    {"flow": "booking" | "lead_qualification" | "cancel_appointment" | None,
     "step": int, "slots": {...}, "retries": int, "offered": [...],
     "profile": {"name", "phone", "email"}, "offer": "booking" | None,
     "clarifications": int}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from app.agents import parsing
from app.agents.guardrails import find_blocked_term
from app.agents.nlu import NLUResult
from app.agents.templates import DEFAULT_HANDOVER_RULES, render
from app.agents.tools.base import ToolContext, run_tool
from app.services import booking_service

EXIT_RE = re.compile(r"^\s*(stop|never ?mind|forget it|exit|quit|start over|cancel that)\s*[.!]*\s*$", re.I)

DEFAULT_QUALIFICATION = [
    {"slot": "need", "question": "What are you looking for?", "kind": "text"},
    {"slot": "name", "question": "May I have your name?", "kind": "name"},
    {"slot": "contact", "question": "What's the best phone number or email to reach you?", "kind": "contact"},
]


@dataclass
class Decision:
    kind: Literal["reply", "llm", "handover"]
    text: str | None = None
    reason: str | None = None
    pending_question: str | None = None
    quick_replies: list[str] = field(default_factory=list)
    resolved: bool = False
    flow_event: str | None = None  # e.g. "lead_qualification.completed"


class DialogueManager:
    def __init__(self, ctx: ToolContext, today: date, llm_available: bool) -> None:
        self.ctx = ctx
        self.agent = ctx.agent
        self.tenant = ctx.tenant
        self.today = today
        self.llm_available = llm_available
        self.rules = {**DEFAULT_HANDOVER_RULES, **(self.agent.handover_rules or {})}
        self.flows_cfg: dict[str, Any] = self.agent.flow or {}
        self.intent_flows = {i["name"]: i.get("flow") for i in (self.agent.intents or []) if i.get("flow")}

    # ------------------------------------------------------------------ helpers
    @property
    def business(self) -> str:
        return self.tenant.name

    def _booking_label(self) -> str:
        return (self.flows_cfg.get("booking") or {}).get("label", "appointment")

    def _services(self) -> list[str]:
        return list((self.flows_cfg.get("booking") or {}).get("services") or [])

    def _flow_available(self, flow: str) -> bool:
        tools = set(self.agent.enabled_tools or [])
        if flow == "booking":
            return "book_appointment" in tools
        if flow == "cancel_appointment":
            return "cancel_appointment" in tools
        if flow == "lead_qualification":
            return "capture_lead" in tools
        return False

    def _quick_replies(self) -> list[str]:
        replies = []
        if self._flow_available("booking"):
            replies.append(f"Book a {self._booking_label()}")
        replies.append("Talk to a human")
        return replies

    @staticmethod
    def _clear(state: dict[str, Any]) -> None:
        for key in ("flow", "step", "slots", "retries", "offered"):
            state.pop(key, None)

    # ------------------------------------------------------------------ entry point
    async def decide(self, state: dict[str, Any], nlu: NLUResult, text: str) -> Decision:
        profile = state.setdefault("profile", {})
        contact = self.ctx.contact
        for key in ("name", "phone", "email"):
            if not profile.get(key) and getattr(contact, key, None):
                profile[key] = getattr(contact, key)

        blocked_terms = (self.tenant.settings or {}).get("blocked_terms") or []
        if nlu.injection_suspected:
            return Decision(
                "reply", f"I can only help with questions about {self.business}. What can I do for you?"
            )
        if find_blocked_term(text, blocked_terms):
            return Decision(
                "reply", "Sorry, I can't help with that topic. Is there anything else I can help with?"
            )
        if nlu.intent == "human_handover":
            return Decision("handover", reason="customer_request")
        if nlu.intent in (self.rules.get("urgent_intents") or []) or any(
            kw in text.lower() for kw in (self.rules.get("urgent_keywords") or [])
        ):
            return Decision("handover", reason="urgent")
        if nlu.sentiment <= float(self.rules["negative_sentiment"]):
            return Decision("handover", reason="negative_sentiment")

        if state.get("flow"):
            return await self._continue_flow(state, nlu, text)

        offer = state.pop("offer", None)
        if offer and nlu.intent == "affirm" and self._flow_available(offer):
            return await self._start_flow(state, offer, nlu, text)
        if nlu.intent in ("affirm", "deny") and (offer or not self.llm_available):
            # A bare yes/no with nothing pending: without an LLM there is no context to act on.
            return Decision(
                "reply",
                "Okay! Is there anything else I can help you with?",
                quick_replies=self._quick_replies(),
            )

        flow = self.intent_flows.get(nlu.intent)
        if flow and self._flow_available(flow):
            return await self._start_flow(state, flow, nlu, text)

        if nlu.intent == "greeting":
            return Decision(
                "reply", render(self.agent.greeting, self.business), quick_replies=self._quick_replies()
            )
        if nlu.intent == "goodbye":
            return Decision(
                "reply", f"Thank you for contacting {self.business}! Have a great day.", resolved=True
            )
        if nlu.intent == "out_of_scope":
            scope = self.flows_cfg.get("scope") or "our products and services"
            return Decision(
                "reply", f"I'm here to help with {scope}. Is there something along those lines I can do?"
            )

        if nlu.confidence < float(self.rules["low_confidence"]) or nlu.intent == "unclear":
            count = int(state.get("clarifications", 0))
            if count < int(self.rules.get("max_clarifications", 1)):
                state["clarifications"] = count + 1
                return Decision(
                    "reply",
                    "Sorry, I didn't quite get that. Could you rephrase? For example, you can ask about our "
                    "services or book an appointment.",
                    quick_replies=self._quick_replies(),
                )
            state["clarifications"] = 0
            return Decision("handover", reason="low_confidence")
        state["clarifications"] = 0

        if self.llm_available:
            return Decision("llm")
        return Decision(
            "reply",
            "I'm not able to answer that right now. Would you like me to connect you with our team?",
            quick_replies=["Talk to a human"],
        )

    # ------------------------------------------------------------------ flows
    def _steps(self, flow: str) -> list[dict[str, Any]]:
        if flow == "lead_qualification":
            return list(self.agent.qualification_questions or DEFAULT_QUALIFICATION)
        if flow == "booking":
            steps: list[dict[str, Any]] = []
            if len(self._services()) > 1:
                steps.append(
                    {
                        "slot": "service",
                        "kind": "service",
                        "question": f"Which service would you like to book? ({', '.join(self._services())})",
                    }
                )
            steps += [
                {
                    "slot": "date",
                    "kind": "date",
                    "question": "Which day works for you? (e.g. tomorrow, Friday, 12 Nov)",
                },
                {"slot": "slot", "kind": "slot_choice", "question": ""},
                {"slot": "name", "kind": "name", "question": "May I have your name for the booking?"},
                {"slot": "phone", "kind": "phone", "question": "And your phone number?"},
                {"slot": "confirm", "kind": "confirm", "question": ""},
            ]
            return steps
        if flow == "cancel_appointment":
            return [
                {
                    "slot": "lookup",
                    "kind": "text",
                    "question": "Please share your booking reference (e.g. APT-ABC123) "
                    "and the phone number used for the booking.",
                },
                {"slot": "confirm", "kind": "confirm", "question": ""},
            ]
        return []

    async def _start_flow(self, state: dict[str, Any], flow: str, nlu: NLUResult, text: str) -> Decision:
        self._clear(state)
        state.update({"flow": flow, "step": 0, "slots": {}, "retries": 0})
        # Pre-fill from what the customer already said ("book me tomorrow at 10").
        for step in self._steps(flow):
            if step["kind"] in ("slot_choice", "confirm"):
                continue
            value = self._extract(step, text, nlu, state, prefill=True)
            if value is not None:
                state["slots"][step["slot"]] = value
        if nlu.entities.get("time"):
            state["slots"]["preferred_time"] = nlu.entities["time"]
        if flow == "cancel_appointment":
            upcoming = await booking_service.find_upcoming(
                self.ctx.db, self.tenant, contact_id=self.ctx.contact.id
            )
            if upcoming:
                state["slots"]["lookup"] = {"appointment_reference": upcoming[0].reference, "own": True}
        return await self._advance(state)

    async def _continue_flow(self, state: dict[str, Any], nlu: NLUResult, text: str) -> Decision:
        flow = state["flow"]
        if EXIT_RE.match(text):
            self._clear(state)
            return Decision(
                "reply",
                "No problem, I've stopped that. What else can I help you with?",
                quick_replies=self._quick_replies(),
            )
        # Switching to another structured flow mid-way (e.g. lead form -> "book a visit").
        new_flow = self.intent_flows.get(nlu.intent)
        if new_flow and new_flow != flow and self._flow_available(new_flow) and nlu.confidence >= 0.8:
            return await self._start_flow(state, new_flow, nlu, text)

        steps = self._steps(flow)
        index = int(state.get("step", 0))
        if index >= len(steps):
            self._clear(state)
            return Decision("llm") if self.llm_available else Decision("reply", "How else can I help?")
        step = steps[index]

        if step["kind"] == "confirm":
            return await self._handle_confirm(state, flow, text, nlu)

        value = self._extract(step, text, nlu, state, prefill=False)
        if value is None:
            looks_like_question = text.strip().endswith("?") or nlu.intent in (
                "question",
                "pricing",
                "services_info",
                "timings",
            )
            pending = await self._question_for(state, step)
            if looks_like_question and self.llm_available:
                return Decision("llm", pending_question=pending)
            state["retries"] = int(state.get("retries", 0)) + 1
            if state["retries"] >= 3:
                self._clear(state)
                return Decision("handover", reason="flow_stuck")
            return Decision(
                "reply", self._hint(step) + " " + pending, quick_replies=self._step_replies(state, step)
            )
        state["slots"][step["slot"]] = value
        state["retries"] = 0
        return await self._advance(state)

    async def _advance(self, state: dict[str, Any]) -> Decision:
        flow = state["flow"]
        steps = self._steps(flow)
        profile = state.get("profile", {})
        for index, step in enumerate(steps):
            slot = step["slot"]
            if slot in state["slots"]:
                continue
            if slot in ("name", "phone", "email") and profile.get(slot):
                state["slots"][slot] = profile[slot]
                continue
            if slot == "contact" and (profile.get("phone") or profile.get("email")):
                state["slots"]["contact"] = {"phone": profile.get("phone"), "email": profile.get("email")}
                continue
            if not step.get("required", True) and step["kind"] != "confirm":
                continue
            state["step"] = index
            if step["kind"] == "confirm":
                return self._confirm_prompt(state, flow)
            question = await self._question_for(state, step)
            if step["kind"] == "slot_choice":
                if not state.get("offered"):
                    self._clear(state)
                    return Decision("reply", question, quick_replies=["Talk to a human"])
                if slot in state["slots"]:  # the customer's preferred time was available
                    return await self._advance(state)
            return Decision("reply", question, quick_replies=self._step_replies(state, step))
        return await self._complete(state)

    # ------------------------------------------------------------------ slot extraction
    def _extract(
        self, step: dict[str, Any], text: str, nlu: NLUResult, state: dict[str, Any], *, prefill: bool
    ) -> Any:
        kind, entities = step["kind"], nlu.entities
        profile = state.setdefault("profile", {})
        if kind == "name":
            name = entities.get("name") if isinstance(entities.get("name"), str) else None
            name = parsing.parse_name(name or "") if name else (None if prefill else parsing.parse_name(text))
            if name:
                profile["name"] = name
            return name
        if kind in ("phone", "contact", "email"):
            phone = parsing.parse_phone(text) or (
                parsing.normalise_phone(str(entities["phone"])) if entities.get("phone") else None
            )
            email = parsing.parse_email(text)
            if phone:
                profile["phone"] = phone
            if email:
                profile["email"] = email
            if kind == "phone":
                return phone
            if kind == "email":
                return email
            return {"phone": phone, "email": email} if (phone or email) else None
        if kind == "date":
            raw = entities.get("date")
            if raw:
                try:
                    return date.fromisoformat(str(raw)).isoformat()
                except ValueError:
                    pass
            parsed = parsing.parse_date(text, self.today)
            return parsed.isoformat() if parsed else None
        if kind == "service":
            lowered = text.lower()
            for i, service in enumerate(self._services(), start=1):
                if service.lower() in lowered or lowered.strip() == str(i):
                    return service
            return None
        if kind == "budget":
            if prefill:
                return entities.get("budget") or (text if entities.get("budget_amount") else None)
            if re.search(r"\d", text) or re.search(r"\b(flexible|open|not sure|any)\b", text, re.I):
                return text.strip()[:120]
            return None
        if kind == "timeline":
            if prefill:
                return entities.get("timeline")
            if parsing.parse_timeline_days(text) is not None or len(text.split()) <= 8:
                return text.strip()[:120]
            return None
        if kind == "slot_choice":
            return self._choose_slot(text, nlu, state)
        if kind == "text":
            if prefill:
                value = entities.get(step["slot"])
                if value:
                    return str(value)[:2000]
                # The opening message usually *is* the need ("I want a 3BHK in Whitefield").
                return text.strip()[:2000] if step["slot"] == "need" and len(text.split()) >= 4 else None
            if step["slot"] == "lookup":
                ref = re.search(r"\b(APT-[A-Z0-9]{6})\b", text.upper())
                phone = parsing.parse_phone(text)
                return (
                    {"appointment_reference": ref.group(1) if ref else None, "phone": phone}
                    if (ref or phone)
                    else None
                )
            return text.strip()[:2000] if len(text.strip()) >= 2 else None
        return None

    def _choose_slot(self, text: str, nlu: NLUResult, state: dict[str, Any]) -> str | None:
        offered = state.get("offered") or []
        if not offered:
            return None
        lowered = text.lower().strip()
        ordinals = {
            "first": 0,
            "1st": 0,
            "second": 1,
            "2nd": 1,
            "third": 2,
            "3rd": 2,
            "fourth": 3,
            "fifth": 4,
            "last": len(offered) - 1,
        }
        m = re.fullmatch(r"(?:option\s*)?#?(\d)\.?", lowered)
        if m and 1 <= int(m.group(1)) <= len(offered):
            return offered[int(m.group(1)) - 1]["id"]
        for word, idx in ordinals.items():
            if re.search(rf"\b{word}\b", lowered) and idx < len(offered):
                return offered[idx]["id"]
        wanted = nlu.entities.get("time") or (
            parsing.parse_time(text).strftime("%H:%M") if parsing.parse_time(text) else None
        )
        if wanted:
            for slot in offered:
                if slot["time"] == wanted:
                    return slot["id"]
        for slot in offered:
            if slot["label"].lower() in lowered:
                return slot["id"]
        return None

    # ------------------------------------------------------------------ prompts
    async def _question_for(self, state: dict[str, Any], step: dict[str, Any]) -> str:
        if step["kind"] != "slot_choice":
            return step["question"]
        tz = booking_service.tenant_tz(self.tenant)
        day = date.fromisoformat(state["slots"]["date"]) if state["slots"].get("date") else None
        service = state["slots"].get("service")
        slots = await booking_service.available_slots(
            self.ctx.db, self.tenant, day=day, service=service, limit=5
        )
        note = ""
        if not slots and day is not None:
            slots = await booking_service.available_slots(self.ctx.db, self.tenant, service=service, limit=5)
            note = f"There are no openings on {day.strftime('%a %d %b')}. "
        if not slots:
            state["offered"] = []
            return (
                "Sorry, there are no open slots in the next 7 days. "
                "Would you like me to connect you with our team to find a time?"
            )
        state["offered"] = [
            {
                "id": str(s.id),
                "label": booking_service.format_slot(s.start_at, tz),
                "time": s.start_at.astimezone(tz).strftime("%H:%M"),
            }
            for s in slots
        ]
        preferred = state["slots"].pop("preferred_time", None)
        if preferred:
            match = next((o for o in state["offered"] if o["time"] == preferred), None)
            if match:
                state["slots"]["slot"] = match["id"]
        lines = "\n".join(f"{i}. {o['label']}" for i, o in enumerate(state["offered"], start=1))
        return f"{note}Here are the available times:\n{lines}\nWhich one works for you?"

    def _step_replies(self, state: dict[str, Any], step: dict[str, Any]) -> list[str]:
        if step["kind"] == "slot_choice":
            return [o["label"] for o in state.get("offered", [])][:5]
        if step["kind"] == "service":
            return self._services()[:5]
        if step["kind"] == "date":
            return ["Today", "Tomorrow"]
        return []

    @staticmethod
    def _hint(step: dict[str, Any]) -> str:
        return {
            "phone": "That doesn't look like a valid phone number.",
            "contact": "I need a valid phone number or email.",
            "date": "Sorry, I couldn't understand the date.",
            "slot_choice": "Please pick one of the listed times.",
            "service": "Please choose one of the listed services.",
            "name": "Sorry, I didn't catch your name.",
        }.get(step["kind"], "Sorry, I didn't catch that.")

    def _confirm_prompt(self, state: dict[str, Any], flow: str) -> Decision:
        slots = state["slots"]
        if flow == "booking":
            label = next(
                (o["label"] for o in state.get("offered", []) if o["id"] == slots.get("slot")),
                "the chosen time",
            )
            service = f" for {slots['service']}" if slots.get("service") else ""
            noun = self._booking_label()
            article = "an" if noun[:1].lower() in "aeiou" else "a"
            text = (
                f"Please confirm: {article} {noun}{service} on {label}, for {slots.get('name')} "
                f"({slots.get('phone')}). Shall I book it?"
            )
        else:
            lookup = slots.get("lookup") or {}
            text = f"Shall I cancel your appointment {lookup.get('appointment_reference') or ''}?".replace(
                "  ", " "
            )
        return Decision("reply", text, quick_replies=["Yes", "No"])

    async def _handle_confirm(self, state: dict[str, Any], flow: str, text: str, nlu: NLUResult) -> Decision:
        if nlu.intent == "affirm" or parsing.is_yes(text):
            return await self._complete(state)
        if nlu.intent == "deny" or parsing.is_no(text):
            if flow == "booking":
                for key in ("date", "slot", "confirm"):
                    state["slots"].pop(key, None)
                state["offered"] = []
                return await self._advance(state)
            self._clear(state)
            return Decision("reply", "Okay, I haven't cancelled anything. Anything else I can help with?")
        return Decision("reply", "Please reply yes or no.", quick_replies=["Yes", "No"])

    # ------------------------------------------------------------------ completion
    async def _complete(self, state: dict[str, Any]) -> Decision:
        flow = state["flow"]
        slots = state["slots"]
        if flow == "lead_qualification":
            contact = slots.get("contact") or {}
            args = {
                k: v
                for k, v in {
                    "name": slots.get("name"),
                    "need": slots.get("need"),
                    "budget": slots.get("budget"),
                    "timeline": slots.get("timeline"),
                    "phone": contact.get("phone") if isinstance(contact, dict) else None,
                    "email": contact.get("email") if isinstance(contact, dict) else None,
                }.items()
                if v
            }
            result = await run_tool(self.ctx, "capture_lead", args)
            self._clear(state)
            if "error" in result:
                return Decision("handover", reason="flow_error")
            message = (self.flows_cfg.get("lead_qualification") or {}).get(
                "completion", "Thanks {name}! Our team will contact you shortly."
            )
            decision = Decision(
                "reply",
                render(message, self.business).replace("{name}", args.get("name", "")).strip(),
                flow_event="lead_qualification.completed",
            )
            if self._flow_available("booking") and "book" in decision.text.lower():
                state["offer"] = "booking"
                decision.quick_replies = ["Yes", "No"]
            return decision

        if flow == "booking":
            result = await run_tool(
                self.ctx,
                "book_appointment",
                {
                    "slot_id": slots["slot"],
                    "name": slots.get("name") or "Customer",
                    "phone": slots.get("phone"),
                    "service": slots.get("service"),
                },
            )
            if "error" in result:
                if "taken" in str(result["error"]) or "past" in str(result["error"]):
                    slots.pop("slot", None)
                    state["offered"] = []
                    retry = await self._advance(state)
                    retry.text = f"Sorry, that time was just taken. {retry.text}"
                    return retry
                self._clear(state)
                return Decision("handover", reason="flow_error")
            self._clear(state)
            return Decision(
                "reply",
                f"You're booked! {result['when']}. Your reference is {result['reference']}. "
                "We'll send you a reminder before your appointment.",
                flow_event="booking.completed",
                resolved=True,
            )

        if flow == "cancel_appointment":
            lookup = slots.get("lookup") or {}
            result = await run_tool(
                self.ctx,
                "cancel_appointment",
                {
                    "reference": lookup.get("appointment_reference"),
                    "phone": lookup.get("phone"),
                },
            )
            self._clear(state)
            if "error" in result:
                return Decision(
                    "reply",
                    f"I couldn't cancel it: {result['error']}. Would you like to talk to our team?",
                    quick_replies=["Talk to a human"],
                )
            if "multiple" in result:
                return Decision(
                    "reply",
                    "You have several upcoming appointments. Please share the reference of the "
                    "one to cancel.",
                )
            return Decision(
                "reply",
                f"Done, your appointment {result['reference']} on {result['when']} is cancelled.",
                flow_event="cancel.completed",
                resolved=True,
            )
        self._clear(state)
        return Decision("reply", "How else can I help?")
