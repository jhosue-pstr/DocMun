"""Cargo de recepción y etiqueta con QR para el expediente físico."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.models.documentos import Documento
from app.services.qr import qr_png

_TZ = ZoneInfo(settings.timezone)
_GRIS = 0.42


def _fecha(valor: datetime | None) -> str:
    if valor is None:
        return "—"
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.astimezone(_TZ).strftime("%d/%m/%Y %H:%M")


def _texto(valor: object | None) -> str:
    if valor is None:
        return "—"
    texto = str(valor).strip()
    return texto or "—"


def _imagen_qr(codigo: str) -> ImageReader:
    return ImageReader(BytesIO(qr_png(codigo, box_size=12)))


def _campo(
    lienzo: canvas.Canvas,
    x: float,
    y: float,
    ancho: float,
    etiqueta: str,
    valor: str,
    *,
    tamano: float = 10,
    salto: float = 13,
) -> float:
    """Dibuja una etiqueta pequeña y su valor con ajuste de línea."""
    lienzo.setFillGray(_GRIS)
    lienzo.setFont("Helvetica", 7)
    lienzo.drawString(x, y, etiqueta.upper())
    lienzo.setFillGray(0)
    lienzo.setFont("Helvetica", tamano)

    lineas = simpleSplit(valor, "Helvetica", tamano, ancho) or ["—"]
    y -= salto
    for linea in lineas:
        lienzo.drawString(x, y, linea)
        y -= salto
    return y - 4


def _ciudadano(documento: Documento) -> str:
    ciudadano = documento.ciudadano
    if ciudadano is None:
        return _texto(documento.administrado)
    detalle = f"{ciudadano.nombres}"
    if ciudadano.numero_documento:
        detalle += f" · {ciudadano.tipo_documento.value.upper()} {ciudadano.numero_documento}"
    return detalle


def _certificado(documento: Documento) -> str:
    if not documento.numero_certificado:
        return "Sin número de certificado"
    if documento.anio:
        return f"{documento.numero_certificado} · {documento.anio}"
    return documento.numero_certificado


def cargo_pdf(documento: Documento) -> bytes:
    """Cargo A4 que se entrega al ciudadano al recibir el expediente."""
    buffer = BytesIO()
    lienzo = canvas.Canvas(buffer, pagesize=A4)
    ancho, alto = A4
    margen = 18 * mm
    ancho_util = ancho - 2 * margen

    # Encabezado
    lienzo.setFillGray(0)
    lienzo.setFont("Helvetica-Bold", 15)
    lienzo.drawString(margen, alto - margen, "CARGO DE RECEPCIÓN DE DOCUMENTO")
    lienzo.setFont("Helvetica", 9)
    lienzo.setFillGray(_GRIS)
    lienzo.drawString(
        margen,
        alto - margen - 14,
        "Municipalidad · Sistema de seguimiento de documentos",
    )

    # Bloque del código y QR, alineado a la derecha
    qr_lado = 42 * mm
    qr_x = ancho - margen - qr_lado
    qr_y = alto - margen - 14 - qr_lado
    lienzo.drawImage(
        _imagen_qr(documento.codigo),
        qr_x,
        qr_y,
        width=qr_lado,
        height=qr_lado,
        preserveAspectRatio=True,
        mask="auto",
    )
    lienzo.setFillGray(0)
    lienzo.setFont("Helvetica-Bold", 9)
    lienzo.drawCentredString(qr_x + qr_lado / 2, qr_y - 12, "Escanee para consultar el estado")

    # Código interno destacado
    lienzo.setFont("Helvetica", 8)
    lienzo.setFillGray(_GRIS)
    lienzo.drawString(margen, alto - margen - 42, "CÓDIGO INTERNO")
    lienzo.setFillGray(0)
    lienzo.setFont("Helvetica-Bold", 22)
    lienzo.drawString(margen, alto - margen - 68, documento.codigo)

    # Línea separadora
    y = alto - margen - 84
    lienzo.setStrokeGray(0.8)
    lienzo.setLineWidth(0.6)
    lienzo.line(margen, y, ancho - margen, y)
    y -= 20

    # Columna izquierda
    ancho_col = ancho_util - qr_lado - 18 * mm
    y = _campo(
        lienzo,
        margen,
        y,
        ancho_col,
        "Tipo de trámite",
        _texto(documento.tipo_tramite.nombre if documento.tipo_tramite else None),
    )
    y = _campo(lienzo, margen, y, ancho_col, "Número de certificado", _certificado(documento))
    y = _campo(lienzo, margen, y, ancho_col, "Asunto", _texto(documento.asunto))
    y = _campo(lienzo, margen, y, ancho_col, "Administrado", _ciudadano(documento))
    y = _campo(lienzo, margen, y, ancho_col, "RUT", _texto(documento.rut))
    y = _campo(
        lienzo,
        margen,
        y,
        ancho_col,
        "Expediente administrativo",
        _texto(documento.expediente_administrativo),
    )

    y -= 6
    lienzo.setStrokeGray(0.85)
    lienzo.line(margen, y, margen + ancho_col, y)
    y -= 18

    y = _campo(lienzo, margen, y, ancho_col, "Fecha de registro", _fecha(documento.fecha_registro))
    y = _campo(
        lienzo,
        margen,
        y,
        ancho_col,
        "Prioridad",
        _texto(documento.prioridad.value if documento.prioridad else None),
    )
    y = _campo(
        lienzo,
        margen,
        y,
        ancho_col,
        "Oficina actual",
        _texto(documento.oficina_actual.nombre if documento.oficina_actual else "Ventanilla"),
    )

    # Pie
    lienzo.setStrokeGray(0.8)
    lienzo.line(margen, 30 * mm, ancho - margen, 30 * mm)
    lienzo.setFillGray(_GRIS)
    lienzo.setFont("Helvetica", 8)
    lienzo.drawString(
        margen,
        24 * mm,
        "Conserve este cargo. El código y el QR permiten consultar el avance del trámite.",
    )
    lienzo.drawString(
        margen,
        19 * mm,
        "El QR solo contiene la dirección de seguimiento, no datos personales.",
    )

    lienzo.showPage()
    lienzo.save()
    return buffer.getvalue()


def etiqueta_pdf(documento: Documento) -> bytes:
    """Etiqueta de 100 × 70 mm para pegar en el expediente físico."""
    ancho, alto = 100 * mm, 70 * mm
    buffer = BytesIO()
    lienzo = canvas.Canvas(buffer, pagesize=(ancho, alto))
    margen = 6 * mm

    qr_lado = 38 * mm
    lienzo.drawImage(
        _imagen_qr(documento.codigo),
        margen,
        (alto - qr_lado) / 2,
        width=qr_lado,
        height=qr_lado,
        preserveAspectRatio=True,
        mask="auto",
    )

    x = margen + qr_lado + 6 * mm
    ancho_texto = ancho - x - margen
    y = alto - margen - 3

    lienzo.setFillGray(_GRIS)
    lienzo.setFont("Helvetica", 6)
    lienzo.drawString(x, y, "CÓDIGO INTERNO")
    y -= 12

    lienzo.setFillGray(0)
    lienzo.setFont("Helvetica-Bold", 13)
    lienzo.drawString(x, y, documento.codigo)
    y -= 14

    lienzo.setFillGray(_GRIS)
    lienzo.setFont("Helvetica", 6)
    lienzo.drawString(x, y, "TRÁMITE")
    y -= 10

    lienzo.setFillGray(0)
    lienzo.setFont("Helvetica", 8)
    nombre = _texto(documento.tipo_tramite.nombre if documento.tipo_tramite else None)
    for linea in (simpleSplit(nombre, "Helvetica", 8, ancho_texto) or ["—"])[:3]:
        lienzo.drawString(x, y, linea)
        y -= 10

    y -= 4
    lienzo.setFillGray(_GRIS)
    lienzo.setFont("Helvetica", 7)
    lienzo.drawString(x, y, _fecha(documento.fecha_registro))

    lienzo.setStrokeGray(0.75)
    lienzo.setLineWidth(0.5)
    lienzo.rect(1.5 * mm, 1.5 * mm, ancho - 3 * mm, alto - 3 * mm)

    lienzo.showPage()
    lienzo.save()
    return buffer.getvalue()
