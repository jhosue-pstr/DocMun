"""Pruebas del job de alertas de plazo."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import Semaforo, TipoEvento
from app.services.alertas import generar_alertas
from app.services.documentos import DatosDocumento, crear_documento

LIMA = ZoneInfo("America/Lima")


def _lima(anio: int, mes: int, dia: int, hora: int) -> datetime:
    return datetime(anio, mes, dia, hora, tzinfo=LIMA)


def _utc(anio: int, mes: int, dia: int, hora: int) -> datetime:
    return _lima(anio, mes, dia, hora).astimezone(UTC)


def _catalogo(db) -> tuple[Documento, Oficina]:
    oficina = Oficina(codigo="OF-ALR", nombre="Oficina Alertas", activa=True)
    db.add(oficina)
    db.commit()
    db.refresh(oficina)

    tipo = TipoTramite(
        codigo="TR-ALR",
        nombre="Trámite alertas",
        oficina_responsable_id=oficina.id,
        sla_horas=10,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)

    documento = crear_documento(
        db,
        DatosDocumento(tipo_tramite_id=tipo.id, asunto="Prueba de alertas"),
    )
    db.commit()
    db.refresh(documento)
    return documento, oficina


def _movimiento(
    db,
    documento: Documento,
    oficina: Oficina,
    *,
    recepcion: datetime | None,
    salida: datetime | None = None,
    sla_horas: int = 10,
) -> Movimiento:
    movimiento = Movimiento(
        documento_id=documento.id,
        oficina_destino_id=oficina.id,
        fecha_envio=recepcion or _utc(2026, 11, 9, 8),
        fecha_recepcion=recepcion,
        fecha_salida=salida,
        sla_horas=sla_horas,
    )
    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)
    return movimiento


def _contar_alertas(db) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(Evento).where(Evento.tipo == TipoEvento.ALERTA_PLAZO)
        )
        or 0
    )


def test_alerta_una_vez_por_nivel_y_movimiento(db):
    documento, oficina = _catalogo(db)
    movimiento = _movimiento(
        db,
        documento,
        oficina,
        recepcion=_utc(2026, 11, 9, 8),
    )

    por_vencer = generar_alertas(db, ahora=_lima(2026, 11, 9, 17))
    db.commit()
    assert por_vencer.total_generadas == 1
    assert por_vencer.generadas[0].semaforo == Semaforo.POR_VENCER
    assert por_vencer.generadas[0].movimiento_id == movimiento.id
    assert _contar_alertas(db) == 1

    repetida = generar_alertas(db, ahora=_lima(2026, 11, 9, 17))
    db.commit()
    assert repetida.total_generadas == 0
    assert repetida.existentes == 1
    assert _contar_alertas(db) == 1

    atrasado = generar_alertas(db, ahora=_lima(2026, 11, 10, 10))
    db.commit()
    assert atrasado.total_generadas == 1
    assert atrasado.generadas[0].semaforo == Semaforo.ATRASADO
    assert _contar_alertas(db) == 2

    muy_atrasado = generar_alertas(db, ahora=_lima(2026, 11, 10, 16))
    db.commit()
    assert muy_atrasado.total_generadas == 1
    assert muy_atrasado.generadas[0].semaforo == Semaforo.MUY_ATRASADO
    assert _contar_alertas(db) == 3

    evento = db.scalar(
        select(Evento).where(
            Evento.tipo == TipoEvento.ALERTA_PLAZO,
            Evento.detalle["movimiento_id"].as_integer() == movimiento.id,
        )
    )
    assert evento is not None
    assert evento.detalle["semaforo"] == "por_vencer"
    assert evento.detalle["sla_horas"] == 10


def test_dry_run_no_escribe_eventos(db):
    documento, oficina = _catalogo(db)
    _movimiento(
        db,
        documento,
        oficina,
        recepcion=_utc(2026, 11, 9, 8),
    )

    resultado = generar_alertas(db, ahora=_lima(2026, 11, 9, 17), dry_run=True)
    db.commit()

    assert resultado.total_generadas == 1
    assert _contar_alertas(db) == 0


def test_no_alerta_en_plazo_ni_tramos_cerrados(db):
    documento, oficina = _catalogo(db)
    _movimiento(
        db,
        documento,
        oficina,
        recepcion=_utc(2026, 11, 9, 8),
    )
    _movimiento(
        db,
        documento,
        oficina,
        recepcion=_utc(2026, 11, 8, 8),
        salida=_utc(2026, 11, 8, 9),
    )

    resultado = generar_alertas(db, ahora=_lima(2026, 11, 9, 10))
    db.commit()

    assert resultado.evaluados == 1
    assert resultado.total_generadas == 0
    assert _contar_alertas(db) == 0


def test_evaluados_cuenta_todos_los_tramos_abiertos(db):
    documento, oficina = _catalogo(db)
    _movimiento(db, documento, oficina, recepcion=_utc(2026, 11, 9, 8))

    resultado = generar_alertas(db, ahora=_lima(2026, 11, 9, 9))

    assert resultado.evaluados == 1
    assert resultado.total_generadas == 0
