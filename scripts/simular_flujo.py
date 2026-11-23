"""Reconstruye un flujo realista para los documentos ya cargados.

Toma los documentos que todavía no tienen movimientos y les crea un recorrido por
oficinas: derivación, recepción, observación, subsanación, finalización, archivo o
anulación. Sirve para datos de demostración; no borra documentos, archivos ni
movimientos y omite los documentos que ya tienen movimientos (por eso es idempotente).

Uso:
    python -m scripts.simular_flujo --dry-run
    python -m scripts.simular_flujo
    python -m scripts.simular_flujo --limite 100 --semilla 7 --lote 200

Los documentos abiertos quedan repartidos entre en plazo, por vencer, atrasado y
muy atrasado, de modo que el semáforo y el job de alertas tengan datos reales.
"""

from __future__ import annotations

import argparse
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.catalogos import Oficina
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import EstadoDocumento, Rol, Semaforo, TipoEvento
from app.models.usuarios import Usuario
from app.services.circulacion import (
    _sla_horas,
    anular,
    archivar,
    derivar,
    finalizar,
    observar,
    recibir,
    subsanar,
)
from app.services.sla import (
    ContextoSla,
    cargar_contexto_sla,
    evaluar_movimiento,
    horas_habiles_entre,
)

LOGGER = logging.getLogger("docmun.simulacion")

# Resultado buscado y peso relativo. Los documentos abiertos se reparten por nivel
# de semáforo para que la demostración muestre los cuatro colores.
RESULTADOS: tuple[tuple[str, int], ...] = (
    ("archivado", 32),
    ("finalizado", 30),
    ("en_plazo", 9),
    ("por_vencer", 6),
    ("atrasado", 5),
    ("muy_atrasado", 3),
    ("observado", 6),
    ("derivado", 4),
    ("anulado", 5),
)

MOTIVOS_OBSERVACION: tuple[str, ...] = (
    "Falta el plano de ubicación firmado por el propietario.",
    "El número de certificado no coincide con el expediente físico.",
    "Falta la copia del DNI del administrado.",
    "La memoria descriptiva no está visada por la oficina correspondiente.",
    "El recibo de pago aparece ilegible.",
)

NOTAS_SUBSANACION: tuple[str, ...] = (
    "El administrado presentó el plano faltante.",
    "Se corrigió el número de certificado en el expediente.",
    "Se adjuntó la copia del documento de identidad.",
    "La oficina remitió la memoria descriptiva visada.",
    "Se reemplazó el recibo de pago por una copia legible.",
)

MOTIVOS_ANULACION: tuple[str, ...] = (
    "El administrado desistió del trámite.",
    "Se registró por duplicado en ventanilla.",
    "El expediente no cumple con los requisitos mínimos.",
    "El pago fue revertido por tesorería.",
)

FRACCION_OBJETIVO = {
    Semaforo.EN_PLAZO: 0.5,
    Semaforo.POR_VENCER: 0.9,
    Semaforo.ATRASADO: 1.2,
    Semaforo.MUY_ATRASADO: 2.0,
}

HORAS_CANDIDATAS = (8, 10, 12, 14, 16)


@dataclass
class Simulacion:
    procesados: int = 0
    omitidos: int = 0
    errores: int = 0
    por_estado: Counter[str] = field(default_factory=Counter)
    por_semaforo: Counter[str] = field(default_factory=Counter)


