"""Catálogos: oficinas, tipos de trámite, plazos, feriados y configuración."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.documentos import Documento
    from app.models.usuarios import Usuario


class Oficina(Base, TimestampMixin):
    __tablename__ = "oficinas"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    siglas: Mapped[str | None] = mapped_column(String(30))
    ubicacion: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(150))
    telefono: Mapped[str | None] = mapped_column(String(30))
    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    usuarios: Mapped[list[Usuario]] = relationship(back_populates="oficina")
    plazos: Mapped[list[Plazo]] = relationship(back_populates="oficina")
    tipos_responsable: Mapped[list[TipoTramite]] = relationship(
        back_populates="oficina_responsable",
        foreign_keys="TipoTramite.oficina_responsable_id",
    )


class TipoTramite(Base, TimestampMixin):
    __tablename__ = "tipos_tramite"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    area: Mapped[str | None] = mapped_column(String(150))
    oficina_responsable_id: Mapped[int | None] = mapped_column(
        ForeignKey("oficinas.id", ondelete="SET NULL")
    )
    sla_horas: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    oficina_responsable: Mapped[Oficina | None] = relationship(
        back_populates="tipos_responsable", foreign_keys=[oficina_responsable_id]
    )
    plazos: Mapped[list[Plazo]] = relationship(back_populates="tipo_tramite")
    documentos: Mapped[list[Documento]] = relationship(back_populates="tipo_tramite")


class Plazo(Base, TimestampMixin):
    """Horas máximas de permanencia de un tipo de trámite en una oficina."""

    __tablename__ = "plazos"
    __table_args__ = (
        UniqueConstraint("tipo_tramite_id", "oficina_id", name="uq_plazo_tipo_oficina"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo_tramite_id: Mapped[int] = mapped_column(
        ForeignKey("tipos_tramite.id", ondelete="CASCADE")
    )
    oficina_id: Mapped[int] = mapped_column(ForeignKey("oficinas.id", ondelete="CASCADE"))
    horas: Mapped[int] = mapped_column(Integer, nullable=False)

    tipo_tramite: Mapped[TipoTramite] = relationship(back_populates="plazos")
    oficina: Mapped[Oficina] = relationship(back_populates="plazos")


class Feriado(Base, TimestampMixin):
    __tablename__ = "feriados"

    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, unique=True, index=True)
    descripcion: Mapped[str] = mapped_column(String(150))


class Configuracion(Base, TimestampMixin):
    """Parámetros editables: horario laboral, umbrales, datos de la muni."""

    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(100), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    descripcion: Mapped[str | None] = mapped_column(String(250))
