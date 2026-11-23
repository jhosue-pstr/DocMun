"""Evalúa plazos y registra alertas internas por nivel de semáforo.

Pensado para ejecutarse desde cron o un timer de systemd:

    python -m scripts.evaluar_plazos
    python -m scripts.evaluar_plazos --dry-run
    python -m scripts.evaluar_plazos --limite 500

No envía notificaciones al ciudadano; eso pertenece a la fase de notificaciones.
"""

from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.services.alertas import generar_alertas


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Máximo de tramos abiertos a evaluar (por defecto, todos).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcula las alertas sin escribir eventos.",
    )
    args = parser.parse_args(argv)

    if args.limite is not None and args.limite < 1:
        raise SystemExit("--limite debe ser mayor que cero.")

    db = SessionLocal()
    try:
        resultado = generar_alertas(
            db,
            limite=args.limite,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            db.commit()

        resumen = ", ".join(
            f"{semaforo}={cantidad}"
            for semaforo, cantidad in sorted(resultado.por_semaforo.items())
        )
        print(f"Tramos evaluados: {resultado.evaluados}")
        print(f"Alertas nuevas: {resultado.total_generadas}" + (f" ({resumen})" if resumen else ""))
        print(f"Alertas ya existentes: {resultado.existentes}")

        for alerta in resultado.generadas[:20]:
            print(
                f"  · {alerta.codigo} | {alerta.semaforo.value} | "
                f"movimiento {alerta.movimiento_id}"
            )
        if resultado.total_generadas > 20:
            print(f"  … y {resultado.total_generadas - 20} más")

        if args.dry_run:
            print("Dry-run: no se escribió ningún evento.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
