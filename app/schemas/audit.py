from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.schemas.common import ORMModel


class AuditLogOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict[str, Any]
    ip: str | None
    request_id: str | None
    created_at: datetime
