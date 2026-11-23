"""Punto de entrada de la aplicación DocMun."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

import app.models  # noqa: F401  (registra todos los modelos en el metadata)
from app.core.config import settings
from app.db.session import get_db
from app.web.admin_routes import router as admin_router
from app.web.circulacion_routes import bandejas_router
from app.web.circulacion_routes import router as circulacion_router
from app.web.documentos_routes import router as documentos_router
from app.web.routes import router as web_router
from app.web.templating import render

app = FastAPI(
    title=settings.app_name,
    description="Seguimiento de documentos municipales",
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie,
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.is_production,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(web_router)
app.include_router(documentos_router)
app.include_router(circulacion_router)
app.include_router(bandejas_router)
app.include_router(admin_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    acepta_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == 401 and acepta_html:
        return RedirectResponse("/login", status_code=303)
    if exc.status_code == 403 and acepta_html:
        return render(request, "403.html", {"detalle": exc.detail}, status_code=403)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/health", tags=["infra"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
    }
