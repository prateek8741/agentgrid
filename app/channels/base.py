"""Channel adapter interface.

Every channel converts its native payload into the unified `InboundMessage`
and knows how to deliver an `OutboundMessage` back to the customer. The
agent orchestrator never sees channel specifics.
"""

from __future__ import annotations

from typing import Protocol

from app.channels.schema import OutboundMessage
from app.models import Channel, Tenant


class ChannelAdapter(Protocol):
    channel: Channel

    async def send(self, tenant: Tenant, message: OutboundMessage) -> None:
        """Deliver a message to the customer (AI reply or human-agent reply)."""
        ...
