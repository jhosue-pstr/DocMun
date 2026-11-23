"""Pruebas de los servicios base de la Fase 1."""

import pytest
from sqlalchemy import select

from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Evento
from app.models.enums import (
    EstadoDocumento,
    TipoDocumentoIdentidad,
    TipoEvento,
)
from app.services.ciudadanos import DatosCiudadano, obtener_o_crear
from app.services.documentos import (
    CodigoNoDisponible,
    DatosDocumento,
    DocumentoDuplicado,
    DocumentoInvalido,
    buscar_documentos,
    crear_documento,
    obtener_por_codigo,
)
from app.services.folio import generar_codigo


@pytest.fixture()
def oficina(db):
    oficina = Oficina(codigo="OF-T", nombre="Oficina de Prueba", activa=True)
    db.add(oficina)
    db.commit()
    db.refresh(oficina)
    return oficina


@pytest.fixture()
def tipo_tramite(db, oficina):
    tipo = TipoTramite(
        codigo="TR-T",
        nombre="Tipo de Prueba",
        area="Pruebas",
        oficina_responsable_id=oficina.id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)
    return tipo


def test_obtener_o_crear_ciudadano_no_duplica(db):
    datos = DatosCiudadano(
        tipo_documento=TipoDocumentoIdentidad.DNI,
        numero_documento=" 1234 5678 ",
        nombres="Ana Pérez",
        telefono="999888777",
    )

    primero, creado = obtener_o_crear(db, datos)
    segundo, creado_de_nuevo = obtener_o_crear(db, datos)
    db.commit()

    assert creado is True
    assert creado_de_nuevo is False
    assert primero.id == segundo.id
    assert primero.numero_documento == "12345678"


def test_crear_documento_genera_codigo_evento_y_no_movimiento(
    db, tipo_tramite, admin_user
):
    documento = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="001-2026",
            anio=2026,
            asunto="Documento de prueba",
        ),
        usuario=admin_user,
        anio_codigo=2026,
    )
    db.commit()

    assert documento.codigo == "DOC-2026-000001"
    assert documento.estado is EstadoDocumento.REGISTRADO
    assert documento.oficina_actual_id is None
    assert documento.creado_por_id == admin_user.id
    assert documento.movimientos == []

    eventos = db.scalars(
        select(Evento).where(Evento.documento_id == documento.id)
    ).all()
    assert len(eventos) == 1
    assert eventos[0].tipo is TipoEvento.CREADO
    assert eventos[0].usuario_id == admin_user.id


def test_generar_codigo_incrementa_por_anio(db, tipo_tramite):
    assert generar_codigo(db, 2026) == "DOC-2026-000001"

    documento = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="001-2026",
            anio=2026,
        ),
        anio_codigo=2026,
    )
    db.commit()

    assert documento.codigo == "DOC-2026-000001"
    assert generar_codigo(db, 2026) == "DOC-2026-000002"


def test_duplicado_mismo_tipo_numero_y_anio(db, tipo_tramite):
    datos = DatosDocumento(
        tipo_tramite_id=tipo_tramite.id,
        numero_certificado="001-2026",
        anio=2026,
    )
    crear_documento(db, datos)
    db.commit()

    with pytest.raises(DocumentoDuplicado):
        crear_documento(db, datos)


def test_mismo_numero_en_otro_tipo_es_valido(db, oficina):
    tipo_uno = TipoTramite(
        codigo="TR-UNO", nombre="Tipo Uno", sla_horas=48, activo=True
    )
    tipo_dos = TipoTramite(
        codigo="TR-DOS", nombre="Tipo Dos", sla_horas=48, activo=True
    )
    db.add_all([tipo_uno, tipo_dos])
    db.commit()
    db.refresh(tipo_uno)
    db.refresh(tipo_dos)

    primero = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_uno.id,
            numero_certificado="001-2026",
            anio=2026,
        ),
        anio_codigo=2026,
    )
    segundo = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_dos.id,
            numero_certificado="001-2026",
            anio=2026,
        ),
        anio_codigo=2026,
    )
    db.commit()

    assert primero.id != segundo.id


def test_numero_sin_anio_es_invalido(db, tipo_tramite):
    with pytest.raises(DocumentoInvalido):
        crear_documento(
            db,
            DatosDocumento(
                tipo_tramite_id=tipo_tramite.id,
                numero_certificado="001-2026",
            ),
        )


def test_colision_de_codigo_reintenta(db, tipo_tramite, monkeypatch):
    primero = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="001-2026",
            anio=2026,
        ),
        anio_codigo=2026,
    )
    db.commit()

    codigos = iter([primero.codigo, "DOC-2026-000099"])
    monkeypatch.setattr(
        "app.services.documentos.generar_codigo",
        lambda db, anio=None: next(codigos),
    )

    segundo = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="002-2026",
            anio=2026,
        ),
        anio_codigo=2026,
    )
    db.commit()

    assert segundo.codigo == "DOC-2026-000099"


def test_codigo_no_disponible_tras_agotar_intentos(
    db, tipo_tramite, monkeypatch
):
    documento = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="001-2026",
            anio=2026,
        ),
        anio_codigo=2026,
    )
    db.commit()

    monkeypatch.setattr(
        "app.services.documentos.generar_codigo",
        lambda db, anio=None: documento.codigo,
    )

    with pytest.raises(CodigoNoDisponible):
        crear_documento(
            db,
            DatosDocumento(
                tipo_tramite_id=tipo_tramite.id,
                numero_certificado="002-2026",
                anio=2026,
            ),
            anio_codigo=2026,
            max_intentos=2,
        )


def test_buscar_y_obtener_por_codigo(db, tipo_tramite):
    primero = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="001-2026",
            anio=2026,
            asunto="Licencia de edificación",
        ),
        anio_codigo=2026,
    )
    segundo = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo_tramite.id,
            numero_certificado="002-2026",
            anio=2026,
            asunto="Certificado de catastro",
        ),
        anio_codigo=2026,
    )
    db.commit()

    items, total = buscar_documentos(db, q="Licencia")
    assert total == 1
    assert items[0].id == primero.id

    items, total = buscar_documentos(
        db, estado=EstadoDocumento.REGISTRADO, per_page=1
    )
    assert total == 2
    assert len(items) == 1

    encontrado = obtener_por_codigo(db, segundo.codigo.lower())
    assert encontrado is not None
    assert encontrado.id == segundo.id
    assert len(encontrado.eventos) == 1
