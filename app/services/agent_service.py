"""CRUD for a tenant's AI agent configurations."""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.core.errors import ConflictError, NotFoundError
from app.models import Agent
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import audit_service


async def list_agents(db: AsyncSession, tenant_id: uuid.UUID) -> list[Agent]:
    stmt = (
        select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.is_default.desc(), Agent.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_agent(db: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if agent is None:
        raise NotFoundError("Agent not found")
    return agent


async def get_default_agent(db: AsyncSession, tenant_id: uuid.UUID) -> Agent | None:
    stmt = (
        select(Agent)
        .where(Agent.tenant_id == tenant_id, Agent.is_active.is_(True))
        .order_by(Agent.is_default.desc(), Agent.created_at)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _clear_default(db: AsyncSession, tenant_id: uuid.UUID, keep: uuid.UUID | None) -> None:
    stmt = update(Agent).where(Agent.tenant_id == tenant_id, Agent.is_default.is_(True))
    if keep is not None:
        stmt = stmt.where(Agent.id != keep)
    await db.execute(stmt.values(is_default=False))


async def create_agent(
    db: AsyncSession, auth: AuthContext, data: AgentCreate, request: Request | None
) -> Agent:
    agent = Agent(tenant_id=auth.tenant_id, **data.model_dump())
    db.add(agent)
    await db.flush()
    if agent.is_default:
        await _clear_default(db, auth.tenant_id, keep=agent.id)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="agent.create",
        resource_type="agent",
        resource_id=agent.id,
        actor=auth.user,
        request=request,
        details={"name": agent.name, "template": agent.template_key},
    )
    await db.commit()
    await db.refresh(agent)
    return agent


async def update_agent(
    db: AsyncSession, auth: AuthContext, agent_id: uuid.UUID, data: AgentUpdate, request: Request | None
) -> Agent:
    agent = await get_agent(db, auth.tenant_id, agent_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_default") is False and agent.is_default:
        raise ConflictError("Mark another agent as default instead of unsetting the default")
    for field, value in changes.items():
        setattr(agent, field, value)
    if changes.get("is_default"):
        await _clear_default(db, auth.tenant_id, keep=agent.id)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="agent.update",
        resource_type="agent",
        resource_id=agent.id,
        actor=auth.user,
        request=request,
        details={"fields": sorted(changes)},
    )
    await db.commit()
    await db.refresh(agent)
    return agent


async def delete_agent(
    db: AsyncSession, auth: AuthContext, agent_id: uuid.UUID, request: Request | None
) -> None:
    agent = await get_agent(db, auth.tenant_id, agent_id)
    if agent.is_default:
        raise ConflictError("The default agent cannot be deleted; make another agent default first")
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="agent.delete",
        resource_type="agent",
        resource_id=agent.id,
        actor=auth.user,
        request=request,
        details={"name": agent.name},
    )
    await db.delete(agent)
    await db.commit()
