"""Pruebas del importador histórico con un workbook temporal."""

from datetime import date, datetime

from openpyxl import Workbook
from sqlalchemy import func, select

from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento, Evento
from app.models.enums import EstadoDocumento, OrigenDocumento, TipoEvento
from app.services.documentos import DatosDocumento, crear_documento
from scripts.importar_historico import (
    HOJA,
    Descartes,
    FilaHistorica,
    escribir_reporte,
    fecha_registro_confiable,
    importar,
    leer_filas,
    mapa_tipos,
    normalizar,
    resumir_seleccion,
)

COLUMNAS = [
    "N°",
    "Área",
    "Número de certificado",
    "Fuente",
    "RUT",
    "Expediente",
    "RUC",
    "Referencia",
    "Administrado",
    "Fecha inicio",
    "Fecha salida",
    "Días",
    "Año",
    "Mes",
    "Estado",
    "Observaciones",
    "Tipo archivo",
    "Archivo de origen",
]


def _fila(
    area: str | None = "Área de Pruebas",
    numero: str | None = "123-2024",
    inicio: datetime | None = None,
    salida: datetime | None = None,
    administrado: str | None = None,
    ruc: str | None = None,
    observaciones: str | None = None,
    archivo: str = "certificado.pdf",
) -> list[object | None]:
    valores: list[object | None] = [None] * len(COLUMNAS)
    valores[1] = area
    valores[2] = numero
    valores[8] = administrado
    valores[9] = inicio
    valores[10] = salida
    valores[6] = ruc
    valores[15] = observaciones
    valores[17] = archivo
    return valores


def _workbook(tmp_path, filas: list[list[object | None]]):
    libro = Workbook()
    hoja = libro.active
    hoja.title = HOJA
    hoja.append(COLUMNAS)
    for fila in filas:
        hoja.append(fila)
    ruta = tmp_path / "historico.xlsx"
    libro.save(ruta)
    libro.close()
    return ruta


def _catalogo(db) -> tuple[Oficina, TipoTramite]:
    oficina = Oficina(codigo="OF-IMP", nombre="Oficina de Importación", activa=True)
    db.add(oficina)
    db.commit()
    db.refresh(oficina)

    tipo = TipoTramite(
        codigo="TR-IMP",
        nombre="Área de Pruebas",
        area="Pruebas",
        oficina_responsable_id=oficina.id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)
    return oficina, tipo


def _filas_importadas(tmp_path, db) -> tuple[list[FilaHistorica], dict[str, TipoTramite]]:
    ruta = _workbook(
        tmp_path,
        [
            _fila(area="AREA DE PRUEBAS", numero="123-2024"),
            _fila(area="Área de Pruebas", numero="456-2024"),
        ],
    )
    tipos = mapa_tipos(db)
    filas, _ = leer_filas(ruta, tipos)
    return filas, tipos


def test_leer_filas_mapea_y_descarta(tmp_path, db):
    _catalogo(db)
    ruta = _workbook(
        tmp_path,
        [
            _fila(
                area="AREA DE PRUEBAS",
                numero="123-2024",
                inicio=datetime(2024, 1, 2, 10, 0),
                salida=datetime(2024, 1, 5, 10, 0),
                administrado="Empresa X",
                ruc="20123456789",
            ),
            _fila(area="Área fantasma", numero="456-2024"),
            _fila(area="Área de Pruebas", numero=None),
            _fila(area="Área de Pruebas", numero="SIN-ANIO"),
        ],
    )

    filas, descartes = leer_filas(ruta, mapa_tipos(db))

    assert len(filas) == 1
    fila = filas[0]
    assert fila.indice == 2
    assert fila.numero_certificado == "123-2024"
    assert fila.anio == 2024
    assert fila.fecha_inicio is not None
    assert fila.fecha_inicio.tzinfo is not None
    assert fila.administrado == "Empresa X"
    assert descartes.motivos == {
        "area_no_mapeada": 1,
        "sin_numero": 1,
        "sin_anio": 1,
    }
    assert descartes.filas["sin_anio"] == [5]
    assert descartes.total == 3


def test_anio_se_infiere_del_archivo_origen(tmp_path, db):
    _catalogo(db)
    ruta = _workbook(
        tmp_path,
        [
            _fila(
                numero="073",
                archivo="CERTIFICADO DE NUMERACION MUNICIPAL/073 EMPRESA 57716-2025 N.M..docx",
            )
        ],
    )

    filas, descartes = leer_filas(ruta, mapa_tipos(db))

    assert descartes.total == 0
    assert len(filas) == 1
    assert filas[0].anio == 2025
    assert filas[0].anio_inferido is True


