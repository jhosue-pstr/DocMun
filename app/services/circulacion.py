"""Circulación de documentos entre oficinas.

Reglas del dominio:

- Los movimientos son inmutables: este módulo solo agrega filas, nunca borra ni
  reescribe un movimiento anterior. El tramo abierto se cierra al derivar,
  finalizar o anular.
- Todo cambio de estado genera un `Evento` con usuario, IP y timestamp.
- Un documento está en una sola oficina a la vez.
- El plazo corre desde la recepción confirmada y se calcula en horas hábiles.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalogos import Oficina, Plazo, TipoTramite
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import EstadoDocumento, Rol, TipoEvento
from app.models.usuarios import Usuario
from app.services.eventos import registrar_evento
from app.services.sla import calcular_fecha_limite_db

ESTADOS_ACTIVOS = {
    EstadoDocumento.REGISTRADO,
    EstadoDocumento.DERIVADO,
    EstadoDocumento.EN_OFICINA,
    EstadoDocumento.OBSERVADO,
}


class CirculacionInvalida(ValueError):
    """La acción no corresponde al estado actual del documento."""


class AccionNoPermitida(CirculacionInvalida):
    """El usuario no tiene permiso para operar este documento."""


def _ahora(valor: datetime | None) -> datetime:
    """Instante de la acción; permite reconstruir flujos históricos en pruebas."""
    if valor is None:
        return datetime.now(UTC)
    if valor.tzinfo is None:
        return valor.replace(tzinfo=UTC)
    return valor


def _exigir(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AccionNoPermitida(mensaje)


def _validar_estado(
    documento: Documento,
    permitidos: set[EstadoDocumento],
    accion: str,
) -> None:
    if documento.estado not in permitidos:
        raise CirculacionInvalida(
            f"No se puede {accion} un documento en estado {documento.estado.value}."
        )


def _es_admin(usuario: Usuario) -> bool:
    return usuario.rol == Rol.ADMIN


def _es_de_la_oficina(documento: Documento, usuario: Usuario) -> bool:
    return (
        usuario.rol == Rol.OFICINA
        and usuario.oficina_id is not None
        and documento.oficina_actual_id == usuario.oficina_id
    )


def puede_derivar(documento: Documento, usuario: Usuario) -> bool:
    if documento.estado not in {EstadoDocumento.REGISTRADO, EstadoDocumento.EN_OFICINA}:
        return False
    if _es_admin(usuario):
        return True
    if usuario.rol == Rol.VENTANILLA:
        return documento.estado == EstadoDocumento.REGISTRADO and documento.oficina_actual_id in {
            None,
            usuario.oficina_id,
        }
    if usuario.rol == Rol.OFICINA:
        return _es_de_la_oficina(documento, usuario)
    return False


def movimiento_por_recibir(db: Session, documento: Documento) -> Movimiento | None:
    return db.scalar(
        select(Movimiento)
        .where(
            Movimiento.documento_id == documento.id,
            Movimiento.fecha_recepcion.is_(None),
            Movimiento.fecha_salida.is_(None),
        )
        .order_by(Movimiento.id.desc())
    )


def puede_recibir(db: Session, documento: Documento, usuario: Usuario) -> bool:
    if documento.estado != EstadoDocumento.DERIVADO:
        return False
    movimiento = movimiento_por_recibir(db, documento)
    if movimiento is None:
        return False
    if _es_admin(usuario):
        return True
    return usuario.rol == Rol.OFICINA and usuario.oficina_id == movimiento.oficina_destino_id


def puede_observar(documento: Documento, usuario: Usuario) -> bool:
    return documento.estado == EstadoDocumento.EN_OFICINA and (
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario)
    )


def puede_subsanar(documento: Documento, usuario: Usuario) -> bool:
    return documento.estado == EstadoDocumento.OBSERVADO and (
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario)
    )


def puede_finalizar(documento: Documento, usuario: Usuario) -> bool:
    return documento.estado == EstadoDocumento.EN_OFICINA and (
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario)
    )


def puede_archivar(documento: Documento, usuario: Usuario) -> bool:
    return documento.estado == EstadoDocumento.FINALIZADO and (
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario)
    )


def puede_anular(documento: Documento, usuario: Usuario) -> bool:
    return usuario.rol in {Rol.ADMIN, Rol.DIRECCION} and documento.estado not in {
        EstadoDocumento.ANULADO,
        EstadoDocumento.ARCHIVADO,
    }


def acciones_disponibles(db: Session, documento: Documento, usuario: Usuario) -> dict[str, bool]:
    return {
        "derivar": puede_derivar(documento, usuario),
        "recibir": puede_recibir(db, documento, usuario),
        "observar": puede_observar(documento, usuario),
        "subsanar": puede_subsanar(documento, usuario),
        "finalizar": puede_finalizar(documento, usuario),
        "archivar": puede_archivar(documento, usuario),
        "anular": puede_anular(documento, usuario),
    }


def obtener_para_actualizar(db: Session, codigo: str) -> Documento | None:
    """Bloquea el documento para que dos acciones simultáneas no se pisen.

    En SQLite el bloqueo se ignora; en PostgreSQL usa `SELECT ... FOR UPDATE`.
    """
    return db.scalar(
        select(Documento).where(Documento.codigo == codigo.strip().upper()).with_for_update()
    )


def _cerrar_tramo(db: Session, documento: Documento, ahora: datetime) -> Movimiento | None:
    movimiento = db.scalar(
        select(Movimiento)
        .where(
            Movimiento.documento_id == documento.id,
            Movimiento.fecha_salida.is_(None),
        )
        .order_by(Movimiento.id.desc())
    )
    if movimiento is not None:
        movimiento.fecha_salida = ahora
    return movimiento


def _sla_horas(db: Session, tipo_tramite_id: int, oficina_id: int) -> int:
    horas = db.scalar(
        select(Plazo.horas).where(
            Plazo.tipo_tramite_id == tipo_tramite_id,
            Plazo.oficina_id == oficina_id,
        )
    )
    if horas is not None and horas > 0:
        return horas
    tipo = db.get(TipoTramite, tipo_tramite_id)
    return tipo.sla_horas if tipo is not None else 0


def _detalle_movimiento(movimiento: Movimiento) -> dict[str, object]:
    detalle: dict[str, object] = {
        "movimiento_id": movimiento.id,
        "origen": (
            movimiento.oficina_origen.codigo if movimiento.oficina_origen is not None else None
        ),
        "destino": movimiento.oficina_destino.codigo,
        "sla_horas": movimiento.sla_horas,
    }
    if movimiento.fecha_recepcion is not None:
        detalle["fecha_recepcion"] = movimiento.fecha_recepcion.isoformat()
    if movimiento.fecha_limite is not None:
        detalle["fecha_limite"] = movimiento.fecha_limite.isoformat()
    return detalle


def derivar(
    db: Session,
    documento: Documento,
    *,
    destino: Oficina,
    usuario: Usuario,
    request: Request | None = None,
    observacion: str | None = None,
    ahora: datetime | None = None,
) -> Movimiento:
    _validar_estado(
        documento,
        {EstadoDocumento.REGISTRADO, EstadoDocumento.EN_OFICINA},
        "derivar",
    )
    _exigir(
        _es_admin(usuario)
        or (usuario.rol == Rol.VENTANILLA and documento.estado == EstadoDocumento.REGISTRADO)
        or _es_de_la_oficina(documento, usuario),
        "No tiene permisos para derivar este documento.",
    )
    if not destino.activa:
        raise CirculacionInvalida("La oficina destino está inactiva.")
    if destino.id == documento.oficina_actual_id:
        raise CirculacionInvalida("El documento ya está en esa oficina.")

    ahora = _ahora(ahora)
    oficina_origen = documento.oficina_actual
    _cerrar_tramo(db, documento, ahora)

    movimiento = Movimiento(
        documento_id=documento.id,
        oficina_origen_id=documento.oficina_actual_id,
        oficina_destino_id=destino.id,
        usuario_envia_id=usuario.id,
        fecha_envio=ahora,
        sla_horas=_sla_horas(db, documento.tipo_tramite_id, destino.id),
        observacion=(observacion or "").strip()[:1000] or None,
    )
    db.add(movimiento)
    db.flush()

    documento.estado = EstadoDocumento.DERIVADO
    documento.oficina_actual_id = destino.id

    registrar_evento(
        db,
        documento,
        TipoEvento.DERIVADO,
        usuario=usuario,
        request=request,
        detalle={
            "movimiento_id": movimiento.id,
            "origen": oficina_origen.codigo if oficina_origen is not None else None,
            "destino": destino.codigo,
            "sla_horas": movimiento.sla_horas,
        },
        creado_en=ahora,
    )
    return movimiento


def recibir(
    db: Session,
    documento: Documento,
    *,
    usuario: Usuario,
    request: Request | None = None,
    ahora: datetime | None = None,
) -> Movimiento:
    _validar_estado(documento, {EstadoDocumento.DERIVADO}, "recibir")
    movimiento = movimiento_por_recibir(db, documento)
    if movimiento is None:
        raise CirculacionInvalida("No hay una derivación pendiente de recepción.")
    _exigir(
        _es_admin(usuario)
        or usuario.rol == Rol.OFICINA
        and usuario.oficina_id == movimiento.oficina_destino_id,
        "Solo la oficina destino puede recibir este documento.",
    )

    ahora = _ahora(ahora)
    horas = movimiento.sla_horas or _sla_horas(
        db, documento.tipo_tramite_id, movimiento.oficina_destino_id
    )
    fecha_limite = calcular_fecha_limite_db(db, ahora, horas)

    movimiento.fecha_recepcion = ahora
    movimiento.fecha_limite = fecha_limite
    movimiento.sla_horas = horas
    documento.estado = EstadoDocumento.EN_OFICINA
    documento.oficina_actual_id = movimiento.oficina_destino_id

    registrar_evento(
        db,
        documento,
        TipoEvento.RECIBIDO,
        usuario=usuario,
        request=request,
        detalle=_detalle_movimiento(movimiento),
        creado_en=ahora,
    )
    return movimiento


def observar(
    db: Session,
    documento: Documento,
    *,
    usuario: Usuario,
    motivo: str,
    request: Request | None = None,
    ahora: datetime | None = None,
) -> Evento:
    _validar_estado(documento, {EstadoDocumento.EN_OFICINA}, "observar")
    _exigir(
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario),
        "Solo la oficina actual puede observar este documento.",
    )
    motivo = (motivo or "").strip()
    if len(motivo) < 3:
        raise CirculacionInvalida("Describe el motivo de la observación.")

    documento.estado = EstadoDocumento.OBSERVADO
    return registrar_evento(
        db,
        documento,
        TipoEvento.OBSERVADO,
        usuario=usuario,
        request=request,
        detalle={"motivo": motivo[:1000]},
        creado_en=_ahora(ahora),
    )


def subsanar(
    db: Session,
    documento: Documento,
    *,
    usuario: Usuario,
    nota: str | None = None,
    request: Request | None = None,
    ahora: datetime | None = None,
) -> Evento:
    _validar_estado(documento, {EstadoDocumento.OBSERVADO}, "subsanar")
    _exigir(
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario),
        "Solo la oficina actual puede subsanar la observación.",
    )

    documento.estado = EstadoDocumento.EN_OFICINA
    nota = (nota or "").strip()
    return registrar_evento(
        db,
        documento,
        TipoEvento.SUBSANADO,
        usuario=usuario,
        request=request,
        detalle={"nota": nota[:1000]} if nota else None,
        creado_en=_ahora(ahora),
    )


def finalizar(
    db: Session,
    documento: Documento,
    *,
    usuario: Usuario,
    observacion: str | None = None,
    request: Request | None = None,
    ahora: datetime | None = None,
) -> Evento:
    _validar_estado(documento, {EstadoDocumento.EN_OFICINA}, "finalizar")
    _exigir(
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario),
        "Solo la oficina actual puede finalizar este documento.",
    )

    ahora = _ahora(ahora)
    movimiento = _cerrar_tramo(db, documento, ahora)
    documento.estado = EstadoDocumento.FINALIZADO
    documento.fecha_finalizacion = ahora

    observacion = (observacion or "").strip()
    detalle: dict[str, object] = {"movimiento_id": movimiento.id if movimiento else None}
    if observacion:
        detalle["observacion"] = observacion[:1000]
    return registrar_evento(
        db,
        documento,
        TipoEvento.FINALIZADO,
        usuario=usuario,
        request=request,
        detalle=detalle,
        creado_en=ahora,
    )


def archivar(
    db: Session,
    documento: Documento,
    *,
    usuario: Usuario,
    request: Request | None = None,
    ahora: datetime | None = None,
) -> Evento:
    _validar_estado(documento, {EstadoDocumento.FINALIZADO}, "archivar")
    _exigir(
        _es_admin(usuario) or _es_de_la_oficina(documento, usuario),
        "Solo la oficina que finalizó puede archivar este documento.",
    )

    documento.estado = EstadoDocumento.ARCHIVADO
    return registrar_evento(
        db,
        documento,
        TipoEvento.ARCHIVADO,
        usuario=usuario,
        request=request,
        creado_en=_ahora(ahora),
    )


def anular(
    db: Session,
    documento: Documento,
    *,
    usuario: Usuario,
    motivo: str,
    request: Request | None = None,
    ahora: datetime | None = None,
) -> Evento:
    _exigir(
        usuario.rol in {Rol.ADMIN, Rol.DIRECCION},
        "Solo administración o dirección pueden anular un documento.",
    )
    if documento.estado in {EstadoDocumento.ANULADO, EstadoDocumento.ARCHIVADO}:
        raise CirculacionInvalida(
            f"No se puede anular un documento en estado {documento.estado.value}."
        )

    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise CirculacionInvalida("Describe el motivo de la anulación.")

    ahora = _ahora(ahora)
    _cerrar_tramo(db, documento, ahora)
    documento.estado = EstadoDocumento.ANULADO
    return registrar_evento(
        db,
        documento,
        TipoEvento.ANULADO,
        usuario=usuario,
        request=request,
        detalle={"motivo": motivo[:1000]},
        creado_en=ahora,
    )
