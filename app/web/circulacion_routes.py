"""Rutas de circulación entre oficinas y bandejas de trabajo."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.csrf import verify_csrf
from app.core.deps import require_user
from app.db.session import get_db
from app.models.catalogos import Oficina
from app.models.documentos import Documento, Movimiento
from app.models.enums import EstadoDocumento, Rol, Semaforo
from app.models.usuarios import Usuario
from app.services.circulacion import (
    AccionNoPermitida,
    CirculacionInvalida,
    anular,
    archivar,
    derivar,
    finalizar,
    observar,
    obtener_para_actualizar,
    recibir,
    subsanar,
)
from app.services.sla import EstadoPlazo, estado_por_documento
from app.web.flash import flash
from app.web.templating import render

router = APIRouter(prefix="/documentos", tags=["circulacion"])
bandejas_router = APIRouter(prefix="/bandejas", tags=["bandejas"])

LIMITE_BANDEJA = 200
ORDEN_SEMAFORO = {
    Semaforo.MUY_ATRASADO: 0,
    Semaforo.ATRASADO: 1,
    Semaforo.POR_VENCER: 2,
    Semaforo.EN_PLAZO: 3,
}


def _clave_urgencia(documento: Documento, plazos: dict[int, EstadoPlazo]) -> tuple:
    estado = plazos.get(documento.id)
    if estado is None:
        return (4, 0.0)
    if estado.semaforo in {Semaforo.ATRASADO, Semaforo.MUY_ATRASADO}:
        return (ORDEN_SEMAFORO[estado.semaforo], -estado.horas_atraso)
    return (ORDEN_SEMAFORO[estado.semaforo], estado.horas_restantes)


def _texto(valor: str | None) -> str:
    return (valor or "").strip()


def _entero(valor: str | None) -> int | None:
    valor = _texto(valor)
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        return None


def _documento_bloqueado(db: Session, codigo: str) -> Documento:
    documento = obtener_para_actualizar(db, codigo)
    if documento is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return documento


def _respuesta(
    request: Request, codigo: str, mensaje: str, categoria: str = "ok"
) -> RedirectResponse:
    flash(request, mensaje, categoria)
    return RedirectResponse(f"/documentos/{codigo}", status_code=303)


def _error(request: Request, codigo: str, exc: Exception) -> RedirectResponse:
    return _respuesta(request, codigo, str(exc), "error")


def _accion(
    request: Request,
    db: Session,
    codigo: str,
    ejecutar,
    mensaje: str,
) -> RedirectResponse:
    """Ejecuta una acción con manejo uniforme de permisos y errores de estado."""
    documento = _documento_bloqueado(db, codigo)
    codigo_documento = documento.codigo
    try:
        ejecutar(documento)
        db.commit()
    except AccionNoPermitida as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CirculacionInvalida as exc:
        db.rollback()
        return _error(request, codigo_documento, exc)
    return _respuesta(request, codigo_documento, mensaje)


# ---------------------------------------------------------------------------
# Acciones sobre el documento
# ---------------------------------------------------------------------------


@router.post("/{codigo}/derivar")
def derivar_documento(
    codigo: str,
    request: Request,
    destino_id: str = Form(""),
    observacion: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    destino_pk = _entero(destino_id)
    destino = db.get(Oficina, destino_pk) if destino_pk is not None else None

    def ejecutar(documento: Documento) -> None:
        if destino is None:
            raise CirculacionInvalida("Selecciona una oficina de destino válida.")
        derivar(
            db,
            documento,
            destino=destino,
            usuario=user,
            request=request,
            observacion=observacion,
        )

    mensaje = (
        f"Documento derivado a {destino.nombre}."
        if destino is not None
        else "No se pudo derivar el documento."
    )
    return _accion(request, db, codigo, ejecutar, mensaje)


@router.post("/{codigo}/recibir")
def recibir_documento(
    codigo: str,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    return _accion(
        request,
        db,
        codigo,
        lambda documento: recibir(db, documento, usuario=user, request=request),
        "Recepción confirmada. El plazo empieza a correr.",
    )


@router.post("/{codigo}/observar")
def observar_documento(
    codigo: str,
    request: Request,
    motivo: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    return _accion(
        request,
        db,
        codigo,
        lambda documento: observar(db, documento, usuario=user, motivo=motivo, request=request),
        "Documento observado.",
    )


@router.post("/{codigo}/subsanar")
def subsanar_documento(
    codigo: str,
    request: Request,
    nota: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    return _accion(
        request,
        db,
        codigo,
        lambda documento: subsanar(db, documento, usuario=user, nota=nota, request=request),
        "Observación subsanada; el documento vuelve a la bandeja.",
    )


@router.post("/{codigo}/finalizar")
def finalizar_documento(
    codigo: str,
    request: Request,
    observacion: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    return _accion(
        request,
        db,
        codigo,
        lambda documento: finalizar(
            db, documento, usuario=user, observacion=observacion, request=request
        ),
        "Documento finalizado.",
    )


@router.post("/{codigo}/archivar")
def archivar_documento(
    codigo: str,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    return _accion(
        request,
        db,
        codigo,
        lambda documento: archivar(db, documento, usuario=user, request=request),
        "Documento archivado.",
    )


@router.post("/{codigo}/anular")
def anular_documento(
    codigo: str,
    request: Request,
    motivo: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    return _accion(
        request,
        db,
        codigo,
        lambda documento: anular(db, documento, usuario=user, motivo=motivo, request=request),
        "Documento anulado.",
    )


# ---------------------------------------------------------------------------
# Bandejas
# ---------------------------------------------------------------------------


def _cargar_documento() -> tuple:
    return (
        selectinload(Documento.tipo_tramite),
        selectinload(Documento.oficina_actual),
        selectinload(Documento.ciudadano),
    )


def _por_recibir(db: Session, oficina_id: int | None) -> list[Movimiento]:
    filtros = [
        Movimiento.fecha_recepcion.is_(None),
        Movimiento.fecha_salida.is_(None),
    ]
    if oficina_id is not None:
        filtros.append(Movimiento.oficina_destino_id == oficina_id)
    return list(
        db.scalars(
            select(Movimiento)
            .where(*filtros)
            .options(
                selectinload(Movimiento.documento).selectinload(Documento.tipo_tramite),
                selectinload(Movimiento.documento).selectinload(Documento.ciudadano),
                selectinload(Movimiento.documento).selectinload(Documento.oficina_actual),
                selectinload(Movimiento.oficina_origen),
                selectinload(Movimiento.oficina_destino),
            )
            .order_by(Movimiento.fecha_envio)
            .limit(LIMITE_BANDEJA)
        ).all()
    )


def _documentos_en_oficina(db: Session, oficina_id: int | None) -> list[Documento]:
    filtros = [Documento.estado.in_([EstadoDocumento.EN_OFICINA, EstadoDocumento.OBSERVADO])]
    if oficina_id is not None:
        filtros.append(Documento.oficina_actual_id == oficina_id)
    return list(
        db.scalars(
            select(Documento)
            .where(*filtros)
            .options(*_cargar_documento())
            .order_by(Documento.fecha_registro.desc(), Documento.id.desc())
            .limit(LIMITE_BANDEJA)
        ).all()
    )


def _registrados_por_derivar(db: Session, oficina_id: int | None) -> list[Documento]:
    filtro_oficina = (
        Documento.oficina_actual_id == oficina_id
        if oficina_id is not None
        else Documento.oficina_actual_id.is_(None)
    )
    return list(
        db.scalars(
            select(Documento)
            .where(Documento.estado == EstadoDocumento.REGISTRADO, filtro_oficina)
            .options(*_cargar_documento())
            .order_by(Documento.fecha_registro.desc(), Documento.id.desc())
            .limit(LIMITE_BANDEJA)
        ).all()
    )


@bandejas_router.get("")
def ver_bandejas(
    request: Request,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    por_recibir: list[Movimiento] = []
    documentos: list[Documento] = []
    titulo = "Bandeja de trabajo"

    if user.rol == Rol.OFICINA and user.oficina_id is not None:
        por_recibir = _por_recibir(db, user.oficina_id)
        documentos = _documentos_en_oficina(db, user.oficina_id)
        titulo = f"Bandeja de {user.oficina.nombre}" if user.oficina else titulo
    elif user.rol == Rol.VENTANILLA:
        documentos = _registrados_por_derivar(db, user.oficina_id)
        titulo = "Documentos registrados por derivar"
    elif user.rol == Rol.ADMIN:
        por_recibir = _por_recibir(db, None)
        documentos = _documentos_en_oficina(db, None)
        titulo = "Bandejas de todas las oficinas"
    else:
        por_recibir = _por_recibir(db, None)
        documentos = _documentos_en_oficina(db, None)
        titulo = "Bandejas (consulta)"

    plazos = estado_por_documento(db, documentos)
    documentos.sort(key=lambda documento: _clave_urgencia(documento, plazos))

    return render(
        request,
        "bandejas.html",
        {
            "current_user": user,
            "titulo": titulo,
            "por_recibir": por_recibir,
            "documentos": documentos,
            "plazos": plazos,
            "total_por_recibir": len(por_recibir),
            "total_documentos": len(documentos),
            "es_ventanilla": user.rol == Rol.VENTANILLA,
        },
    )