def _con_zona(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor


def _elegir(rng: random.Random, opciones: tuple[tuple[str, int], ...]) -> str:
    return rng.choices(
        [opcion for opcion, _ in opciones],
        weights=[peso for _, peso in opciones],
        k=1,
    )[0]


def _nivel(frac: float, contexto: ContextoSla) -> Semaforo:
    umbrales = contexto.umbrales
    if frac >= umbrales.muy_atrasado:
        return Semaforo.MUY_ATRASADO
    if frac >= umbrales.atrasado:
        return Semaforo.ATRASADO
    if frac >= umbrales.por_vencer:
        return Semaforo.POR_VENCER
    return Semaforo.EN_PLAZO


def _candidato(ahora: datetime, zona: ZoneInfo, dias: int, hora: int) -> datetime:
    local = (ahora - timedelta(days=dias)).astimezone(zona)
    return local.replace(hour=hora, minute=0, second=0, microsecond=0).astimezone(UTC)


def _fraccion(
    inicio: datetime,
    horas: int,
    contexto: ContextoSla,
    ahora: datetime,
) -> float:
    consumidas = horas_habiles_entre(
        inicio,
        ahora,
        horario=contexto.horario,
        feriados=contexto.feriados,
        zona=contexto.zona,
    )
    return consumidas / horas


def _primer_dia(
    horas: int,
    objetivo: float,
    contexto: ContextoSla,
    ahora: datetime,
    zona: ZoneInfo,
) -> int:
    """Día (hacia atrás) en que la fracción consumida cruza el objetivo."""
    maximo = 730
    if _fraccion(_candidato(ahora, zona, 0, 9), horas, contexto, ahora) >= objetivo:
        return 0
    if _fraccion(_candidato(ahora, zona, maximo, 9), horas, contexto, ahora) < objetivo:
        return maximo

    bajo, alto = 0, maximo
    while bajo + 1 < alto:
        medio = (bajo + alto) // 2
        if _fraccion(_candidato(ahora, zona, medio, 9), horas, contexto, ahora) >= objetivo:
            alto = medio
        else:
            bajo = medio
    return alto


def _buscar_recepcion(
    horas: int,
    objetivo: Semaforo,
    contexto: ContextoSla,
    ahora: datetime,
    zona: ZoneInfo,
    minimo: datetime,
) -> datetime:
    """Busca una fecha de recepción que produzca el semáforo pedido.

    Si el plazo es tan corto que un día salta varios niveles, devuelve la fecha
    más cercana posible en lugar de fallar.
    """
    objetivo_frac = FRACCION_OBJETIVO[objetivo]
    cruce = _primer_dia(horas, objetivo_frac, contexto, ahora, zona)
    mejores: list[tuple[float, datetime]] = []

    for dias in (cruce + 2, cruce + 1, cruce, cruce - 1, cruce - 2):
        if dias < 0:
            continue
        for hora in HORAS_CANDIDATAS:
            candidato = _candidato(ahora, zona, dias, hora)
            if candidato >= ahora or candidato < minimo:
                continue
            fraccion = _fraccion(candidato, horas, contexto, ahora)
            if _nivel(fraccion, contexto) == objetivo:
                return candidato
            mejores.append((abs(fraccion - objetivo_frac), candidato))

    if mejores:
        return min(mejores)[1]

    respaldo = min(ahora - timedelta(hours=1), max(minimo, ahora - timedelta(days=1)))
    return respaldo


def _aplicar(
    db: Session,
    documento: Documento,
    usuario: Usuario,
    pasos: list[tuple[str, timedelta, Oficina | None]],
    inicio: datetime,
    ahora: datetime,
    rng: random.Random,
) -> None:
    for accion, desplazamiento, oficina in pasos:
        cuando = inicio + desplazamiento
        if cuando > ahora - timedelta(minutes=5):
            cuando = ahora - timedelta(minutes=5)
        if accion == "derivar":
            assert oficina is not None
            derivar(
                db,
                documento,
                destino=oficina,
                usuario=usuario,
                ahora=cuando,
                observacion=rng.choice(("", "", "Se remite para su atención.")),
            )
        elif accion == "recibir":
            recibir(db, documento, usuario=usuario, ahora=cuando)
        elif accion == "observar":
            observar(
                db,
                documento,
                usuario=usuario,
                motivo=rng.choice(MOTIVOS_OBSERVACION),
                ahora=cuando,
            )
        elif accion == "subsanar":
            subsanar(
                db,
                documento,
                usuario=usuario,
                nota=rng.choice(NOTAS_SUBSANACION),
                ahora=cuando,
            )
        elif accion == "finalizar":
            finalizar(
                db,
                documento,
                usuario=usuario,
                observacion=rng.choice(("", "", "Trámite concluido.")),
                ahora=cuando,
            )
        elif accion == "archivar":
            archivar(db, documento, usuario=usuario, ahora=cuando)
        elif accion == "anular":
            anular(
                db,
                documento,
                usuario=usuario,
                motivo=rng.choice(MOTIVOS_ANULACION),
                ahora=cuando,
            )
        else:  # pragma: no cover - error de programación
            raise ValueError(f"Acción desconocida: {accion}")


def _pasos_cerrados(
    archivar_al_final: bool,
    con_observacion: bool,
    segunda_oficina: Oficina | None,
    primera: Oficina,
    rng: random.Random,
) -> tuple[list[tuple[str, timedelta, Oficina | None]], timedelta]:
    pasos: list[tuple[str, timedelta, Oficina | None]] = []
    t = timedelta(0)
    pasos.append(("derivar", t, primera))
    t += timedelta(hours=4)
    pasos.append(("recibir", t, None))

    if con_observacion:
        t += timedelta(days=1)
        pasos.append(("observar", t, None))
        t += timedelta(days=2)
        pasos.append(("subsanar", t, None))

    if segunda_oficina is not None and segunda_oficina.id != primera.id:
        t += timedelta(days=1)
        pasos.append(("derivar", t, segunda_oficina))
        t += timedelta(hours=4)
        pasos.append(("recibir", t, None))
        if rng.random() < 0.15:
            t += timedelta(days=1)
            pasos.append(("observar", t, None))
            t += timedelta(days=2)
            pasos.append(("subsanar", t, None))

    t += timedelta(days=2)
    pasos.append(("finalizar", t, None))
    if archivar_al_final:
        t += timedelta(days=2)
        pasos.append(("archivar", t, None))
    return pasos, t


def _comprimir(
    pasos: list[tuple[str, timedelta, Oficina | None]],
    duracion: timedelta,
    disponible: timedelta,
) -> list[tuple[str, timedelta, Oficina | None]]:
    """Ajusta el calendario cuando el documento se registró hace muy poco."""
    if disponible >= duracion + timedelta(hours=1) or duracion.total_seconds() <= 0:
        return pasos
    factor = max(0.01, (disponible.total_seconds() - 3600) / duracion.total_seconds())
    return [(accion, desplazamiento * factor, oficina) for accion, desplazamiento, oficina in pasos]


def _resetear(db: Session, documento: Documento) -> None:
    """Deja el documento listo para reconstruir su flujo desde cero."""
    documento.estado = EstadoDocumento.REGISTRADO
    documento.oficina_actual_id = None
    documento.fecha_finalizacion = None

    # Los eventos de una corrida anterior no aplican; el CREADO se conserva.
    db.execute(
        delete(Evento).where(
            Evento.documento_id == documento.id,
            Evento.tipo != TipoEvento.CREADO,
        )
    )
    db.expire(documento, ["oficina_actual", "movimientos", "eventos"])

    creado = db.scalar(
        select(Evento)
        .where(
            Evento.documento_id == documento.id,
            Evento.tipo == TipoEvento.CREADO,
        )
        .order_by(Evento.id)
    )
    if creado is not None:
        creado.creado_en = _con_zona(documento.fecha_registro)


def _simular_documento(
    db: Session,
    documento: Documento,
    usuario: Usuario,
    oficinas: list[Oficina],
    contexto: ContextoSla,
    rng: random.Random,
    ahora: datetime,
) -> str:
    _resetear(db, documento)
    resultado = _elegir(rng, RESULTADOS)
    zona = contexto.zona

    tipo = documento.tipo_tramite
    primera = tipo.oficina_responsable or rng.choice(oficinas)
    if not primera.activa:
        primera = rng.choice(oficinas)
    segunda = None
    if len(oficinas) > 1 and rng.random() < 0.30:
        segunda = rng.choice([oficina for oficina in oficinas if oficina.id != primera.id])

    if resultado == "derivado":
        cuando = ahora - timedelta(
            days=rng.randint(1, 5),
            hours=rng.randint(0, 7),
            minutes=rng.randint(0, 59),
        )
        derivar(db, documento, destino=primera, usuario=usuario, ahora=cuando)
        return "derivado"

    if resultado == "anulado":
        pasos = [
            ("derivar", timedelta(0), primera),
            ("recibir", timedelta(hours=4), None),
            ("anular", timedelta(days=3), None),
        ]
        duracion = timedelta(days=3)
        base = _con_zona(documento.fecha_registro)
        inicio = base if base < ahora else ahora - timedelta(days=10)
        disponible = ahora - inicio
        pasos = _comprimir(pasos, duracion, disponible)
        _aplicar(db, documento, usuario, pasos, inicio, ahora, rng)
        return "anulado"

    if resultado in {"finalizado", "archivado"}:
        pasos, duracion = _pasos_cerrados(
            archivar_al_final=resultado == "archivado",
            con_observacion=rng.random() < 0.20,
            segunda_oficina=segunda,
            primera=primera,
            rng=rng,
        )
        base = _con_zona(documento.fecha_registro)
        inicio = base if base < ahora else ahora - timedelta(days=10)
        disponible = ahora - inicio
        pasos = _comprimir(pasos, duracion, disponible)
        _aplicar(db, documento, usuario, pasos, inicio, ahora, rng)
        return resultado

    # Documentos abiertos: se elige una recepción que produzca el nivel buscado.
    if resultado == "observado":
        objetivo = rng.choice(
            (Semaforo.POR_VENCER, Semaforo.ATRASADO, Semaforo.MUY_ATRASADO)
        )
    else:
        objetivo = Semaforo(resultado)

    horas = _sla_horas(db, documento.tipo_tramite_id, primera.id) or 72
    base = _con_zona(documento.fecha_registro)
    minimo = min(base + timedelta(hours=2), ahora - timedelta(hours=1))
    recepcion = _buscar_recepcion(horas, objetivo, contexto, ahora, zona, minimo)

    derivar(
        db,
        documento,
        destino=primera,
        usuario=usuario,
        ahora=recepcion - timedelta(hours=2),
    )
    recibir(db, documento, usuario=usuario, ahora=recepcion)

    if resultado == "observado":
        cuando = min(recepcion + timedelta(days=1), ahora - timedelta(hours=1))
        cuando = max(cuando, recepcion + timedelta(hours=1))
        observar(
            db,
            documento,
            usuario=usuario,
            motivo=rng.choice(MOTIVOS_OBSERVACION),
            ahora=cuando,
        )
        return "observado"
    return "en_oficina"


def _semaforo_actual(
    db: Session,
    documento: Documento,
    contexto: ContextoSla,
    ahora: datetime,
) -> Semaforo | None:
    # La sesión no hace autoflush; sin esto la consulta no ve la recepción recién
    # registrada y el semáforo saldría vacío.
    db.flush()
    movimiento = db.scalar(
        select(Movimiento)
        .where(
            Movimiento.documento_id == documento.id,
            Movimiento.fecha_recepcion.is_not(None),
            Movimiento.fecha_salida.is_(None),
        )
        .order_by(Movimiento.id.desc())
    )
    if movimiento is None:
        return None
    estado = evaluar_movimiento(movimiento, contexto, ahora=ahora)
    return estado.semaforo if estado is not None else None


def simular(
    db: Session,
    *,
    limite: int | None = None,
    semilla: int = 20261111,
    lote: int = 200,
    dry_run: bool = False,
) -> Simulacion:
    rng = random.Random(semilla)
    ahora = datetime.now(UTC)
    contexto = cargar_contexto_sla(db)
    usuario = db.scalar(select(Usuario).where(Usuario.rol == Rol.ADMIN).order_by(Usuario.id))
    if usuario is None:
        raise RuntimeError("No hay un usuario administrador para registrar el flujo.")
    oficinas = list(db.scalars(select(Oficina).where(Oficina.activa.is_(True))).all())
    if not oficinas:
        raise RuntimeError("No hay oficinas activas.")

    con_movimientos = set(db.scalars(select(Movimiento.documento_id).distinct()).all())
    documentos = list(db.scalars(select(Documento).order_by(Documento.id)).all())
    resultado = Simulacion()

    for documento in documentos:
        if documento.id in con_movimientos:
            resultado.omitidos += 1
            continue
        if limite is not None and resultado.procesados >= limite:
            break

        try:
            with db.begin_nested():
                estado = _simular_documento(
                    db,
                    documento,
                    usuario,
                    oficinas,
                    contexto,
                    rng,
                    ahora,
                )
                semaforo = _semaforo_actual(db, documento, contexto, ahora)
            resultado.procesados += 1
            resultado.por_estado[estado] += 1
            if semaforo is not None:
                resultado.por_semaforo[semaforo.value] += 1
        except Exception:
            db.expire(documento)
            resultado.errores += 1
            LOGGER.exception("No se pudo simular el documento %s", documento.codigo)

        if not dry_run and resultado.procesados and resultado.procesados % lote == 0:
            db.commit()
            db.expire_all()

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return resultado


def _resumen(resultado: Simulacion) -> str:
    estados = ", ".join(
        f"{estado}={cantidad}" for estado, cantidad in sorted(resultado.por_estado.items())
    )
    semaforos = ", ".join(
        f"{nivel}={cantidad}" for nivel, cantidad in sorted(resultado.por_semaforo.items())
    )
    return (
        f"Procesados: {resultado.procesados} | Omitidos (ya tienen movimientos): "
        f"{resultado.omitidos} | Errores: {resultado.errores}\n"
        f"Estados: {estados or 'sin cambios'}\n"
        f"Semáforo de abiertos: {semaforos or 'sin tramos abiertos'}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=None, help="Máximo de documentos a simular.")
    parser.add_argument("--semilla", type=int, default=20261111, help="Semilla aleatoria.")
    parser.add_argument("--lote", type=int, default=200, help="Documentos por commit.")
    parser.add_argument("--dry-run", action="store_true", help="No guarda cambios.")
    args = parser.parse_args(argv)

    if args.limite is not None and args.limite < 1:
        raise SystemExit("--limite debe ser mayor que cero.")
    if args.lote < 1:
        raise SystemExit("--lote debe ser mayor que cero.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = SessionLocal()
    try:
        resultado = simular(
            db,
            limite=args.limite,
            semilla=args.semilla,
            lote=args.lote,
            dry_run=args.dry_run,
        )
        print(_resumen(resultado))
        if args.dry_run:
            print("Dry-run: no se escribió ningún cambio.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
