"""Cola de análisis, persistencia de resultados y enlace con el expediente."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analisis import DocumentoAnalisis
from app.models.archivos import DocumentoArchivo
from app.models.catalogos import TipoTramite
from app.models.documentos import Documento
from app.models.enums import EstadoAnalisis, TipoEvento
from app.services.archivos import ruta_absoluta
from app.services.eventos import registrar_evento
from app.services.ia import motor
from app.services.ia.extraccion import VERSION_REGLAS, construir_sugerencias

LOGGER = logging.getLogger("docmun.ia.cola")

# (clave en `campos`, etiqueta visible, nombre del input del formulario)
CAMPOS_UI: tuple[tuple[str, str, str], ...] = (
    ("dni", "DNI del ciudadano", "ciudadano_numero_documento"),
    ("ruc", "RUC", "ruc"),
    ("expediente", "Expediente", "expediente_administrativo"),
    ("numero_certificado", "Número de certificado", "numero_certificado"),
    ("anio", "Año", "anio"),
    ("asunto", "Asunto", "asunto"),
    ("referencia", "Referencia", "referencia"),
    ("administrado", "Administrado", "administrado"),
)


@dataclass
class ResultadoCola:
    procesados: int = 0
    errores: int = 0


def _base_storage() -> Path:
    return Path(settings.storage_dir).resolve()


def _ruta_segura(relativa: str) -> Path:
    base = _base_storage()
    ruta = (base / relativa).resolve()
    if ruta != base and not str(ruta).startswith(f"{base}{os.sep}"):
        raise ValueError("La ruta del borrador no es válida.")
    return ruta


def guardar_borrador(contenido: bytes, hash_sha256: str, extension: str) -> str:
    """Guarda el archivo temporal de ventanilla y devuelve su ruta relativa."""
    relativa = Path("borradores") / f"{hash_sha256}{extension}"
    absoluta = _base_storage() / relativa
    absoluta.parent.mkdir(parents=True, exist_ok=True)
    if not absoluta.exists():
        absoluta.write_bytes(contenido)
    return str(relativa).replace("\\", "/")


def ruta_borrador_absoluta(analisis: DocumentoAnalisis) -> Path:
    if not analisis.ruta_borrador:
        raise ValueError("El análisis no tiene archivo borrador.")
    return _ruta_segura(analisis.ruta_borrador)


def eliminar_borrador(relativa: str | None) -> None:
    if not relativa:
        return
    try:
        _ruta_segura(relativa).unlink(missing_ok=True)
    except (OSError, ValueError):
        LOGGER.warning("No se pudo borrar el borrador %s", relativa)


def _copiar_resultado(destino: DocumentoAnalisis, origen: DocumentoAnalisis) -> None:
    destino.texto_ocr = origen.texto_ocr
    destino.paginas = origen.paginas
    destino.campos = origen.campos
    destino.tipo_clase = origen.tipo_clase
    destino.tipo_confianza = origen.tipo_confianza
    destino.tipo_tramite_sugerido_id = origen.tipo_tramite_sugerido_id
    destino.sugerencia_confianza = origen.sugerencia_confianza
    destino.motor = origen.motor
    destino.version_modelo = origen.version_modelo
    destino.duracion_ms = 0
    destino.estado = EstadoAnalisis.LISTO
    destino.error = None


def _cache_por_hash(db: Session, hash_sha256: str) -> DocumentoAnalisis | None:
    """Resultado ya calculado para el mismo archivo, con las reglas vigentes.

    Si las reglas cambiaron, el resultado viejo se ignora y el documento vuelve a
    analizarse; así un ajuste de extracción no queda tapado por la caché.
    """
    candidatos = db.scalars(
        select(DocumentoAnalisis)
        .where(
            DocumentoAnalisis.hash_sha256 == hash_sha256,
            DocumentoAnalisis.estado == EstadoAnalisis.LISTO,
            DocumentoAnalisis.texto_ocr.is_not(None),
        )
        .order_by(DocumentoAnalisis.id.desc())
        .limit(20)
    ).all()
    for candidato in candidatos:
        if (candidato.campos or {}).get("reglas") == VERSION_REGLAS:
            return candidato
    return None


def encolar_analisis(
    db: Session,
    archivo: DocumentoArchivo,
    *,
    documento: Documento | None = None,
) -> DocumentoAnalisis | None:
    """Crea (o reutiliza) el análisis de un archivo ya guardado."""
    if not settings.ia_habilitada:
        return None
    if archivo.id is None:
        db.flush()

    existente = db.scalar(
        select(DocumentoAnalisis).where(DocumentoAnalisis.archivo_id == archivo.id)
    )
    if existente is not None:
        reglas_vigentes = (existente.campos or {}).get("reglas") == VERSION_REGLAS
        if existente.estado in {EstadoAnalisis.PENDIENTE, EstadoAnalisis.PROCESANDO}:
            return existente
        if existente.estado == EstadoAnalisis.LISTO and reglas_vigentes:
            return existente
        # Error, o resultado calculado con reglas anteriores: se reprocesa.
        existente.estado = EstadoAnalisis.PENDIENTE
        existente.intentos = 0
        existente.error = None
        return existente

    analisis = DocumentoAnalisis(
        documento_id=documento.id if documento is not None else archivo.documento_id,
        archivo_id=archivo.id,
        hash_sha256=archivo.hash_sha256,
        nombre_original=archivo.nombre_original,
        mime=archivo.mime,
        estado=EstadoAnalisis.PENDIENTE,
    )

    cache = _cache_por_hash(db, archivo.hash_sha256)
    if cache is not None:
        _copiar_resultado(analisis, cache)
        archivo.texto_ocr = cache.texto_ocr
        archivo.paginas = cache.paginas

    db.add(analisis)
    db.flush()
    return analisis


def encolar_borrador(
    db: Session,
    *,
    contenido: bytes,
    nombre_original: str,
    mime: str,
    extension: str,
    hash_sha256: str,
) -> DocumentoAnalisis | None:
    """Encola un archivo subido antes de que exista el documento."""
    if not settings.ia_habilitada:
        return None

    ruta = guardar_borrador(contenido, hash_sha256, extension)
    existente = db.scalar(
        select(DocumentoAnalisis).where(
            DocumentoAnalisis.hash_sha256 == hash_sha256,
            DocumentoAnalisis.documento_id.is_(None),
            DocumentoAnalisis.archivo_id.is_(None),
        )
    )
    if existente is not None:
        reglas_vigentes = (existente.campos or {}).get("reglas") == VERSION_REGLAS
        if existente.estado == EstadoAnalisis.ERROR or (
            existente.estado == EstadoAnalisis.LISTO and not reglas_vigentes
        ):
            existente.estado = EstadoAnalisis.PENDIENTE
            existente.intentos = 0
            existente.error = None
        existente.ruta_borrador = ruta
        existente.nombre_original = nombre_original
        existente.mime = mime
        return existente

    analisis = DocumentoAnalisis(
        hash_sha256=hash_sha256,
        nombre_original=nombre_original,
        mime=mime,
        ruta_borrador=ruta,
        estado=EstadoAnalisis.PENDIENTE,
    )

    cache = _cache_por_hash(db, hash_sha256)
    if cache is not None:
        _copiar_resultado(analisis, cache)

    db.add(analisis)
    db.flush()
    return analisis


def reclamar_pendiente(db: Session) -> DocumentoAnalisis | None:
    """Marca como procesando un pendiente. En Postgres evita dobles tomas."""
    stmt = (
        select(DocumentoAnalisis)
        .where(DocumentoAnalisis.estado == EstadoAnalisis.PENDIENTE)
        .order_by(DocumentoAnalisis.id)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    analisis = db.scalar(stmt)
    if analisis is None:
        return None

    analisis.estado = EstadoAnalisis.PROCESANDO
    analisis.intentos += 1
    db.commit()
    return analisis


def _ruta_analisis(analisis: DocumentoAnalisis) -> Path:
    if analisis.ruta_borrador:
        return _ruta_segura(analisis.ruta_borrador)
    if analisis.archivo is not None:
        return ruta_absoluta(analisis.archivo)
    raise ValueError("El análisis no tiene archivo asociado.")


def _mensaje_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)}"[:300]


def procesar_analisis(db: Session, analisis: DocumentoAnalisis) -> bool:
    """Procesa una fila reclamada. Devuelve True si terminó correctamente."""
    analisis_id = analisis.id
    try:
        resultado = motor.analizar_archivo(_ruta_analisis(analisis))
        tipos = {
            tipo.codigo: tipo.nombre for tipo in db.scalars(select(TipoTramite)).all()
        }
        sugerencias = construir_sugerencias(
            resultado.texto_ocr,
            clase_ia=(
                {"clase": resultado.tipo_clase, "probabilidad": resultado.tipo_confianza}
                if resultado.tipo_clase
                else None
            ),
            tipos=tipos,
        )
        if resultado.kie:
            # Se conserva como diagnóstico y como datos para el futuro dataset.
            sugerencias["kie"] = resultado.kie

        tipo_codigo = (sugerencias.get("tipo") or {}).get("codigo")
        tipo = (
            db.scalar(select(TipoTramite).where(TipoTramite.codigo == tipo_codigo))
            if tipo_codigo
            else None
        )

        analisis.texto_ocr = resultado.texto_ocr
        analisis.paginas = resultado.paginas
        analisis.campos = sugerencias
        analisis.tipo_clase = resultado.tipo_clase
        analisis.tipo_confianza = resultado.tipo_confianza
        analisis.tipo_tramite_sugerido_id = tipo.id if tipo is not None else None
        analisis.sugerencia_confianza = (
            (sugerencias.get("tipo") or {}).get("confianza") if tipo is not None else None
        )
        analisis.motor = resultado.motor
        analisis.version_modelo = resultado.version_modelo
        analisis.duracion_ms = resultado.duracion_ms
        analisis.estado = EstadoAnalisis.LISTO
        analisis.error = None

        if analisis.archivo is not None:
            analisis.archivo.texto_ocr = resultado.texto_ocr
            analisis.archivo.paginas = resultado.paginas

        documento = analisis.documento or (
            db.get(Documento, analisis.documento_id) if analisis.documento_id else None
        )
        if documento is not None:
            registrar_evento(
                db,
                documento,
                TipoEvento.ANALIZADO,
                detalle={
                    "analisis_id": analisis.id,
                    "motor": resultado.motor,
                    "version_modelo": resultado.version_modelo,
                    "paginas": resultado.paginas,
                    "tipo_sugerido": tipo_codigo,
                    "confianza": analisis.sugerencia_confianza,
                },
            )

        db.commit()
        return True
    except Exception as exc:
        LOGGER.warning("Análisis %s falló: %s", analisis_id, type(exc).__name__)
        db.rollback()
        actual = db.get(DocumentoAnalisis, analisis_id)
        if actual is not None:
            actual.error = _mensaje_error(exc)
            actual.estado = (
                EstadoAnalisis.PENDIENTE
                if actual.intentos < max(1, settings.ia_max_intentos)
                else EstadoAnalisis.ERROR
            )
            db.commit()
        return False


def procesar_pendientes(db: Session, limite: int | None = 1) -> ResultadoCola:
    """Procesa pendientes. Con `limite=None` vacía la cola."""
    resultado = ResultadoCola()
    procesados = 0
    while limite is None or procesados < limite:
        analisis = reclamar_pendiente(db)
        if analisis is None:
            break
        if procesar_analisis(db, analisis):
            resultado.procesados += 1
        else:
            resultado.errores += 1
        procesados += 1
    return resultado


def limpiar_borradores(db: Session, horas: int | None = None) -> int:
    """Borra borradores antiguos que nunca se convirtieron en documento."""
    horas = horas if horas is not None else settings.ia_borrador_horas
    limite = datetime.now(UTC) - timedelta(hours=max(1, horas))
    borradores = list(
        db.scalars(
            select(DocumentoAnalisis)
            .where(
                DocumentoAnalisis.documento_id.is_(None),
                DocumentoAnalisis.estado.in_(
                    [EstadoAnalisis.LISTO, EstadoAnalisis.ERROR]
                ),
            )
            .limit(200)
        ).all()
    )

    eliminados = 0
    for analisis in borradores:
        creado = analisis.creado_en
        if creado is None:
            continue
        if creado.tzinfo is None:
            creado = creado.replace(tzinfo=UTC)
        if creado > limite:
            continue
        eliminar_borrador(analisis.ruta_borrador)
        db.delete(analisis)
        eliminados += 1

    if eliminados:
        db.commit()
    return eliminados


def vincular_analisis(
    db: Session,
    analisis: DocumentoAnalisis,
    documento: Documento,
    archivo: DocumentoArchivo,
    *,
    tipo_confirmado_id: int | None = None,
) -> str | None:
    """Enlaza el borrador con el documento recién creado.

    Devuelve la ruta relativa del borrador para borrarla después del commit.
    """
    ruta = analisis.ruta_borrador
    analisis.documento_id = documento.id
    analisis.archivo_id = archivo.id
    analisis.nombre_original = archivo.nombre_original
    analisis.mime = archivo.mime
    analisis.ruta_borrador = None
    if tipo_confirmado_id is not None:
        analisis.tipo_tramite_confirmado_id = tipo_confirmado_id

    archivo.texto_ocr = analisis.texto_ocr
    archivo.paginas = analisis.paginas
    return ruta


def confirmar_tipo(db: Session, analisis: DocumentoAnalisis, tipo_id: int) -> None:
    """Guarda la etiqueta real para el futuro reentrenamiento."""
    analisis.tipo_tramite_confirmado_id = tipo_id
    db.flush()


def contexto_analisis(analisis: DocumentoAnalisis | None) -> dict:
    """Datos listos para la plantilla (sugerencias, OCR y confianza)."""
    if analisis is None:
        return {"analisis": None, "campos": [], "ocr": "", "clase_ia": None}

    datos = analisis.campos or {}
    campos: list[dict] = []

    if analisis.tipo_tramite_sugerido is not None:
        tipo = analisis.tipo_tramite_sugerido
        campos.append(
            {
                "clave": "tipo_tramite_id",
                "etiqueta": "Tipo de trámite",
                "texto": f"{tipo.codigo} · {tipo.nombre}",
                "valor": str(tipo.id),
                "confianza": analisis.sugerencia_confianza,
                "evidencia": (datos.get("tipo") or {}).get("evidencia", ""),
            }
        )

    for clave, etiqueta, destino in CAMPOS_UI:
        dato = (datos.get("campos") or {}).get(clave)
        if not dato or not dato.get("valor"):
            continue
        campos.append(
            {
                "clave": destino,
                "etiqueta": etiqueta,
                "texto": str(dato["valor"]),
                "valor": str(dato["valor"]),
                "confianza": dato.get("confianza"),
                "evidencia": dato.get("evidencia", ""),
            }
        )

    return {
        "analisis": analisis,
        "campos": campos,
        "ocr": analisis.texto_ocr or "",
        "clase_ia": datos.get("clase_ia"),
    }
