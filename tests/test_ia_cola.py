"""Pruebas de la cola de análisis: encolado, motor simulado y enlace."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.analisis import DocumentoAnalisis
from app.models.archivos import DocumentoArchivo
from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento, Evento
from app.models.enums import EstadoAnalisis, TipoEvento
from app.services.archivos import ArchivoValidado, guardar_validado
from app.services.documentos import DatosDocumento, crear_documento
from app.services.ia import cola, motor

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
OTRO_PDF = PDF + b"%" * 10
TEXTO = (
    "MUNICIPALIDAD PROVINCIAL\nCARTA N° 045-2026-MP\n"
    "ASUNTO: Solicitud de certificado de numeración municipal\n"
    "SEÑOR ALCALDE:\nYo, Ana Pérez, identificada con DNI: 12345678, solicito.\n"
)


@pytest.fixture()
def ia_activa(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ia_habilitada", True)
    return tmp_path


def _catalogo(db) -> tuple[Oficina, TipoTramite]:
    oficina = Oficina(codigo="OF-01", nombre="Obras", activa=True)
    db.add(oficina)
    db.commit()
    tipo = TipoTramite(
        codigo="TR-01",
        nombre="Numeración municipal",
        area="Obras",
        oficina_responsable_id=oficina.id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    return oficina, tipo


def _documento(db, tipo, **extra) -> Documento:
    campos = {"tipo_tramite_id": tipo.id, "asunto": "Solicitud de prueba"}
    campos.update(extra)
    documento = crear_documento(db, DatosDocumento(**campos), anio_codigo=2026)
    db.commit()
    db.refresh(documento)
    return documento


def _validado(contenido: bytes = PDF, nombre: str = "oficio.pdf") -> ArchivoValidado:
    return ArchivoValidado(
        contenido=contenido,
        nombre_original=nombre,
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
    )


def _resultado(texto: str = TEXTO) -> motor.ResultadoMotor:
    return motor.ResultadoMotor(
        texto_ocr=texto,
        paginas=2,
        tipo_clase="letter",
        tipo_confianza=0.4,
        top_clases=[{"clase": "letter", "probabilidad": 0.4}],
        kie={"campos": {"HEADER": ["CARTA N° 045-2026-MP"]}, "pares": []},
        motor="tesseract+layoutlmv3",
        version_modelo="layoutlmv3-test",
        duracion_ms=1234,
    )


def test_guardar_archivo_encola_analisis(db, ia_activa):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)

    archivo = guardar_validado(db, documento, _validado())
    db.commit()

    analisis = db.scalar(select(DocumentoAnalisis))
    assert analisis is not None
    assert analisis.archivo_id == archivo.id
    assert analisis.documento_id == documento.id
    assert analisis.estado == EstadoAnalisis.PENDIENTE
    assert analisis.intentos == 0


def test_guardar_archivo_sin_ia_no_encola(db, ia_activa, monkeypatch):
    monkeypatch.setattr(settings, "ia_habilitada", False)
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)

    guardar_validado(db, documento, _validado())
    db.commit()

    assert db.scalar(select(DocumentoAnalisis)) is None


def test_procesar_analisis_guarda_texto_tipo_y_evento(db, ia_activa, monkeypatch):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    archivo = guardar_validado(db, documento, _validado())
    db.commit()

    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())

    resultado = cola.procesar_pendientes(db)
    assert resultado.procesados == 1
    assert resultado.errores == 0

    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    assert analisis is not None
    assert analisis.estado == EstadoAnalisis.LISTO
    assert analisis.texto_ocr.startswith("MUNICIPALIDAD")
    assert analisis.paginas == 2
    assert analisis.motor == "tesseract+layoutlmv3"
    assert analisis.version_modelo == "layoutlmv3-test"
    assert analisis.tipo_tramite_sugerido_id == tipo.id
    assert analisis.sugerencia_confianza is not None
    assert analisis.campos["tipo"]["codigo"] == "TR-01"
    assert analisis.campos["campos"]["dni"]["valor"] == "12345678"
    assert analisis.tipo_clase == "letter"

    archivo_db = db.get(DocumentoArchivo, archivo.id)
    assert archivo_db.texto_ocr.startswith("MUNICIPALIDAD")

    eventos = db.scalars(select(Evento).where(Evento.documento_id == documento.id)).all()
    analizado = [evento for evento in eventos if evento.tipo == TipoEvento.ANALIZADO]
    assert len(analizado) == 1
    # El evento no debe guardar datos personales.
    assert "12345678" not in str(analizado[0].detalle)
    assert analizado[0].detalle["tipo_sugerido"] == "TR-01"


def test_error_de_motor_reintenta_y_marca_error(db, ia_activa, monkeypatch):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    guardar_validado(db, documento, _validado())
    db.commit()

    def falla(ruta, **kwargs):
        raise motor.MotorNoDisponible("Tesseract no está instalado.")

    monkeypatch.setattr(motor, "analizar_archivo", falla)
    monkeypatch.setattr(settings, "ia_max_intentos", 1)

    resultado = cola.procesar_pendientes(db)
    assert resultado.errores == 1

    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    assert analisis.estado == EstadoAnalisis.ERROR
    assert analisis.intentos == 1
    assert "MotorNoDisponible" in analisis.error
    assert analisis.texto_ocr is None


def test_borrador_procesado_y_cache_por_hash(db, ia_activa, monkeypatch):
    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())

    primero = cola.encolar_borrador(
        db,
        contenido=PDF,
        nombre_original="oficio.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(PDF).hexdigest(),
    )
    db.commit()
    assert primero is not None
    assert primero.estado == EstadoAnalisis.PENDIENTE
    assert cola.ruta_borrador_absoluta(primero).is_file()

    cola.procesar_pendientes(db)
    db.refresh(primero)
    assert primero.estado == EstadoAnalisis.LISTO
    assert primero.documento_id is None

    # El mismo contenido en otra subida aprovecha el resultado ya calculado.
    segundo = cola.encolar_borrador(
        db,
        contenido=PDF,
        nombre_original="copia.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(PDF).hexdigest(),
    )
    db.commit()
    assert segundo is not None
    assert segundo.id == primero.id
    assert segundo.estado == EstadoAnalisis.LISTO


def test_cache_de_reglas_anteriores_se_reprocesa(db, ia_activa, monkeypatch):
    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())
    comun = {
        "contenido": PDF,
        "nombre_original": "oficio.pdf",
        "mime": "application/pdf",
        "extension": ".pdf",
        "hash_sha256": hashlib.sha256(PDF).hexdigest(),
    }

    primero = cola.encolar_borrador(db, **comun)
    db.commit()
    cola.procesar_pendientes(db)
    db.refresh(primero)
    assert primero.estado == EstadoAnalisis.LISTO

    # Simula un resultado guardado con una versión anterior de las reglas.
    primero.campos = {**primero.campos, "reglas": "reglas-viejas"}
    db.commit()

    segundo = cola.encolar_borrador(db, **comun)
    db.commit()
    assert segundo is not None
    assert segundo.id == primero.id
    assert segundo.estado == EstadoAnalisis.PENDIENTE


def test_borrador_duplicado_en_memoria_reutiliza_resultado(db, ia_activa, monkeypatch):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    archivo = guardar_validado(db, documento, _validado(PDF))
    db.commit()

    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())
    cola.procesar_pendientes(db)

    otro = cola.encolar_borrador(
        db,
        contenido=PDF,
        nombre_original="mismo.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=archivo.hash_sha256,
    )
    db.commit()
    assert otro is not None
    assert otro.estado == EstadoAnalisis.LISTO
    assert otro.texto_ocr.startswith("MUNICIPALIDAD")
    assert otro.campos["tipo"]["codigo"] == "TR-01"


def test_vincular_borrador_lo_traslada_al_expediente(db, ia_activa, monkeypatch):
    _, tipo = _catalogo(db)
    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())

    borrador = cola.encolar_borrador(
        db,
        contenido=PDF,
        nombre_original="oficio.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(PDF).hexdigest(),
    )
    db.commit()
    cola.procesar_pendientes(db)
    db.refresh(borrador)

    documento = _documento(db, tipo)
    archivo = guardar_validado(db, documento, _validado(PDF), encolar=False)
    ruta = cola.vincular_analisis(
        db, borrador, documento, archivo, tipo_confirmado_id=tipo.id
    )
    db.commit()

    db.refresh(borrador)
    assert borrador.documento_id == documento.id
    assert borrador.archivo_id == archivo.id
    assert borrador.ruta_borrador is None
    assert borrador.tipo_tramite_confirmado_id == tipo.id
    assert archivo.texto_ocr.startswith("MUNICIPALIDAD")

    # El borrador físico se puede borrar sin afectar el archivo del expediente.
    assert ruta is not None
    cola.eliminar_borrador(ruta)
    assert not (ia_activa / ruta).exists()


def test_limpiar_borradores_viejos(db, ia_activa, monkeypatch):
    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())

    viejo = cola.encolar_borrador(
        db,
        contenido=PDF,
        nombre_original="viejo.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(PDF).hexdigest(),
    )
    nuevo = cola.encolar_borrador(
        db,
        contenido=OTRO_PDF,
        nombre_original="nuevo.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(OTRO_PDF).hexdigest(),
    )
    db.commit()
    cola.procesar_pendientes(db, limite=None)

    db.get(DocumentoAnalisis, viejo.id).creado_en = datetime.now(UTC) - timedelta(days=3)
    db.commit()
    ruta_vieja = ia_activa / viejo.ruta_borrador

    assert cola.limpiar_borradores(db, horas=24) == 1
    db.expire_all()
    assert db.get(DocumentoAnalisis, viejo.id) is None
    assert db.get(DocumentoAnalisis, nuevo.id) is not None
    assert not ruta_vieja.exists()


def test_contexto_analisis_arma_campos_para_la_plantilla(db, ia_activa, monkeypatch):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    guardar_validado(db, documento, _validado())
    db.commit()
    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())
    cola.procesar_pendientes(db)

    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    ctx = cola.contexto_analisis(analisis)
    claves = {campo["clave"] for campo in ctx["campos"]}
    assert "tipo_tramite_id" in claves
    assert "ciudadano_numero_documento" in claves
    assert "asunto" in claves
    assert ctx["ocr"].startswith("MUNICIPALIDAD")
    assert ctx["clase_ia"]["clase"] == "letter"

    vacio = cola.contexto_analisis(None)
    assert vacio == {"analisis": None, "campos": [], "ocr": "", "clase_ia": None}


def test_ruta_borrador_no_sale_de_storage(db, ia_activa):
    analisis = DocumentoAnalisis(hash_sha256="x" * 64, ruta_borrador="../secreto.pdf")
    with pytest.raises(ValueError):
        cola.ruta_borrador_absoluta(analisis)
