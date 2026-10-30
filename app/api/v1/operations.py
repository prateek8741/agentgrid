"""Conversations (read), workflow action log and LLM usage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select, update

from app.agents.llm.router import get_llm_router
from app.api.deps import DB, AdminAuth, ViewerAuth
from app.core.errors import ConflictError, NotFoundError
from app.models import ActionLog, ActionStatus, Channel, Conversation, ConversationStatus, Message
from app.schemas.common import Page
from app.schemas.conversation import ActionLogOut, ConversationOut, MessageOut, UsageOut
from app.services import audit_service, usage_service
from app.workers.workflow import get_workflow_engine

router = APIRouter(tags=["operations"])


@router.get("/conversations", response_model=Page[ConversationOut])
async def list_conversations(
    auth: ViewerAuth,
    db: DB,
    status_filter: ConversationStatus | None = Query(default=None, alias="status"),
    channel: Channel | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ConversationOut]:
    stmt = select(Conversation).where(Conversation.tenant_id == auth.tenant_id)
    if status_filter:
        stmt = stmt.where(Conversation.status == status_filter)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Conversation.last_message_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page(
        items=[ConversationOut.model_validate(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: uuid.UUID, auth: ViewerAuth, db: DB) -> ConversationOut:
    conv = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise NotFoundError("Conversation not found")
    return ConversationOut.model_validate(conv)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def conversation_messages(
    conversation_id: uuid.UUID, auth: ViewerAuth, db: DB, limit: int = Query(default=200, ge=1, le=1000)
) -> list[MessageOut]:
    exists = (
        await db.execute(
            select(Conversation.id).where(
                Conversation.id == conversation_id, Conversation.tenant_id == auth.tenant_id
            )
        )
    ).first()
    if exists is None:
        raise NotFoundError("Conversation not found")
    rows = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.tenant_id == auth.tenant_id)
                .order_by(Message.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [MessageOut.model_validate(m) for m in rows]


@router.get("/actions", response_model=Page[ActionLogOut], summary="Workflow action log")
async def list_actions(
    auth: AdminAuth,
    db: DB,
    status_filter: ActionStatus | None = Query(default=None, alias="status"),
    action_type: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ActionLogOut]:
    stmt = select(ActionLog).where(ActionLog.tenant_id == auth.tenant_id)
    if status_filter:
        stmt = stmt.where(ActionLog.status == status_filter)
    if action_type:
        stmt = stmt.where(ActionLog.action_type == action_type)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(ActionLog.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page(
        items=[ActionLogOut.model_validate(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


@router.post("/actions/{action_id}/retry", response_model=ActionLogOut, summary="Retry a failed action now")
async def retry_action(action_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> ActionLogOut:
    action = (
        await db.execute(
            select(ActionLog).where(ActionLog.id == action_id, ActionLog.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if action is None:
        raise NotFoundError("Action not found")
    if action.status is not ActionStatus.FAILED:
        raise ConflictError("Only failed actions can be retried")
    await db.execute(
        update(ActionLog)
        .where(ActionLog.id == action.id)
        .values(status=ActionStatus.PENDING, run_at=datetime.now(UTC), max_attempts=ActionLog.attempts + 3)
        .execution_options(synchronize_session=False)
    )
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="action.retry",
        resource_type="action",
        resource_id=action.id,
        actor=auth.user,
        request=request,
    )
    await db.commit()
    await db.refresh(action)
    get_workflow_engine().wake()
    return ActionLogOut.model_validate(action)


@router.get("/usage", response_model=UsageOut, summary="Token usage this month vs quota")
async def usage(auth: ViewerAuth, db: DB) -> UsageOut:
    status = await usage_service.quota_status(db, auth.tenant)
    return UsageOut(
        month=datetime.now(UTC).strftime("%Y-%m"),
        tokens_used=status.used,
        monthly_quota=status.quota,
        remaining=status.remaining,
        exceeded=status.exceeded,
        llm_providers=get_llm_router().provider_names,
    )
