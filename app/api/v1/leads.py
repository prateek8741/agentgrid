from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.api.deps import DB, AgentAuth, ViewerAuth
from app.models import LeadStatus, LeadTemperature
from app.schemas.common import Page
from app.schemas.crm import LeadCreate, LeadOut, LeadUpdate
from app.services import audit_service, lead_service
from app.workers.events import emit
from app.workers.workflow import get_workflow_engine

router = APIRouter(prefix="/leads", tags=["crm"])


@router.get("", response_model=Page[LeadOut])
async def list_leads(
    auth: ViewerAuth,
    db: DB,
    status_filter: LeadStatus | None = Query(default=None, alias="status"),
    temperature: LeadTemperature | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[LeadOut]:
    rows, total = await lead_service.list_leads(
        db, auth.tenant_id, status=status_filter, temperature=temperature, q=q, limit=limit, offset=offset
    )
    return Page(items=[LeadOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/pipeline", response_model=dict[str, int], summary="Lead count per pipeline stage")
async def pipeline(auth: ViewerAuth, db: DB) -> dict[str, int]:
    return await lead_service.pipeline_counts(db, auth.tenant_id)


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED, summary="Add a lead manually")
async def create_lead(body: LeadCreate, request: Request, auth: AgentAuth, db: DB) -> LeadOut:
    lead, _ = await lead_service.capture_lead(
        db, tenant_id=auth.tenant_id, fields=body.model_dump(exclude_none=True)
    )
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="lead.create",
        resource_type="lead",
        resource_id=lead.id,
        actor=auth.user,
        request=request,
    )
    await db.commit()
    await db.refresh(lead)
    get_workflow_engine().wake()
    await emit("lead.created", auth.tenant_id, lead_service.lead_event_payload(lead))
    return LeadOut.model_validate(lead)


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(lead_id: uuid.UUID, auth: ViewerAuth, db: DB) -> LeadOut:
    return LeadOut.model_validate(await lead_service.get_lead(db, auth.tenant_id, lead_id))


@router.patch("/{lead_id}", response_model=LeadOut, summary="Update a lead / move it through the pipeline")
async def update_lead(
    lead_id: uuid.UUID, body: LeadUpdate, request: Request, auth: AgentAuth, db: DB
) -> LeadOut:
    lead = await lead_service.get_lead(db, auth.tenant_id, lead_id)
    changed = await lead_service.update_lead(db, lead, body.model_dump(exclude_unset=True))
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="lead.update",
        resource_type="lead",
        resource_id=lead.id,
        actor=auth.user,
        request=request,
        details={"fields": changed},
    )
    await db.commit()
    await db.refresh(lead)
    await emit("lead.updated", auth.tenant_id, lead_service.lead_event_payload(lead))
    return LeadOut.model_validate(lead)
