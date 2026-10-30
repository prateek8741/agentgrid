"""Appointment tools: availability, booking, cancellation."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.agents import parsing
from app.agents.tools.base import ToolContext, tool
from app.models import Appointment
from app.services import booking_service


def appointment_payload(appt: Appointment, ctx: ToolContext) -> dict[str, Any]:
    tz = booking_service.tenant_tz(ctx.tenant)
    return {
        "appointment_id": str(appt.id),
        "reference": appt.reference,
        "when": booking_service.format_slot(appt.start_at, tz),
        "start_at": appt.start_at.isoformat(),
        "service": appt.service,
        "customer_name": appt.customer_name,
        "status": appt.status.value,
    }


class CheckAvailabilityArgs(BaseModel):
    date: str | None = Field(
        default=None,
        max_length=60,
        description="Day to check: YYYY-MM-DD, 'tomorrow', 'friday'... Omit for next 7 days",
    )
    service: str | None = Field(default=None, max_length=120)


@tool(
    "check_availability",
    "List open appointment slots. Always call this before booking; never invent times.",
    CheckAvailabilityArgs,
)
async def check_availability(ctx: ToolContext, args: CheckAvailabilityArgs) -> dict[str, Any]:
    day = None
    if args.date:
        day = parsing.parse_date(args.date, booking_service.local_today(ctx.tenant))
        if day is None:
            return {"error": f"Could not understand the date '{args.date}'. Use YYYY-MM-DD."}
    slots = await booking_service.available_slots(ctx.db, ctx.tenant, day=day, service=args.service, limit=8)
    tz = booking_service.tenant_tz(ctx.tenant)
    return {
        "date": day.isoformat() if day else "next 7 days",
        "slots": [
            {
                "slot_id": str(s.id),
                "time": booking_service.format_slot(s.start_at, tz),
                "resource": s.resource,
            }
            for s in slots
        ],
        "note": "No open slots" if not slots else "Offer these times; use slot_id to book.",
    }


class BookAppointmentArgs(BaseModel):
    slot_id: uuid.UUID = Field(description="slot_id returned by check_availability")
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    service: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _contact(self) -> BookAppointmentArgs:
        if not (self.phone and parsing.normalise_phone(self.phone)) and not (
            self.email and parsing.parse_email(self.email)
        ):
            raise ValueError("a valid phone number or email is required to book")
        return self


@tool(
    "book_appointment",
    "Book an appointment in an open slot (needs slot_id from check_availability, the customer's name "
    "and phone or email). Confirm details with the customer before calling.",
    BookAppointmentArgs,
)
async def book_appointment(ctx: ToolContext, args: BookAppointmentArgs) -> dict[str, Any]:
    customer = booking_service.Customer(
        name=parsing.parse_name(args.name) or args.name.strip(),
        phone=parsing.normalise_phone(args.phone) if args.phone else None,
        email=parsing.parse_email(args.email) if args.email else None,
    )
    appt = await booking_service.book(
        ctx.db,
        ctx.tenant,
        slot_id=args.slot_id,
        customer=customer,
        service=args.service,
        notes=args.notes,
        conversation=ctx.conversation,
        contact=ctx.contact,
    )
    ctx.contact.name = ctx.contact.name or customer.name
    ctx.contact.phone = ctx.contact.phone or customer.phone
    ctx.contact.email = ctx.contact.email or customer.email
    ctx.actions_enqueued = True
    payload = appointment_payload(appt, ctx)
    ctx.emit("appointment.booked", payload)
    return {"booked": True, **payload}


class CancelAppointmentArgs(BaseModel):
    reference: str | None = Field(
        default=None, max_length=20, description="Booking reference like APT-ABC123"
    )
    phone: str | None = Field(default=None, max_length=40, description="Phone used for the booking")
    reason: str | None = Field(default=None, max_length=300)


@tool(
    "cancel_appointment",
    "Cancel the customer's upcoming appointment, found by booking reference or phone number.",
    CancelAppointmentArgs,
)
async def cancel_appointment(ctx: ToolContext, args: CancelAppointmentArgs) -> dict[str, Any]:
    phone = parsing.normalise_phone(args.phone) if args.phone else None
    reference = args.reference.strip().upper() if args.reference else None
    candidates = await booking_service.find_upcoming(
        ctx.db, ctx.tenant, phone=phone, reference=reference, contact_id=ctx.contact.id
    )
    # Ownership check: the appointment must belong to this customer, or the caller must
    # know BOTH its reference and the phone it was booked with (a reference alone is not proof).
    matches = [
        a
        for a in candidates
        if a.contact_id == ctx.contact.id
        or (reference and phone and a.reference == reference and a.customer_phone == phone)
    ]
    if reference:
        matches = [a for a in matches if a.reference == reference]
    if not matches:
        return {"error": "No upcoming appointment found for those details"}
    if len(matches) > 1 and not args.reference:
        return {
            "multiple": [appointment_payload(a, ctx) for a in matches],
            "note": "Ask which one to cancel, then call again with its reference",
        }
    appt = await booking_service.cancel(ctx.db, ctx.tenant, matches[0], args.reason)
    payload = appointment_payload(appt, ctx)
    ctx.emit("appointment.cancelled", payload)
    return {"cancelled": True, **payload}
