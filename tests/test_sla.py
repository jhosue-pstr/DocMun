"""Pruebas del cálculo de plazos en horas hábiles."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.catalogos import Configuracion, Feriado
from app.services.sla import (
    HORARIO_POR_DEFECTO,
    HorarioInvalido,
    calcular_fecha_limite,
    calcular_fecha_limite_db,
    cargar_horario,
    normalizar_horario,
)

LIMA = ZoneInfo("America/Lima")
HORARIO = {
    "lunes_viernes": [["08:00", "13:00"], ["14:00", "17:00"]],
    "sabado": None,
    "domingo": None,
}


def _lima(anio: int, mes: int, dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(anio, mes, dia, hora, minuto, tzinfo=LIMA)


def _en_lima(resultado: datetime) -> datetime:
    return resultado.astimezone(LIMA)


def test_plazo_dentro_de_la_semana():
    # Lunes 10:00 + 8h = 6h del lunes + 2h del martes.
    resultado = calcular_fecha_limite(_lima(2026, 11, 9, 10), 8, horario=HORARIO)

    assert _en_lima(resultado) == _lima(2026, 11, 10, 10)


def test_plazo_cruza_el_fin_de_semana():
    # Viernes 15:00 + 2h de la tarde + 2h del lunes.
    resultado = calcular_fecha_limite(_lima(2026, 11, 13, 15), 4, horario=HORARIO)

    assert _en_lima(resultado) == _lima(2026, 11, 16, 10)


def test_plazo_salta_el_corte_de_mediodia():
    # Lunes 12:00 + 1h antes del corte + 1h después del almuerzo.
    resultado = calcular_fecha_limite(_lima(2026, 11, 9, 12), 2, horario=HORARIO)

    assert _en_lima(resultado) == _lima(2026, 11, 9, 15)


def test_plazo_salta_feriado():
    resultado = calcular_fecha_limite(
        _lima(2026, 11, 9, 16),
        2,
        horario=HORARIO,
        feriados={date(2026, 11, 10)},
    )

    assert _en_lima(resultado) == _lima(2026, 11, 11, 9)


def test_inicio_antes_de_la_apertura():
    resultado = calcular_fecha_limite(_lima(2026, 11, 9, 7), 2, horario=HORARIO)

    assert _en_lima(resultado) == _lima(2026, 11, 9, 10)


def test_inicio_despues_del_cierre():
    resultado = calcular_fecha_limite(_lima(2026, 11, 13, 17), 2, horario=HORARIO)

    assert _en_lima(resultado) == _lima(2026, 11, 16, 10)


def test_sabado_cerrado():
    # Viernes 16:00 + 1h de la tarde + 2h del lunes.
    resultado = calcular_fecha_limite(_lima(2026, 11, 13, 16), 3, horario=HORARIO)

    assert _en_lima(resultado) == _lima(2026, 11, 16, 10)


def test_horas_invalidas():
    with pytest.raises(ValueError, match="mayores que cero"):
        calcular_fecha_limite(_lima(2026, 11, 9, 10), 0, horario=HORARIO)


def test_inicio_sin_zona_horaria():
    with pytest.raises(ValueError, match="zona horaria"):
        calcular_fecha_limite(datetime(2026, 11, 9, 10), 2, horario=HORARIO)


def test_horario_invalido():
    with pytest.raises(HorarioInvalido):
        normalizar_horario({"lunes_viernes": ["16:00", "08:00"]})
    with pytest.raises(HorarioInvalido):
        normalizar_horario({"lunes_viernes": ["08:00"]})
    with pytest.raises(HorarioInvalido):
        normalizar_horario({"lunes_viernes": [["08:00", "13:00"], ["12:00", "17:00"]]})
    with pytest.raises(HorarioInvalido):
        normalizar_horario([])


def test_horario_de_un_solo_tramo_sigue_siendo_valido():
    horario = normalizar_horario({"lunes_viernes": ["08:00", "16:00"]})

    assert horario["lunes_viernes"] == ["08:00", "16:00"]


def test_horario_por_defecto_si_falta_o_esta_mal(db):
    assert cargar_horario(db) == normalizar_horario(HORARIO_POR_DEFECTO)

    db.add(Configuracion(clave="horario_laboral", valor="no es json"))
    db.commit()

    assert cargar_horario(db) == normalizar_horario(HORARIO_POR_DEFECTO)


def test_calcular_desde_base_de_datos(db):
    db.add(
        Configuracion(
            clave="horario_laboral",
            valor='{"lunes_viernes": ["09:00", "17:00"], "sabado": null, "domingo": null}',
        )
    )
    db.commit()

    # Lunes 16:00 + 2h = 1h del lunes + 1h del martes.
    resultado = calcular_fecha_limite_db(db, _lima(2026, 11, 9, 16), 2)
    assert _en_lima(resultado) == _lima(2026, 11, 10, 10)

    db.add(Feriado(fecha=date(2026, 11, 10), descripcion="Feriado de prueba"))
    db.commit()

    resultado = calcular_fecha_limite_db(db, _lima(2026, 11, 9, 16), 2)
    assert _en_lima(resultado) == _lima(2026, 11, 11, 10)
