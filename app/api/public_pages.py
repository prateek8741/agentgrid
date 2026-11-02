"""Static widget script and a demo storefront that embeds it."""

from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select

from app.api.deps import DB
from app.core.config import BASE_DIR, settings
from app.core.errors import NotFoundError
from app.models import ApiKey, ApiKeyKind, Tenant

router = APIRouter(include_in_schema=False)
WIDGET_JS = BASE_DIR / "app" / "static" / "widget.js"


@router.get("/widget.js")
async def widget_js() -> FileResponse:
    return FileResponse(
        WIDGET_JS,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _demo_page(tenant_name: str, key: str) -> str:
    name = html.escape(tenant_name)
    base = html.escape(settings.public_base_url.rstrip("/"))
    snippet = html.escape(
        f'<script src="{settings.public_base_url.rstrip("/")}/widget.js" data-key="{key}" async></script>'
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{name} - demo website</title>
<style>body{{margin:0;font-family:system-ui,-apple-system,sans-serif;color:#1f2937;background:#f8fafc}}
header{{background:#0f172a;color:#fff;padding:18px 28px;font-weight:700;font-size:20px}}
main{{max-width:860px;margin:40px auto;padding:0 20px}}.hero{{background:#fff;border-radius:16px;padding:36px;
box-shadow:0 2px 12px rgba(0,0,0,.06)}}h1{{margin-top:0}}code{{display:block;background:#0f172a;color:#e2e8f0;
padding:14px;border-radius:10px;font-size:13px;white-space:pre-wrap;word-break:break-all}}
.note{{color:#64748b;font-size:14px}}</style></head><body><header>{name}</header><main><div class="hero">
<h1>Welcome to {name}</h1><p>This is a sample customer website. The chat bubble in the corner is the
AgentGrid widget, embedded with one line of HTML:</p><code>{snippet}</code>
<p class="note">Try: ask a question about the business, book an appointment, or type "talk to a human" and
reply from the AgentGrid inbox.</p></div></main>
<script src="{base}/widget.js" data-key="{html.escape(key)}" data-api="{base}" async></script>
</body></html>"""


@router.get("/demo/{tenant_slug}", response_class=HTMLResponse)
async def demo_site(tenant_slug: str, db: DB) -> HTMLResponse:
    tenant = (await db.execute(select(Tenant).where(Tenant.slug == tenant_slug[:80]))).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        raise NotFoundError("Demo site not found")
    key = (
        await db.execute(
            select(ApiKey.public_key)
            .where(
                ApiKey.tenant_id == tenant.id, ApiKey.kind == ApiKeyKind.PUBLIC, ApiKey.revoked_at.is_(None)
            )
            .order_by(ApiKey.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if key is None:
        raise NotFoundError("This tenant has no active widget key")
    return HTMLResponse(_demo_page(tenant.name, key))
