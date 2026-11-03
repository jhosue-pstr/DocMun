"""Generación del código interno de documentos."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.documentos import Documento


def anio_actual() -> int:
    return datetime.now(UTC).year


def generar_codigo(db: Session, anio: int | None = None) -> str:
    """Genera el siguiente código DOC-AAAA-NNNNNN para el año indicado.

    No es un generador libre de colisiones: el llamador debe reintentar si la
    restricción única de `codigo` falla (ver servicio de documentos, fase 1).
    """
    anio = anio or anio_actual()
    prefijo = f"DOC-{anio}-"
    ultimo = db.scalar(
        select(func.max(Documento.codigo)).where(Documento.codigo.like(f"{prefijo}%"))
    )
    siguiente = 1
    if ultimo:
        try:
            siguiente = int(ultimo.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            siguiente = 1
    return f"{prefijo}{siguiente:06d}"
