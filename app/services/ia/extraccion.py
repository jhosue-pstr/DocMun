"""Extracción de campos y tipo de trámite con reglas de español.

Este módulo es puro: no importa torch, Tesseract ni la base de datos. Así se
puede probar rápido y el worker puede usarlo aunque los modelos no estén
disponibles.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# Cada regla es (código de TipoTramite, patrón, peso). Los códigos TR-xx
# corresponden, en orden, a las 16 áreas OF-01…OF-16 del seed.
REGLAS_TIPO: tuple[tuple[str, str, float], ...] = (
    ("TR-01", r"numeraci[oó]n municipal", 1.0),
    ("TR-01", r"certificado de numeraci[oó]n", 1.0),
    ("TR-02", r"par[aá]metros urban[ií]sticos", 1.0),
    ("TR-02", r"par[aá]metros edificatorios", 0.8),
    ("TR-03", r"visado de planos", 1.0),
    ("TR-03", r"memoria descriptiva", 0.7),
    ("TR-04", r"certificado negativo de catastro", 1.0),
    ("TR-04", r"catastro", 0.5),
    ("TR-05", r"constancia de posesi[oó]n", 1.0),
    ("TR-05", r"posesi[oó]n para servicios", 0.9),
    ("TR-06", r"zonificaci[oó]n", 1.0),
    ("TR-06", r"certificado de zonificaci[oó]n", 1.0),
    ("TR-07", r"licencias? de edificaci[oó]n", 1.0),
    ("TR-08", r"apertura de zanja", 1.0),
    ("TR-08", r"zanja en predios", 0.9),
    ("TR-09", r"compatibilidad de uso", 1.0),
    ("TR-10", r"ocupaci[oó]n de v[ií]a p[uú]blica", 1.0),
    ("TR-10", r"v[ií]a p[uú]blica con materiales", 0.9),
    ("TR-11", r"certificado de habitabilidad", 1.0),
    ("TR-11", r"habitabilidad", 0.6),
    ("TR-12", r"certificado de jurisdicci[oó]n", 1.0),
    ("TR-12", r"jurisdicci[oó]n", 0.6),
    ("TR-13", r"telecomunicaciones", 1.0),
    ("TR-14", r"constancia de alineamiento", 1.0),
    ("TR-14", r"alineamiento", 0.6),
    ("TR-15", r"conformidad de obra", 1.0),
    ("TR-15", r"declaratoria de edificaci[oó]n", 0.9),
    ("TR-16", r"predio r[uú]stico", 1.0),
    ("TR-16", r"constancia de predio", 0.7),
)

# Sube esta versión al cambiar las reglas: invalida los resultados cacheados por
# hash para que un documento ya analizado se vuelva a procesar con las reglas nuevas.
VERSION_REGLAS = "2026-11-22.1"

_PATRON_DNI = re.compile(
    r"\bDNI\b[\s:.\-N°º]{0,15}(\d{8})\b"
    r"|DOCUMENTO\s+NACIONAL\s+DE\s+IDENTIDAD[\s:.\-N°º]{0,20}(\d{8})\b",
    re.IGNORECASE,
)
_PATRON_RUC = re.compile(
    r"\bRUC\b[\s:.\-N°º]{0,15}((?:10|15|17|20)\d{9})\b",
    re.IGNORECASE,
)
_PATRON_EXPEDIENTE = re.compile(
    r"\bEXP(?:EDIENTE)?\b[\s.:N°º\-]{0,15}([A-Z0-9][A-Z0-9\-/]{3,30})",
    re.IGNORECASE,
)
_PATRON_NUMERO = re.compile(
    r"\b(?:CARTA|CERTIFICADO|CONSTANCIA|RESOLUCI[OÓ]N|OFICIO|MEMORANDO|INFORME)\b"
    r"\s*\|?\s*(?:N\s*[?º°¨'′.]?|NRO\.?|N[UÚ]MERO)?\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9\-/.]{1,45})",
    re.IGNORECASE,
)
# El OCR a veces lee "CERTIFICADO | N°629-2026" y también "CERTIFICADO NO ACREDITA";
# una coincidencia sin dígitos es texto, no un número de certificado.
_NO_ES_NUMERO = {"NO", "DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "A", "EN", "POR"}
_PATRON_ASUNTO = re.compile(r"\bASUNTO\b\s*[:\-]\s*(.{3,220})", re.IGNORECASE)
_PATRON_REFERENCIA = re.compile(r"\bREFERENCIA\b\s*[:\-]\s*(.{3,220})", re.IGNORECASE)
_PATRON_ANIO = re.compile(r"\b((?:19|20)\d{2})\b")
# "Fecha de Expedición: Juliaca, 23 de setiembre del 2026" o "Fecha: 01-09-2026".
_PATRON_FECHA = re.compile(
    r"\bFECHA(?:\s+DE\s+EXPEDICI[OÓ]N)?\b[^\n]{0,60}?"
    r"\b\d{1,2}[-/.]\d{1,2}[-/.]((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_PATRON_FECHA_CORTA = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]((?:19|20)\d{2})\b")
_PATRON_ADMINISTRADO = re.compile(
    r"\b(?:SE[ÑN]OR(?:A)?|DO[ÑN]A|DON|SOLICITANTE|ADMINISTRADO(?:/A)?|NOMBRES?)\b"
    r"\s*[:\-]\s*([^\n]{3,120})"
    r"|\bA\s+PETICI[OÓ]N\s+DE\b\s*[:\-]?\s*([^\n]{3,120})",
    re.IGNORECASE,
)
# Quita la etiqueta repetida: "— Doña: CANAZA CAMARGO, Jessica" → "CANAZA CAMARGO, Jessica".
_ETIQUETA_ADMINISTRADO = re.compile(
    r"^(?:SE[ÑN]OR(?:A)?|DO[ÑN]A|DON|SOLICITANTE|ADMINISTRADO(?:/A)?|NOMBRES?)\s*[:\-]\s*",
    re.IGNORECASE,
)

# "Yo, Ana Pérez, identificada con DNI …" es la fórmula más común en oficios.
_PATRON_YO_IDENTIFICADO = re.compile(
    r"\bYO,?\s+([A-ZÁÉÍÓÚÑ][^\n,;:]{2,80}?)\s*,?\s+"
    r"(?:identificad[oa]|con\s+DNI|con\s+documento)",
    re.IGNORECASE,
)

# Cortes para valores que ocupan una línea y siguen con otra etiqueta.
_CORTES = re.compile(
    r"\b(?:REFERENCIA|ASUNTO|DE MI CONSIDERACI[OÓ]N|SE[ÑN]OR(?:A)?|"
    r"ATENTAMENTE|SOLICITO|EXPEDIENTE)\b",
    re.IGNORECASE,
)


def normalizar_texto(texto: str) -> str:
    """Limpia espacios y saltos sin destruir la estructura de líneas."""
    texto = (texto or "").replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def texto_plano(texto: str) -> str:
    """Versión de una sola línea para buscar patrones que cruzan renglones."""
    return re.sub(r"\s+", " ", normalizar_texto(texto)).strip()


def _limpiar_valor(valor: str) -> str:
    valor = re.sub(r"\s+", " ", valor).strip()
    valor = valor.strip(" .,;:-\t")
    return valor


def _recortar(valor: str) -> str:
    corte = _CORTES.search(valor)
    if corte and corte.start() > 0:
        valor = valor[: corte.start()]
    return _limpiar_valor(valor)


def _anio_de_fecha(plano: str) -> str | None:
    """Año de la línea de fecha, que es más fiable que el encabezado."""
    for patron in (_PATRON_FECHA, _PATRON_FECHA_CORTA):
        if coincidencia := patron.search(plano):
            return coincidencia.group(1)
    return None


def _anio_plausible(plano: str) -> str | None:
    """Último recurso: el año reciente más alto (evita el '1926' del aniversario)."""
    limite = date.today().year + 1
    anios = [int(valor) for valor in _PATRON_ANIO.findall(plano)]
    candidatos = [anio for anio in anios if 2000 <= anio <= limite]
    return str(max(candidatos)) if candidatos else None


def _limpiar_administrado(valor: str) -> str:
    valor = _recortar(valor).strip(" .,;:-\t—–")
    valor = _ETIQUETA_ADMINISTRADO.sub("", valor)
    return valor.strip(" .,;:-\t—–")


def detectar_tipo_tramite(texto: str) -> dict[str, Any] | None:
    """Sugiere un tipo de trámite por palabras clave y explica por qué."""
    plano = texto_plano(texto).lower()
    if not plano:
        return None

    puntajes: dict[str, float] = {}
    evidencias: dict[str, str] = {}
    for codigo, patron, peso in REGLAS_TIPO:
        coincidencia = re.search(patron, plano, re.IGNORECASE)
        if coincidencia is None:
            continue
        puntajes[codigo] = puntajes.get(codigo, 0.0) + peso
        evidencias.setdefault(codigo, coincidencia.group(0))

    if not puntajes:
        return None

    codigo = max(puntajes, key=lambda clave: (puntajes[clave], -list(puntajes).index(clave)))
    confianza = min(0.95, 0.55 + 0.1 * puntajes[codigo])
    return {
        "codigo": codigo,
        "confianza": round(confianza, 2),
        "evidencia": evidencias[codigo],
    }


def extraer_campos(
    texto: str, nombre_tipo: str | None = None
) -> dict[str, dict[str, Any]]:
    """Extrae datos útiles para prellenar el registro, siempre como sugerencia."""
    normalizado = normalizar_texto(texto)
    plano = texto_plano(normalizado)
    campos: dict[str, dict[str, Any]] = {}

    if coincidencia := _PATRON_DNI.search(plano):
        valor = coincidencia.group(1) or coincidencia.group(2)
        if valor and valor != "00000000":
            campos["dni"] = {
                "valor": valor,
                "confianza": 0.9,
                "evidencia": _limpiar_valor(coincidencia.group(0)),
            }

    if coincidencia := _PATRON_RUC.search(plano):
        campos["ruc"] = {
            "valor": coincidencia.group(1),
            "confianza": 0.9,
            "evidencia": _limpiar_valor(coincidencia.group(0)),
        }

    if coincidencia := _PATRON_EXPEDIENTE.search(plano):
        campos["expediente"] = {
            "valor": coincidencia.group(1).upper().rstrip("-/"),
            "confianza": 0.75,
            "evidencia": _limpiar_valor(coincidencia.group(0)),
        }

    numero = None
    # El OCR a veces separa el número con espacios alrededor del guion.
    plano_numero = re.sub(r"(?<=[A-Z0-9])\s*-\s*(?=[A-Z0-9])", "-", plano)
    for coincidencia in _PATRON_NUMERO.finditer(plano_numero):
        candidato = _recortar(coincidencia.group(1))
        if not re.search(r"\d", candidato):
            continue  # "CERTIFICADO NO ACREDITA…" no es un número.
        if candidato.upper().strip(".") in _NO_ES_NUMERO:
            continue
        numero = candidato
        campos["numero_certificado"] = {
            "valor": numero,
            "confianza": 0.8,
            "evidencia": _limpiar_valor(coincidencia.group(0))[:150],
        }
        break

    anio = None
    confianza_anio = 0.45
    if numero and (coincidencia_anio := _PATRON_ANIO.search(numero)):
        # Caso normal: DOC-2026 / 629-2026.
        anio = coincidencia_anio.group(1)
        confianza_anio = 0.85
    elif (anio_fecha := _anio_de_fecha(plano)) is not None:
        anio = anio_fecha
        confianza_anio = 0.7
    elif (anio_plausible := _anio_plausible(plano)) is not None:
        anio = anio_plausible
        confianza_anio = 0.45
    if anio:
        campos["anio"] = {
            "valor": anio,
            "confianza": confianza_anio,
            "evidencia": f"Año detectado: {anio}",
        }

    if coincidencia := _PATRON_ASUNTO.search(normalizado):
        valor = _recortar(coincidencia.group(1))
        if len(valor) >= 3:
            campos["asunto"] = {
                "valor": valor[:300],
                "confianza": 0.7,
                "evidencia": "Línea ASUNTO",
            }

    if coincidencia := _PATRON_REFERENCIA.search(normalizado):
        valor = _recortar(coincidencia.group(1))
        if len(valor) >= 3:
            campos["referencia"] = {
                "valor": valor[:200],
                "confianza": 0.65,
                "evidencia": "Línea REFERENCIA",
            }

    if coincidencia := _PATRON_ADMINISTRADO.search(normalizado):
        valor = _limpiar_administrado(
            coincidencia.group(1) or coincidencia.group(2) or ""
        )
        # Evita capturar etiquetas sueltas o líneas vacías.
        if len(valor) >= 5 and not _CORTES.fullmatch(valor):
            campos["administrado"] = {
                "valor": valor[:200],
                "confianza": 0.65,
                "evidencia": _limpiar_valor(coincidencia.group(0))[:150],
            }
    elif coincidencia := _PATRON_YO_IDENTIFICADO.search(normalizado):
        valor = _recortar(coincidencia.group(1))
        if len(valor) >= 5:
            campos["administrado"] = {
                "valor": valor[:200],
                "confianza": 0.6,
                "evidencia": _limpiar_valor(coincidencia.group(0))[:150],
            }

    # Muchas constancias no traen la palabra ASUNTO. En ese caso el nombre del
    # trámite detectado es un asunto razonable (siempre editable por ventanilla).
    if "asunto" not in campos and nombre_tipo:
        campos["asunto"] = {
            "valor": nombre_tipo[:300],
            "confianza": 0.5,
            "evidencia": "Nombre del tipo de trámite detectado",
        }

    return campos


def construir_sugerencias(
    texto: str,
    clase_ia: dict[str, Any] | None = None,
    tipos: dict[str, str] | None = None,
    nombre_tipo: str | None = None,
) -> dict[str, Any]:
    """Devuelve un diccionario serializable para guardar en `documento_analisis`.

    `tipos` es el mapa `{codigo: nombre}` de los trámites; se usa solo para el
    respaldo del asunto cuando el documento no trae una línea ASUNTO.
    """
    normalizado = normalizar_texto(texto)
    tipo = detectar_tipo_tramite(normalizado)
    codigo = (tipo or {}).get("codigo", "")
    respaldo = nombre_tipo or (tipos or {}).get(codigo)
    return {
        "tipo": tipo,
        "campos": extraer_campos(normalizado, nombre_tipo=respaldo),
        "clase_ia": clase_ia,
        "reglas": VERSION_REGLAS,
        "caracteres": len(normalizado),
    }
