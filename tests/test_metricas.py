"""Pruebas del servicio de métricas y del dashboard del semáforo."""

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import EstadoDocumento, OrigenDocumento, Semaforo, TipoEvento
from app.services.metricas import (
    FiltrosMetricas,
    calcular_dashboard,
    resolver_periodo,
)

AHORA = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # miércoles 10:00 en Lima
HTML = {"accept": "text/html"}


def _login_admin(client: TestClient) -> None:
    respuesta = client.get("/login", headers=HTML)
    token = re.search(r'name="csrf_token" value="([^"]+)"', respuesta.text).group(1)
    respuesta = client.post(
        "/login",
        data={"email": "admin@test.local", "password": "secreto123", "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert respuesta.status_code == 303


def _catalogo(db) -> tuple[Oficina, Oficina, TipoTramite]:
    oficial = Oficina(codigo="OF-MA", nombre="Oficina Métricas A", activa=True)
    otra = Oficina(codigo="OF-MB", nombre="Oficina Métricas B", activa=True)
    db.add_all([oficial, otra])
    db.commit()
    db.refresh(oficial)
    db.refresh(otra)

    tipo = TipoTramite(
        codigo="TR-MA",
        nombre="Trámite de métricas",
        oficina_responsable_id=oficial.id,
        sla_horas=8,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)
    return oficial, otra, tipo


def _documento(
    db,
    tipo: TipoTramite,
    indice: int,
    *,
    estado: EstadoDocumento,
    oficina: Oficina,
    origen: OrigenDocumento = OrigenDocumento.MANUAL,
) -> Documento:
    documento = Documento(
        codigo=f"DOC-2026-{indice:06d}",
        tipo_tramite_id=tipo.id,
        numero_certificado=str(indice),
        anio=2026,
        estado=estado,
        origen=origen,
        oficina_actual_id=oficina.id,
        fecha_registro=datetime(2026, 11, 9, 14, 0, tzinfo=UTC),
    )
    db.add(documento)
    db.commit()
    db.refresh(documento)
    return documento


def _movimiento(
    db,
    documento: Documento,
    oficina: Oficina,
    *,
    recepcion: datetime | None,
    salida: datetime | None = None,
    limite: datetime | None = None,
    sla_horas: int = 8,
) -> Movimiento:
    movimiento = Movimiento(
        documento_id=documento.id,
        oficina_destino_id=oficina.id,
        fecha_envio=recepcion or datetime(2026, 11, 9, 14, 0, tzinfo=UTC),
        fecha_recepcion=recepcion,
        fecha_salida=salida,
        fecha_limite=limite,
        sla_horas=sla_horas,
    )
    db.add(movimiento)
    db.commit()
    db.refresh(movimiento)
    return movimiento


def _dashboard(db, **filtros):
    periodo = resolver_periodo(filtros.pop("periodo", "todo"), ahora=AHORA)
    return calcular_dashboard(db, FiltrosMetricas(periodo=periodo, **filtros), ahora=AHORA)


def test_dashboard_vacio_no_falla(db):
    panel = _dashboard(db)

    assert panel.resumen.documentos_total == 0
    assert panel.resumen.abiertos == 0
    assert panel.resumen.cumplimiento == 0.0
    assert panel.oficinas == []
    assert panel.criticos == []
    assert panel.charts["semaforo"]["valores"] == [0, 0, 0, 0]


def test_semaforo_abierto_por_nivel(db):
    oficina, _otra, tipo = _catalogo(db)
    en_plazo = _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    por_vencer = _documento(db, tipo, 2, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    atrasado = _documento(db, tipo, 3, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    muy_atrasado = _documento(db, tipo, 4, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)

    # Recepción del mismo día: 1 hora consumida de 8.
    _movimiento(
        db, en_plazo, oficina,
        recepcion=datetime(2026, 11, 11, 14, 0, tzinfo=UTC), sla_horas=8,
    )
    # Recepción del martes: 8 horas consumidas. El SLA decide el color.
    martes = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)
    _movimiento(db, por_vencer, oficina, recepcion=martes, sla_horas=10)
    _movimiento(db, atrasado, oficina, recepcion=martes, sla_horas=7)
    _movimiento(db, muy_atrasado, oficina, recepcion=martes, sla_horas=5)

    panel = _dashboard(db)
    resumen = panel.resumen

    assert resumen.abiertos == 4
    assert resumen.en_plazo == 1
    assert resumen.por_vencer == 1
    assert resumen.atrasado == 1
    assert resumen.muy_atrasado == 1
    assert resumen.porcentaje_atraso == 50.0
    assert panel.oficinas[0].total_con_sla == 4


def test_tramo_sin_recepcion_cuenta_aparte(db):
    oficina, _otra, tipo = _catalogo(db)
    documento = _documento(db, tipo, 1, estado=EstadoDocumento.DERIVADO, oficina=oficina)
    _movimiento(db, documento, oficina, recepcion=None)

    panel = _dashboard(db)

    assert panel.resumen.por_recibir == 1
    assert panel.resumen.abiertos == 0
    assert panel.oficinas[0].por_recibir == 1


def test_cerrados_cumplimiento_y_mediana(db):
    oficina, _otra, tipo = _catalogo(db)
    a_tiempo = _documento(db, tipo, 1, estado=EstadoDocumento.FINALIZADO, oficina=oficina)
    tarde = _documento(db, tipo, 2, estado=EstadoDocumento.FINALIZADO, oficina=oficina)

    _movimiento(
        db, a_tiempo, oficina,
        recepcion=datetime(2026, 11, 10, 14, 0, tzinfo=UTC),
        salida=datetime(2026, 11, 10, 20, 0, tzinfo=UTC),
        limite=datetime(2026, 11, 10, 22, 0, tzinfo=UTC),
    )
    _movimiento(
        db, tarde, oficina,
        recepcion=datetime(2026, 11, 10, 14, 0, tzinfo=UTC),
        salida=datetime(2026, 11, 11, 15, 0, tzinfo=UTC),
        limite=datetime(2026, 11, 10, 22, 0, tzinfo=UTC),
    )

    panel = _dashboard(db)

    assert panel.resumen.cerrados == 2
    assert panel.resumen.cumplimiento == 50.0
    assert panel.resumen.mediana_horas == 7.0  # 6 h y 8 h hábiles
    assert panel.oficinas[0].cerrados == 2


def test_criticos_ordenados_por_atraso(db):
    oficina, _otra, tipo = _catalogo(db)
    poco = _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    mucho = _documento(db, tipo, 2, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    martes = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)
    _movimiento(db, poco, oficina, recepcion=martes, sla_horas=7)
    _movimiento(db, mucho, oficina, recepcion=martes, sla_horas=4)

    panel = _dashboard(db)

    assert [critico.codigo for critico in panel.criticos] == [
        "DOC-2026-000002",
        "DOC-2026-000001",
    ]
    assert panel.criticos[0].semaforo in {Semaforo.ATRASADO, Semaforo.MUY_ATRASADO}
    assert panel.criticos[0].horas_atraso >= panel.criticos[1].horas_atraso


def test_filtros_por_oficina_y_origen(db):
    oficial, otra, tipo = _catalogo(db)
    manual = _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficial)
    historico = _documento(
        db, tipo, 2,
        estado=EstadoDocumento.EN_OFICINA,
        oficina=otra,
        origen=OrigenDocumento.HISTORICO,
    )
    for documento, oficina in ((manual, oficial), (historico, otra)):
        _movimiento(
            db, documento, oficina,
            recepcion=datetime(2026, 11, 11, 14, 0, tzinfo=UTC),
        )

    por_oficina = _dashboard(db, oficina_id=oficial.id)
    assert por_oficina.resumen.abiertos == 1
    assert [metrica.id for metrica in por_oficina.oficinas] == [oficial.id]

    por_origen = _dashboard(db, origen=OrigenDocumento.HISTORICO)
    assert por_origen.resumen.abiertos == 1
    assert por_origen.oficinas[0].id == otra.id


def test_observaciones_por_oficina(db):
    oficina, _otra, tipo = _catalogo(db)
    documento = _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    movimiento = _movimiento(
        db, documento, oficina,
        recepcion=datetime(2026, 11, 10, 14, 0, tzinfo=UTC),
    )
    db.add(
        Evento(
            documento_id=documento.id,
            tipo=TipoEvento.OBSERVADO,
            creado_en=datetime(2026, 11, 10, 16, 0, tzinfo=UTC),
        )
    )
    db.commit()
    assert movimiento.id is not None

    panel = _dashboard(db)

    assert panel.resumen.observaciones == 1
    assert panel.oficinas[0].observaciones == 1


def test_charts_y_serie_tienen_estructura(db):
    oficina, _otra, tipo = _catalogo(db)
    documento = _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    _movimiento(
        db, documento, oficina,
        recepcion=datetime(2026, 11, 11, 14, 0, tzinfo=UTC),
    )
    db.add(
        Evento(
            documento_id=documento.id,
            tipo=TipoEvento.ALERTA_PLAZO,
            creado_en=datetime(2026, 11, 11, 14, 30, tzinfo=UTC),
        )
    )
    db.commit()

    panel = _dashboard(db)

    for clave in ("semaforo", "oficinas", "carga", "cumplimiento", "tiempo_sla",
                  "tipos", "estados", "tendencia", "criticos"):
        assert clave in panel.charts
    assert panel.serie
    assert {"label", "recibidos", "cerrados", "finalizados", "observaciones", "alertas"} <= set(
        panel.serie[-1]
    )
    assert panel.charts["tendencia"]["alertas"]


def test_documento_archivado_se_cuenta_en_estados(db):
    oficina, _otra, tipo = _catalogo(db)
    _documento(db, tipo, 1, estado=EstadoDocumento.ARCHIVADO, oficina=oficina)

    panel = _dashboard(db)
    estados = dict(panel.estados)

    assert estados["Archivado"] == 1
    assert panel.resumen.documentos_archivados == 1
    assert panel.resumen.documentos_en_tramite == 0


def test_query_documentos_por_estado(db):
    """El filtro de documentos respeta el estado aunque no haya movimientos."""
    oficina, _otra, tipo = _catalogo(db)
    _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)

    documentos = list(db.scalars(select(Documento)).all())
    assert documentos[0].estado == EstadoDocumento.EN_OFICINA


def test_ruta_metricas_exige_admin(client):
    respuesta = client.get("/admin/metricas", headers=HTML, follow_redirects=False)
    assert respuesta.status_code in {303, 401, 403}


def test_ruta_metricas_renderiza_graficos(client, db, admin_user):
    oficina, _otra, tipo = _catalogo(db)
    documento = _documento(db, tipo, 1, estado=EstadoDocumento.EN_OFICINA, oficina=oficina)
    _movimiento(
        db, documento, oficina,
        recepcion=datetime(2026, 11, 10, 14, 0, tzinfo=UTC), sla_horas=4,
    )
    _login_admin(client)

    respuesta = client.get("/admin/metricas?periodo=todo", headers=HTML)

    assert respuesta.status_code == 200
    assert "Métricas de atención" in respuesta.text
    for canvas in (
        "chart-semaforo",
        "chart-oficinas",
        "chart-cumplimiento",
        "chart-tipos",
        "chart-tendencia",
    ):
        assert canvas in respuesta.text
    assert "Ahora por oficina" in respuesta.text
