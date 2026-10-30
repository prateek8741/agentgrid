"""Built-in calendar: availability slots, booking, cancellation, reminders.

Slots are stored in UTC and presented in the tenant's timezone. Booking
claims capacity with a conditional UPDATE (`booked_count < capacity`), so two
customers can never take the last place in a slot at the same time.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Appointment, AppointmentStatus, AvailabilitySlot, Contact, Conversation, Tenant
from app.workers.workflow import enqueue

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def tenant_tz(tenant: Tenant) -> ZoneInfo:
    try:
        return ZoneInfo((tenant.settings or {}).get("timezone") or "Asia/Kolkata")
    except Exception:  # invalid tz in legacy data: fall back rather than fail a booking
        return ZoneInfo("UTC")


def new_reference(prefix: str) -> str:
    return f"{prefix}-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


def format_slot(slot_start: datetime, tz: ZoneInfo) -> str:
    local = slot_start.astimezone(tz)
    return local.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


def local_today(tenant: Tenant) -> date:
    return datetime.now(tenant_tz(tenant)).date()


async def available_slots(
    db: AsyncSession,
    tenant: Tenant,
    *,
    day: date | None = None,
    days_ahead: int = 7,
    service: str | None = None,
    limit: int = 6,
) -> list[AvailabilitySlot]:
    """Open slots on `day` (tenant-local), or the next `days_ahead` days if no day is given."""
    tz = tenant_tz(tenant)
    now = datetime.now(UTC)
    start_day = day or datetime.now(tz).date()
    start = datetime.combine(start_day, time.min, tz).astimezone(UTC)
    end = datetime.combine(start_day + timedelta(days=1 if day else days_ahead), time.min, tz).astimezone(UTC)
    stmt = (
        select(AvailabilitySlot)
        .where(
            AvailabilitySlot.tenant_id == tenant.id,
            AvailabilitySlot.is_active.is_(True),
            AvailabilitySlot.booked_count < AvailabilitySlot.capacity,
            AvailabilitySlot.start_at >= max(start, now),
            AvailabilitySlot.start_at < end,
        )
        .order_by(AvailabilitySlot.start_at)
        .limit(limit)
    )
    if service:
        stmt = stmt.where(or_(AvailabilitySlot.service.is_(None), AvailabilitySlot.service.ilike(service)))
    return list((await db.execute(stmt)).scalars().all())


@dataclass
class Customer:
    name: str
    phone: str | None = None
    email: str | None = None


async def book(
    db: AsyncSession,
    tenant: Tenant,
    *,
    slot_id: uuid.UUID,
    customer: Customer,
    service: str | None = None,
    notes: str | None = None,
    conversation: Conversation | None = None,
    contact: Contact | None = None,
) -> Appointment:
    slot = (
        await db.execute(
            select(AvailabilitySlot).where(
                AvailabilitySlot.id == slot_id, AvailabilitySlot.tenant_id == tenant.id
            )
        )
    ).scalar_one_or_none()
    if slot is None or not slot.is_active:
        raise NotFoundError("That slot does not exist")
    if slot.start_at <= datetime.now(UTC):
        raise ConflictError("That slot is in the past")

    claimed = await db.execute(
        update(AvailabilitySlot)
        .where(
            AvailabilitySlot.id == slot.id,
            AvailabilitySlot.tenant_id == tenant.id,
            AvailabilitySlot.booked_count < AvailabilitySlot.capacity,
        )
        .values(booked_count=AvailabilitySlot.booked_count + 1)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise ConflictError("Sorry, that slot was just taken")

    appointment = Appointment(
        tenant_id=tenant.id,
        reference=new_reference("APT"),
        slot_id=slot.id,
        contact_id=contact.id if contact else None,
        conversation_id=conversation.id if conversation else None,
        customer_name=customer.name[:200],
        customer_phone=customer.phone,
        customer_email=customer.email,
        service=(service or slot.service),
        resource=slot.resource,
        start_at=slot.start_at,
        end_at=slot.end_at,
        notes=notes,
    )
    db.add(appointment)
    await db.flush()

    # Timer: reminder 24h before (or 1h before for short-notice bookings).
    lead_time = slot.start_at - datetime.now(UTC)
    remind_at = slot.start_at - (
        timedelta(hours=24) if lead_time > timedelta(hours=25) else timedelta(hours=1)
    )
    if remind_at > datetime.now(UTC):
        enqueue(
            db,
            tenant_id=tenant.id,
            action_type="appointment.reminder",
            payload={"appointment_id": str(appointment.id)},
            run_at=remind_at,
            correlation_id=str(appointment.id),
        )
    return appointment


async def find_upcoming(
    db: AsyncSession,
    tenant: Tenant,
    *,
    phone: str | None = None,
    reference: str | None = None,
    contact_id: uuid.UUID | None = None,
) -> list[Appointment]:
    conditions = []
    if reference:
        conditions.append(Appointment.reference == reference.strip().upper())
    if phone:
        conditions.append(Appointment.customer_phone == phone)
    if contact_id:
        conditions.append(Appointment.contact_id == contact_id)
    if not conditions:
        return []
    stmt = (
        select(Appointment)
        .where(
            Appointment.tenant_id == tenant.id,
            Appointment.status == AppointmentStatus.BOOKED,
            Appointment.start_at >= datetime.now(UTC),
            or_(*conditions),
        )
        .order_by(Appointment.start_at)
        .limit(5)
    )
    return list((await db.execute(stmt)).scalars().all())


async def cancel(
    db: AsyncSession, tenant: Tenant, appointment: Appointment, reason: str | None = None
) -> Appointment:
    if appointment.status is not AppointmentStatus.BOOKED:
        raise ConflictError(f"Appointment is already {appointment.status.value}")
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_at = datetime.now(UTC)
    if reason:
        appointment.notes = ((appointment.notes or "") + f"\nCancelled: {reason}").strip()[:2000]
    if appointment.slot_id:
        await db.execute(
            update(AvailabilitySlot)
            .where(AvailabilitySlot.id == appointment.slot_id, AvailabilitySlot.booked_count > 0)
            .values(booked_count=AvailabilitySlot.booked_count - 1)
            .execution_options(synchronize_session=False)
        )
    return appointment


async def generate_slots(
    db: AsyncSession,
    tenant: Tenant,
    *,
    start_date: date,
    end_date: date,
    weekdays: list[int],
    day_start: time,
    day_end: time,
    slot_minutes: int,
    capacity: int = 1,
    resource: str | None = None,
    service: str | None = None,
) -> int:
    """Create recurring slots (tenant-local times). Skips slots that already exist."""
    tz = tenant_tz(tenant)
    existing = set(
        (
            await db.execute(
                select(AvailabilitySlot.start_at).where(
                    AvailabilitySlot.tenant_id == tenant.id,
                    AvailabilitySlot.start_at >= datetime.combine(start_date, time.min, tz).astimezone(UTC),
                    AvailabilitySlot.start_at
                    < datetime.combine(end_date + timedelta(days=1), time.min, tz).astimezone(UTC),
                    AvailabilitySlot.resource.is_(None)
                    if resource is None
                    else AvailabilitySlot.resource == resource,
                )
            )
        ).scalars()
    )
    existing = {dt.astimezone(UTC) for dt in existing}
    created = 0
    day = start_date
    while day <= end_date:
        if day.weekday() in weekdays:
            cursor = datetime.combine(day, day_start, tz)
            stop = datetime.combine(day, day_end, tz)
            while cursor + timedelta(minutes=slot_minutes) <= stop:
                start_utc = cursor.astimezone(UTC)
                if start_utc not in existing:
                    db.add(
                        AvailabilitySlot(
                            tenant_id=tenant.id,
                            start_at=start_utc,
                            end_at=(cursor + timedelta(minutes=slot_minutes)).astimezone(UTC),
                            capacity=capacity,
                            resource=resource,
                            service=service,
                        )
                    )
                    created += 1
                cursor += timedelta(minutes=slot_minutes)
        day += timedelta(days=1)
    return created
