"""Utilidades para columnas reutilizables."""

import enum

from sqlalchemy import Enum


def enum_col(enum_cls: type[enum.Enum], length: int = 30) -> Enum:
    """Enum almacenado como VARCHAR con check, usando los valores (no los nombres)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [item.value for item in cls],
    )
