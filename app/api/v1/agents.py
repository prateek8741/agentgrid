from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status

from app.api.deps import DB, AdminAuth, ViewerAuth
from app.schemas.agent import AgentCreate, AgentOut, AgentUpdate
from app.services import agent_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(auth: ViewerAuth, db: DB) -> list[AgentOut]:
    return [AgentOut.model_validate(a) for a in await agent_service.list_agents(db, auth.tenant_id)]


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, request: Request, auth: AdminAuth, db: DB) -> AgentOut:
    return AgentOut.model_validate(await agent_service.create_agent(db, auth, body, request))


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: uuid.UUID, auth: ViewerAuth, db: DB) -> AgentOut:
    return AgentOut.model_validate(await agent_service.get_agent(db, auth.tenant_id, agent_id))


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdate, request: Request, auth: AdminAuth, db: DB
) -> AgentOut:
    return AgentOut.model_validate(await agent_service.update_agent(db, auth, agent_id, body, request))


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> Response:
    await agent_service.delete_agent(db, auth, agent_id, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
