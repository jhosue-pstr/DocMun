"""Modelos de DocMun.

Importar este módulo garantiza que todas las clases estén registradas en el
metadata de SQLAlchemy antes de crear tablas o ejecutar migraciones.
"""

from app.models.analisis import DocumentoAnalisis
from app.models.archivos import DocumentoArchivo
from app.models.catalogos import Configuracion, Feriado, Oficina, Plazo, TipoTramite
from app.models.ciudadanos import Ciudadano
from app.models.documentos import Documento, Evento, Movimiento
from app.models.notificaciones import Notificacion
from app.models.usuarios import Usuario

__all__ = [
    "Configuracion",
    "Feriado",
    "Oficina",
    "Plazo",
    "TipoTramite",
    "Ciudadano",
    "Documento",
    "DocumentoAnalisis",
    "DocumentoArchivo",
    "Evento",
    "Movimiento",
    "Notificacion",
    "Usuario",
]
