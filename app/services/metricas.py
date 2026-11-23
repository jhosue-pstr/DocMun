"""Métricas del semáforo y del desempeño por oficina.

Todas las cifras se calculan desde los movimientos y eventos; el semáforo nunca
se guarda. Las horas son hábiles y usan el horario y los feriados configurados.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.catalogos import Oficina
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import EstadoDocumento, OrigenDocumento, Semaforo, TipoEvento
from app.services.sla import (
    ContextoSla,
    cargar_contexto_sla,
    evaluar_movimiento,
    horas_habiles_entre,
)

PERIODOS: dict[str, tuple[int | None, int, str]] = {
    "7": (7, 4, "Últimos 7 días"),
    "30": (30, 8, "Últimos 30 días"),
    "90": (90, 13, "Últimos 90 días"),
    "180": (180, 26, "Últimos 180 días"),
    "todo": (None, 26, "Todo el histórico"),
}

ESTADOS_ETIQUETA = {
    EstadoDocumento.REGISTRADO: "Registrado",
    EstadoDocumento.DERIVADO: "Derivado",
    EstadoDocumento.EN_OFICINA: "En oficina",
    EstadoDocumento.OBSERVADO: "Observado",
    EstadoDocumento.FINALIZADO: "Finalizado",
    EstadoDocumento.ARCHIVADO: "Archivado",
    EstadoDocumento.ANULADO: "Anulado",
}


@dataclass(frozen=True)
class Periodo:
    clave: str
    desde: datetime | None
    hasta: datetime
    semanas: int
    etiqueta: str


@dataclass(frozen=True)
class FiltrosMetricas:
    periodo: Periodo
    oficina_id: int | None = None
    tipo_tramite_id: int | None = None
    origen: OrigenDocumento | None = None


@dataclass
class MetricaOficina:
    id: int
    codigo: str
    nombre: str
    abiertos: int = 0
    por_recibir: int = 0
    en_plazo: int = 0
    por_vencer: int = 0
    atrasado: int = 0
    muy_atrasado: int = 0
    atraso_promedio: float = 0.0
    antiguedad_maxima: float = 0.0
    cerrados: int = 0
    mediana_horas: float = 0.0
    mediana_sla: float = 0.0
    cumplimiento: float = 0.0
    observaciones: int = 0
    finalizados: int = 0

    @property
    def total_con_sla(self) -> int:
        return self.en_plazo + self.por_vencer + self.atrasado + self.muy_atrasado

    @property
    def porcentaje_atraso(self) -> float:
        if self.total_con_sla == 0:
            return 0.0
        return (self.atrasado + self.muy_atrasado) / self.total_con_sla * 100


@dataclass
class ResumenMetricas:
    documentos_total: int = 0
    documentos_en_tramite: int = 0
    documentos_finalizados: int = 0
    documentos_archivados: int = 0
    abiertos: int = 0
    por_recibir: int = 0
    en_plazo: int = 0
    por_vencer: int = 0
    atrasado: int = 0
    muy_atrasado: int = 0
    cerrados: int = 0
    finalizados_periodo: int = 0
    observaciones: int = 0
    mediana_horas: float = 0.0
    mediana_sla: float = 0.0
    cumplimiento: float = 0.0
    antiguedad_maxima: float = 0.0

    @property
    def total_con_sla(self) -> int:
        return self.en_plazo + self.por_vencer + self.atrasado + self.muy_atrasado

    @property
    def porcentaje_atraso(self) -> float:
        if self.total_con_sla == 0:
            return 0.0
        return (self.atrasado + self.muy_atrasado) / self.total_con_sla * 100


@dataclass(frozen=True)
class DocumentoCritico:
    documento_id: int
    codigo: str
    tipo: str
    oficina: str
    oficina_codigo: str
    semaforo: Semaforo
    horas_atraso: float
    fecha_limite: datetime | None


@dataclass(frozen=True)
class TipoMetrica:
    nombre: str
    abiertos: int
    atrasados: int


@dataclass
class DashboardMetricas:
    resumen: ResumenMetricas
    oficinas: list[MetricaOficina]
    criticos: list[DocumentoCritico]
    tipos: list[TipoMetrica]
    estados: list[tuple[str, int]]
    charts: dict[str, Any]
    serie: list[dict[str, Any]] = field(default_factory=list)


def resolver_periodo(clave: str | None, ahora: datetime | None = None) -> Periodo:
    ahora = _con_zona(ahora or datetime.now(UTC))
    clave = clave if clave in PERIODOS else "30"
    dias, semanas, etiqueta = PERIODOS[clave]
    desde = ahora - timedelta(days=dias) if dias is not None else None
    return Periodo(clave=clave, desde=desde, hasta=ahora, semanas=semanas, etiqueta=etiqueta)


def _con_zona(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor


def _en_periodo(valor: datetime | None, periodo: Periodo) -> bool:
    if valor is None:
        return False
    valor = _con_zona(valor)
    if valor > periodo.hasta:
        return False
    return periodo.desde is None or valor >= periodo.desde


def _inicio_semana(valor: datetime) -> datetime:
    valor = _con_zona(valor)
    return (valor - timedelta(days=valor.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _filtrar_movimientos(db: Session, filtros: FiltrosMetricas) -> list[Movimiento]:
    stmt = (
        select(Movimiento)
        .join(Documento, Documento.id == Movimiento.documento_id)
        .options(
            selectinload(Movimiento.documento).selectinload(Documento.tipo_tramite),
            selectinload(Movimiento.oficina_destino),
        )
    )
    if filtros.oficina_id is not None:
        stmt = stmt.where(Movimiento.oficina_destino_id == filtros.oficina_id)
    if filtros.tipo_tramite_id is not None:
        stmt = stmt.where(Documento.tipo_tramite_id == filtros.tipo_tramite_id)
    if filtros.origen is not None:
        stmt = stmt.where(Documento.origen == filtros.origen)
    return list(db.scalars(stmt).all())


def _filtrar_documentos(db: Session, filtros: FiltrosMetricas) -> list[Documento]:
    stmt = select(Documento).options(selectinload(Documento.tipo_tramite))
    if filtros.oficina_id is not None:
        stmt = stmt.where(Documento.oficina_actual_id == filtros.oficina_id)
    if filtros.tipo_tramite_id is not None:
        stmt = stmt.where(Documento.tipo_tramite_id == filtros.tipo_tramite_id)
    if filtros.origen is not None:
        stmt = stmt.where(Documento.origen == filtros.origen)
    return list(db.scalars(stmt).all())


def _oficina_de_evento(
    evento: Evento,
    movimientos_por_documento: dict[int, list[Movimiento]],
) -> int | None:
    """Atribuye una observación al tramo abierto cuando ocurrió."""
    cuando = _con_zona(evento.creado_en)
    for movimiento in movimientos_por_documento.get(evento.documento_id, []):
        if movimiento.fecha_recepcion is None:
            continue
        if cuando < _con_zona(movimiento.fecha_recepcion):
            continue
        if movimiento.fecha_salida is not None and cuando > _con_zona(movimiento.fecha_salida):
            continue
        return movimiento.oficina_destino_id
    return None


def _eventos_por_tipo(
    db: Session,
    filtros: FiltrosMetricas,
    tipo: TipoEvento,
) -> tuple[Counter[int], Counter[int]]:
    """Cuenta eventos de un tipo por oficina y por semana.

    La oficina se atribuye al tramo abierto del documento cuando ocurrió el evento.
    """
    stmt = (
        select(Evento)
        .join(Documento, Documento.id == Evento.documento_id)
        .where(Evento.tipo == tipo)
    )
    if filtros.tipo_tramite_id is not None:
        stmt = stmt.where(Documento.tipo_tramite_id == filtros.tipo_tramite_id)
    if filtros.origen is not None:
        stmt = stmt.where(Documento.origen == filtros.origen)

    eventos = list(db.scalars(stmt).all())
    ids = {evento.documento_id for evento in eventos}
    if not ids:
        return Counter(), Counter()

    movimientos_todos = list(
        db.scalars(select(Movimiento).where(Movimiento.documento_id.in_(ids))).all()
    )
    por_documento: dict[int, list[Movimiento]] = defaultdict(list)
    for movimiento in movimientos_todos:
        por_documento[movimiento.documento_id].append(movimiento)

    por_oficina: Counter[int] = Counter()
    por_semana: Counter[int] = Counter()
    for evento in eventos:
        if not _en_periodo(evento.creado_en, filtros.periodo):
            continue
        oficina_id = _oficina_de_evento(evento, por_documento)
        if oficina_id is None:
            continue
        if filtros.oficina_id is not None and oficina_id != filtros.oficina_id:
            continue
        por_oficina[oficina_id] += 1
        por_semana[_inicio_semana(evento.creado_en).toordinal()] += 1
    return por_oficina, por_semana


def _cerrados(
    movimientos: list[Movimiento],
    contexto: ContextoSla,
    periodo: Periodo,
) -> list[tuple[Movimiento, float, bool]]:
    """Tramos cerrados dentro del periodo: (movimiento, horas, a_tiempo)."""
    resultado: list[tuple[Movimiento, float, bool]] = []
    for movimiento in movimientos:
        if movimiento.fecha_recepcion is None or movimiento.fecha_salida is None:
            continue
        if not _en_periodo(movimiento.fecha_salida, periodo):
            continue
        horas = horas_habiles_entre(
            _con_zona(movimiento.fecha_recepcion),
            _con_zona(movimiento.fecha_salida),
            horario=contexto.horario,
            feriados=contexto.feriados,
            zona=contexto.zona,
        )
        a_tiempo = (
            movimiento.fecha_limite is not None
            and _con_zona(movimiento.fecha_salida) <= _con_zona(movimiento.fecha_limite)
        )
        resultado.append((movimiento, horas, a_tiempo))
    return resultado


def _mediana(valores: list[float]) -> float:
    return round(median(valores), 1) if valores else 0.0


def calcular_dashboard(
    db: Session,
    filtros: FiltrosMetricas,
    *,
    ahora: datetime | None = None,
) -> DashboardMetricas:
    ahora = _con_zona(ahora or filtros.periodo.hasta)
    contexto = cargar_contexto_sla(db)
    oficinas = list(db.scalars(select(Oficina).order_by(Oficina.codigo)).all())
    if filtros.oficina_id is not None:
        oficinas = [oficina for oficina in oficinas if oficina.id == filtros.oficina_id]

    movimientos = _filtrar_movimientos(db, filtros)
    documentos = _filtrar_documentos(db, filtros)

    metricas: dict[int, MetricaOficina] = {
        oficina.id: MetricaOficina(
            id=oficina.id,
            codigo=oficina.codigo,
            nombre=oficina.nombre,
        )
        for oficina in oficinas
    }

    abiertos: list[tuple[Movimiento, Any]] = []
    cerrados_globales: list[tuple[Movimiento, float, bool]] = []
    atrasos: dict[int, list[float]] = defaultdict(list)
    antiguedades: dict[int, list[float]] = defaultdict(list)
    cerrados_por_oficina: dict[int, list[tuple[Movimiento, float, bool]]] = defaultdict(list)

    for movimiento in movimientos:
        oficina_id = movimiento.oficina_destino_id
        metrica = metricas.get(oficina_id)
        if metrica is None:
            continue

        if movimiento.fecha_recepcion is None:
            if movimiento.fecha_salida is None:
                metrica.por_recibir += 1
            continue

        if movimiento.fecha_salida is None:
            estado = evaluar_movimiento(movimiento, contexto, ahora=ahora)
            if estado is None:
                continue
            abiertos.append((movimiento, estado))
            metrica.abiertos += 1
            setattr(metrica, estado.semaforo.value, getattr(metrica, estado.semaforo.value) + 1)
            if estado.semaforo in {Semaforo.ATRASADO, Semaforo.MUY_ATRASADO}:
                atrasos[oficina_id].append(estado.horas_atraso)
            antiguedad = horas_habiles_entre(
                _con_zona(movimiento.fecha_recepcion),
                ahora,
                horario=contexto.horario,
                feriados=contexto.feriados,
                zona=contexto.zona,
            )
            antiguedades[oficina_id].append(antiguedad)

    for movimiento, horas, a_tiempo in _cerrados(movimientos, contexto, filtros.periodo):
        oficina_id = movimiento.oficina_destino_id
        metrica = metricas.get(oficina_id)
        if metrica is None:
            continue
        cerrados_globales.append((movimiento, horas, a_tiempo))
        cerrados_por_oficina[oficina_id].append((movimiento, horas, a_tiempo))

    for oficina_id, lista in cerrados_por_oficina.items():
        metrica = metricas[oficina_id]
        metrica.cerrados = len(lista)
        metrica.mediana_horas = _mediana([horas for _, horas, _ in lista])
        con_limite = [
            a_tiempo
            for movimiento, _, a_tiempo in lista
            if movimiento.fecha_limite is not None
        ]
        metrica.cumplimiento = (
            round(sum(1 for a_tiempo in con_limite if a_tiempo) / len(con_limite) * 100, 1)
            if con_limite
            else 0.0
        )
        fracciones = [
            horas / movimiento.sla_horas
            for movimiento, horas, _ in lista
            if movimiento.sla_horas
        ]
        metrica.mediana_sla = round(_mediana(fracciones), 1)
        metrica.atraso_promedio = (
            round(sum(atrasos[oficina_id]) / len(atrasos[oficina_id]), 1)
            if atrasos[oficina_id]
            else 0.0
        )
        metrica.antiguedad_maxima = (
            round(max(antiguedades[oficina_id]), 1) if antiguedades[oficina_id] else 0.0
        )

    # Las oficinas sin movimientos también necesitan sus promedios.
    for oficina_id, metrica in metricas.items():
        metrica.atraso_promedio = (
            round(sum(atrasos[oficina_id]) / len(atrasos[oficina_id]), 1)
            if atrasos[oficina_id]
            else 0.0
        )
        metrica.antiguedad_maxima = (
            round(max(antiguedades[oficina_id]), 1) if antiguedades[oficina_id] else 0.0
        )

    observaciones, observaciones_semana = _eventos_por_tipo(
        db, filtros, TipoEvento.OBSERVADO
    )
    for oficina_id, cantidad in observaciones.items():
        if oficina_id in metricas:
            metricas[oficina_id].observaciones = cantidad

    _, alertas_semana = _eventos_por_tipo(db, filtros, TipoEvento.ALERTA_PLAZO)

    finalizados_por_semana: Counter[int] = Counter()
    for documento in documentos:
        if documento.fecha_finalizacion is None:
            continue
        if not _en_periodo(documento.fecha_finalizacion, filtros.periodo):
            continue
        if documento.oficina_actual_id in metricas:
            metricas[documento.oficina_actual_id].finalizados += 1
        finalizados_por_semana[_inicio_semana(documento.fecha_finalizacion).toordinal()] += 1

    resumen = _resumen(
        documentos,
        metricas,
        abiertos,
        cerrados_globales,
        observaciones,
        finalizados_por_semana,
    )

    criticos = _criticos(abiertos)
    tipos = _tipos(movimientos, contexto, ahora)
    estados = _estados(documentos)
    serie = _serie(
        movimientos,
        filtros,
        ahora,
        observaciones_semana,
        finalizados_por_semana,
        alertas_semana,
    )
    charts = _charts(resumen, list(metricas.values()), tipos, estados, serie, criticos)

    return DashboardMetricas(
        resumen=resumen,
        oficinas=sorted(
            metricas.values(),
            key=lambda metrica: (
                metrica.atrasado + metrica.muy_atrasado,
                metrica.porcentaje_atraso,
                metrica.abiertos,
            ),
            reverse=True,
        ),
        criticos=criticos,
        tipos=tipos,
        estados=estados,
        charts=charts,
        serie=serie,
    )


def _tiene_limite(movimiento: Movimiento) -> bool:
    """El tramo tiene plazo calculado y por tanto cuenta para el cumplimiento."""
    return movimiento.fecha_limite is not None


def _resumen(
    documentos: list[Documento],
    metricas: dict[int, MetricaOficina],
    abiertos: list[tuple[Movimiento, Any]],
    cerrados: list[tuple[Movimiento, float, bool]],
    observaciones: Counter[int],
    finalizados_por_semana: Counter[int],
) -> ResumenMetricas:
    estados = Counter(documento.estado for documento in documentos)
    con_limite = [
        a_tiempo for movimiento, _, a_tiempo in cerrados if movimiento.fecha_limite is not None
    ]
    fracciones = [
        horas / movimiento.sla_horas
        for movimiento, horas, _ in cerrados
        if movimiento.sla_horas
    ]
    resumen = ResumenMetricas(
        documentos_total=len(documentos),
        documentos_en_tramite=sum(
            estados.get(estado, 0)
            for estado in (
                EstadoDocumento.REGISTRADO,
                EstadoDocumento.DERIVADO,
                EstadoDocumento.EN_OFICINA,
                EstadoDocumento.OBSERVADO,
            )
        ),
        documentos_finalizados=estados.get(EstadoDocumento.FINALIZADO, 0),
        documentos_archivados=estados.get(EstadoDocumento.ARCHIVADO, 0),
        abiertos=len(abiertos),
        por_recibir=sum(metrica.por_recibir for metrica in metricas.values()),
        en_plazo=sum(metrica.en_plazo for metrica in metricas.values()),
        por_vencer=sum(metrica.por_vencer for metrica in metricas.values()),
        atrasado=sum(metrica.atrasado for metrica in metricas.values()),
        muy_atrasado=sum(metrica.muy_atrasado for metrica in metricas.values()),
        cerrados=len(cerrados),
        finalizados_periodo=sum(finalizados_por_semana.values()),
        observaciones=sum(observaciones.values()),
        mediana_horas=_mediana([horas for _, horas, _ in cerrados]),
        mediana_sla=round(_mediana(fracciones), 1),
        cumplimiento=(
            round(sum(1 for a_tiempo in con_limite if a_tiempo) / len(con_limite) * 100, 1)
            if con_limite
            else 0.0
        ),
        antiguedad_maxima=max(
            (metrica.antiguedad_maxima for metrica in metricas.values()),
            default=0.0,
        ),
    )
    return resumen


def _criticos(abiertos: list[tuple[Movimiento, Any]]) -> list[DocumentoCritico]:
    criticos: list[DocumentoCritico] = []
    for movimiento, estado in abiertos:
        if estado.semaforo not in {Semaforo.ATRASADO, Semaforo.MUY_ATRASADO}:
            continue
        documento = movimiento.documento
        tipo = documento.tipo_tramite
        oficina = movimiento.oficina_destino
        criticos.append(
            DocumentoCritico(
                documento_id=documento.id,
                codigo=documento.codigo,
                tipo=tipo.nombre if tipo is not None else "—",
                oficina=oficina.nombre if oficina is not None else "—",
                oficina_codigo=oficina.codigo if oficina is not None else "—",
                semaforo=estado.semaforo,
                horas_atraso=round(estado.horas_atraso, 1),
                fecha_limite=estado.fecha_limite,
            )
        )
    criticos.sort(key=lambda critico: critico.horas_atraso, reverse=True)
    return criticos[:15]


def _tipos(
    movimientos: list[Movimiento],
    contexto: ContextoSla,
    ahora: datetime,
) -> list[TipoMetrica]:
    abiertos: Counter[str] = Counter()
    atrasados: Counter[str] = Counter()
    for movimiento in movimientos:
        if movimiento.fecha_salida is not None:
            continue
        tipo = movimiento.documento.tipo_tramite
        nombre = tipo.nombre if tipo is not None else "Sin tipo"
        estado = evaluar_movimiento(movimiento, contexto, ahora=ahora)
        if estado is None:
            continue
        abiertos[nombre] += 1
        if estado.semaforo in {Semaforo.ATRASADO, Semaforo.MUY_ATRASADO}:
            atrasados[nombre] += 1
    nombres = sorted(
        set(abiertos) | set(atrasados),
        key=lambda nombre: (abiertos[nombre], atrasados[nombre]),
        reverse=True,
    )[:8]
    return [
        TipoMetrica(nombre=nombre, abiertos=abiertos[nombre], atrasados=atrasados[nombre])
        for nombre in nombres
    ]


def _estados(documentos: list[Documento]) -> list[tuple[str, int]]:
    conteo = Counter(documento.estado for documento in documentos)
    siempre = {EstadoDocumento.EN_OFICINA, EstadoDocumento.FINALIZADO}
    return [
        (ESTADOS_ETIQUETA[estado], conteo.get(estado, 0))
        for estado in EstadoDocumento
        if conteo.get(estado, 0) or estado in siempre
    ]


def _serie(
    movimientos: list[Movimiento],
    filtros: FiltrosMetricas,
    ahora: datetime,
    observaciones_semana: Counter[int],
    finalizados_por_semana: Counter[int],
    alertas_semana: Counter[int],
) -> list[dict[str, Any]]:
    inicio = _inicio_semana(ahora) - timedelta(weeks=filtros.periodo.semanas - 1)
    semanas = [inicio + timedelta(weeks=indice) for indice in range(filtros.periodo.semanas)]

    recibidos: Counter[int] = Counter()
    cerrados: Counter[int] = Counter()
    for movimiento in movimientos:
        if movimiento.fecha_recepcion is not None:
            recibidos[_inicio_semana(movimiento.fecha_recepcion).toordinal()] += 1
        if movimiento.fecha_salida is not None:
            cerrados[_inicio_semana(movimiento.fecha_salida).toordinal()] += 1

    return [
        {
            "label": semana.strftime("%d/%m"),
            "recibidos": recibidos[semana.toordinal()],
            "cerrados": cerrados[semana.toordinal()],
            "finalizados": finalizados_por_semana[semana.toordinal()],
            "observaciones": observaciones_semana[semana.toordinal()],
            "alertas": alertas_semana[semana.toordinal()],
        }
        for semana in semanas
    ]


def _charts(
    resumen: ResumenMetricas,
    oficinas: list[MetricaOficina],
    tipos: list[TipoMetrica],
    estados: list[tuple[str, int]],
    serie: list[dict[str, Any]],
    criticos: list[DocumentoCritico],
) -> dict[str, Any]:
    top_oficinas = sorted(oficinas, key=lambda metrica: metrica.abiertos, reverse=True)[:10]
    con_muestra = [metrica for metrica in oficinas if metrica.cerrados >= 5]
    con_muestra.sort(key=lambda metrica: metrica.cumplimiento, reverse=True)

    carga = sorted(oficinas, key=lambda metrica: metrica.abiertos, reverse=True)
    top_carga = carga[:8]
    otras = sum(metrica.abiertos for metrica in carga[8:])

    return {
        "semaforo": {
            "labels": ["En plazo", "Por vencer", "Atrasado", "Muy atrasado"],
            "valores": [
                resumen.en_plazo,
                resumen.por_vencer,
                resumen.atrasado,
                resumen.muy_atrasado,
            ],
        },
        "oficinas": {
            "labels": [metrica.codigo for metrica in top_oficinas],
            "en_plazo": [metrica.en_plazo for metrica in top_oficinas],
            "por_vencer": [metrica.por_vencer for metrica in top_oficinas],
            "atrasado": [metrica.atrasado for metrica in top_oficinas],
            "muy_atrasado": [metrica.muy_atrasado for metrica in top_oficinas],
        },
        "carga": {
            "labels": [metrica.codigo for metrica in top_carga] + (["Otras"] if otras else []),
            "valores": [metrica.abiertos for metrica in top_carga] + ([otras] if otras else []),
        },
        "cumplimiento": {
            "labels": [metrica.codigo for metrica in con_muestra],
            "valores": [metrica.cumplimiento for metrica in con_muestra],
        },
        "tiempo_sla": {
            "labels": [metrica.codigo for metrica in con_muestra],
            "valores": [metrica.mediana_sla for metrica in con_muestra],
        },
        "tipos": {
            "labels": [tipo.nombre[:32] for tipo in tipos],
            "abiertos": [tipo.abiertos for tipo in tipos],
            "atrasados": [tipo.atrasados for tipo in tipos],
        },
        "estados": {
            "labels": [nombre for nombre, _ in estados],
            "valores": [cantidad for _, cantidad in estados],
        },
        "tendencia": {
            "labels": [punto["label"] for punto in serie],
            "recibidos": [punto["recibidos"] for punto in serie],
            "cerrados": [punto["cerrados"] for punto in serie],
            "finalizados": [punto["finalizados"] for punto in serie],
            "observaciones": [punto["observaciones"] for punto in serie],
            "alertas": [punto["alertas"] for punto in serie],
        },
        "criticos": [
            {
                "codigo": critico.codigo,
                "oficina": critico.oficina_codigo,
                "horas": critico.horas_atraso,
            }
            for critico in criticos[:8]
        ],
    }