def test_importar_crea_documentos_archivados(tmp_path, db):
    oficina, tipo = _catalogo(db)
    ruta = _workbook(
        tmp_path,
        [
            _fila(
                numero="123-2024",
                inicio=datetime(2024, 1, 2, 10, 0),
                salida=datetime(2024, 1, 5, 10, 0),
                administrado="Empresa X",
                ruc="20123456789",
                observaciones="Importado de prueba",
            ),
            _fila(numero="456-2024", salida=datetime(2024, 2, 1, 9, 0)),
        ],
    )
    filas, _ = leer_filas(ruta, mapa_tipos(db))

    resultado = importar(db, filas, mapa_tipos(db), lote=1)

    assert resultado["creados"] == 2
    assert resultado["duplicados"] == 0
    assert resultado["invalidos"] == 0

    documentos = db.scalars(select(Documento).order_by(Documento.id)).all()
    assert len(documentos) == 2
    primero = documentos[0]
    assert primero.estado == EstadoDocumento.ARCHIVADO
    assert primero.origen == OrigenDocumento.HISTORICO
    assert primero.oficina_actual_id == oficina.id
    assert primero.tipo_tramite_id == tipo.id
    assert primero.asunto == tipo.nombre
    assert primero.fecha_finalizacion is not None
    assert primero.observaciones_importacion == "Importado de prueba"
    assert primero.ciudadano is not None
    assert primero.ciudadano.numero_documento == "20123456789"
    assert primero.ciudadano.nombres == "Empresa X"

    tipos_evento = set(
        db.scalars(select(Evento.tipo).where(Evento.documento_id == primero.id)).all()
    )
    assert tipos_evento == {TipoEvento.CREADO}


def test_importar_es_idempotente(tmp_path, db):
    _catalogo(db)
    filas, tipos = _filas_importadas(tmp_path, db)

    primero = importar(db, filas, tipos)
    segundo = importar(db, filas, tipos)

    assert primero["creados"] == 2
    assert segundo["creados"] == 0
    assert segundo["duplicados"] == 2
    assert db.scalar(select(func.count()).select_from(Documento)) == 2


def test_duplicado_en_archivo_no_aborta_el_lote(tmp_path, db):
    _catalogo(db)
    ruta = _workbook(
        tmp_path,
        [
            _fila(numero="111-2024"),
            _fila(numero="222-2024"),
            _fila(numero="222-2024"),
            _fila(numero="333-2024"),
        ],
    )
    filas, _ = leer_filas(ruta, mapa_tipos(db))

    resultado = importar(db, filas, mapa_tipos(db), lote=2)

    assert resultado["creados"] == 3
    assert resultado["duplicados"] == 1
    assert resultado["errores"] == [{"fila": 4, "motivo": "duplicado"}]
    assert db.scalar(select(func.count()).select_from(Documento)) == 3


def test_fecha_inicio_posterior_a_emision_usa_emision(tmp_path, db):
    _catalogo(db)
    ruta = _workbook(
        tmp_path,
        [
            _fila(
                numero="123-2024",
                inicio=datetime(2024, 5, 10, 10, 0),
                salida=datetime(2024, 5, 1, 10, 0),
            )
        ],
    )
    filas, _ = leer_filas(ruta, mapa_tipos(db))

    resultado = importar(db, filas, mapa_tipos(db))

    assert resultado["fechas_corregidas"] == 1
    documento = db.scalar(select(Documento))
    assert documento is not None
    assert documento.fecha_registro.date() == date(2024, 5, 1)


def test_fecha_registro_confiable_sin_fechas():
    sin_fechas = FilaHistorica(
        indice=2,
        area="Área de Pruebas",
        numero_certificado="123-2024",
        anio=2024,
        fecha_inicio=None,
        fecha_salida=None,
        fuente_numero=None,
        rut=None,
        expediente=None,
        ruc=None,
        referencia=None,
        administrado=None,
        observaciones=None,
        archivo_origen=None,
    )
    assert fecha_registro_confiable(sin_fechas) == (None, True)


def test_resumir_seleccion_detecta_duplicados_y_existentes(tmp_path, db):
    _, tipo = _catalogo(db)
    crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo.id,
            numero_certificado="222-2024",
            anio=2024,
            asunto="Ya existe",
        ),
    )
    db.commit()

    ruta = _workbook(
        tmp_path,
        [
            _fila(numero="111-2024"),
            _fila(numero="222-2024"),
            _fila(numero="222-2024"),
        ],
    )
    tipos = mapa_tipos(db)
    filas, _ = leer_filas(ruta, tipos)

    resumen = resumir_seleccion(db, filas, tipos)

    assert resumen == {
        "seleccionadas": 3,
        "claves_unicas": 2,
        "duplicadas_en_archivo": 1,
        "ya_en_bd": 1,
        "por_crear": 1,
    }
    assert db.scalar(select(func.count()).select_from(Documento)) == 1


def test_escribir_reporte_json(tmp_path):
    ruta = escribir_reporte({"creados": 1, "detalle": ["á"]}, tmp_path / "reporte.json")

    assert ruta.is_file()
    contenido = ruta.read_text(encoding="utf-8")
    assert '"creados": 1' in contenido
    assert "á" in contenido


def test_descartes_limita_la_muestra_de_indices():
    descartes = Descartes()
    for indice in range(2, 80):
        descartes.anotar("sin_anio", indice)

    assert descartes.motivos["sin_anio"] == 78
    assert len(descartes.filas["sin_anio"]) == 50
    assert normalizar("  Área   de Pruebas ") == "AREA DE PRUEBAS"
