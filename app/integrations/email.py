"""Email notifications via SMTP (stdlib smtplib in a worker thread).

If SMTP_HOST is not configured the message is logged (PII-masked) instead,
so notification flows still work end-to-end in the demo.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import anyio

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    if not settings.smtp_host:
        log.info("email (SMTP not configured, logged only) to=%s subject=%s", to, subject)
        return {"delivered": False, "mode": "logged"}
    await anyio.to_thread.run_sync(_send_sync, to, subject, body)
    return {"delivered": True, "mode": "smtp"}
