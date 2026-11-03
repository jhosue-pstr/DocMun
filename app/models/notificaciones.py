"""Notificaciones al ciudadano y su log de envío."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.columns import enum_col
from app.models.enums import CanalNotificacion, EstadoNotificacion

if TYPE_CHECKING:
    from app.models.documentos import Documento


class Notificacion(Base, TimestampMixin):
    __tablename__ = "notificaciones"
    __table_args__ = (Index("ix_notificaciones_estado", "estado"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id", ondelete="CASCADE"))
    canal: Mapped[CanalNotificacion] = mapped_column(
        enum_col(CanalNotificacion), nullable=False
    )
    destinatario: Mapped[str] = mapped_column(String(200))
    plantilla: Mapped[str] = mapped_column(String(100))
    estado: Mapped[EstadoNotificacion] = mapped_column(
        enum_col(EstadoNotificacion), default=EstadoNotificacion.PENDIENTE, nullable=False
    )
    intentos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    proveedor_id: Mapped[str | None] = mapped_column(String(150))
    error: Mapped[str | None] = mapped_column(Text)
    enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documento: Mapped[Documento] = relationship(back_populates="notificaciones")
