from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app.agents.orchestrator import get_orchestrator
from app.agents.templates import TEMPLATES, AgentTemplate, get_template, render
from app.api.deps import DB, AdminAuth, AgentAuth, ViewerAuth
from app.channels.schema import AgentReply, ContactInfo, InboundMessage
from app.core.errors import NotFoundError
from app.core.rate_limit import enforce
from app.models import Channel
from app.schemas.agent import AgentCreate, AgentOut, AgentUpdate
from app.schemas.conversation import PlaygroundRequest
from app.services import agent_service

templates_router = APIRouter(prefix="/agent-templates", tags=["agents"])
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


# ---------------------------------------------------------------- templates
class FromTemplateRequest(BaseModel):
    template_key: str = Field(max_length=60)
    name: str | None = Field(default=None, max_length=120)
    make_default: bool = False


@templates_router.get("", response_model=list[AgentTemplate], summary="Industry agent template library")
async def list_templates(_auth: ViewerAuth) -> list[AgentTemplate]:
    return list(TEMPLATES.values())


@templates_router.get("/{template_key}", response_model=AgentTemplate)
async def get_template_detail(template_key: str, _auth: ViewerAuth) -> AgentTemplate:
    template = get_template(template_key)
    if template is None:
        raise NotFoundError("Template not found")
    return template


@router.post(
    "/from-template",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent from an industry template in one click",
)
async def create_from_template(
    body: FromTemplateRequest, request: Request, auth: AdminAuth, db: DB
) -> AgentOut:
    template = get_template(body.template_key)
    if template is None:
        raise NotFoundError("Template not found")
    fields = template.agent_fields()
    fields["persona"] = render(fields["persona"], auth.tenant.name)
    fields["greeting"] = render(fields["greeting"], auth.tenant.name)
    data = AgentCreate(name=body.name or template.name, is_default=body.make_default, **fields)
    return AgentOut.model_validate(await agent_service.create_agent(db, auth, data, request))


# ---------------------------------------------------------------- playground
@router.post(
    "/{agent_id}/playground", response_model=AgentReply, summary="Chat with an agent from the console"
)
async def playground(agent_id: uuid.UUID, body: PlaygroundRequest, auth: AgentAuth, db: DB) -> AgentReply:
    await enforce("playground", str(auth.user.id), 30)
    session = body.session_id or uuid.uuid4().hex[:12]
    inbound = InboundMessage(
        tenant_id=auth.tenant_id,
        channel=Channel.PLAYGROUND,
        session_id=f"pg-{auth.user.id.hex[:8]}-{session}",
        text=body.message,
        agent_id=agent_id,
        contact=ContactInfo(external_id=f"console-{auth.user.id}"),
    )
    reply = await get_orchestrator().handle(db, inbound)
    reply.debug["session_id"] = session
    return reply
