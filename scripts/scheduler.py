"""Job continuo de evaluación de plazos y alertas internas.

Uso:
    python -m scripts.scheduler                 # bucle cada SLA_INTERVALO_SEGUNDOS
    python -m scripts.scheduler --una-vez       # una sola pasada (útil en cron)
    python -m scripts.scheduler --intervalo 300 # forzar intervalo en segundos

No envía notificaciones al ciudadano; eso pertenece a la fase de notificaciones.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from types import FrameType

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.alertas import generar_alertas

LOGGER = logging.getLogger("docmun.scheduler")


def evaluar_una_vez(limite: int | None = None) -> bool:
    """Ejecuta el job y devuelve si terminó sin errores."""
    db = SessionLocal()
    try:
        resultado = generar_alertas(db, limite=limite)
        db.commit()
        LOGGER.info(
            "Plazos evaluados=%s alertas_nuevas=%s alertas_existentes=%s detalle=%s",
            resultado.evaluados,
            resultado.total_generadas,
            resultado.existentes,
            resultado.por_semaforo or "sin alertas",
        )
        return True
    except Exception:
        db.rollback()
        LOGGER.exception("Falló la evaluación de plazos")
        return False
    finally:
        db.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intervalo",
        type=int,
        default=settings.sla_intervalo_segundos,
        help="Segundos entre ejecuciones (por defecto, SLA_INTERVALO_SEGUNDOS).",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Máximo de tramos abiertos a evaluar en cada pasada.",
    )
    parser.add_argument(
        "--una-vez",
        action="store_true",
        help="Ejecuta una sola pasada y termina.",
    )
    args = parser.parse_args(argv)

    if args.intervalo < 1:
        raise SystemExit("--intervalo debe ser mayor que cero.")
    if args.limite is not None and args.limite < 1:
        raise SystemExit("--limite debe ser mayor que cero.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    detenido = False

    def detener(signum: int, frame: FrameType | None) -> None:
        nonlocal detenido
        LOGGER.info("Señal %s recibida; deteniendo el scheduler", signum)
        detenido = True

    signal.signal(signal.SIGTERM, detener)
    signal.signal(signal.SIGINT, detener)

    LOGGER.info(
        "Scheduler iniciado: intervalo=%ss limite=%s",
        args.intervalo,
        args.limite or "todos",
    )
    while not detenido:
        evaluar_una_vez(args.limite)
        if args.una_vez:
            break
        # Espera en tramos cortos para atender SIGTERM sin demorar el apagado.
        vencimiento = time.monotonic() + args.intervalo
        while not detenido and time.monotonic() < vencimiento:
            time.sleep(min(1.0, vencimiento - time.monotonic()))
    LOGGER.info("Scheduler detenido")


if __name__ == "__main__":
    main()
