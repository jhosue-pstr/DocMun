"""Ciudadanos y empresas que solicitan trámites."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.columns import enum_col
from app.models.enums import TipoDocumentoIdentidad

if TYPE_CHECKING:
    from app.models.documentos import Documento


class Ciudadano(Base, TimestampMixin):
    __tablename__ = "ciudadanos"
    __table_args__ = (
        UniqueConstraint(
            "tipo_documento", "numero_documento", name="uq_ciudadano_documento"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo_documento: Mapped[TipoDocumentoIdentidad] = mapped_column(
        enum_col(TipoDocumentoIdentidad),
        default=TipoDocumentoIdentidad.DNI,
        nullable=False,
    )
    numero_documento: Mapped[str] = mapped_column(String(20), index=True)
    nombres: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(150))
    telefono: Mapped[str | None] = mapped_column(String(30))
    direccion: Mapped[str | None] = mapped_column(String(250))

    documentos: Mapped[list[Documento]] = relationship(back_populates="ciudadano")
