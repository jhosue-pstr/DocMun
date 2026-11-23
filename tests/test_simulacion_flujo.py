"""Pruebas de la reconstrucción del flujo histórico de demostración."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento, Movimiento
from app.models.enums import EstadoDocumento
from scripts.simular_flujo import simular


def _catalogo(db) -> tuple[Oficina, Oficina, TipoTramite]:
    oficinas = [
        Oficina(codigo="OF-SIM-A", nombre="Oficina Simulación A", activa=True),
        Oficina(codigo="OF-SIM-B", nombre="Oficina Simulación B", activa=True),
    ]
    db.add_all(oficinas)
    db.commit()
    for oficina in oficinas:
        db.refresh(oficina)

    tipo = TipoTramite(
        codigo="TR-SIM",
        nombre="Trámite de simulación",
        oficina_responsable_id=oficinas[0].id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)
    return oficinas[0], oficinas[1], tipo


def _documentos(db, tipo: TipoTramite, cantidad: int) -> list[Documento]:
    documentos = [
        Documento(
            codigo=f"DOC-2026-{indice:06d}",
            tipo_tramite_id=tipo.id,
            numero_certificado=str(indice),
            anio=2026,
            fecha_registro=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        for indice in range(cantidad)
    ]
    db.add_all(documentos)
    db.commit()
    return documentos


def test_simular_reparte_estados_y_cierra_tramos(db, admin_user):
    _oficina_a, _oficina_b, tipo = _catalogo(db)
    documentos = _documentos(db, tipo, 40)

    resultado = simular(db, semilla=20261111, lote=100)

    assert resultado.procesados == len(documentos)
    assert resultado.omitidos == 0
    assert resultado.errores == 0
    assert resultado.por_semaforo

    for documento in documentos:
        movimientos = list(
            db.scalars(
                select(Movimiento)
                .where(Movimiento.documento_id == documento.id)
                .order_by(Movimiento.id)
            ).all()
        )
        assert movimientos, f"{documento.codigo} debería tener al menos un movimiento"

        if documento.estado in {EstadoDocumento.FINALIZADO, EstadoDocumento.ARCHIVADO}:
            assert documento.fecha_finalizacion is not None
            assert all(movimiento.fecha_salida is not None for movimiento in movimientos)
        elif documento.estado in {EstadoDocumento.EN_OFICINA, EstadoDocumento.OBSERVADO}:
            abiertos = [movimiento for movimiento in movimientos if movimiento.fecha_salida is None]
            assert len(abiertos) == 1
            assert abiertos[0].fecha_recepcion is not None
            assert abiertos[0].fecha_limite is not None
        elif documento.estado == EstadoDocumento.DERIVADO:
            assert movimientos[-1].fecha_recepcion is None
            assert movimientos[-1].fecha_salida is None


def test_simular_es_idempotente(db, admin_user):
    _oficina_a, _oficina_b, tipo = _catalogo(db)
    _documentos(db, tipo, 12)

    primera = simular(db, semilla=7)
    segunda = simular(db, semilla=7)

    assert primera.procesados == 12
    assert primera.errores == 0
    assert segunda.procesados == 0
    assert segunda.omitidos == 12
    assert db.scalar(select(func.count()).select_from(Movimiento)) >= 12
