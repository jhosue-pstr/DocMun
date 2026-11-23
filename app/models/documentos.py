"""Documentos, movimientos entre oficinas y eventos de auditoría."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.columns import enum_col
from app.models.enums import (
    EstadoDocumento,
    OrigenDocumento,
    Prioridad,
    TipoEvento,
)

if TYPE_CHECKING:
    from app.models.archivos import DocumentoArchivo
    from app.models.catalogos import Oficina, TipoTramite
    from app.models.ciudadanos import Ciudadano
    from app.models.notificaciones import Notificacion
    from app.models.usuarios import Usuario


class Documento(Base, TimestampMixin):
    __tablename__ = "documentos"
    __table_args__ = (
        UniqueConstraint(
            "tipo_tramite_id",
            "numero_certificado",
            "anio",
            name="uq_documento_tipo_numero_anio",
        ),
        Index("ix_documentos_estado", "estado"),
        Index("ix_documentos_oficina_actual", "oficina_actual_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    tipo_tramite_id: Mapped[int] = mapped_column(
        ForeignKey("tipos_tramite.id", ondelete="RESTRICT")
    )
    ciudadano_id: Mapped[int | None] = mapped_column(
        ForeignKey("ciudadanos.id", ondelete="SET NULL")
    )

    # Identificadores del documento físico / histórico
    numero_certificado: Mapped[str | None] = mapped_column(String(50), index=True)
    anio: Mapped[int | None] = mapped_column(Integer, index=True)
    fuente_numero: Mapped[str | None] = mapped_column(String(50))
    rut: Mapped[str | None] = mapped_column(String(30))
    expediente_administrativo: Mapped[str | None] = mapped_column(String(50))
    ruc: Mapped[str | None] = mapped_column(String(20))
    referencia: Mapped[str | None] = mapped_column(String(200))
    administrado: Mapped[str | None] = mapped_column(String(200))
    asunto: Mapped[str | None] = mapped_column(String(300))

    prioridad: Mapped[Prioridad] = mapped_column(
        enum_col(Prioridad), default=Prioridad.NORMAL, nullable=False
    )
    estado: Mapped[EstadoDocumento] = mapped_column(
        enum_col(EstadoDocumento), default=EstadoDocumento.REGISTRADO, nullable=False
    )
    origen: Mapped[OrigenDocumento] = mapped_column(
        enum_col(OrigenDocumento), default=OrigenDocumento.MANUAL, nullable=False
    )

    oficina_actual_id: Mapped[int | None] = mapped_column(
        ForeignKey("oficinas.id", ondelete="SET NULL")
    )
    creado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL")
    )

    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    fecha_finalizacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    observaciones: Mapped[str | None] = mapped_column(Text)
    observaciones_importacion: Mapped[str | None] = mapped_column(Text)
    archivo_origen: Mapped[str | None] = mapped_column(String(500))

    tipo_tramite: Mapped[TipoTramite] = relationship(back_populates="documentos")
    ciudadano: Mapped[Ciudadano | None] = relationship(back_populates="documentos")
    oficina_actual: Mapped[Oficina | None] = relationship(foreign_keys=[oficina_actual_id])
    creado_por: Mapped[Usuario | None] = relationship(foreign_keys=[creado_por_id])
    movimientos: Mapped[list[Movimiento]] = relationship(
        back_populates="documento",
        cascade="all, delete-orphan",
        order_by="Movimiento.fecha_envio",
    )
    eventos: Mapped[list[Evento]] = relationship(
        back_populates="documento",
        cascade="all, delete-orphan",
        order_by="Evento.creado_en",
    )
    notificaciones: Mapped[list[Notificacion]] = relationship(back_populates="documento")
    archivos: Mapped[list[DocumentoArchivo]] = relationship(
        back_populates="documento",
        cascade="all, delete-orphan",
        order_by="DocumentoArchivo.creado_en",
    )


class Movimiento(Base, TimestampMixin):
    """Cada derivación de un documento entre oficinas. Nunca se modifica ni se borra."""

    __tablename__ = "movimientos"
    __table_args__ = (
        Index("ix_movimientos_documento", "documento_id"),
        Index("ix_movimientos_destino", "oficina_destino_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id", ondelete="CASCADE"))
    oficina_origen_id: Mapped[int | None] = mapped_column(
        ForeignKey("oficinas.id", ondelete="SET NULL")
    )
    oficina_destino_id: Mapped[int] = mapped_column(
        ForeignKey("oficinas.id", ondelete="RESTRICT")
    )
    usuario_envia_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL")
    )
    usuario_recibe_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL")
    )

    fecha_envio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fecha_recepcion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_salida: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sla_horas: Mapped[int | None] = mapped_column(Integer)
    fecha_limite: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    observacion: Mapped[str | None] = mapped_column(Text)

    documento: Mapped[Documento] = relationship(back_populates="movimientos")
    oficina_origen: Mapped[Oficina | None] = relationship(foreign_keys=[oficina_origen_id])
    oficina_destino: Mapped[Oficina] = relationship(foreign_keys=[oficina_destino_id])
    usuario_envia: Mapped[Usuario | None] = relationship(foreign_keys=[usuario_envia_id])
    usuario_recibe: Mapped[Usuario | None] = relationship(foreign_keys=[usuario_recibe_id])


class Evento(Base):
    """Auditoría inmutable de todo lo que ocurre con un documento."""

    __tablename__ = "eventos"
    __table_args__ = (Index("ix_eventos_documento", "documento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id", ondelete="CASCADE"))
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL")
    )
    tipo: Mapped[TipoEvento] = mapped_column(enum_col(TipoEvento), nullable=False)
    detalle: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(45))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documento: Mapped[Documento] = relationship(back_populates="eventos")
    usuario: Mapped[Usuario | None] = relationship()
