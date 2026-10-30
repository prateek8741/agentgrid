from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class AgentBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    persona: str = Field(default="", max_length=8000)
    greeting: str = Field(default="Hi! How can I help you today?", max_length=500)
    llm_model: str | None = Field(default=None, max_length=80)
    temperature: float = Field(default=0.3, ge=0.0, le=1.5)
    language: str = Field(default="en", max_length=10)
    enabled_tools: list[str] = Field(default_factory=list, max_length=50)
    intents: list[Any] = Field(default_factory=list, max_length=100)
    qualification_questions: list[Any] = Field(default_factory=list, max_length=30)
    flow: dict[str, Any] = Field(default_factory=dict)
    handover_rules: dict[str, Any] = Field(default_factory=dict)
    recommended_integrations: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True


class AgentCreate(AgentBase):
    template_key: str | None = Field(default=None, max_length=60)
    is_default: bool = False


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    persona: str | None = Field(default=None, max_length=8000)
    greeting: str | None = Field(default=None, max_length=500)
    llm_model: str | None = Field(default=None, max_length=80)
    temperature: float | None = Field(default=None, ge=0.0, le=1.5)
    language: str | None = Field(default=None, max_length=10)
    enabled_tools: list[str] | None = Field(default=None, max_length=50)
    intents: list[Any] | None = None
    qualification_questions: list[Any] | None = None
    flow: dict[str, Any] | None = None
    handover_rules: dict[str, Any] | None = None
    recommended_integrations: list[str] | None = None
    is_active: bool | None = None
    is_default: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> AgentUpdate:
        # Omitting a field leaves it unchanged; sending null is only meaningful for
        # llm_model ("use the platform default"). Every other column is NOT NULL.
        nulled = sorted(f for f in self.model_fields_set if getattr(self, f) is None and f != "llm_model")
        if nulled:
            raise ValueError(f"These fields cannot be null: {', '.join(nulled)}")
        return self


class AgentOut(AgentBase, ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    template_key: str | None
    is_default: bool
    created_at: datetime
    updated_at: datetime
