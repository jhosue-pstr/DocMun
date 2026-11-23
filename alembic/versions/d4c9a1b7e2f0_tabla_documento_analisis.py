"""tabla documento_analisis

Revision ID: d4c9a1b7e2f0
Revises: cf01505205fe
Create Date: 2026-11-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d4c9a1b7e2f0"
down_revision: str | None = "cf01505205fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documento_analisis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("documento_id", sa.Integer(), nullable=True),
        sa.Column("archivo_id", sa.Integer(), nullable=True),
        sa.Column("hash_sha256", sa.String(length=64), nullable=False),
        sa.Column("nombre_original", sa.String(length=255), nullable=True),
        sa.Column("mime", sa.String(length=100), nullable=True),
        sa.Column("ruta_borrador", sa.String(length=500), nullable=True),
        sa.Column(
            "estado",
            sa.Enum(
                "pendiente",
                "procesando",
                "listo",
                "error",
                name="estadoanalisis",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("texto_ocr", sa.Text(), nullable=True),
        sa.Column("paginas", sa.Integer(), nullable=True),
        sa.Column("campos", sa.JSON(), nullable=True),
        sa.Column("tipo_clase", sa.String(length=80), nullable=True),
        sa.Column("tipo_confianza", sa.Float(), nullable=True),
        sa.Column("tipo_tramite_sugerido_id", sa.Integer(), nullable=True),
        sa.Column("tipo_tramite_confirmado_id", sa.Integer(), nullable=True),
        sa.Column("sugerencia_confianza", sa.Float(), nullable=True),
        sa.Column("motor", sa.String(length=80), nullable=True),
        sa.Column("version_modelo", sa.String(length=60), nullable=True),
        sa.Column("duracion_ms", sa.Integer(), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["documento_id"], ["documentos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["archivo_id"], ["documento_archivos.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tipo_tramite_sugerido_id"], ["tipos_tramite.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tipo_tramite_confirmado_id"], ["tipos_tramite.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("archivo_id", name="uq_analisis_archivo"),
    )
    op.create_index(
        "ix_documento_analisis_estado", "documento_analisis", ["estado"], unique=False
    )
    op.create_index(
        "ix_documento_analisis_hash", "documento_analisis", ["hash_sha256"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_documento_analisis_hash", table_name="documento_analisis")
    op.drop_index("ix_documento_analisis_estado", table_name="documento_analisis")
    op.drop_table("documento_analisis")
