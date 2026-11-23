"""Exporta los análisis confirmados como dataset de entrenamiento.

Uso:
    python -m scripts.exportar_dataset_ia --salida dataset/ia
    python -m scripts.exportar_dataset_ia --salida dataset/ia --solo-confirmados
    python -m scripts.exportar_dataset_ia --salida dataset/ia --no-copiar

El dataset contiene escaneos y, por tanto, datos personales. `dataset/` está
en .gitignore: no lo subas ni lo compartas sin autorización.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import SessionLocal
from app.models.analisis import DocumentoAnalisis
from app.models.documentos import Documento
from app.models.enums import EstadoAnalisis
from app.services.archivos import ruta_absoluta


def _manifest(db, args) -> tuple[list[dict], Counter, list[str]]:
    stmt = (
        select(DocumentoAnalisis)
        .where(
            DocumentoAnalisis.estado == EstadoAnalisis.LISTO,
            DocumentoAnalisis.archivo_id.is_not(None),
            DocumentoAnalisis.documento_id.is_not(None),
        )
        .options(
            selectinload(DocumentoAnalisis.archivo),
            selectinload(DocumentoAnalisis.documento).selectinload(Documento.tipo_tramite),
            selectinload(DocumentoAnalisis.tipo_tramite_confirmado),
        )
        .order_by(DocumentoAnalisis.id)
    )
    if args.limite:
        stmt = stmt.limit(args.limite)

    lineas: list[dict] = []
    conteo: Counter = Counter()
    omitidos: list[str] = []
    vistos: set[str] = set()

    for analisis in db.scalars(stmt).all():
        if analisis.hash_sha256 in vistos:
            continue
        archivo = analisis.archivo
        documento = analisis.documento
        if archivo is None or documento is None:
            omitidos.append(f"analisis {analisis.id}: sin archivo o documento")
            continue

        tipo = analisis.tipo_tramite_confirmado or documento.tipo_tramite
        if tipo is None:
            omitidos.append(f"analisis {analisis.id}: sin tipo de trámite")
            continue
        if args.solo_confirmados and analisis.tipo_tramite_confirmado is None:
            continue

        try:
            origen = ruta_absoluta(archivo)
        except Exception:
            omitidos.append(f"analisis {analisis.id}: ruta inválida")
            continue
        if not origen.is_file():
            omitidos.append(f"analisis {analisis.id}: archivo no encontrado")
            continue

        vistos.add(analisis.hash_sha256)
        if args.no_copiar:
            destino_relativo = str(origen)
        else:
            destino = args.salida / "archivos" / f"{analisis.hash_sha256}{origen.suffix.lower()}"
            destino.parent.mkdir(parents=True, exist_ok=True)
            if not destino.exists():
                shutil.copy2(origen, destino)
            destino_relativo = str(destino.relative_to(args.salida))

        lineas.append(
            {
                "ruta": destino_relativo,
                "tipo": tipo.codigo,
                "hash": analisis.hash_sha256,
                "paginas": analisis.paginas,
            }
        )
        conteo[tipo.codigo] += 1

    return lineas, conteo, omitidos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, default=Path("dataset/ia"))
    parser.add_argument(
        "--solo-confirmados",
        action="store_true",
        help="Solo ejemplos cuyo tipo fue confirmado por una persona.",
    )
    parser.add_argument(
        "--no-copiar",
        action="store_true",
        help="Referencia los archivos originales en vez de copiarlos.",
    )
    parser.add_argument("--limite", type=int, default=0)
    parser.add_argument(
        "--minimo-por-tipo",
        type=int,
        default=50,
        help="Solo avisa: clases por debajo de este número.",
    )
    args = parser.parse_args(argv)
    args.salida.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        lineas, conteo, omitidos = _manifest(db, args)
    finally:
        db.close()

    manifiesto = args.salida / "manifest.jsonl"
    with manifiesto.open("w", encoding="utf-8") as archivo:
        for linea in lineas:
            archivo.write(json.dumps(linea, ensure_ascii=False) + "\n")

    faltantes = {
        codigo: cantidad
        for codigo, cantidad in sorted(conteo.items())
        if cantidad < args.minimo_por_tipo
    }
    resumen = {
        "generado_en": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": len(lineas),
        "por_tipo": dict(sorted(conteo.items())),
        "tipos_bajo_minimo": faltantes,
        "omitidos": omitidos,
        "minimo_por_tipo": args.minimo_por_tipo,
    }
    (args.salida / "resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Manifiesto: {manifiesto}")
    print(f"Ejemplos: {len(lineas)}")
    for codigo, cantidad in sorted(conteo.items()):
        print(f"  {codigo}: {cantidad}")
    if faltantes:
        print(f"Clases por debajo de {args.minimo_por_tipo}: {faltantes}")
    if omitidos:
        print(f"Omitidos: {len(omitidos)} (ver resumen.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
