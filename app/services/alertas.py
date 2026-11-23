"""Alertas internas cuando un plazo entra en riesgo o se vence.

El semáforo es calculado y no se guarda. Este módulo solo registra un `Evento`
de auditoría la primera vez que un tramo abierto alcanza cada nivel; así el job
es idempotente y no llena el historial en cada ejecución.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.documentos import Documento, Evento
from app.models.enums import Semaforo, TipoEvento
from app.services.eventos import registrar_evento
from app.services.sla import ContextoSla, evaluar_plazos

ALERTABLES = {Semaforo.POR_VENCER, Semaforo.ATRASADO, Semaforo.MUY_ATRASADO}


@dataclass(frozen=True)
class AlertaGenerada:
    documento_id: int
    codigo: str
    movimiento_id: int
    semaforo: Semaforo
    fecha_limite: datetime | None


@dataclass
class ResultadoAlertas:
    evaluados: int = 0
    generadas: list[AlertaGenerada] = field(default_factory=list)
    existentes: int = 0
    por_semaforo: dict[str, int] = field(default_factory=dict)

    @property
    def total_generadas(self) -> int:
        return len(self.generadas)


def _alertas_existentes(db: Session, documento_ids: set[int]) -> set[tuple[int, int, str]]:
    if not documento_ids:
        return set()

    eventos = db.scalars(
        select(Evento).where(
            Evento.documento_id.in_(documento_ids),
            Evento.tipo == TipoEvento.ALERTA_PLAZO,
        )
    ).all()
    claves: set[tuple[int, int, str]] = set()
    for evento in eventos:
        detalle = evento.detalle or {}
        movimiento_id = detalle.get("movimiento_id")
        semaforo = detalle.get("semaforo")
        if isinstance(movimiento_id, int) and isinstance(semaforo, str):
            claves.add((evento.documento_id, movimiento_id, semaforo))
    return claves


def generar_alertas(
    db: Session,
    *,
    ahora: datetime | None = None,
    contexto: ContextoSla | None = None,
    limite: int | None = None,
    dry_run: bool = False,
) -> ResultadoAlertas:
    """Registra alertas nuevas. El llamador decide si hace `commit`."""
    ahora = ahora or datetime.now(UTC)
    resultados = evaluar_plazos(db, ahora=ahora, contexto=contexto, limite=limite)
    pendientes = [
        (movimiento, estado) for movimiento, estado in resultados if estado.semaforo in ALERTABLES
    ]

    documento_ids = {movimiento.documento_id for movimiento, _ in pendientes}
    existentes = _alertas_existentes(db, documento_ids)
    documentos: dict[int, Documento] = {}
    if documento_ids:
        documentos = {
            documento.id: documento
            for documento in db.scalars(
                select(Documento).where(Documento.id.in_(documento_ids))
            ).all()
        }

    resultado = ResultadoAlertas(evaluados=len(resultados))
    conteo: Counter[str] = Counter()

    for movimiento, estado in pendientes:
        clave = (movimiento.documento_id, movimiento.id, estado.semaforo.value)
        if clave in existentes:
            resultado.existentes += 1
            continue

        documento = documentos.get(movimiento.documento_id)
        if documento is None:
            continue

        if not dry_run:
            registrar_evento(
                db,
                documento,
                TipoEvento.ALERTA_PLAZO,
                detalle={
                    "movimiento_id": movimiento.id,
                    "semaforo": estado.semaforo.value,
                    "sla_horas": movimiento.sla_horas,
                    "fecha_limite": (
                        estado.fecha_limite.isoformat() if estado.fecha_limite is not None else None
                    ),
                },
            )

        resultado.generadas.append(
            AlertaGenerada(
                documento_id=movimiento.documento_id,
                codigo=documento.codigo,
                movimiento_id=movimiento.id,
                semaforo=estado.semaforo,
                fecha_limite=estado.fecha_limite,
            )
        )
        conteo[estado.semaforo.value] += 1

    resultado.por_semaforo = dict(conteo)
    return resultado
