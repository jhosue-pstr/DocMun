"""Guardado y validación de archivos escaneados."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.archivos import DocumentoArchivo
from app.models.documentos import Documento
from app.models.enums import OrigenArchivo, TipoEvento
from app.models.usuarios import Usuario
from app.services.eventos import registrar_evento

# Firma real del archivo -> (mime, extensión, orígenes de bytes válidos).
TIPOS_PERMITIDOS: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "application/pdf": (".pdf", (b"%PDF-",)),
    "image/jpeg": (".jpg", (b"\xff\xd8\xff",)),
    "image/png": (".png", (b"\x89PNG\r\n\x1a\n",)),
    "image/tiff": (".tif", (b"II*\x00", b"MM\x00*")),
}

EXTENSIONES_ACEPTADAS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff")

LOGGER = logging.getLogger(__name__)


class ArchivoInvalido(ValueError):
    """El archivo no cumple las reglas de formato o tamaño."""


class ArchivoDuplicado(ArchivoInvalido):
    """El mismo archivo ya está adjunto al trámite."""


@dataclass(frozen=True)
class ArchivoValidado:
    """Contenido ya leído y verificado, listo para asociarse a un documento."""

    contenido: bytes
    nombre_original: str
    mime: str
    extension: str
    hash_sha256: str


def _base_dir() -> Path:
    return Path(settings.storage_dir).resolve()


def _detectar_tipo(contenido: bytes) -> tuple[str, str]:
    for mime, (extension, firmas) in TIPOS_PERMITIDOS.items():
        if any(contenido.startswith(firma) for firma in firmas):
            return mime, extension
    raise ArchivoInvalido("Formato no permitido. Adjunta un PDF, JPG, PNG o TIFF.")


def ruta_absoluta(archivo: DocumentoArchivo) -> Path:
    """Resuelve la ruta del archivo sin permitir salir de `storage/`."""
    base = _base_dir()
    ruta = (base / archivo.ruta).resolve()
    if ruta != base and not str(ruta).startswith(f"{base}{os.sep}"):
        raise ArchivoInvalido("La ruta del archivo no es válida.")
    return ruta


def eliminar_archivo_fisico(archivo: DocumentoArchivo) -> None:
    """Borra el archivo de disco; se usa solo para limpiar fallos de guardado."""
    try:
        ruta_absoluta(archivo).unlink(missing_ok=True)
    except (OSError, ArchivoInvalido):
        pass


async def validar_archivo(upload: UploadFile) -> ArchivoValidado:
    """Lee el archivo y verifica tamaño, tipo real y hash.

    Se ejecuta antes de crear el documento para no dejar registros a medias
    cuando el archivo es inválido.
    """
    limite = settings.max_upload_mb * 1024 * 1024
    contenido = await upload.read(limite + 1)
    if not contenido:
        raise ArchivoInvalido("El archivo está vacío.")
    if len(contenido) > limite:
        raise ArchivoInvalido(f"El archivo supera el máximo de {settings.max_upload_mb} MB.")

    mime, extension = _detectar_tipo(contenido)
    nombre_original = (upload.filename or "archivo").strip()[:255] or "archivo"
    return ArchivoValidado(
        contenido=contenido,
        nombre_original=nombre_original,
        mime=mime,
        extension=extension,
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
    )


def guardar_validado(
    db: Session,
    documento: Documento,
    validado: ArchivoValidado,
    *,
    usuario: Usuario | None = None,
    request: Request | None = None,
    encolar: bool = True,
) -> DocumentoArchivo:
    """Asocia un archivo ya validado a un documento existente.

    No hace commit: el llamador controla la transacción. Si el INSERT falla,
    el archivo recién escrito se elimina para no dejar huérfanos.
    """
    if documento.id is None:
        db.flush()

    duplicado = db.scalar(
        select(DocumentoArchivo.id).where(
            DocumentoArchivo.documento_id == documento.id,
            DocumentoArchivo.hash_sha256 == validado.hash_sha256,
        )
    )
    if duplicado is not None:
        raise ArchivoDuplicado("Ese archivo ya está adjunto a este trámite.")

    relativa = Path("documentos") / documento.codigo / f"{uuid.uuid4().hex}{validado.extension}"
    absoluta = _base_dir() / relativa
    absoluta.parent.mkdir(parents=True, exist_ok=True)
    absoluta.write_bytes(validado.contenido)

    archivo = DocumentoArchivo(
        documento_id=documento.id,
        nombre_original=validado.nombre_original,
        ruta=str(relativa).replace("\\", "/"),
        mime=validado.mime,
        tamano_bytes=len(validado.contenido),
        hash_sha256=validado.hash_sha256,
        origen=(
            OrigenArchivo.ESCANEO if validado.mime == "application/pdf" else OrigenArchivo.CAMARA
        ),
        subido_por_id=usuario.id if usuario is not None else None,
    )

    try:
        with db.begin_nested():
            db.add(archivo)
            db.flush()
    except IntegrityError as exc:
        eliminar_archivo_fisico(archivo)
        raise ArchivoDuplicado("Ese archivo ya está adjunto a este trámite.") from exc
    except Exception:
        eliminar_archivo_fisico(archivo)
        raise

    registrar_evento(
        db,
        documento,
        TipoEvento.ADJUNTO_AGREGADO,
        usuario=usuario,
        request=request,
        detalle={
            "archivo_id": archivo.id,
            "mime": archivo.mime,
            "tamano_bytes": archivo.tamano_bytes,
        },
    )

    # El análisis es opcional: si falla el encolado, el documento igual se guarda.
    if settings.ia_habilitada and encolar:
        try:
            from app.services.ia.cola import encolar_analisis

            with db.begin_nested():
                encolar_analisis(db, archivo)
        except Exception:
            LOGGER.exception("No se pudo encolar el análisis IA del archivo %s", archivo.id)

    return archivo


async def guardar_archivo(
    db: Session,
    documento: Documento,
    upload: UploadFile,
    *,
    usuario: Usuario | None = None,
    request: Request | None = None,
) -> DocumentoArchivo:
    """Valida el upload y lo asocia a un documento ya creado."""
    validado = await validar_archivo(upload)
    return guardar_validado(db, documento, validado, usuario=usuario, request=request)
