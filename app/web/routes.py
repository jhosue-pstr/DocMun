"""Rutas web con plantillas Jinja2."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.csrf import verify_csrf
from app.core.deps import get_optional_user, require_user
from app.core.security import verify_password
from app.db.session import get_db
from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento
from app.models.enums import EstadoDocumento
from app.models.usuarios import Usuario
from app.web.templating import render

router = APIRouter()


@router.get("/")
def inicio(user: Usuario | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/panel", status_code=303)


@router.get("/login")
def login_form(
    request: Request, user: Usuario | None = Depends(get_optional_user)
):
    if user is not None:
        return RedirectResponse("/panel", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    usuario = db.scalar(
        select(Usuario).where(Usuario.email == email.strip().lower())
    )
    if usuario is None or not usuario.activo or not verify_password(
        usuario.password_hash, password
    ):
        return render(
            request,
            "login.html",
            {"error": "Correo o contraseña incorrectos", "email": email},
            status_code=401,
        )

    request.session.clear()
    request.session["user_id"] = usuario.id
    usuario.ultimo_acceso = datetime.now(UTC)
    db.commit()

    return RedirectResponse("/panel", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/panel")
def panel(
    request: Request,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    por_estado = dict(
        db.execute(
            select(Documento.estado, func.count(Documento.id)).group_by(Documento.estado)
        ).all()
    )
    total = sum(por_estado.values())
    en_tramite = sum(
        por_estado.get(estado, 0)
        for estado in (
            EstadoDocumento.REGISTRADO,
            EstadoDocumento.DERIVADO,
            EstadoDocumento.EN_OFICINA,
            EstadoDocumento.OBSERVADO,
        )
    )

    contexto = {
        "current_user": user,
        "total_documentos": total,
        "en_tramite": en_tramite,
        "finalizados": por_estado.get(EstadoDocumento.FINALIZADO, 0),
        "oficinas": db.scalar(
            select(func.count()).select_from(Oficina).where(Oficina.activa.is_(True))
        ),
        "tipos": db.scalar(
            select(func.count())
            .select_from(TipoTramite)
            .where(TipoTramite.activo.is_(True))
        ),
        "por_estado": [
            (estado, por_estado.get(estado, 0)) for estado in EstadoDocumento
        ],
    }
    return render(request, "panel.html", contexto)
