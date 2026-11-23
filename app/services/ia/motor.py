"""Motor de OCR e inferencia LayoutLMv3.

Las importaciones pesadas (torch, transformers, pytesseract, pymupdf) viven
dentro de las funciones para que la app web pueda importar este módulo sin
instalar el stack de IA. El worker carga cada modelo una sola vez y lo reutiliza.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

LOGGER = logging.getLogger("docmun.ia")

SUFIJO_IMAGEN = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
CONFIANZA_MINIMA_OCR = 20.0


class MotorNoDisponible(RuntimeError):
    """Falta Tesseract, un modelo o el archivo no se puede leer."""


@dataclass
class ResultadoMotor:
    texto_ocr: str
    paginas: int
    tipo_clase: str | None = None
    tipo_confianza: float | None = None
    top_clases: list[dict] = field(default_factory=list)
    kie: dict | None = None
    motor: str = "tesseract"
    version_modelo: str | None = None
    duracion_ms: int = 0


@dataclass
class _ModeloCargado:
    modelo: object
    tokenizer: object
    procesador: object
    etiquetas: list[str]
    max_length: int
    version: str


_MODELOS: dict[tuple[str, float], _ModeloCargado] = {}
_VERSIONES: dict[tuple[str, float], str] = {}
_AVISO_MODELOS = False


def modelos_dir() -> Path:
    return Path(settings.ia_modelos_dir).resolve()


def ruta_modelo_clasificacion() -> Path:
    return modelos_dir() / settings.ia_modelo_clasificacion


def ruta_modelo_kie() -> Path:
    return modelos_dir() / settings.ia_modelo_kie


def tesseract_disponible() -> bool:
    if shutil.which("tesseract"):
        return True
    for candidato in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ):
        if os.path.exists(candidato):
            return True
    return False


def modelos_disponibles() -> bool:
    return ruta_modelo_clasificacion().is_file() and ruta_modelo_kie().is_file()


def disponible() -> bool:
    """Indica si el worker puede procesar al menos el OCR."""
    return settings.ia_habilitada and tesseract_disponible()


def estado_motor() -> dict[str, object]:
    """Diagnóstico sin cargar los modelos (para CLI y logs de arranque)."""
    return {
        "habilitada": settings.ia_habilitada,
        "tesseract": tesseract_disponible(),
        "modelos": modelos_disponibles(),
        "clasificacion": str(ruta_modelo_clasificacion()),
        "kie": str(ruta_modelo_kie()),
        "dispositivo": settings.ia_dispositivo,
    }


def _configurar_tesseract() -> None:
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


def _version_archivo(ruta: Path) -> str:
    clave = (str(ruta), ruta.stat().st_mtime)
    if clave in _VERSIONES:
        return _VERSIONES[clave]
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    version = digest.hexdigest()[:12]
    _VERSIONES[clave] = version
    return version


def _cargar_paquete(ruta: Path) -> dict:
    import torch

    try:
        # Preferimos el modo seguro: el paquete propio solo tiene dicts, listas
        # y tensores. Si falla, el archivo viene de un formato antiguo.
        return torch.load(ruta, map_location="cpu", weights_only=True)
    except Exception:
        LOGGER.warning("Paquete %s requiere carga pickle (weights_only=False)", ruta.name)
        return torch.load(ruta, map_location="cpu", weights_only=False)


def _construir_tokenizer(paquete: dict):
    from transformers import LayoutLMv3TokenizerFast

    descriptor, ruta = tempfile.mkstemp(suffix=".json")
    os.close(descriptor)
    try:
        Path(ruta).write_text(paquete["tokenizer_json"], encoding="utf-8")
        return LayoutLMv3TokenizerFast(
            tokenizer_file=ruta,
            model_max_length=int(paquete.get("max_length", 512)),
        )
    finally:
        os.unlink(ruta)


def _construir_imagen_proc(paquete: dict):
    from transformers import LayoutLMv3ImageProcessor

    procesador = LayoutLMv3ImageProcessor.from_dict(paquete["image_processor_config"])
    procesador.apply_ocr = False
    return procesador


def _dispositivo():
    import torch

    if settings.ia_dispositivo.lower().startswith("cuda") and torch.cuda.is_available():
        return torch.device(settings.ia_dispositivo)
    return torch.device("cpu")


def _cargar_modelo(ruta: Path) -> _ModeloCargado:
    if not ruta.is_file():
        raise MotorNoDisponible(f"No existe el modelo {ruta}")

    clave = (str(ruta), ruta.stat().st_mtime)
    if clave in _MODELOS:
        return _MODELOS[clave]

    import torch
    from transformers import (
        LayoutLMv3Config,
        LayoutLMv3ForSequenceClassification,
        LayoutLMv3ForTokenClassification,
    )

    torch.set_num_threads(max(1, settings.ia_hilos))
    paquete = _cargar_paquete(ruta)

    config = LayoutLMv3Config.from_dict(paquete["config"])
    if paquete.get("tipo") == "kie":
        modelo = LayoutLMv3ForTokenClassification(config)
    else:
        modelo = LayoutLMv3ForSequenceClassification(config)
    modelo.load_state_dict(paquete["state_dict"])
    modelo.eval()

    cargado = _ModeloCargado(
        modelo=modelo,
        tokenizer=_construir_tokenizer(paquete),
        procesador=_construir_imagen_proc(paquete),
        etiquetas=list(paquete.get("etiquetas", [])),
        max_length=int(paquete.get("max_length", 512)),
        version=f"{paquete.get('modelo_base', 'layoutlmv3')}-{_version_archivo(ruta)}",
    )
    # Liberar los pesos duplicados; el modelo ya tiene su propia copia.
    paquete.pop("state_dict", None)
    _MODELOS[clave] = cargado
    LOGGER.info("Modelo cargado: %s (%s)", ruta.name, cargado.version)
    return cargado


def _cargar_imagenes(ruta: Path) -> list:
    from PIL import Image, ImageOps

    if not ruta.is_file():
        raise MotorNoDisponible("El archivo del análisis no existe.")

    if ruta.suffix.lower() == ".pdf":
        import pymupdf

        documento = pymupdf.open(str(ruta))
        try:
            total = min(documento.page_count, max(1, settings.ia_max_paginas))
            imagenes = []
            for indice in range(total):
                pagina = documento.load_page(indice)
                pix = pagina.get_pixmap(dpi=settings.ia_dpi)
                imagenes.append(
                    Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                )
            return imagenes
        finally:
            documento.close()

    if ruta.suffix.lower() not in SUFIJO_IMAGEN:
        raise MotorNoDisponible(f"Formato no soportado: {ruta.suffix}")

    with Image.open(ruta) as original:
        return [ImageOps.exif_transpose(original).convert("RGB")]


def _caja_normalizada(caja, ancho: int, alto: int) -> list[int]:
    x0, y0, x1, y1 = (float(valor) for valor in caja)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))

    def escalar(valor: float, tamano: int) -> int:
        return int(max(0, min(1000, round(1000.0 * valor / max(tamano, 1)))))

    return [escalar(x0, ancho), escalar(y0, alto), escalar(x1, ancho), escalar(y1, alto)]


def _leer_ocr(imagen, idioma: str):
    import pytesseract
    from pytesseract import Output

    return pytesseract.image_to_data(
        imagen,
        lang=idioma,
        config=f"--psm {settings.ia_ocr_psm}",
        output_type=Output.DICT,
    )


def _ocr(imagen) -> tuple[list[str], list[list[int]], str]:
    import pytesseract

    _configurar_tesseract()
    try:
        datos = _leer_ocr(imagen, settings.ia_ocr_idiomas)
    except pytesseract.TesseractNotFoundError as exc:
        raise MotorNoDisponible(
            "Tesseract no está instalado o no está en el PATH."
        ) from exc
    except pytesseract.TesseractError:
        # Si falta el paquete de español, no perdemos el OCR completo.
        datos = _leer_ocr(imagen, "eng")

    ancho, alto = imagen.size
    palabras: list[str] = []
    cajas: list[list[int]] = []
    lineas: dict[tuple[int, int, int, int], list[str]] = {}

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

        palabras.append(texto)
        cajas.append(
            _caja_normalizada(
                [x, y, x + ancho_palabra, y + alto_palabra], ancho, alto
            )
        )
        clave = (
            int(datos["page_num"][indice]),
            int(datos["block_num"][indice]),
            int(datos["par_num"][indice]),
            int(datos["line_num"][indice]),
        )
        lineas.setdefault(clave, []).append(texto)

    texto_pagina = "\n".join(" ".join(partes) for partes in lineas.values())
    return palabras, cajas, texto_pagina


def _predecir_clasificacion(modelo: _ModeloCargado, imagen, palabras, cajas) -> dict:
    import torch

    dispositivo = _dispositivo()
    modelo.modelo.to(dispositivo)
    codificacion = modelo.tokenizer(
        palabras,
        boxes=cajas,
        truncation=True,
        max_length=modelo.max_length,
        return_tensors="pt",
    )
    entradas = {
        clave: valor.to(dispositivo)
        for clave, valor in codificacion.items()
        if clave in {"input_ids", "attention_mask", "bbox"}
    }
    pixeles = modelo.procesador([imagen], return_tensors="pt")["pixel_values"].to(
        dispositivo
    )

    with torch.inference_mode():
        logits = modelo.modelo(pixel_values=pixeles, **entradas).logits[0]

    probabilidades = torch.softmax(logits, dim=-1)
    valores, indices = torch.topk(probabilidades, k=min(3, len(modelo.etiquetas)))
    top = [
        {"clase": modelo.etiquetas[int(indice)], "probabilidad": round(float(valor), 4)}
        for valor, indice in zip(valores, indices, strict=False)
    ]
    return {"clase": top[0]["clase"], "probabilidad": top[0]["probabilidad"], "top": top}


def _agrupar(palabras: list[str], etiquetas: list[str]) -> list[dict[str, str]]:
    grupos: list[dict[str, object]] = []
    actual: dict[str, object] | None = None
    for palabra, etiqueta in zip(palabras, etiquetas, strict=False):
        if etiqueta == "O":
            actual = None
            continue
        prefijo, tipo = etiqueta.split("-", 1)
        if prefijo == "B" or actual is None or actual["tipo"] != tipo:
            actual = {"tipo": tipo, "palabras": [palabra]}
            grupos.append(actual)
        else:
            actual["palabras"].append(palabra)  # type: ignore[union-attr]
    return [
        {"tipo": str(grupo["tipo"]), "texto": " ".join(grupo["palabras"]).strip()}
        for grupo in grupos
    ]


def _predecir_kie(modelo: _ModeloCargado, imagen, palabras, cajas) -> dict:
    import torch

    dispositivo = _dispositivo()
    modelo.modelo.to(dispositivo)
    ventana = max(1, modelo.max_length - 2)
    pixeles = modelo.procesador([imagen], return_tensors="pt")["pixel_values"].to(
        dispositivo
    )
    predicciones: list[str] = []

    for inicio in range(0, len(palabras), ventana):
        trozo_palabras = palabras[inicio : inicio + ventana]
        trozo_cajas = cajas[inicio : inicio + ventana]
        codificacion = modelo.tokenizer(
            trozo_palabras,
            boxes=trozo_cajas,
            truncation=True,
            max_length=modelo.max_length,
            return_tensors="pt",
        )
        ids_palabra = codificacion.word_ids()
        entradas = {
            clave: valor.to(dispositivo)
            for clave, valor in codificacion.items()
            if clave in {"input_ids", "attention_mask", "bbox"}
        }
        with torch.inference_mode():
            logits = modelo.modelo(pixel_values=pixeles, **entradas).logits[0]

        primera_posicion: dict[int, int] = {}
        for posicion, id_palabra in enumerate(ids_palabra):
            if id_palabra is not None and id_palabra not in primera_posicion:
                primera_posicion[id_palabra] = posicion

        for indice in range(len(trozo_palabras)):
            posicion = primera_posicion.get(indice)
            predicciones.append(
                "O" if posicion is None else modelo.etiquetas[int(logits[posicion].argmax())]
            )

    grupos = _agrupar(palabras, predicciones)
    campos = {
        "HEADER": [g["texto"] for g in grupos if g["tipo"] == "HEADER"],
        "QUESTION": [g["texto"] for g in grupos if g["tipo"] == "QUESTION"],
        "ANSWER": [g["texto"] for g in grupos if g["tipo"] == "ANSWER"],
    }
    pares = []
    pregunta = None
    for grupo in grupos:
        if grupo["tipo"] == "QUESTION":
            pregunta = grupo["texto"]
        elif grupo["tipo"] == "ANSWER" and pregunta:
            pares.append({"pregunta": pregunta, "respuesta": grupo["texto"]})
            pregunta = None
    return {"campos": campos, "pares": pares}


def analizar_archivo(ruta: str | Path, *, usar_modelos: bool = True) -> ResultadoMotor:
    """Ejecuta OCR + clasificación + KIE sobre las primeras páginas.

    El OCR es obligatorio; si falta un modelo, se devuelve igualmente el texto
    para que las reglas puedan sugerir el tipo y los campos.
    """
    global _AVISO_MODELOS

    if not settings.ia_habilitada:
        raise MotorNoDisponible("El análisis IA está deshabilitado.")

    inicio = time.monotonic()
    imagenes = _cargar_imagenes(Path(ruta))
    if not imagenes:
        raise MotorNoDisponible("El archivo no contiene páginas legibles.")

    textos: list[str] = []
    primera_imagen = None
    primeras_palabras: list[str] = []
    primeras_cajas: list[list[int]] = []

    for indice, imagen in enumerate(imagenes):
        palabras, cajas, texto = _ocr(imagen)
        textos.append(texto)
        if indice == 0:
            primera_imagen = imagen
            primeras_palabras = palabras
            primeras_cajas = cajas

    texto_ocr = "\n\n".join(texto for texto in textos if texto).strip()
    resultado = ResultadoMotor(
        texto_ocr=texto_ocr,
        paginas=len(imagenes),
        motor="tesseract",
        version_modelo="ocr",
    )

    if usar_modelos and modelos_disponibles() and primera_imagen is not None and primeras_palabras:
        try:
            clasificacion = _cargar_modelo(ruta_modelo_clasificacion())
            prediccion = _predecir_clasificacion(
                clasificacion, primera_imagen, primeras_palabras, primeras_cajas
            )
            resultado.tipo_clase = prediccion["clase"]
            resultado.tipo_confianza = prediccion["probabilidad"]
            resultado.top_clases = prediccion["top"]
            resultado.version_modelo = clasificacion.version
            resultado.motor = "tesseract+layoutlmv3"
        except Exception as exc:  # un modelo roto no debe tumbar el OCR
            LOGGER.warning("Falló la clasificación LayoutLMv3: %s", type(exc).__name__)

        try:
            kie = _cargar_modelo(ruta_modelo_kie())
            resultado.kie = _predecir_kie(
                kie, primera_imagen, primeras_palabras, primeras_cajas
            )
            if resultado.version_modelo in (None, "ocr"):
                resultado.version_modelo = kie.version
        except Exception as exc:
            LOGGER.warning("Falló el KIE LayoutLMv3: %s", type(exc).__name__)
    elif not _AVISO_MODELOS:
        _AVISO_MODELOS = True
        LOGGER.info(
            "Modelos LayoutLMv3 no encontrados en %s; se procesará solo con OCR.",
            modelos_dir(),
        )

    resultado.duracion_ms = int((time.monotonic() - inicio) * 1000)
    return resultado
