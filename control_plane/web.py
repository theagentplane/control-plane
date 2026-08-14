"""HTML control-plane UI (Admin / Chronicle / TokenOps). Talks to /v1 over HTTP."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.requests import Request

_WEB = Path(__file__).resolve().parent / "web"
_templates = Jinja2Templates(directory=str(_WEB / "templates"))


def mount_web(app: FastAPI) -> None:
    static = _WEB / "static"
    static.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return _templates.TemplateResponse(
            request,
            "index.html",
            {"version": getattr(request.app.state.settings, "version", "0.1.0")},
        )
