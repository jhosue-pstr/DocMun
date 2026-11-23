"""Catálogo base: las 16 áreas del Excel son las 16 oficinas municipales.

Fuente: `docs/fuentes/Consolidado_certificados_municipales_todos_los_ZIP.xlsx`
(carpetas de origen). Los nombres se normalizaron a UTF-8 y los SLA son valores
iniciales editables desde administración.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AreaMunicipal:
    codigo: str
    nombre: str
    siglas: str
    sla_horas: int


AREAS: tuple[AreaMunicipal, ...] = (
    AreaMunicipal("OF-01", "Certificado de Numeración Municipal", "NUM", 48),
    AreaMunicipal(
        "OF-02", "Certificado de Parámetros Urbanísticos y Edificatorios", "PAR", 120
    ),
    AreaMunicipal(
        "OF-03",
        "Visado de Planos de Ubicación, Perimétrico y Memoria Descriptiva de Predio Urbano",
        "VIS",
        120,
    ),
    AreaMunicipal("OF-04", "Certificado Negativo de Catastro", "CAT", 48),
    AreaMunicipal("OF-05", "Constancia de Posesión para Servicios Básicos", "POS", 72),
    AreaMunicipal("OF-06", "Certificado de Zonificación y Vías", "ZON", 72),
    AreaMunicipal("OF-07", "Licencias de Edificación", "LIC", 240),
    AreaMunicipal("OF-08", "Autorización para Apertura de Zanja en Predios Urbanos", "ZAN", 48),
    AreaMunicipal("OF-09", "Certificado de Compatibilidad de Uso", "COM", 72),
    AreaMunicipal(
        "OF-10",
        "Autorización para Ocupación de Vía Pública con Materiales de Construcción",
        "VIA",
        48,
    ),
    AreaMunicipal("OF-11", "Certificado de Habitabilidad", "HAB", 120),
    AreaMunicipal("OF-12", "Certificado de Jurisdicción", "JUR", 72),
    AreaMunicipal("OF-13", "Autorización para Telecomunicaciones", "TEL", 72),
    AreaMunicipal("OF-14", "Constancia de Alineamiento", "ALI", 48),
    AreaMunicipal(
        "OF-15",
        "Certificado de Conformidad de Obra y Declaratoria de Edificación",
        "CON",
        120,
    ),
    AreaMunicipal("OF-16", "Constancia de Predio Rústico", "RUS", 72),
)

FERIADOS_2026: tuple[tuple[str, str], ...] = (
    ("2026-01-01", "Año Nuevo"),
    ("2026-04-02", "Jueves Santo"),
    ("2026-04-03", "Viernes Santo"),
    ("2026-05-01", "Día del Trabajo"),
    ("2026-06-29", "San Pedro y San Pablo"),
    ("2026-07-23", "Día de la Fuerza Aérea del Perú"),
    ("2026-07-28", "Fiestas Patrias"),
    ("2026-07-29", "Fiestas Patrias"),
    ("2026-08-06", "Batalla de Junín"),
    ("2026-08-30", "Santa Rosa de Lima"),
    ("2026-10-08", "Combate de Angamos"),
    ("2026-11-01", "Todos los Santos"),
    ("2026-12-08", "Inmaculada Concepción"),
    ("2026-12-09", "Batalla de Ayacucho"),
    ("2026-12-25", "Navidad"),
)
