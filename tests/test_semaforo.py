"""Pruebas del semáforo calculado y de sus umbrales."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.models.catalogos import Configuracion, Oficina, TipoTramite
from app.models.documentos import Documento, Movimiento
from app.models.enums import Semaforo
from app.services.documentos import DatosDocumento, crear_documento
from app.services.sla import (
    ContextoSla,
    Umbrales,
    cargar_umbrales,
    estado_por_documento,
    evaluar_movimiento,
    evaluar_plazos,
    horas_habiles_entre,
)

LIMA = ZoneInfo("America/Lima")
HORARIO = {
    "lunes_viernes": [["08:00", "13:00"], ["14:00", "17:00"]],
    "sabado": None,
    "domingo": None,
}
CONTEXTO = ContextoSla(
    horario=HORARIO,
    feriados=set(),
    umbrales=Umbrales(0.8, 1.0, 1.5),
    zona=LIMA,
)


def _lima(anio: int, mes: int, dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(anio, mes, dia, hora, minuto, tzinfo=LIMA)


def _catalogo(db) -> tuple[Documento, Oficina]:
    oficina = Oficina(codigo="OF-SEM", nombre="Oficina Semáforo", activa=True)
    db.add(oficina)
    db.commit()
    db.refresh(oficina)

    tipo = TipoTramite(
        codigo="TR-SEM",
        nombre="Trámite semáforo",
        oficina_responsable_id=oficina.id,
        sla_horas=10,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)

    documento = crear_documento(
        db,
        DatosDocumento(tipo_tramite_id=tipo.id, asunto="Prueba de semáforo"),
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
    limite: datetime | None = None,
    salida: datetime | None = None,
    sla_horas: int = 10,
) -> Movimiento:
    movimiento = Movimiento(
        documento_id=documento.id,
        oficina_destino_id=oficina.id,
        fecha_envio=recepcion or _lima(2026, 11, 9, 8),
        fecha_recepcion=recepcion,
        fecha_limite=limite,
        fecha_salida=salida,
        sla_horas=sla_horas,
    )
    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)
    return movimiento


def test_horas_habiles_entre_una_jornada():
    assert (
        horas_habiles_entre(_lima(2026, 11, 9, 10), _lima(2026, 11, 9, 12), horario=HORARIO) == 2.0
    )


def test_horas_habiles_entre_salta_el_corte_de_mediodia():
    # Lunes 12:30 → 14:30: media hora antes y media hora después del corte.
    assert (
        horas_habiles_entre(_lima(2026, 11, 9, 12, 30), _lima(2026, 11, 9, 14, 30), horario=HORARIO)
        == 1.0
    )


def test_horas_habiles_entre_salta_noche_y_fin_de_semana():
    # Viernes 15:00 → lunes 10:00: 2 h del viernes + 2 h del lunes.
    assert (
        horas_habiles_entre(_lima(2026, 11, 13, 15), _lima(2026, 11, 16, 10), horario=HORARIO)
        == 4.0
    )


def test_horas_habiles_entre_salta_feriado():
    assert (
        horas_habiles_entre(
            _lima(2026, 11, 9, 15),
            _lima(2026, 11, 11, 10),
            horario=HORARIO,
            feriados={date(2026, 11, 10)},
        )
        == 4.0
    )


def test_horas_habiles_entre_invertido_o_igual_es_cero():
    assert horas_habiles_entre(_lima(2026, 11, 9, 12), _lima(2026, 11, 9, 10)) == 0.0
    assert horas_habiles_entre(_lima(2026, 11, 9, 10), _lima(2026, 11, 9, 10)) == 0.0


def test_semaforo_respeta_los_umbrales(db):
    documento, oficina = _catalogo(db)
    movimiento = _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 9, 8),
    )

    def semaforo(hora: int, minuto: int = 0) -> Semaforo:
        estado = evaluar_movimiento(movimiento, CONTEXTO, ahora=_lima(2026, 11, 9, hora, minuto))
        assert estado is not None
        return estado.semaforo

    assert semaforo(10) == Semaforo.EN_PLAZO
    assert semaforo(16) == Semaforo.EN_PLAZO
    # Lunes 17:00: jornada completa (8 h) → 80%.
    assert semaforo(17) == Semaforo.POR_VENCER

    # Martes 10:00: 8 h del lunes + 2 h del martes = 10 h → 100%.
    estado = evaluar_movimiento(movimiento, CONTEXTO, ahora=_lima(2026, 11, 10, 10))
    assert estado is not None
    assert estado.semaforo == Semaforo.ATRASADO
    assert estado.horas_atraso == 0.0

    estado = evaluar_movimiento(movimiento, CONTEXTO, ahora=_lima(2026, 11, 10, 16))
    assert estado is not None
    assert estado.horas_consumidas == 15.0
    assert estado.semaforo == Semaforo.MUY_ATRASADO
    assert estado.horas_atraso == 5.0


def test_semaforo_sin_recepcion_o_cerrado_no_aplica(db):
    documento, oficina = _catalogo(db)
    sin_recepcion = _movimiento(db, documento, oficina, recepcion=None)
    cerrado = _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 9, 8),
        salida=_lima(2026, 11, 9, 9),
    )
    sin_plazo = _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 9, 8),
        sla_horas=0,
    )

    ahora = _lima(2026, 11, 9, 12)
    assert evaluar_movimiento(sin_recepcion, CONTEXTO, ahora=ahora) is None
    assert evaluar_movimiento(cerrado, CONTEXTO, ahora=ahora) is None
    assert evaluar_movimiento(sin_plazo, CONTEXTO, ahora=ahora) is None


def test_estado_por_documento_ignora_tramos_cerrados(db):
    documento, oficina = _catalogo(db)
    abierto = _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 9, 8),
    )
    _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 8, 8),
        salida=_lima(2026, 11, 8, 9),
    )

    estados = estado_por_documento(
        db,
        [documento],
        ahora=_lima(2026, 11, 9, 10),
        contexto=CONTEXTO,
    )

    assert set(estados) == {documento.id}
    assert estados[documento.id].semaforo == Semaforo.EN_PLAZO
    assert abierto.id is not None


def test_cargar_umbrales_desde_configuracion(db):
    db.add_all(
        [
            Configuracion(clave="umbral_por_vencer", valor="0.5"),
            Configuracion(clave="umbral_atrasado", valor="1.2"),
            Configuracion(clave="umbral_muy_atrasado", valor="2.0"),
        ]
    )
    db.commit()

    umbrales = cargar_umbrales(db)

    assert umbrales == Umbrales(0.5, 1.2, 2.0)


def test_cargar_umbrales_invalidos_caen_a_settings(db):
    db.add_all(
        [
            Configuracion(clave="umbral_por_vencer", valor="2"),
            Configuracion(clave="umbral_atrasado", valor="1"),
            Configuracion(clave="umbral_muy_atrasado", valor="3"),
        ]
    )
    db.commit()

    umbrales = cargar_umbrales(db)

    assert umbrales == Umbrales(
        settings.sla_por_vencer,
        settings.sla_atrasado,
        settings.sla_muy_atrasado,
    )


def test_evaluar_plazos_solo_devuelve_tramos_abiertos(db):
    documento, oficina = _catalogo(db)
    abierto = _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 9, 8),
    )
    _movimiento(
        db,
        documento,
        oficina,
        recepcion=_lima(2026, 11, 8, 8),
        salida=_lima(2026, 11, 8, 9),
    )

    resultados = evaluar_plazos(db, ahora=_lima(2026, 11, 9, 10), contexto=CONTEXTO)

    assert [movimiento.id for movimiento, _ in resultados] == [abierto.id]
    assert db.scalar(select(Movimiento).where(Movimiento.id == abierto.id)) is not None
