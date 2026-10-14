"""Mount the built-in web page (Admin, Chronicle and TokenOps tabs) on the application.

The page is plain HTML and JavaScript that talks to ``/v1`` over HTTP with the same bearer key a
sidecar would use, so it needs no server-side rendering beyond the index template.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.templating import Jinja2Templates

from control_plane import __version__

_WEB = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_WEB / "templates"))


def mount_web(app: FastAPI) -> None:
    """Serve ``/static`` and the index page."""
    app.mount("/static", StaticFiles(directory=str(_WEB / "static")), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return _templates.TemplateResponse(request, "index.html", {"version": __version__})
