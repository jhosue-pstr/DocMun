"""Generación del código QR que se imprime en el cargo o la etiqueta."""

from __future__ import annotations

from io import BytesIO

import qrcode

from app.core.config import settings


def url_documento(codigo: str) -> str:
    """URL interna de seguimiento que se codifica en el QR.

    El QR nunca contiene DNI, nombres ni teléfonos: solo el código interno.
    """
    base = settings.public_base_url.rstrip("/")
    return f"{base}/documentos/{codigo}"


def qr_png(codigo: str, *, box_size: int = 10, borde: int = 2) -> bytes:
    """Devuelve la imagen PNG del QR en memoria, sin tocar disco."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=borde,
    )
    qr.add_data(url_documento(codigo))
    qr.make(fit=True)

    imagen = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()
