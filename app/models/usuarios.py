"""Usuarios del sistema y sus roles."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.columns import enum_col
from app.models.enums import Rol

if TYPE_CHECKING:
    from app.models.catalogos import Oficina


class Usuario(Base, TimestampMixin):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    rol: Mapped[Rol] = mapped_column(enum_col(Rol), default=Rol.VENTANILLA, nullable=False)
    oficina_id: Mapped[int | None] = mapped_column(
        ForeignKey("oficinas.id", ondelete="SET NULL")
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    oficina: Mapped[Oficina | None] = relationship(back_populates="usuarios")

    @property
    def es_admin(self) -> bool:
        return self.rol == Rol.ADMIN
