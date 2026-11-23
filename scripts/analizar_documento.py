"""Prueba el motor local con un PDF o imagen, sin base de datos.

Uso:
    python -m scripts.analizar_documento --archivo ruta.pdf
    python -m scripts.analizar_documento --archivo ruta.pdf --solo ocr
    python -m scripts.analizar_documento --archivo ruta.pdf --json
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.config import settings
from app.services.ia import motor
from app.services.ia.extraccion import construir_sugerencias


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archivo", required=True, help="PDF o imagen a analizar.")
    parser.add_argument(
        "--solo",
        choices=["completo", "ocr"],
        default="completo",
        help="'ocr' omite los modelos LayoutLMv3.",
    )
    parser.add_argument(
        "--modelos-dir",
        default=None,
        help="Carpeta de modelos (por defecto, IA_MODELOS_DIR).",
    )
    parser.add_argument(
        "--tipo-nombre",
        default=None,
        help="Nombre del trámite para respaldar el asunto si el documento no lo trae.",
    )
    parser.add_argument("--mostrar-ocr", action="store_true")
    parser.add_argument("--json", action="store_true", help="Salida JSON por stdout.")
    args = parser.parse_args(argv)

    # El CLI debe funcionar aunque la app tenga la IA deshabilitada.
    settings.ia_habilitada = True
    if args.modelos_dir:
        settings.ia_modelos_dir = args.modelos_dir

    estado = motor.estado_motor()
    if not estado["tesseract"]:
        print("Tesseract no está instalado o no está en el PATH.", file=sys.stderr)
        print(json.dumps(estado, indent=2, ensure_ascii=False), file=sys.stderr)
        return 2

    resultado = motor.analizar_archivo(
        args.archivo, usar_modelos=args.solo != "ocr"
    )
    sugerencias = construir_sugerencias(
        resultado.texto_ocr,
        clase_ia=(
            {"clase": resultado.tipo_clase, "probabilidad": resultado.tipo_confianza}
            if resultado.tipo_clase
            else None
        ),
        nombre_tipo=args.tipo_nombre,
    )

    if args.json:
        salida = {
            "archivo": args.archivo,
            "paginas": resultado.paginas,
            "duracion_ms": resultado.duracion_ms,
            "motor": resultado.motor,
            "version_modelo": resultado.version_modelo,
            "tipo_clase": resultado.tipo_clase,
            "tipo_confianza": resultado.tipo_confianza,
            "top_clases": resultado.top_clases,
            "tipo_sugerido": sugerencias.get("tipo"),
            "campos": sugerencias.get("campos"),
            "kie": resultado.kie,
        }
        if args.mostrar_ocr:
            salida["texto_ocr"] = resultado.texto_ocr
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        return 0

    print("=" * 60)
    print(f"Archivo: {args.archivo}")
    print(
        f"Páginas: {resultado.paginas} · Motor: {resultado.motor} · "
        f"{resultado.duracion_ms} ms · versión: {resultado.version_modelo}"
    )
    print(f"OCR: {len(resultado.texto_ocr.split())} palabras")

    tipo = sugerencias.get("tipo")
    if tipo:
        print(f"\nTipo sugerido: {tipo['codigo']} ({tipo['confianza']:.0%})")
        print(f"  Evidencia: {tipo['evidencia']}")
    else:
        print("\nTipo sugerido: sin coincidencias de palabras clave")

    campos = sugerencias.get("campos") or {}
    if campos:
        print("\nCampos detectados:")
        for clave, dato in campos.items():
            print(f"  {clave:<20} {dato['valor']} ({dato['confianza']:.0%})")

    if resultado.tipo_clase:
        print("\nClasificador genérico (LayoutLMv3):")
        for item in resultado.top_clases:
            print(f"  {item['clase']:<25} {item['probabilidad']:.2%}")

    if resultado.kie:
        print("\nKIE (HEADER / QUESTION / ANSWER):")
        for clave, valores in resultado.kie["campos"].items():
            for texto in valores[:5]:
                print(f"  [{clave}] {texto}")

    if args.mostrar_ocr:
        print("\nTexto OCR:")
        print(resultado.texto_ocr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
