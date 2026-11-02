"""Serves the admin console (React SPA, pre-built into app/static/console).

Hashed assets are cached for a year; every other non-API path returns
index.html so client-side routes (/inbox, /leads, ...) work on reload.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import BASE_DIR

CONSOLE_DIR = BASE_DIR / "app" / "static" / "console"
_RESERVED = (
    "api/",
    "docs",
    "redoc",
    "openapi.json",
    "health",
    "ready",
    "metrics",
    "webhooks/",
    "widget.js",
    "demo/",
    "pay/",
)

router = APIRouter(include_in_schema=False)


class CachedStatic(StaticFiles):
    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_console(app: FastAPI) -> None:
    assets = CONSOLE_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", CachedStatic(directory=assets), name="console-assets")
    app.include_router(router)


@router.get("/{path:path}")
async def console(path: str) -> Response:
    if path.startswith(_RESERVED):
        return JSONResponse({"error": {"code": "not_found", "message": "Not found"}}, status_code=404)
    index = CONSOLE_DIR / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<h1>AgentGrid API</h1><p>The admin console build is missing. Run <code>npm run build</code> in "
            "<code>frontend/</code>, or use the API at <a href='/docs'>/docs</a>.</p>",
            status_code=200,
        )
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
