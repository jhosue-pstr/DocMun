"""Mensajes flash guardados en la sesión firmada."""

from __future__ import annotations

from fastapi import Request

_CLAVE = "flash_messages"


def flash(request: Request, mensaje: str, categoria: str = "ok") -> None:
    """Encola un mensaje para mostrarlo en la siguiente pantalla."""
    mensajes = request.session.get(_CLAVE)
    if not isinstance(mensajes, list):
        mensajes = []
    mensajes.append({"mensaje": mensaje, "categoria": categoria})
    request.session[_CLAVE] = mensajes


def pop_flash(request: Request) -> list[dict[str, str]]:
    """Devuelve y limpia los mensajes pendientes de la sesión."""
    mensajes = request.session.pop(_CLAVE, [])
    if not isinstance(mensajes, list):
        return []

    limpios: list[dict[str, str]] = []
    for item in mensajes:
        if not isinstance(item, dict):
            continue
        mensaje = item.get("mensaje")
        if not isinstance(mensaje, str) or not mensaje.strip():
            continue
        categoria = item.get("categoria", "info")
        limpios.append(
            {
                "mensaje": mensaje,
                "categoria": categoria if isinstance(categoria, str) else "info",
            }
        )
    return limpios
