"""Pruebas de las reglas de extracción (sin torch ni base de datos)."""

from app.services.ia.extraccion import (
    construir_sugerencias,
    detectar_tipo_tramite,
    extraer_campos,
    normalizar_texto,
    texto_plano,
)

CARTA = """
MUNICIPALIDAD DISTRITAL DE PRUEBA
CARTA N° 045-2026-MDP
ASUNTO: Solicitud de certificado de numeración municipal
REFERENCIA: Expediente 1234-2026
SEÑOR ALCALDE:
Yo, Ana Pérez, identificada con DNI: 12345678, solicito la
numeración municipal del predio ubicado en Jr. Los Álamos 123.
Atentamente,
"""

# OCR real de un Certificado de Numeración Municipal: el OCR lee el separador
# como "|" y el encabezado trae el aniversario "1926 - 2026".
CERTIFICADO_NUMERACION = """
MUNICIPALIDAD DE SAN ROMÁN
JULIACA
1926 - 2026
CERTIFICADO | N°629-2026
Exp. Adm. N°
Bp, Adm NUMERACION MUNICIPAL sGDU
Fecha: Código de Encuesta: Recibo:
01-09-2026 056207 R008-00053220
En conformidad a los documentos y planos presentados. A petición de:
— Doña: CANAZA CAMARGO, Jessica.
ESTE CERTIFICADO NO ACREDITA DERECHO
DE PROPIEDAD Y/O TITULARIDAD.
"""


def test_normalizar_texto_respeta_lineas():
    assert normalizar_texto("  hola   mundo\r\n\r\n\r\nfin  ") == "hola mundo\n\nfin"
    assert texto_plano("hola\nmundo") == "hola mundo"


def test_detecta_tipo_por_palabras_clave():
    resultado = detectar_tipo_tramite(CARTA)
    assert resultado is not None
    assert resultado["codigo"] == "TR-01"
    assert 0.5 <= resultado["confianza"] <= 0.95
    assert "numeraci" in resultado["evidencia"].lower()


def test_detecta_tipo_parametrico():
    resultado = detectar_tipo_tramite("Solicito parámetros urbanísticos del lote 12")
    assert resultado is not None
    assert resultado["codigo"] == "TR-02"


def test_sin_palabras_clave_no_sugiere_tipo():
    assert detectar_tipo_tramite("Hola, buenos días") is None
    assert detectar_tipo_tramite("") is None


def test_extrae_dni_ruc_expediente_y_numero():
    campos = extraer_campos(CARTA)

    assert campos["dni"]["valor"] == "12345678"
    assert campos["expediente"]["valor"] == "1234-2026"
    assert campos["numero_certificado"]["valor"].startswith("045-2026")
    assert campos["anio"]["valor"] == "2026"

    con_ruc = extraer_campos("RUC: 20123456789")
    assert con_ruc["ruc"]["valor"] == "20123456789"


def test_extrae_asunto_referencia_y_administrado():
    campos = extraer_campos(CARTA)
    assert "numeración municipal" in campos["asunto"]["valor"]
    assert "1234-2026" in campos["referencia"]["valor"]
    assert "Ana Pérez" in campos["administrado"]["valor"]
    # La evidencia no debe arrastrar el resto del oficio.
    assert "Atentamente" not in campos["administrado"]["valor"]


def test_dni_invalido_no_se_sugiere():
    assert "dni" not in extraer_campos("DNI: 00000000")


def test_construir_sugerencias_es_serializable():
    sugerencias = construir_sugerencias(CARTA, clase_ia={"clase": "letter", "probabilidad": 0.9})
    assert sugerencias["tipo"]["codigo"] == "TR-01"
    assert sugerencias["clase_ia"] == {"clase": "letter", "probabilidad": 0.9}
    assert sugerencias["caracteres"] > 0
    assert sugerencias["campos"]["dni"]["valor"] == "12345678"
    assert sugerencias["reglas"]


def test_ocr_con_barra_en_el_numero_no_captura_palabras():
    campos = extraer_campos(CERTIFICADO_NUMERACION)

    assert campos["numero_certificado"]["valor"] == "629-2026"
    assert campos["anio"]["valor"] == "2026"


def test_no_confunde_certificado_con_la_frase_no_acredita():
    campos = extraer_campos("ESTE CERTIFICADO NO ACREDITA DERECHO DE PROPIEDAD.")

    assert "numero_certificado" not in campos


def test_administrado_con_dona_y_a_peticion_de():
    campos = extraer_campos(CERTIFICADO_NUMERACION)

    assert campos["administrado"]["valor"] == "CANAZA CAMARGO, Jessica"


def test_anio_usa_la_fecha_si_no_hay_numero():
    campos = extraer_campos("CONSTANCIA DE POSESIÓN\nFecha: 15/03/2025\n1926 - 2026")

    assert campos["anio"]["valor"] == "2025"


def test_asunto_de_respaldo_con_el_nombre_del_tipo():
    sugerencias = construir_sugerencias(
        CERTIFICADO_NUMERACION,
        tipos={"TR-01": "Certificado de Numeración Municipal"},
    )

    assert sugerencias["tipo"]["codigo"] == "TR-01"
    assert (
        sugerencias["campos"]["asunto"]["valor"]
        == "Certificado de Numeración Municipal"
    )
