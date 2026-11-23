"""Alta y búsqueda de ciudadanos por documento de identidad."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ciudadanos import Ciudadano
from app.models.enums import TipoDocumentoIdentidad


@dataclass(frozen=True)
class DatosCiudadano:
    tipo_documento: TipoDocumentoIdentidad
    numero_documento: str
    nombres: str
    email: str | None = None
    telefono: str | None = None
    direccion: str | None = None


def normalizar_numero_documento(valor: str) -> str:
    return "".join(valor.split()).upper()


def obtener_ciudadano(
    db: Session,
    tipo_documento: TipoDocumentoIdentidad,
    numero_documento: str,
) -> Ciudadano | None:
    numero = normalizar_numero_documento(numero_documento)
    return db.scalar(
        select(Ciudadano).where(
            Ciudadano.tipo_documento == tipo_documento,
            Ciudadano.numero_documento == numero,
        )
    )


def obtener_o_crear(
    db: Session,
    datos: DatosCiudadano,
) -> tuple[Ciudadano, bool]:
    """Devuelve el ciudadano existente o lo crea.

    El segundo valor indica si se creó. No se actualizan los datos de un
    ciudadano existente para no sobrescribir información validada.
    """
    numero = normalizar_numero_documento(datos.numero_documento)
    existente = obtener_ciudadano(db, datos.tipo_documento, numero)
    if existente is not None:
        return existente, False

    ciudadano = Ciudadano(
        tipo_documento=datos.tipo_documento,
        numero_documento=numero,
        nombres=datos.nombres.strip(),
        email=(datos.email or "").strip() or None,
        telefono=(datos.telefono or "").strip() or None,
        direccion=(datos.direccion or "").strip() or None,
    )

    try:
        with db.begin_nested():
            db.add(ciudadano)
            db.flush()
    except IntegrityError:
        # Otra transacción pudo crearlo entre la consulta y el INSERT.
        existente = obtener_ciudadano(db, datos.tipo_documento, numero)
        if existente is None:
            raise
        return existente, False

    return ciudadano, True
