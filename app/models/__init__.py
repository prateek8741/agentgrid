"""ORM models. Importing this package registers every table on `Base.metadata`."""

from app.models.agent import Agent
from app.models.analytics import AnalyticsDaily
from app.models.api_key import ApiKey, ApiKeyKind
from app.models.audit import AuditLog
from app.models.base import Base, TenantScopedMixin
from app.models.booking import Appointment, AvailabilitySlot
from app.models.commerce import PaymentLink, Product, Ticket
from app.models.conversation import Contact, Conversation, Message
from app.models.crm import Lead
from app.models.enums import (
    ActionStatus,
    AppointmentStatus,
    Channel,
    ConversationStatus,
    LeadStatus,
    LeadTemperature,
    MessageRole,
    PaymentStatus,
    TicketPriority,
    TicketStatus,
)
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.tenant import Tenant, TenantKind, TenantStatus
from app.models.usage import LLMUsage
from app.models.user import RefreshToken, User
from app.models.workflow import ActionLog

__all__ = [
    "ActionLog",
    "ActionStatus",
    "Agent",
    "AnalyticsDaily",
    "ApiKey",
    "ApiKeyKind",
    "Appointment",
    "AppointmentStatus",
    "AuditLog",
    "AvailabilitySlot",
    "Base",
    "Channel",
    "Contact",
    "Conversation",
    "ConversationStatus",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "LLMUsage",
    "Lead",
    "LeadStatus",
    "LeadTemperature",
    "Message",
    "MessageRole",
    "PaymentLink",
    "PaymentStatus",
    "Product",
    "RefreshToken",
    "Tenant",
    "TenantKind",
    "TenantScopedMixin",
    "TenantStatus",
    "Ticket",
    "TicketPriority",
    "TicketStatus",
    "User",
]
