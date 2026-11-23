"""Archivos digitalizados y adjuntos a un trámite."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.columns import enum_col
from app.models.enums import OrigenArchivo

if TYPE_CHECKING:
    from app.models.documentos import Documento
    from app.models.usuarios import Usuario


class DocumentoArchivo(Base, TimestampMixin):
    """Metadatos de un PDF o imagen guardada en `storage/`.

    El contenido binario no vive en PostgreSQL: se guarda en disco y aquí solo
    se registra la ruta, el hash y el tamaño. Un mismo trámite puede tener
    varios archivos (por ejemplo, el formulario y los anexos escaneados).
    """

    __tablename__ = "documento_archivos"
    __table_args__ = (
        UniqueConstraint("documento_id", "hash_sha256", name="uq_archivo_documento_hash"),
        Index("ix_documento_archivos_documento", "documento_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id", ondelete="CASCADE"))
    nombre_original: Mapped[str] = mapped_column(String(255))
    ruta: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(100))
    tamano_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), index=True)
    paginas: Mapped[int | None] = mapped_column(Integer)
    origen: Mapped[OrigenArchivo] = mapped_column(
        enum_col(OrigenArchivo, length=20),
        default=OrigenArchivo.ESCANEO,
        nullable=False,
    )
    texto_ocr: Mapped[str | None] = mapped_column(Text)
    subido_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL")
    )

    documento: Mapped[Documento] = relationship(back_populates="archivos")
    subido_por: Mapped[Usuario | None] = relationship()
