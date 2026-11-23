"""Análisis automático (OCR + IA) de archivos escaneados.

La app web crea una fila en estado `pendiente`; el contenedor `ia` la toma,
ejecuta OCR y los modelos, y guarda el resultado. Nunca se almacena el binario:
el texto OCR y las sugerencias viven aquí, y el archivo sigue en `storage/`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.columns import enum_col
from app.models.enums import EstadoAnalisis

if TYPE_CHECKING:
    from app.models.archivos import DocumentoArchivo
    from app.models.catalogos import TipoTramite
    from app.models.documentos import Documento


class DocumentoAnalisis(Base, TimestampMixin):
    """Resultado del OCR y de las sugerencias de IA para un archivo.

    También representa un borrador: mientras `documento_id` es NULL, el archivo
    vive en `storage/borradores/` y todavía no pertenece a ningún expediente.
    """

    __tablename__ = "documento_analisis"
    __table_args__ = (
        UniqueConstraint("archivo_id", name="uq_analisis_archivo"),
        Index("ix_documento_analisis_estado", "estado"),
        Index("ix_documento_analisis_hash", "hash_sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int | None] = mapped_column(
        ForeignKey("documentos.id", ondelete="CASCADE")
    )
    archivo_id: Mapped[int | None] = mapped_column(
        ForeignKey("documento_archivos.id", ondelete="CASCADE")
    )

    # Identificación del contenido: permite reutilizar el análisis del mismo archivo.
    hash_sha256: Mapped[str] = mapped_column(String(64))
    nombre_original: Mapped[str | None] = mapped_column(String(255))
    mime: Mapped[str | None] = mapped_column(String(100))
    ruta_borrador: Mapped[str | None] = mapped_column(String(500))

    estado: Mapped[EstadoAnalisis] = mapped_column(
        enum_col(EstadoAnalisis, length=20),
        default=EstadoAnalisis.PENDIENTE,
        nullable=False,
    )

    texto_ocr: Mapped[str | None] = mapped_column(Text)
    paginas: Mapped[int | None] = mapped_column(Integer)

    # Salida estructurada de las reglas y del KIE. No incluye el binario.
    campos: Mapped[dict | None] = mapped_column(JSON)

    # Clasificador genérico (hoy RVL-CDIP). Se guarda como diagnóstico.
    tipo_clase: Mapped[str | None] = mapped_column(String(80))
    tipo_confianza: Mapped[float | None] = mapped_column(Float)

    # Sugerencia por reglas de español y confirmación humana (para el dataset).
    tipo_tramite_sugerido_id: Mapped[int | None] = mapped_column(
        ForeignKey("tipos_tramite.id", ondelete="SET NULL")
    )
    tipo_tramite_confirmado_id: Mapped[int | None] = mapped_column(
        ForeignKey("tipos_tramite.id", ondelete="SET NULL")
    )
    sugerencia_confianza: Mapped[float | None] = mapped_column(Float)

    motor: Mapped[str | None] = mapped_column(String(80))
    version_modelo: Mapped[str | None] = mapped_column(String(60))
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    intentos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    documento: Mapped[Documento | None] = relationship()
    archivo: Mapped[DocumentoArchivo | None] = relationship()
    tipo_tramite_sugerido: Mapped[TipoTramite | None] = relationship(
        foreign_keys=[tipo_tramite_sugerido_id]
    )
    tipo_tramite_confirmado: Mapped[TipoTramite | None] = relationship(
        foreign_keys=[tipo_tramite_confirmado_id]
    )
