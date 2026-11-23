"""Reglas de negocio para el alta y la consulta de documentos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.archivos import DocumentoArchivo
from app.models.catalogos import TipoTramite
from app.models.ciudadanos import Ciudadano
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import EstadoDocumento, OrigenDocumento, Prioridad, TipoEvento
from app.models.usuarios import Usuario
from app.services.eventos import registrar_evento
from app.services.folio import generar_codigo


class DocumentoInvalido(ValueError):
    """Los datos del documento no cumplen una regla de negocio."""


class DocumentoDuplicado(DocumentoInvalido):
    """Ya existe un documento con el mismo tipo, número y año."""


class CodigoNoDisponible(RuntimeError):
    """No se pudo generar un código único después de varios intentos."""


@dataclass(frozen=True)
class DatosDocumento:
    tipo_tramite_id: int
    numero_certificado: str | None = None
    anio: int | None = None
    prioridad: Prioridad = Prioridad.NORMAL
    estado: EstadoDocumento = EstadoDocumento.REGISTRADO
    origen: OrigenDocumento = OrigenDocumento.MANUAL
    ciudadano_id: int | None = None
    oficina_actual_id: int | None = None
    creado_por_id: int | None = None
    fecha_registro: datetime | None = None
    fecha_finalizacion: datetime | None = None
    fuente_numero: str | None = None
    rut: str | None = None
    expediente_administrativo: str | None = None
    ruc: str | None = None
    referencia: str | None = None
    administrado: str | None = None
    asunto: str | None = None
    observaciones: str | None = None
    observaciones_importacion: str | None = None
    archivo_origen: str | None = None


def _mensaje_integridad(exc: IntegrityError) -> str:
    return str(exc.orig).lower()


def _es_duplicado_certificado(exc: IntegrityError) -> bool:
    mensaje = _mensaje_integridad(exc)
    return "uq_documento_tipo_numero_anio" in mensaje or (
        "tipo_tramite_id" in mensaje
        and "numero_certificado" in mensaje
        and "anio" in mensaje
    )


def _es_colision_codigo(exc: IntegrityError) -> bool:
    mensaje = _mensaje_integridad(exc)
    return (
        "ix_documentos_codigo" in mensaje
        or "uq_documento_codigo" in mensaje
        or "documentos.codigo" in mensaje
    )


def crear_documento(
    db: Session,
    datos: DatosDocumento,
    *,
    usuario: Usuario | None = None,
    request=None,
    anio_codigo: int | None = None,
    generar_evento: bool = True,
    commit: bool = False,
    max_intentos: int = 5,
) -> Documento:
    """Crea un documento y, opcionalmente, su evento de auditoría.

    No crea movimientos: la primera derivación pertenece a la fase de
    circulación. El código se reintenta si otra transacción lo ocupó.
    """
    tipo = db.get(TipoTramite, datos.tipo_tramite_id)
    if tipo is None:
        raise DocumentoInvalido("El tipo de trámite no existe.")

    numero = (datos.numero_certificado or "").strip() or None
    if numero is not None and datos.anio is None:
        raise DocumentoInvalido(
            "Si registra el número de certificado, indique también el año."
        )

    if numero is not None:
        existente = db.scalar(
            select(Documento.id).where(
                Documento.tipo_tramite_id == datos.tipo_tramite_id,
                Documento.numero_certificado == numero,
                Documento.anio == datos.anio,
            )
        )
        if existente is not None:
            raise DocumentoDuplicado(
                f"Ya existe el documento {numero}-{datos.anio} para este tipo de trámite."
            )

    oficina_actual_id = datos.oficina_actual_id
    if oficina_actual_id is None and usuario is not None:
        oficina_actual_id = usuario.oficina_id

    creado_por_id = datos.creado_por_id
    if creado_por_id is None and usuario is not None:
        creado_por_id = usuario.id

    campos = {
        "tipo_tramite_id": datos.tipo_tramite_id,
        "ciudadano_id": datos.ciudadano_id,
        "numero_certificado": numero,
        "anio": datos.anio,
        "fuente_numero": datos.fuente_numero,
        "rut": datos.rut,
        "expediente_administrativo": datos.expediente_administrativo,
        "ruc": datos.ruc,
        "referencia": datos.referencia,
        "administrado": datos.administrado,
        "asunto": datos.asunto,
        "prioridad": datos.prioridad,
        "estado": datos.estado,
        "origen": datos.origen,
        "oficina_actual_id": oficina_actual_id,
        "creado_por_id": creado_por_id,
        "fecha_finalizacion": datos.fecha_finalizacion,
        "observaciones": datos.observaciones,
        "observaciones_importacion": datos.observaciones_importacion,
        "archivo_origen": datos.archivo_origen,
    }
    if datos.fecha_registro is not None:
        campos["fecha_registro"] = datos.fecha_registro

    documento: Documento | None = None
    for _ in range(max_intentos):
        codigo = generar_codigo(db, anio_codigo)
        candidato = Documento(codigo=codigo, **campos)
        try:
            with db.begin_nested():
                db.add(candidato)
                db.flush()
        except IntegrityError as exc:
            if _es_duplicado_certificado(exc):
                raise DocumentoDuplicado(
                    f"Ya existe el documento {numero}-{datos.anio} "
                    "para este tipo de trámite."
                ) from exc
            if _es_colision_codigo(exc):
                continue
            raise
        documento = candidato
        break

    if documento is None:
        raise CodigoNoDisponible(
            "No se pudo asignar un código interno único. Intente nuevamente."
        )

    if generar_evento:
        registrar_evento(
            db,
            documento,
            TipoEvento.CREADO,
            usuario=usuario,
            request=request,
            detalle={
                "codigo": documento.codigo,
                "origen": documento.origen.value,
                "estado": documento.estado.value,
            },
        )

    if commit:
        db.commit()
        db.refresh(documento)

    return documento


def buscar_documentos(
    db: Session,
    *,
    q: str | None = None,
    estado: EstadoDocumento | None = None,
    tipo_tramite_id: int | None = None,
    oficina_id: int | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[Documento], int]:
    """Lista documentos con filtros simples y paginación."""
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)

    filtros = []
    if q and q.strip():
        patron = f"%{q.strip()}%"
        filtros.append(
            or_(
                Documento.codigo.ilike(patron),
                Documento.numero_certificado.ilike(patron),
                Documento.administrado.ilike(patron),
                Documento.asunto.ilike(patron),
                Documento.rut.ilike(patron),
                Documento.expediente_administrativo.ilike(patron),
                Documento.ruc.ilike(patron),
                Documento.referencia.ilike(patron),
                Documento.ciudadano.has(
                    or_(
                        Ciudadano.nombres.ilike(patron),
                        Ciudadano.numero_documento.ilike(patron),
                    )
                ),
                Documento.archivos.any(DocumentoArchivo.texto_ocr.ilike(patron)),
            )
        )
    if estado is not None:
        filtros.append(Documento.estado == estado)
    if tipo_tramite_id is not None:
        filtros.append(Documento.tipo_tramite_id == tipo_tramite_id)
    if oficina_id is not None:
        filtros.append(Documento.oficina_actual_id == oficina_id)

    total = (
        db.scalar(select(func.count()).select_from(Documento).where(*filtros)) or 0
    )
    stmt = (
        select(Documento)
        .where(*filtros)
        .options(
            selectinload(Documento.tipo_tramite),
            selectinload(Documento.oficina_actual),
            selectinload(Documento.ciudadano),
            selectinload(Documento.archivos),
        )
        .order_by(Documento.fecha_registro.desc(), Documento.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    return list(db.scalars(stmt).all()), total


def obtener_por_codigo(db: Session, codigo: str) -> Documento | None:
    """Obtiene un documento con sus relaciones para el detalle y la timeline."""
    stmt = (
        select(Documento)
        .where(Documento.codigo == codigo.strip().upper())
        .options(
            selectinload(Documento.tipo_tramite),
            selectinload(Documento.ciudadano),
            selectinload(Documento.oficina_actual),
            selectinload(Documento.creado_por),
            selectinload(Documento.archivos).selectinload(DocumentoArchivo.subido_por),
            selectinload(Documento.eventos).selectinload(Evento.usuario),
            selectinload(Documento.movimientos).selectinload(Movimiento.oficina_origen),
            selectinload(Documento.movimientos).selectinload(Movimiento.oficina_destino),
        )
    )
    return db.scalar(stmt)
