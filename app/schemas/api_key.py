from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.api_key import ApiKeyKind
from app.schemas.common import ORMModel


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["public", "secret"]


class ApiKeyOut(ORMModel):
    id: uuid.UUID
    name: str
    kind: ApiKeyKind
    prefix: str
    public_key: str | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Full key. Secret keys are shown only once - store it safely.")
