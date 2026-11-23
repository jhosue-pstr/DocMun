"""Importa el consolidado Excel como documentos históricos.

El archivo fuente contiene datos personales y está fuera de git. Este script
solo debe ejecutarse en entornos locales o controlados.

Uso:
    python -m scripts.importar_historico --dry-run
    python -m scripts.importar_historico
    python -m scripts.importar_historico --limite 20 --lote 50
    python -m scripts.importar_historico --reporte /tmp/reporte.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.catalogos import TipoTramite
from app.models.documentos import Documento
from app.models.enums import EstadoDocumento, OrigenDocumento, TipoDocumentoIdentidad
from app.services.ciudadanos import DatosCiudadano, obtener_o_crear
from app.services.documentos import (
    CodigoNoDisponible,
    DatosDocumento,
    DocumentoDuplicado,
    DocumentoInvalido,
    crear_documento,
)

ARCHIVO_POR_DEFECTO = Path("docs/fuentes/Consolidado_certificados_municipales_todos_los_ZIP.xlsx")
HOJA = "Base consolidada"
ZONA = ZoneInfo("America/Lima")
ANIO_MIN = 1900
ANIO_MAX = 2100
PATRON_ANIO = re.compile(r"(?<!\d)(\d{4})(?!\d)")
MAX_MUESTRA_ERRORES = 50


@dataclass(frozen=True)
class FilaHistorica:
    indice: int
    area: str
    numero_certificado: str
    anio: int
    fecha_inicio: datetime | None
    fecha_salida: datetime | None
    fuente_numero: str | None
    rut: str | None
    expediente: str | None
    ruc: str | None
    referencia: str | None
    administrado: str | None
    observaciones: str | None
    archivo_origen: str | None
    anio_inferido: bool = False


@dataclass
class Descartes:
    """Cuenta las filas descartadas y conserva una muestra de sus índices."""

    motivos: dict[str, int] = field(default_factory=dict)
    filas: dict[str, list[int]] = field(default_factory=dict)

    def anotar(self, motivo: str, indice: int) -> None:
        self.motivos[motivo] = self.motivos.get(motivo, 0) + 1
        muestra = self.filas.setdefault(motivo, [])
        if len(muestra) < MAX_MUESTRA_ERRORES:
            muestra.append(indice)

    @property
    def total(self) -> int:
        return sum(self.motivos.values())


def normalizar(texto: str) -> str:
    sin_tildes = "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caracter)
    )
    return re.sub(r"\s+", " ", sin_tildes).strip().upper()


def texto(valor: object) -> str | None:
    if valor is None:
        return None
    limpio = str(valor).strip()
    return limpio or None


def recortar(valor: str | None, largo: int) -> str | None:
    if valor is None:
        return None
    return valor[:largo]


def extraer_anio(numero: str) -> int | None:
    coincidencias = PATRON_ANIO.findall(numero)
    for candidato in reversed(coincidencias):
        anio = int(candidato)
        if ANIO_MIN <= anio <= ANIO_MAX:
            return anio
    return None


def como_fecha_lima(valor: object) -> datetime | None:
    if not isinstance(valor, datetime):
        return None
    if valor.tzinfo is None:
        return valor.replace(tzinfo=ZONA)
    return valor.astimezone(ZONA)


def mapa_tipos(db: Session) -> dict[str, TipoTramite]:
    tipos = db.scalars(select(TipoTramite).where(TipoTramite.activo.is_(True))).all()
    return {normalizar(tipo.nombre): tipo for tipo in tipos}


def leer_filas(
    archivo: Path, tipos: dict[str, TipoTramite]
) -> tuple[list[FilaHistorica], Descartes]:
    libro = load_workbook(archivo, read_only=True, data_only=True)
    if HOJA not in libro.sheetnames:
        raise SystemExit(f"El archivo no contiene la hoja {HOJA!r}.")
    hoja = libro[HOJA]

    filas: list[FilaHistorica] = []
    descartes = Descartes()
    for indice, fila in enumerate(hoja.iter_rows(min_row=2, values_only=True), start=2):
        area = texto(fila[1])
        numero = texto(fila[2])
        if not area:
            descartes.anotar("sin_area", indice)
            continue
        if not numero:
            descartes.anotar("sin_numero", indice)
            continue
        if normalizar(area) not in tipos:
            descartes.anotar("area_no_mapeada", indice)
            continue

        archivo_origen = texto(fila[17])
        anio = extraer_anio(numero)
        anio_inferido = False
        if anio is None and archivo_origen:
            # Algunas filas traen solo el número correlativo ("073") y el año
            # aparece en el nombre del archivo de origen.
            anio = extraer_anio(archivo_origen)
            anio_inferido = anio is not None
        if anio is None:
            descartes.anotar("sin_anio", indice)
            continue

        filas.append(
            FilaHistorica(
                indice=indice,
                area=area,
                numero_certificado=numero,
                anio=anio,
                fecha_inicio=como_fecha_lima(fila[9]),
                fecha_salida=como_fecha_lima(fila[10]),
                fuente_numero=texto(fila[3]),
                rut=texto(fila[4]),
                expediente=texto(fila[5]),
                ruc=texto(fila[6]),
                referencia=texto(fila[7]),
                administrado=texto(fila[8]),
                observaciones=texto(fila[15]),
                archivo_origen=archivo_origen,
                anio_inferido=anio_inferido,
            )
        )

    libro.close()
    return filas, descartes


def elegir_muestra(
    filas: list[FilaHistorica],
    tipos: dict[str, TipoTramite],
    limite: int,
) -> list[FilaHistorica]:
    """Reparte la muestra entre todas las áreas antes de repetir alguna."""
    por_tipo: dict[int, list[FilaHistorica]] = defaultdict(list)
    for fila in filas:
        tipo = tipos[normalizar(fila.area)]
        por_tipo[tipo.id].append(fila)

    orden_tipos = [tipo.id for tipo in tipos.values() if por_tipo.get(tipo.id)]
    orden_tipos.sort(key=lambda tipo_id: (-len(por_tipo[tipo_id]), tipo_id))

    elegidas: list[FilaHistorica] = []
    vueltas = 0
    while len(elegidas) < limite and vueltas < max((len(v) for v in por_tipo.values()), default=0):
        for tipo_id in orden_tipos:
            if len(elegidas) >= limite:
                break
            candidatas = por_tipo[tipo_id]
            if vueltas < len(candidatas):
                elegidas.append(candidatas[vueltas])
        vueltas += 1
    return elegidas


def ruc_valido(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpio = re.sub(r"\D", "", valor)
    return limpio if len(limpio) == 11 else None


def crear_ciudadano(db: Session, fila: FilaHistorica) -> int | None:
    ruc = ruc_valido(fila.ruc)
    if not ruc or not fila.administrado:
        return None
    ciudadano, _ = obtener_o_crear(
        db,
        DatosCiudadano(
            tipo_documento=TipoDocumentoIdentidad.RUC,
            numero_documento=ruc,
            nombres=fila.administrado,
        ),
    )
    return ciudadano.id


def fecha_registro_confiable(fila: FilaHistorica) -> tuple[datetime | None, bool]:
    """Devuelve la fecha de registro y si fue necesario corregirla.

    Una fecha de inicio posterior a la emisión no es confiable: en ese caso se
    usa la fecha de emisión. Si no hay inicio, también se usa la emisión.
    """
    if fila.fecha_inicio is None:
        return fila.fecha_salida, True
    if fila.fecha_salida is not None and fila.fecha_inicio > fila.fecha_salida:
        return fila.fecha_salida, True
    return fila.fecha_inicio, False


def claves_documentos(
    filas: list[FilaHistorica], tipos: dict[str, TipoTramite]
) -> list[tuple[int, str, int]]:
    return [(tipos[normalizar(fila.area)].id, fila.numero_certificado, fila.anio) for fila in filas]


def resumir_seleccion(
    db: Session, filas: list[FilaHistorica], tipos: dict[str, TipoTramite]
) -> dict[str, int]:
    """Calcula qué se crearía sin escribir en la base (para el dry-run)."""
    claves = claves_documentos(filas, tipos)
    unicas = set(claves)

    existentes: set[tuple[int, str, int]] = set()
    if unicas:
        stmt = select(
            Documento.tipo_tramite_id,
            Documento.numero_certificado,
            Documento.anio,
        ).where(Documento.numero_certificado.is_not(None))
        for tipo_id, numero, anio in db.execute(stmt):
            clave = (tipo_id, numero, anio)
            if clave in unicas:
                existentes.add(clave)

    por_crear = unicas - existentes
    return {
        "seleccionadas": len(filas),
        "claves_unicas": len(unicas),
        "duplicadas_en_archivo": len(filas) - len(unicas),
        "ya_en_bd": len(existentes),
        "por_crear": len(por_crear),
    }


def importar(
    db: Session,
    filas: list[FilaHistorica],
    tipos: dict[str, TipoTramite],
    *,
    lote: int = 200,
    max_errores: int = 500,
) -> dict[str, object]:
    """Crea los documentos históricos por lotes, sin abortar por una fila mala."""
    resultado: dict[str, object] = {
        "creados": 0,
        "duplicados": 0,
        "invalidos": 0,
        "fechas_corregidas": 0,
        "sin_fechas": 0,
        "errores": [],
    }
    errores: list[dict[str, object]] = resultado["errores"]  # type: ignore[assignment]
    pendientes = 0

    def anotar_error(indice: int, motivo: str) -> None:
        if len(errores) < max_errores:
            errores.append({"fila": indice, "motivo": motivo})

    def anotar(clave: str) -> None:
        resultado[clave] = int(resultado[clave]) + 1

    for fila in filas:
        tipo = tipos[normalizar(fila.area)]
        fecha_registro, corregida = fecha_registro_confiable(fila)
        if corregida:
            anotar("fechas_corregidas")
        if fecha_registro is None:
            anotar("sin_fechas")

        try:
            with db.begin_nested():
                ciudadano_id = crear_ciudadano(db, fila)
                crear_documento(
                    db,
                    DatosDocumento(
                        tipo_tramite_id=tipo.id,
                        numero_certificado=fila.numero_certificado,
                        anio=fila.anio,
                        estado=EstadoDocumento.ARCHIVADO,
                        origen=OrigenDocumento.HISTORICO,
                        ciudadano_id=ciudadano_id,
                        oficina_actual_id=tipo.oficina_responsable_id,
                        fecha_registro=fecha_registro,
                        fecha_finalizacion=fila.fecha_salida,
                        fuente_numero=recortar(fila.fuente_numero, 50),
                        rut=recortar(fila.rut, 30),
                        expediente_administrativo=recortar(fila.expediente, 50),
                        ruc=recortar(fila.ruc, 20),
                        referencia=recortar(fila.referencia, 200),
                        administrado=recortar(fila.administrado, 200),
                        asunto=recortar(tipo.nombre, 300),
                        observaciones_importacion=recortar(fila.observaciones, 1000),
                        archivo_origen=recortar(fila.archivo_origen, 500),
                    ),
                    commit=False,
                )
        except DocumentoDuplicado:
            anotar("duplicados")
            anotar_error(fila.indice, "duplicado")
            continue
        except (DocumentoInvalido, CodigoNoDisponible) as exc:
            anotar("invalidos")
            anotar_error(fila.indice, str(exc))
            continue

        anotar("creados")
        pendientes += 1
        if pendientes >= lote:
            db.commit()
            pendientes = 0

    if pendientes:
        db.commit()

    return resultado


def escribir_reporte(reporte: dict[str, object], ruta: Path | None = None) -> Path:
    if ruta is None:
        base = Path(settings.storage_dir) / "reportes"
        marca = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        ruta = base / f"importacion-{marca}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def main(argv: list[str] | None = None, *, limite_por_defecto: int = 0) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archivo", type=Path, default=ARCHIVO_POR_DEFECTO)
    parser.add_argument(
        "--limite",
        type=int,
        default=limite_por_defecto,
        help="Máximo de filas a procesar; 0 importa todas.",
    )
    parser.add_argument("--lote", type=int, default=200, help="Filas por commit.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reporte", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.archivo.is_file():
        raise SystemExit(f"No se encontró el archivo: {args.archivo}")
    if args.limite < 0:
        raise SystemExit("--limite no puede ser negativo.")
    if args.lote < 1:
        raise SystemExit("--lote debe ser mayor que cero.")

    db = SessionLocal()
    try:
        tipos = mapa_tipos(db)
        if not tipos:
            raise SystemExit("No hay tipos de trámite. Ejecuta primero scripts.seed.")

        filas, descartes = leer_filas(args.archivo, tipos)
        seleccion = filas if args.limite == 0 else elegir_muestra(filas, tipos, args.limite)
        resumen = resumir_seleccion(db, seleccion, tipos)
        anios_inferidos = sum(1 for fila in seleccion if fila.anio_inferido)

        print(
            f"Filas válidas: {len(filas)} | descartadas: {descartes.total} | "
            f"seleccionadas: {len(seleccion)} | años inferidos: {anios_inferidos}"
        )
        if descartes.motivos:
            detalle = ", ".join(f"{k}={v}" for k, v in sorted(descartes.motivos.items()))
            print(f"Descartes: {detalle}")
        print(
            "Resumen: "
            f"{resumen['claves_unicas']} claves únicas, "
            f"{resumen['duplicadas_en_archivo']} repetidas en el archivo, "
            f"{resumen['ya_en_bd']} ya en la base, "
            f"{resumen['por_crear']} por crear."
        )
        for fila in seleccion[:10]:
            tipo = tipos[normalizar(fila.area)]
            print(f"  · fila {fila.indice} | {tipo.codigo} | {fila.numero_certificado}")

        resultado = None
        if args.dry_run:
            print("Dry-run: no se escribió ningún documento.")
        else:
            resultado = importar(db, seleccion, tipos, lote=args.lote)
            print(
                "Importación terminada: "
                f"{resultado['creados']} creados, "
                f"{resultado['duplicados']} duplicados, "
                f"{resultado['invalidos']} inválidos, "
                f"{resultado['fechas_corregidas']} fechas corregidas, "
                f"{resultado['sin_fechas']} sin fechas."
            )

        reporte = {
            "generado_en": datetime.now(UTC).isoformat(),
            "archivo": args.archivo.name,
            "hoja": HOJA,
            "dry_run": args.dry_run,
            "limite": args.limite,
            "lote": args.lote,
            "filas": {
                "validas": len(filas),
                "seleccionadas": len(seleccion),
                "anios_inferidos": anios_inferidos,
                "descartadas": descartes.motivos,
                "descartadas_muestra": descartes.filas,
            },
            "resumen": resumen,
            "resultado": resultado,
        }
        ruta = escribir_reporte(reporte, args.reporte)
        print(f"Reporte: {ruta}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
