"""Cálculo de plazos en horas hábiles.

El plazo corre desde la recepción confirmada por la oficina. El horario y los
feriados viven en `configuracion` y `feriados`; este módulo no guarda estado.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.catalogos import Configuracion, Feriado
from app.models.documentos import Movimiento
from app.models.enums import Semaforo

HORARIO_POR_DEFECTO: dict[str, Any] = {
    "lunes_viernes": [["08:00", "13:00"], ["14:00", "17:00"]],
    "sabado": None,
    "domingo": None,
}
DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")

Rango = tuple[time, time]


@dataclass(frozen=True)
class Umbrales:
    """Fracciones del plazo que marcan cada nivel del semáforo."""

    por_vencer: float = settings.sla_por_vencer
    atrasado: float = settings.sla_atrasado
    muy_atrasado: float = settings.sla_muy_atrasado


@dataclass(frozen=True)
class ContextoSla:
    """Horario, feriados y umbrales cargados una vez por request o job."""

    horario: dict[str, Any]
    feriados: set[date]
    umbrales: Umbrales
    zona: ZoneInfo


@dataclass(frozen=True)
class EstadoPlazo:
    """Resultado calculado del semáforo para un tramo abierto."""

    semaforo: Semaforo
    fraccion: float
    horas_consumidas: float
    horas_restantes: float
    horas_atraso: float
    fecha_limite: datetime | None


class HorarioInvalido(ValueError):
    """El horario configurado no se puede interpretar."""


def _parse_rango(rango: Any) -> Rango:
    if not isinstance(rango, list | tuple) or len(rango) != 2:
        raise HorarioInvalido("Cada tramo debe ser [apertura, cierre].")
    try:
        apertura = time.fromisoformat(str(rango[0]))
        cierre = time.fromisoformat(str(rango[1]))
    except ValueError as exc:
        raise HorarioInvalido("Las horas del horario laboral no son válidas.") from exc
    if cierre <= apertura:
        raise HorarioInvalido("El cierre debe ser posterior a la apertura.")
    return apertura, cierre


def _parse_rangos(valor: Any) -> tuple[Rango, ...]:
    """Acepta un tramo `["08:00", "13:00"]` o varios tramos anidados."""
    if valor is None:
        return ()
    if not isinstance(valor, list | tuple):
        raise HorarioInvalido("El horario debe ser [apertura, cierre] o una lista de tramos.")
    if not valor:
        return ()
    if len(valor) == 2 and all(isinstance(parte, str) for parte in valor):
        return (_parse_rango(valor),)
    if all(isinstance(tramo, list | tuple) for tramo in valor):
        rangos = tuple(sorted(_parse_rango(tramo) for tramo in valor))
        for anterior, siguiente in zip(rangos, rangos[1:], strict=False):
            if siguiente[0] < anterior[1]:
                raise HorarioInvalido("Los tramos del horario no pueden superponerse.")
        return rangos
    raise HorarioInvalido("El horario debe ser [apertura, cierre] o una lista de tramos.")


def normalizar_horario(datos: Any) -> dict[str, Any]:
    """Valida el JSON de configuración y completa las claves faltantes."""
    if not isinstance(datos, dict):
        raise HorarioInvalido("El horario laboral debe ser un objeto JSON.")

    normalizado: dict[str, Any] = {}
    for clave, valor in HORARIO_POR_DEFECTO.items():
        rangos = datos.get(clave, valor)
        _parse_rangos(rangos)
        normalizado[clave] = rangos

    for dia in DIAS:
        if dia in datos:
            _parse_rangos(datos[dia])
            normalizado[dia] = datos[dia]
    return normalizado


def cargar_horario(db: Session) -> dict[str, Any]:
    """Lee `horario_laboral`; si falta o está mal, usa el horario por defecto."""
    registro = db.get(Configuracion, "horario_laboral")
    if registro is None:
        return normalizar_horario(HORARIO_POR_DEFECTO)
    try:
        return normalizar_horario(json.loads(registro.valor))
    except (json.JSONDecodeError, HorarioInvalido):
        return normalizar_horario(HORARIO_POR_DEFECTO)


def cargar_feriados(db: Session) -> set[date]:
    return set(db.scalars(select(Feriado.fecha)).all())


def _rangos_del_dia(dia: date, horario: dict[str, Any]) -> tuple[Rango, ...]:
    nombre = DIAS[dia.weekday()]
    if nombre in horario:
        return _parse_rangos(horario[nombre])
    if dia.weekday() <= 4:
        return _parse_rangos(horario.get("lunes_viernes"))
    if dia.weekday() == 5:
        return _parse_rangos(horario.get("sabado"))
    return _parse_rangos(horario.get("domingo"))


def calcular_fecha_limite(
    inicio: datetime,
    horas: int,
    *,
    horario: dict[str, Any] | None = None,
    feriados: set[date] | None = None,
    zona: ZoneInfo | None = None,
) -> datetime:
    """Suma `horas` hábiles a `inicio` y devuelve el resultado en UTC.

    - Si `inicio` cae fuera del horario, el conteo arranca en la próxima apertura.
    - Los feriados y los días cerrados no consumen plazo.
    - No soporta horarios que cruzan la medianoche.
    """
    if horas <= 0:
        raise ValueError("Las horas del plazo deben ser mayores que cero.")
    if inicio.tzinfo is None:
        raise ValueError("La fecha de inicio debe incluir zona horaria.")

    horario = normalizar_horario(horario or HORARIO_POR_DEFECTO)
    feriados = feriados or set()
    zona = zona or ZoneInfo(settings.timezone)

    restante = float(horas)
    actual = inicio.astimezone(zona)
    for _ in range(366 * 3):
        dia = actual.date()
        rangos = () if dia in feriados else _rangos_del_dia(dia, horario)
        for apertura, cierre in rangos:
            apertura_dt = datetime.combine(dia, apertura, tzinfo=zona)
            cierre_dt = datetime.combine(dia, cierre, tzinfo=zona)
            if actual >= cierre_dt:
                continue
            if actual < apertura_dt:
                actual = apertura_dt

            disponible = (cierre_dt - actual).total_seconds() / 3600
            if restante <= disponible:
                return (actual + timedelta(hours=restante)).astimezone(UTC)

            restante -= disponible
            actual = cierre_dt

        actual = datetime.combine(dia + timedelta(days=1), time.min, tzinfo=zona)

    raise HorarioInvalido("No se pudo calcular el plazo: revisa el horario laboral.")


def _umbral(db: Session, clave: str, por_defecto: float) -> float:
    registro = db.get(Configuracion, clave)
    if registro is None:
        return por_defecto
    try:
        valor = float(registro.valor)
    except (TypeError, ValueError):
        return por_defecto
    if not math.isfinite(valor) or valor <= 0:
        return por_defecto
    return valor


def cargar_umbrales(db: Session) -> Umbrales:
    """Lee los umbrales de `configuracion` y cae a los valores de `Settings`.

    Si los tres valores no forman una secuencia creciente válida, se usan los
    valores por defecto para no producir un semáforo incoherente.
    """
    por_vencer = _umbral(db, "umbral_por_vencer", settings.sla_por_vencer)
    atrasado = _umbral(db, "umbral_atrasado", settings.sla_atrasado)
    muy_atrasado = _umbral(db, "umbral_muy_atrasado", settings.sla_muy_atrasado)
    if 0 < por_vencer < atrasado < muy_atrasado:
        return Umbrales(por_vencer, atrasado, muy_atrasado)
    return Umbrales(
        settings.sla_por_vencer,
        settings.sla_atrasado,
        settings.sla_muy_atrasado,
    )


def cargar_contexto_sla(db: Session) -> ContextoSla:
    return ContextoSla(
        horario=cargar_horario(db),
        feriados=cargar_feriados(db),
        umbrales=cargar_umbrales(db),
        zona=ZoneInfo(settings.timezone),
    )


def horas_habiles_entre(
    inicio: datetime,
    fin: datetime,
    *,
    horario: dict[str, Any] | None = None,
    feriados: set[date] | None = None,
    zona: ZoneInfo | None = None,
) -> float:
    """Horas hábiles transcurridas entre dos instantes.

    Devuelve `0.0` si `fin` es anterior o igual a `inicio`. No cuenta noches,
    fines de semana, feriados ni días cerrados.
    """
    if inicio.tzinfo is None or fin.tzinfo is None:
        raise ValueError("Las fechas deben incluir zona horaria.")
    if fin <= inicio:
        return 0.0

    horario = normalizar_horario(horario or HORARIO_POR_DEFECTO)
    feriados = feriados or set()
    zona = zona or ZoneInfo(settings.timezone)

    actual = inicio.astimezone(zona)
    limite = fin.astimezone(zona)
    total = 0.0

    while actual.date() <= limite.date():
        dia = actual.date()
        rangos = () if dia in feriados else _rangos_del_dia(dia, horario)
        for apertura, cierre in rangos:
            apertura_dt = datetime.combine(dia, apertura, tzinfo=zona)
            cierre_dt = datetime.combine(dia, cierre, tzinfo=zona)
            desde = max(actual, apertura_dt)
            hasta = min(limite, cierre_dt)
            if hasta > desde:
                total += (hasta - desde).total_seconds() / 3600

        actual = datetime.combine(dia + timedelta(days=1), time.min, tzinfo=zona)

    return total


def _con_zona(valor: datetime) -> datetime:
    """SQLite devuelve datetimes sin zona; se asume UTC como en el resto del sistema."""
    if valor.tzinfo is None:
        return valor.replace(tzinfo=UTC)
    return valor


def evaluar_movimiento(
    movimiento: Movimiento,
    contexto: ContextoSla | None = None,
    *,
    ahora: datetime | None = None,
) -> EstadoPlazo | None:
    """Calcula el semáforo de un tramo abierto.

    Devuelve `None` si el tramo todavía no fue recibido, ya cerró o no tiene un
    plazo válido. El semáforo nunca se guarda: se deriva en cada consulta.
    """
    if (
        movimiento.fecha_recepcion is None
        or movimiento.fecha_salida is not None
        or not movimiento.sla_horas
        or movimiento.sla_horas <= 0
    ):
        return None

    contexto = contexto or ContextoSla(
        horario=normalizar_horario(HORARIO_POR_DEFECTO),
        feriados=set(),
        umbrales=Umbrales(),
        zona=ZoneInfo(settings.timezone),
    )
    ahora = _con_zona(ahora or datetime.now(UTC))
    recepcion = _con_zona(movimiento.fecha_recepcion)

    consumidas = horas_habiles_entre(
        recepcion,
        ahora,
        horario=contexto.horario,
        feriados=contexto.feriados,
        zona=contexto.zona,
    )
    fraccion = consumidas / movimiento.sla_horas
    umbrales = contexto.umbrales

    if fraccion >= umbrales.muy_atrasado:
        semaforo = Semaforo.MUY_ATRASADO
    elif fraccion >= umbrales.atrasado:
        semaforo = Semaforo.ATRASADO
    elif fraccion >= umbrales.por_vencer:
        semaforo = Semaforo.POR_VENCER
    else:
        semaforo = Semaforo.EN_PLAZO

    return EstadoPlazo(
        semaforo=semaforo,
        fraccion=fraccion,
        horas_consumidas=consumidas,
        horas_restantes=max(0.0, movimiento.sla_horas - consumidas),
        horas_atraso=max(0.0, consumidas - movimiento.sla_horas),
        fecha_limite=movimiento.fecha_limite,
    )


def movimientos_abiertos(
    db: Session,
    *,
    documento_ids: list[int] | None = None,
    limite: int | None = None,
) -> list[Movimiento]:
    """Tramos recibidos y todavía sin salida."""
    if documento_ids is not None and not documento_ids:
        return []

    stmt = select(Movimiento).where(
        Movimiento.fecha_recepcion.is_not(None),
        Movimiento.fecha_salida.is_(None),
    )
    if documento_ids is not None:
        stmt = stmt.where(Movimiento.documento_id.in_(documento_ids))
    stmt = stmt.order_by(Movimiento.fecha_limite, Movimiento.id)
    if limite is not None:
        stmt = stmt.limit(limite)
    return list(db.scalars(stmt).all())


def estado_por_documento(
    db: Session,
    documentos,
    *,
    ahora: datetime | None = None,
    contexto: ContextoSla | None = None,
) -> dict[int, EstadoPlazo]:
    """Semáforo del tramo abierto de cada documento, indexado por `documento.id`."""
    ids = [documento.id for documento in documentos if documento.id is not None]
    movimientos = movimientos_abiertos(db, documento_ids=ids)
    if not movimientos:
        return {}

    contexto = contexto or cargar_contexto_sla(db)
    ahora = _con_zona(ahora or datetime.now(UTC))
    resultado: dict[int, EstadoPlazo] = {}
    for movimiento in movimientos:
        estado = evaluar_movimiento(movimiento, contexto, ahora=ahora)
        if estado is not None:
            resultado[movimiento.documento_id] = estado
    return resultado


def evaluar_plazos(
    db: Session,
    *,
    ahora: datetime | None = None,
    contexto: ContextoSla | None = None,
    limite: int | None = None,
) -> list[tuple[Movimiento, EstadoPlazo]]:
    """Evalúa todos los tramos abiertos; base del job de alertas."""
    contexto = contexto or cargar_contexto_sla(db)
    ahora = _con_zona(ahora or datetime.now(UTC))
    resultado: list[tuple[Movimiento, EstadoPlazo]] = []
    for movimiento in movimientos_abiertos(db, limite=limite):
        estado = evaluar_movimiento(movimiento, contexto, ahora=ahora)
        if estado is not None:
            resultado.append((movimiento, estado))
    return resultado


def calcular_fecha_limite_db(db: Session, inicio: datetime, horas: int) -> datetime:
    """Atajo que toma horario y feriados de la base de datos."""
    return calcular_fecha_limite(
        inicio,
        horas,
        horario=cargar_horario(db),
        feriados=cargar_feriados(db),
    )
