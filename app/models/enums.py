"""Enumeraciones del dominio."""

import enum


class Rol(str, enum.Enum):
    ADMIN = "admin"
    VENTANILLA = "ventanilla"
    OFICINA = "oficina"
    DIRECCION = "direccion"


class EstadoDocumento(str, enum.Enum):
    REGISTRADO = "registrado"
    DERIVADO = "derivado"
    EN_OFICINA = "en_oficina"
    OBSERVADO = "observado"
    FINALIZADO = "finalizado"
    ARCHIVADO = "archivado"
    ANULADO = "anulado"


class Semaforo(str, enum.Enum):
    EN_PLAZO = "en_plazo"
    POR_VENCER = "por_vencer"
    ATRASADO = "atrasado"
    MUY_ATRASADO = "muy_atrasado"


class Prioridad(str, enum.Enum):
    BAJA = "baja"
    NORMAL = "normal"
    ALTA = "alta"
    URGENTE = "urgente"


class OrigenDocumento(str, enum.Enum):
    MANUAL = "manual"
    HISTORICO = "historico"
    IMPORTADO = "importado"


class TipoDocumentoIdentidad(str, enum.Enum):
    DNI = "dni"
    RUC = "ruc"
    CE = "ce"
    PASAPORTE = "pasaporte"


class CanalNotificacion(str, enum.Enum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class EstadoNotificacion(str, enum.Enum):
    PENDIENTE = "pendiente"
    ENVIADO = "enviado"
    FALLIDO = "fallido"


class TipoEvento(str, enum.Enum):
    CREADO = "creado"
    DERIVADO = "derivado"
    RECIBIDO = "recibido"
    OBSERVADO = "observado"
    FINALIZADO = "finalizado"
    ANULADO = "anulado"
    NOTIFICADO = "notificado"
