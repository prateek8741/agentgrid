from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import DB, AdminAuth, AgentAuth, ViewerAuth
from app.core.errors import ConflictError, NotFoundError
from app.models import Appointment, AppointmentStatus, AvailabilitySlot
from app.schemas.common import Page
from app.schemas.crm import AppointmentOut, AppointmentStatusUpdate, SlotGenerate, SlotOut
from app.services import audit_service, booking_service
from app.workers.events import emit

router = APIRouter(tags=["booking"])


@router.get("/slots", response_model=list[SlotOut])
async def list_slots(
    auth: ViewerAuth,
    db: DB,
    start: date | None = None,
    days: int = Query(default=14, ge=1, le=92),
) -> list[SlotOut]:
    tz = booking_service.tenant_tz(auth.tenant)
    first = start or datetime.now(tz).date()
    lo = datetime.combine(first, time.min, tz).astimezone(UTC)
    hi = datetime.combine(first + timedelta(days=days), time.min, tz).astimezone(UTC)
    rows = (
        (
            await db.execute(
                select(AvailabilitySlot)
                .where(
                    AvailabilitySlot.tenant_id == auth.tenant_id,
                    AvailabilitySlot.start_at >= lo,
                    AvailabilitySlot.start_at < hi,
                )
                .order_by(AvailabilitySlot.start_at)
                .limit(2000)
            )
        )
        .scalars()
        .all()
    )
    return [SlotOut.model_validate(r) for r in rows]


@router.post(
    "/slots/generate",
    response_model=dict[str, int],
    status_code=status.HTTP_201_CREATED,
    summary="Create recurring availability slots",
)
async def generate_slots(body: SlotGenerate, request: Request, auth: AdminAuth, db: DB) -> dict[str, int]:
    created = await booking_service.generate_slots(db, auth.tenant, **body.model_dump())
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="slots.generate",
        resource_type="slot",
        actor=auth.user,
        request=request,
        details={"created": created, **body.model_dump(mode="json")},
    )
    await db.commit()
    return {"created": created}


@router.delete("/slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_slot(slot_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> Response:
    slot = (
        await db.execute(
            select(AvailabilitySlot).where(
                AvailabilitySlot.id == slot_id, AvailabilitySlot.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if slot is None:
        raise NotFoundError("Slot not found")
    if slot.booked_count > 0:
        raise ConflictError("Slot has bookings; cancel them first or deactivate the slot")
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="slot.delete",
        resource_type="slot",
        resource_id=slot.id,
        actor=auth.user,
        request=request,
    )
    await db.delete(slot)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/appointments", response_model=Page[AppointmentOut])
async def list_appointments(
    auth: ViewerAuth,
    db: DB,
    status_filter: AppointmentStatus | None = Query(default=None, alias="status"),
    upcoming: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AppointmentOut]:
    stmt = select(Appointment).where(Appointment.tenant_id == auth.tenant_id)
    if status_filter:
        stmt = stmt.where(Appointment.status == status_filter)
    if upcoming:
        stmt = stmt.where(Appointment.start_at >= datetime.now(UTC))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    order = Appointment.start_at.asc() if upcoming else Appointment.start_at.desc()
    rows = (await db.execute(stmt.order_by(order).limit(limit).offset(offset))).scalars().all()
    return Page(
        items=[AppointmentOut.model_validate(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


@router.patch(
    "/appointments/{appointment_id}",
    response_model=AppointmentOut,
    summary="Cancel / complete / mark no-show",
)
async def update_appointment(
    appointment_id: uuid.UUID, body: AppointmentStatusUpdate, request: Request, auth: AgentAuth, db: DB
) -> AppointmentOut:
    appt = (
        await db.execute(
            select(Appointment).where(
                Appointment.id == appointment_id, Appointment.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if appt is None:
        raise NotFoundError("Appointment not found")
    if body.status is AppointmentStatus.CANCELLED:
        await booking_service.cancel(db, auth.tenant, appt, body.reason)
    elif body.status is AppointmentStatus.BOOKED:
        raise ConflictError("Re-booking a cancelled appointment is not supported; create a new booking")
    else:
        appt.status = body.status
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="appointment.update",
        resource_type="appointment",
        resource_id=appt.id,
        actor=auth.user,
        request=request,
        details={"status": body.status.value},
    )
    await db.commit()
    await db.refresh(appt)
    if body.status is AppointmentStatus.CANCELLED:
        await emit(
            "appointment.cancelled",
            auth.tenant_id,
            {"appointment_id": str(appt.id), "reference": appt.reference},
        )
    return AppointmentOut.model_validate(appt)
