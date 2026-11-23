"""Worker continuo de análisis de documentos (OCR + LayoutLMv3).

Uso:
    python -m scripts.worker_ia                  # bucle
    python -m scripts.worker_ia --una-vez        # una pasada
    python -m scripts.worker_ia --limite 10      # máximo por pasada
    python -m scripts.worker_ia --intervalo 5    # segundos entre pasadas

No imprime texto OCR ni datos personales: solo conteos, ids y tiempos.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from types import FrameType

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.ia import motor
from app.services.ia.cola import ResultadoCola, limpiar_borradores, procesar_pendientes

LOGGER = logging.getLogger("docmun.ia.worker")


def procesar_una_vez(limite: int | None = None) -> ResultadoCola:
    db = SessionLocal()
    try:
        borradores = limpiar_borradores(db)
        resultado = procesar_pendientes(db, limite=limite)
        LOGGER.info(
            "Análisis procesados=%s errores=%s borradores_limpiados=%s",
            resultado.procesados,
            resultado.errores,
            borradores,
        )
        return resultado
    except Exception:
        db.rollback()
        LOGGER.exception("Falló la pasada del worker de análisis")
        return ResultadoCola()
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intervalo",
        type=int,
        default=settings.ia_intervalo_segundos,
        help="Segundos de espera cuando no hay pendientes.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=1,
        help="Máximo de análisis por pasada.",
    )
    parser.add_argument(
        "--una-vez",
        action="store_true",
        help="Procesa una sola pasada y termina.",
    )
    args = parser.parse_args(argv)

    if args.intervalo < 1:
        raise SystemExit("--intervalo debe ser mayor que cero.")
    if args.limite < 1:
        raise SystemExit("--limite debe ser mayor que cero.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    estado = motor.estado_motor()
    LOGGER.info(
        "Worker IA: habilitada=%s tesseract=%s modelos=%s dispositivo=%s",
        estado["habilitada"],
        estado["tesseract"],
        estado["modelos"],
        estado["dispositivo"],
    )

    if not settings.ia_habilitada:
        LOGGER.warning("IA_HABILITADA=false; el worker no procesará nada.")
        return 0
    if not estado["tesseract"]:
        LOGGER.error(
            "Tesseract no está instalado. En Docker usa la imagen Dockerfile.ia."
        )
        return 1
    if not estado["modelos"]:
        LOGGER.warning(
            "Faltan los modelos en %s; se procesará solo el OCR.",
            estado["clasificacion"],
        )

    detenido = False

    def detener(signum: int, frame: FrameType | None) -> None:
        nonlocal detenido
        LOGGER.info("Señal %s recibida; deteniendo el worker", signum)
        detenido = True

    signal.signal(signal.SIGTERM, detener)
    signal.signal(signal.SIGINT, detener)

    LOGGER.info("Worker iniciado: intervalo=%ss limite=%s", args.intervalo, args.limite)
    while not detenido:
        resultado = procesar_una_vez(args.limite)
        if args.una_vez:
            break
        if resultado.procesados or resultado.errores:
            continue
        vencimiento = time.monotonic() + args.intervalo
        while not detenido and time.monotonic() < vencimiento:
            time.sleep(min(1.0, vencimiento - time.monotonic()))

    LOGGER.info("Worker detenido")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
