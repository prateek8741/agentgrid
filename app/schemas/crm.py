from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models import AppointmentStatus, LeadStatus, LeadTemperature, TicketPriority, TicketStatus
from app.schemas.common import ORMModel


class LeadOut(ORMModel):
    id: uuid.UUID
    contact_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    name: str | None
    email: str | None
    phone: str | None
    need: str | None
    budget: str | None
    budget_amount: Decimal | None
    timeline: str | None
    timeline_days: int | None
    score: int
    temperature: LeadTemperature
    status: LeadStatus
    source_channel: str | None
    notes: str | None
    assigned_user_id: uuid.UUID | None
    custom: dict[str, Any]
    external_ids: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LeadCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    need: str | None = Field(default=None, max_length=2000)
    budget: str | None = Field(default=None, max_length=120)
    timeline: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class LeadUpdate(LeadCreate):
    status: LeadStatus | None = None
    assigned_user_id: uuid.UUID | None = None


class SlotOut(ORMModel):
    id: uuid.UUID
    start_at: datetime
    end_at: datetime
    capacity: int
    booked_count: int
    resource: str | None
    service: str | None
    is_active: bool


class SlotGenerate(BaseModel):
    """Create recurring slots in the tenant's timezone."""

    start_date: date
    end_date: date
    weekdays: list[int] = Field(default=[0, 1, 2, 3, 4, 5], description="0=Monday ... 6=Sunday")
    day_start: time = time(10, 0)
    day_end: time = time(18, 0)
    slot_minutes: int = Field(default=30, ge=10, le=480)
    capacity: int = Field(default=1, ge=1, le=100)
    resource: str | None = Field(default=None, max_length=120)
    service: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check(self) -> SlotGenerate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 92:
            raise ValueError("Generate at most 3 months of slots at a time")
        if not self.weekdays or any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("weekdays must be values 0-6")
        if self.day_end <= self.day_start:
            raise ValueError("day_end must be after day_start")
        return self


class AppointmentOut(ORMModel):
    id: uuid.UUID
    reference: str
    slot_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    customer_name: str
    customer_phone: str | None
    customer_email: str | None
    service: str | None
    resource: str | None
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    notes: str | None
    cancelled_at: datetime | None
    reminder_sent_at: datetime | None
    created_at: datetime


class AppointmentStatusUpdate(BaseModel):
    status: AppointmentStatus
    reason: str | None = Field(default=None, max_length=300)


class ProductOut(ORMModel):
    id: uuid.UUID
    sku: str
    name: str
    description: str
    category: str | None
    price: Decimal
    currency: str
    tags: list[str]
    url: str | None
    image_url: str | None
    in_stock: bool


class ProductImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str]


class TicketOut(ORMModel):
    id: uuid.UUID
    reference: str
    conversation_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    subject: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    external_ref: str | None
    created_at: datetime
    updated_at: datetime


class TicketUpdate(BaseModel):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
