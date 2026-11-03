"""Configuración de Jinja2 y helper de render."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.csrf import get_csrf_token

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_TZ = ZoneInfo(settings.timezone)


def _fecha(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(_TZ).strftime(fmt)


templates.env.filters["fecha"] = _fecha


def render(
    request: Request,
    name: str,
    context: dict | None = None,
    status_code: int = 200,
):
    ctx: dict = {
        "request": request,
        "app_name": settings.app_name,
        "csrf_token": get_csrf_token(request),
        "current_user": None,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(
        request=request, name=name, context=ctx, status_code=status_code
    )
