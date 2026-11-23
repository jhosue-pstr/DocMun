"""Entrena un clasificador municipal LayoutLMv3 a partir del dataset exportado.

Este script NO se ejecuta en el servidor de DocMun: está pensado para una
máquina con GPU (o CPU, con paciencia). Genera un `modelo.pt` compatible con
`app/services/ia/motor.py`.

Flujo:
    1. En el servidor: python -m scripts.exportar_dataset_ia --salida dataset/ia
    2. Copiar dataset/ia a la máquina de entrenamiento.
    3. pip install -r requirements-entrenamiento.txt
    4. python -m scripts.entrenar_ia --dataset dataset/ia --salida models/ia/v2

Uso rápido:
    python -m scripts.entrenar_ia --dataset dataset/ia --dry-run
    python -m scripts.entrenar_ia --dataset dataset/ia --salida models/ia/v2 \
        --epocas 5 --batch 4 --balancear
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger("docmun.ia.entrenamiento")
CONFIANZA_MINIMA_OCR = 20.0
SUFIJO_IMAGEN = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class Ejemplo:
    ruta: Path
    tipo: str
    hash: str


def _leer_manifest(ruta: Path, raiz: Path) -> tuple[list[Ejemplo], int]:
    ejemplos: list[Ejemplo] = []
    faltantes = 0
    with ruta.open(encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea:
                continue
            datos = json.loads(linea)
            imagen = Path(datos["ruta"])
            if not imagen.is_absolute():
                imagen = raiz / imagen
            if not imagen.is_file():
                faltantes += 1
                continue
            hash_archivo = str(
                datos.get("hash")
                or hashlib.sha1(str(imagen).encode("utf-8")).hexdigest()
            )
            ejemplos.append(
                Ejemplo(ruta=imagen, tipo=str(datos["tipo"]), hash=hash_archivo)
            )
    return ejemplos, faltantes


def _imprimir_resumen(ejemplos: list[Ejemplo], faltantes: int) -> None:
    conteo = Counter(ejemplo.tipo for ejemplo in ejemplos)
    print(f"Ejemplos válidos: {len(ejemplos)}")
    if faltantes:
        print(f"Archivos no encontrados: {faltantes}")
    for tipo, cantidad in sorted(conteo.items()):
        print(f"  {tipo}: {cantidad}")
    if len(conteo) < 2:
        print("Se necesitan al menos 2 tipos para entrenar un clasificador.")


def _configurar_tesseract() -> None:
    import os

    import pytesseract

    if shutil.which("tesseract"):
        return
    for candidato in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ):
        if os.path.exists(candidato):
            pytesseract.pytesseract.tesseract_cmd = candidato
            return


def _cargar_imagen(ruta: Path, dpi: int):
    from PIL import Image, ImageOps

    if ruta.suffix.lower() == ".pdf":
        import pymupdf

        documento = pymupdf.open(str(ruta))
        try:
            pagina = documento.load_page(0)
            pix = pagina.get_pixmap(dpi=dpi)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            documento.close()

    if ruta.suffix.lower() not in SUFIJO_IMAGEN:
        return None
    with Image.open(ruta) as original:
        return ImageOps.exif_transpose(original).convert("RGB")


def _ocr(imagen, idiomas: str, psm: int) -> tuple[list[str], list[list[int]]]:
    import pytesseract
    from pytesseract import Output

    _configurar_tesseract()
    try:
        datos = pytesseract.image_to_data(
            imagen, lang=idiomas, config=f"--psm {psm}", output_type=Output.DICT
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise SystemExit(
            "Tesseract no está instalado. Instálalo y vuelve a ejecutar."
        ) from exc
    except pytesseract.TesseractError:
        datos = pytesseract.image_to_data(
            imagen, lang="eng", config=f"--psm {psm}", output_type=Output.DICT
        )

    ancho, alto = imagen.size
    palabras: list[str] = []
    cajas: list[list[int]] = []
    for indice, texto in enumerate(datos.get("text", [])):
        texto = str(texto or "").strip()
        if not texto:
            continue
        try:
            confianza = float(datos["conf"][indice])
        except (TypeError, ValueError):
            confianza = 0.0
        if confianza < CONFIANZA_MINIMA_OCR:
            continue
        x = int(datos["left"][indice])
        y = int(datos["top"][indice])
        ancho_palabra = int(datos["width"][indice])
        alto_palabra = int(datos["height"][indice])
        if ancho_palabra <= 0 or alto_palabra <= 0:
            continue

        def escalar(valor: float, tamano: int) -> int:
            return int(max(0, min(1000, round(1000.0 * valor / max(tamano, 1)))))

        palabras.append(texto)
        cajas.append(
            [
                escalar(x, ancho),
                escalar(y, alto),
                escalar(x + ancho_palabra, ancho),
                escalar(y + alto_palabra, alto),
            ]
        )
    return palabras, cajas


def _ocr_con_cache(
    ejemplo: Ejemplo,
    imagen,
    cache_dir: Path,
    idiomas: str,
    psm: int,
) -> tuple[list[str], list[list[int]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ruta_cache = cache_dir / f"{ejemplo.hash}.json"
    if ruta_cache.is_file():
        datos = json.loads(ruta_cache.read_text(encoding="utf-8"))
        return list(datos.get("palabras", [])), list(datos.get("cajas", []))

    palabras, cajas = _ocr(imagen, idiomas, psm)
    ruta_cache.write_text(
        json.dumps({"palabras": palabras, "cajas": cajas}, ensure_ascii=False),
        encoding="utf-8",
    )
    return palabras, cajas


def _dividir(
    ejemplos: list[Ejemplo],
    fraccion_val: float,
    fraccion_test: float,
    semilla: int,
) -> tuple[list[Ejemplo], list[Ejemplo], list[Ejemplo]]:
    random.Random(semilla).shuffle(ejemplos)
    por_tipo: dict[str, list[Ejemplo]] = defaultdict(list)
    for ejemplo in ejemplos:
        por_tipo[ejemplo.tipo].append(ejemplo)

    train: list[Ejemplo] = []
    validacion: list[Ejemplo] = []
    prueba: list[Ejemplo] = []
    for _, items in sorted(por_tipo.items()):
        cantidad = len(items)
        if cantidad < 4:
            train.extend(items)
            continue
        n_val = max(1, round(cantidad * fraccion_val))
        n_test = max(1, round(cantidad * fraccion_test))
        if n_val + n_test >= cantidad:
            n_val = 0
            n_test = 0
        train.extend(items[: cantidad - n_val - n_test])
        validacion.extend(items[cantidad - n_val - n_test : cantidad - n_test])
        prueba.extend(items[cantidad - n_test :])
    return train, validacion, prueba


def _construir_features(
    ejemplos: list[Ejemplo],
    tokenizer,
    image_processor,
    id2label: dict[str, int],
    max_length: int,
    cache_dir: Path,
    dpi: int,
    idiomas: str,
    psm: int,
):
    import torch

    features = []
    omitidos = 0
    for ejemplo in ejemplos:
        imagen = _cargar_imagen(ejemplo.ruta, dpi)
        if imagen is None:
            omitidos += 1
            continue
        palabras, cajas = _ocr_con_cache(ejemplo, imagen, cache_dir, idiomas, psm)
        if not palabras:
            omitidos += 1
            continue

        codificacion = tokenizer(
            palabras,
            boxes=cajas,
            truncation=True,
            padding="max_length",
            max_length=max_length,
            return_tensors="pt",
        )
        pixeles = image_processor([imagen], return_tensors="pt")["pixel_values"][0]
        features.append(
            {
                "input_ids": codificacion["input_ids"][0],
                "attention_mask": codificacion["attention_mask"][0],
                "bbox": codificacion["bbox"][0],
                "pixel_values": pixeles,
                "labels": torch.tensor(id2label[ejemplo.tipo], dtype=torch.long),
            }
        )
    return features, omitidos


class DocumentoDataset:
    """Dataset mínimo para el Trainer; las features ya vienen tokenizadas."""

    def __init__(self, features):
        self.features = features

    def __len__(self):
        return len(self.features)

    def __getitem__(self, indice):
        return self.features[indice]


def _pesos_balanceo(features):
    from collections import Counter

    conteo = Counter(int(item["labels"]) for item in features)
    pesos = [1.0 / conteo[int(item["labels"])] for item in features]
    return pesos


def entrenar(args, ejemplos: list[Ejemplo]) -> int:
    import numpy as np
    import torch
    from sklearn.metrics import accuracy_score, f1_score
    from torch.utils.data import WeightedRandomSampler
    from transformers import (
        LayoutLMv3Config,
        LayoutLMv3ForSequenceClassification,
        LayoutLMv3ImageProcessor,
        LayoutLMv3TokenizerFast,
        Trainer,
        TrainingArguments,
    )

    etiquetas = sorted({ejemplo.tipo for ejemplo in ejemplos})
    if len(etiquetas) < 2:
        print("Se necesitan al menos 2 tipos de trámite.", file=sys.stderr)
        return 1

    id2label = {indice: etiqueta for indice, etiqueta in enumerate(etiquetas)}
    label2id = {etiqueta: indice for indice, etiqueta in enumerate(etiquetas)}

    train, validacion, prueba = _dividir(
        ejemplos, args.val, args.test, args.semilla
    )
    if not train:
        print("No quedaron ejemplos de entrenamiento.", file=sys.stderr)
        return 1
    if not validacion:
        LOGGER.warning("Sin conjunto de validación; se usará entrenamiento para evaluar.")
        validacion = train
    if not prueba:
        prueba = validacion

    print(
        f"Split: train={len(train)} val={len(validacion)} test={len(prueba)} "
        f"clases={len(etiquetas)}"
    )

    tokenizer = LayoutLMv3TokenizerFast.from_pretrained(args.modelo_base)
    image_processor = LayoutLMv3ImageProcessor.from_pretrained(args.modelo_base)
    image_processor.apply_ocr = False

    cache_dir = args.cache_ocr
    features_train, omitidos_train = _construir_features(
        train, tokenizer, image_processor, label2id, args.max_length,
        cache_dir, args.dpi, args.idiomas, args.psm,
    )
    features_val, omitidos_val = _construir_features(
        validacion, tokenizer, image_processor, label2id, args.max_length,
        cache_dir, args.dpi, args.idiomas, args.psm,
    )
    features_test, omitidos_test = _construir_features(
        prueba, tokenizer, image_processor, label2id, args.max_length,
        cache_dir, args.dpi, args.idiomas, args.psm,
    )

    if omitidos_train or omitidos_val or omitidos_test:
        LOGGER.warning(
            "Omitidos sin OCR: train=%s val=%s test=%s",
            omitidos_train,
            omitidos_val,
            omitidos_test,
        )
    if not features_train:
        print("Ningún ejemplo produjo OCR utilizable.", file=sys.stderr)
        return 1
    if not features_val:
        features_val = features_train
    if not features_test:
        features_test = features_val

    config = LayoutLMv3Config.from_pretrained(
        args.modelo_base,
        num_labels=len(etiquetas),
        id2label=id2label,
        label2id=label2id,
    )
    model = LayoutLMv3ForSequenceClassification.from_pretrained(
        args.modelo_base,
        config=config,
        ignore_mismatched_sizes=True,
    )

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        predicciones = np.argmax(logits, axis=-1)
        return {
            "accuracy": float(accuracy_score(labels, predicciones)),
            "f1_macro": float(
                f1_score(labels, predicciones, average="macro", zero_division=0)
            ),
        }

    class TrainerBalanceado(Trainer):
        def __init__(self, *args_, pesos=None, **kwargs):
            super().__init__(*args_, **kwargs)
            self._pesos = pesos

        def _get_train_sampler(self, *args_, **kwargs):
            if self._pesos is None:
                return super()._get_train_sampler(*args_, **kwargs)
            return WeightedRandomSampler(
                self._pesos, num_samples=len(self._pesos), replacement=True
            )

    args_salida = args.salida
    args_salida.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(args_salida / "checkpoints"),
        num_train_epochs=args.epocas,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        learning_rate=args.lr,
        warmup_ratio=0.1,
        weight_decay=0.01,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="f1_macro",
        greater_is_better=True,
        remove_unused_columns=False,
        fp16=bool(args.fp16 and torch.cuda.is_available()),
        report_to=[],
        seed=args.semilla,
    )

    trainer = TrainerBalanceado(
        model=model,
        args=training_args,
        train_dataset=DocumentoDataset(features_train),
        eval_dataset=DocumentoDataset(features_val),
        compute_metrics=compute_metrics,
        pesos=_pesos_balanceo(features_train) if args.balancear else None,
    )

    trainer.train()
    metricas = trainer.evaluate(DocumentoDataset(features_test))
    print("Métricas de prueba:", metricas)

    modelo = trainer.model
    modelo.eval()
    paquete = {
        "tipo": "clasificacion",
        "modelo_base": args.modelo_base,
        "config": modelo.config.to_dict(),
        "etiquetas": etiquetas,
        "max_length": args.max_length,
        "state_dict": {
            clave: valor.detach().cpu() for clave, valor in modelo.state_dict().items()
        },
        "tokenizer_json": tokenizer.backend_tokenizer.to_str(),
        "image_processor_config": image_processor.to_dict(),
        "version": datetime.now(UTC).isoformat(timespec="seconds"),
        "metricas": {clave: float(valor) for clave, valor in metricas.items()},
    }
    destino = args.salida / "modelo.pt"
    torch.save(paquete, destino)

    informe = {
        "generado_en": paquete["version"],
        "modelo_base": args.modelo_base,
        "etiquetas": etiquetas,
        "por_tipo": dict(sorted(Counter(e.tipo for e in ejemplos).items())),
        "split": {
            "train": len(features_train),
            "val": len(features_val),
            "test": len(features_test),
        },
        "metricas": paquete["metricas"],
        "hiperparametros": {
            "epocas": args.epocas,
            "batch": args.batch,
            "lr": args.lr,
            "max_length": args.max_length,
            "balancear": args.balancear,
        },
    }
    (args.salida / "informe.json").write_text(
        json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nModelo guardado en: {destino}")
    print(f"Informe: {args.salida / 'informe.json'}")
    print(
        "Para usarlo, copia modelo.pt a models/ia/clasificacion/ o ajusta "
        "IA_MODELO_CLASIFICACION."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("dataset/ia"),
        help="Carpeta con manifest.jsonl.",
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--raiz", type=Path, default=None)
    parser.add_argument("--salida", type=Path, default=Path("models/ia/v2"))
    parser.add_argument("--cache-ocr", type=Path, default=None)
    parser.add_argument("--modelo-base", default="microsoft/layoutlmv3-base")
    parser.add_argument("--epocas", type=float, default=5)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--psm", type=int, default=3)
    parser.add_argument("--idiomas", default="spa+eng")
    parser.add_argument("--val", type=float, default=0.15)
    parser.add_argument("--test", type=float, default=0.15)
    parser.add_argument("--semilla", type=int, default=42)
    parser.add_argument("--limite", type=int, default=0, help="Máximo de ejemplos (pruebas).")
    parser.add_argument("--balancear", action="store_true")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida el manifiesto y los conteos sin cargar torch.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    manifest = args.manifest or (args.dataset / "manifest.jsonl")
    if not manifest.is_file():
        print(f"No existe el manifiesto: {manifest}", file=sys.stderr)
        return 1
    raiz = args.raiz or manifest.parent
    args.cache_ocr = args.cache_ocr or (args.salida / "cache_ocr")

    ejemplos, faltantes = _leer_manifest(manifest, raiz)
    if args.limite:
        random.Random(args.semilla).shuffle(ejemplos)
        ejemplos = ejemplos[: args.limite]

    _imprimir_resumen(ejemplos, faltantes)
    if args.dry_run:
        return 0
    if not ejemplos:
        print("No hay ejemplos para entrenar.", file=sys.stderr)
        return 1
    return entrenar(args, ejemplos)


if __name__ == "__main__":
    raise SystemExit(main())
