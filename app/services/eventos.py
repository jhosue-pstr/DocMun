"""Auditoría de documentos: eventos inmutables con usuario, IP y timestamp."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.documentos import Documento, Evento
from app.models.enums import TipoEvento
from app.models.usuarios import Usuario


def ip_desde_request(request: Request | None) -> str | None:
    """Obtiene la IP del cliente sin confiar todavía en cabeceras de proxy."""
    if request is None or request.client is None:
        return None
    return request.client.host


def registrar_evento(
    db: Session,
    documento: Documento,
    tipo: TipoEvento,
    *,
    usuario: Usuario | None = None,
    request: Request | None = None,
    detalle: dict[str, object] | None = None,
    creado_en: datetime | None = None,
    commit: bool = False,
) -> Evento:
    """Registra un evento de auditoría para un documento.

    `detalle` nunca debe contener DNI, teléfono, correo ni otro dato personal.
    El llamador controla la transacción salvo que pase `commit=True`.
    `creado_en` solo se usa para reconstruir flujos históricos de demostración.
    """
    if documento.id is None:
        db.flush()

    evento = Evento(
        documento_id=documento.id,
        usuario_id=usuario.id if usuario is not None else None,
        tipo=tipo,
        detalle=detalle,
        ip=ip_desde_request(request),
        creado_en=creado_en or datetime.now(UTC),
    )
    db.add(evento)
    if commit:
        db.commit()
        db.refresh(evento)
    return evento
