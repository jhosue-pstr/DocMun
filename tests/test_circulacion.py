"""Pruebas de circulación entre oficinas, permisos y bandejas."""

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models.catalogos import Oficina, Plazo, TipoTramite
from app.models.documentos import Documento, Evento, Movimiento
from app.models.enums import (
    EstadoDocumento,
    OrigenDocumento,
    Rol,
    TipoEvento,
)
from app.models.usuarios import Usuario
from app.services.circulacion import derivar, recibir
from app.services.documentos import DatosDocumento, crear_documento

HTML = {"accept": "text/html"}


def _csrf(client: TestClient, path: str = "/login") -> str:
    response = client.get(path, headers=HTML)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, f"La página {path} debe incluir el token CSRF"
    return match.group(1)


def _login(client: TestClient, email: str, password: str = "secreto123") -> str:
    client.cookies.clear()
    token = _csrf(client)
    response = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _csrf(client, "/panel")


def _crear_usuario(db, rol: Rol, email: str, oficina_id: int | None = None) -> Usuario:
    usuario = Usuario(
        nombre=f"Usuario {rol.value}",
        email=email,
        password_hash=hash_password("secreto123"),
        rol=rol,
        oficina_id=oficina_id,
        activo=True,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def _catalogo(db) -> tuple[Oficina, Oficina, Oficina, TipoTramite]:
    oficinas = [
        Oficina(codigo="OF-A", nombre="Oficina A", activa=True),
        Oficina(codigo="OF-B", nombre="Oficina B", activa=True),
        Oficina(codigo="OF-C", nombre="Oficina C", activa=True),
    ]
    db.add_all(oficinas)
    db.commit()
    for oficina in oficinas:
        db.refresh(oficina)

    tipo = TipoTramite(
        codigo="TR-CIRC",
        nombre="Trámite de circulación",
        oficina_responsable_id=oficinas[0].id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)

    db.add(Plazo(tipo_tramite_id=tipo.id, oficina_id=oficinas[1].id, horas=24))
    db.commit()
    return oficinas[0], oficinas[1], oficinas[2], tipo


def _crear_documento(
    db,
    tipo: TipoTramite,
    *,
    oficina: Oficina | None = None,
    estado: EstadoDocumento = EstadoDocumento.REGISTRADO,
    origen: OrigenDocumento = OrigenDocumento.MANUAL,
) -> Documento:
    documento = crear_documento(
        db,
        DatosDocumento(
            tipo_tramite_id=tipo.id,
            asunto="Trámite de prueba",
            estado=estado,
            origen=origen,
            oficina_actual_id=oficina.id if oficina else None,
        ),
    )
    db.commit()
    db.refresh(documento)
    return documento


def _en_oficina(db, tipo, oficina, admin_user, usuario_oficina) -> Documento:
    documento = _crear_documento(db, tipo)
    derivar(db, documento, destino=oficina, usuario=admin_user)
    recibir(db, documento, usuario=usuario_oficina)
    db.commit()
    db.refresh(documento)
    return documento


def test_derivar_y_recibir_inicia_el_plazo(client: TestClient, admin_user, db):
    _, oficina_b, _, tipo = _catalogo(db)
    _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    documento = _crear_documento(db, tipo)

    token = _login(client, "admin@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/derivar",
        data={
            "destino_id": str(oficina_b.id),
            "observacion": "Para revisión",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.DERIVADO
    assert documento.oficina_actual_id == oficina_b.id

    movimiento = db.scalar(select(Movimiento).where(Movimiento.documento_id == documento.id))
    assert movimiento is not None
    assert movimiento.oficina_origen_id is None
    assert movimiento.oficina_destino_id == oficina_b.id
    assert movimiento.sla_horas == 24
    assert movimiento.fecha_recepcion is None
    assert movimiento.observacion == "Para revisión"
    assert TipoEvento.DERIVADO in set(
        db.scalars(select(Evento.tipo).where(Evento.documento_id == documento.id)).all()
    )

    token = _login(client, "oficina-b@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/recibir",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    movimiento = db.scalar(select(Movimiento).where(Movimiento.documento_id == documento.id))
    assert documento.estado == EstadoDocumento.EN_OFICINA
    assert movimiento.fecha_recepcion is not None
    assert movimiento.fecha_limite is not None
    assert movimiento.sla_horas == 24
    assert TipoEvento.RECIBIDO in set(
        db.scalars(select(Evento.tipo).where(Evento.documento_id == documento.id)).all()
    )


def test_sla_usa_el_del_tipo_si_no_hay_plazo_por_oficina(client: TestClient, admin_user, db):
    _, _, oficina_c, tipo = _catalogo(db)
    documento = _crear_documento(db, tipo)

    token = _login(client, "admin@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/derivar",
        data={"destino_id": str(oficina_c.id), "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    movimiento = db.scalar(select(Movimiento).where(Movimiento.documento_id == documento.id))
    assert movimiento.sla_horas == tipo.sla_horas


def test_oficina_ajena_no_puede_recibir(client: TestClient, admin_user, db):
    _, oficina_b, oficina_c, tipo = _catalogo(db)
    _crear_usuario(db, Rol.OFICINA, "oficina-c@test.local", oficina_c.id)
    documento = _crear_documento(db, tipo)
    derivar(db, documento, destino=oficina_b, usuario=admin_user)
    db.commit()

    token = _login(client, "oficina-c@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/recibir",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "Acceso denegado" in response.text

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.DERIVADO


def test_oficina_ajena_y_ventanilla_no_pueden_derivar(client: TestClient, admin_user, db):
    oficina_a, oficina_b, oficina_c, tipo = _catalogo(db)
    usuario_b = _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    _crear_usuario(db, Rol.OFICINA, "oficina-c@test.local", oficina_c.id)
    _crear_usuario(db, Rol.VENTANILLA, "ventanilla@test.local")
    documento = _en_oficina(db, tipo, oficina_b, admin_user, usuario_b)

    token = _login(client, "oficina-c@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/derivar",
        data={"destino_id": str(oficina_a.id), "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 403

    token = _login(client, "ventanilla@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/derivar",
        data={"destino_id": str(oficina_c.id), "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_observar_y_subsanar(client: TestClient, admin_user, db):
    _, oficina_b, _, tipo = _catalogo(db)
    usuario_b = _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    documento = _en_oficina(db, tipo, oficina_b, admin_user, usuario_b)

    token = _login(client, "oficina-b@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/observar",
        data={"motivo": "Falta la firma del administrado", "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.OBSERVADO
    evento = db.scalar(
        select(Evento).where(
            Evento.documento_id == documento.id, Evento.tipo == TipoEvento.OBSERVADO
        )
    )
    assert evento is not None
    assert evento.detalle["motivo"] == "Falta la firma del administrado"

    detalle = client.get(f"/documentos/{documento.codigo}", headers=HTML)
    assert "Falta la firma del administrado" in detalle.text

    response = client.post(
        f"/documentos/{documento.codigo}/subsanar",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.EN_OFICINA
    assert (
        db.scalar(
            select(Evento).where(
                Evento.documento_id == documento.id,
                Evento.tipo == TipoEvento.SUBSANADO,
            )
        )
        is not None
    )


def test_finalizar_y_archivar(client: TestClient, admin_user, db):
    _, oficina_b, _, tipo = _catalogo(db)
    usuario_b = _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    documento = _en_oficina(db, tipo, oficina_b, admin_user, usuario_b)

    token = _login(client, "oficina-b@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/finalizar",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    movimiento = db.scalar(select(Movimiento).where(Movimiento.documento_id == documento.id))
    assert documento.estado == EstadoDocumento.FINALIZADO
    assert documento.fecha_finalizacion is not None
    assert movimiento.fecha_salida is not None

    response = client.post(
        f"/documentos/{documento.codigo}/finalizar",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "No se puede finalizar" in response.text

    response = client.post(
        f"/documentos/{documento.codigo}/archivar",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.ARCHIVADO
    assert (
        db.scalar(
            select(Evento).where(
                Evento.documento_id == documento.id,
                Evento.tipo == TipoEvento.ARCHIVADO,
            )
        )
        is not None
    )


def test_solo_admin_o_direccion_anulan(client: TestClient, admin_user, db):
    _, oficina_b, _, tipo = _catalogo(db)
    usuario_b = _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    usuario_direccion = _crear_usuario(db, Rol.DIRECCION, "direccion@test.local")
    documento = _en_oficina(db, tipo, oficina_b, admin_user, usuario_b)

    token = _login(client, "oficina-b@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/anular",
        data={"motivo": "Intento sin permiso", "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 403

    token = _login(client, "direccion@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/anular",
        data={"motivo": "Documento duplicado", "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.ANULADO
    evento = db.scalar(
        select(Evento).where(Evento.documento_id == documento.id, Evento.tipo == TipoEvento.ANULADO)
    )
    assert evento is not None
    assert evento.detalle["motivo"] == "Documento duplicado"
    assert usuario_direccion.id == evento.usuario_id


def test_historico_archivado_no_se_mueve(client: TestClient, admin_user, db):
    oficina_a, oficina_b, _, tipo = _catalogo(db)
    documento = _crear_documento(
        db,
        tipo,
        oficina=oficina_a,
        estado=EstadoDocumento.ARCHIVADO,
        origen=OrigenDocumento.HISTORICO,
    )

    token = _login(client, "admin@test.local")
    response = client.post(
        f"/documentos/{documento.codigo}/derivar",
        data={"destino_id": str(oficina_b.id), "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento).where(Documento.id == documento.id))
    assert documento.estado == EstadoDocumento.ARCHIVADO
    assert db.scalar(select(Movimiento).where(Movimiento.documento_id == documento.id)) is None

    detalle = client.get(f"/documentos/{documento.codigo}", headers=HTML)
    assert "No se puede derivar" in detalle.text


def test_acciones_exigen_csrf(client: TestClient, admin_user, db):
    _, oficina_b, _, tipo = _catalogo(db)
    documento = _crear_documento(db, tipo)
    _login(client, "admin@test.local")

    response = client.post(
        f"/documentos/{documento.codigo}/derivar",
        data={"destino_id": str(oficina_b.id), "csrf_token": "invalido"},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_bandeja_de_oficina_muestra_solo_sus_documentos(client: TestClient, admin_user, db):
    _, oficina_b, oficina_c, tipo = _catalogo(db)
    usuario_b = _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    usuario_c = _crear_usuario(db, Rol.OFICINA, "oficina-c@test.local", oficina_c.id)

    pendiente = _crear_documento(db, tipo)
    derivar(db, pendiente, destino=oficina_b, usuario=admin_user)
    db.commit()

    en_b = _en_oficina(db, tipo, oficina_b, admin_user, usuario_b)
    en_c = _en_oficina(db, tipo, oficina_c, admin_user, usuario_c)

    _login(client, "oficina-b@test.local")
    response = client.get("/bandejas", headers=HTML)

    assert response.status_code == 200
    assert pendiente.codigo in response.text
    assert en_b.codigo in response.text
    assert en_c.codigo not in response.text
    assert "Recibir" in response.text


def test_bandeja_de_ventanilla_lista_registrados(client: TestClient, admin_user, db):
    _, _, _, tipo = _catalogo(db)
    _crear_usuario(db, Rol.VENTANILLA, "ventanilla@test.local")
    documento = _crear_documento(db, tipo)

    _login(client, "ventanilla@test.local")
    response = client.get("/bandejas", headers=HTML)

    assert response.status_code == 200
    assert documento.codigo in response.text
    assert "Registrados por derivar" in response.text
    assert "Por recibir" not in response.text


def test_detalle_muestra_acciones_segun_rol(client: TestClient, admin_user, db):
    _, oficina_b, _, tipo = _catalogo(db)
    usuario_b = _crear_usuario(db, Rol.OFICINA, "oficina-b@test.local", oficina_b.id)
    documento = _en_oficina(db, tipo, oficina_b, admin_user, usuario_b)

    _login(client, "oficina-b@test.local")
    detalle = client.get(f"/documentos/{documento.codigo}", headers=HTML)
    assert "Derivar" in detalle.text
    assert "Observar" in detalle.text
    assert "Finalizar" in detalle.text
    assert "Anular" not in detalle.text

    _login(client, "admin@test.local")
    detalle = client.get(f"/documentos/{documento.codigo}", headers=HTML)
    assert "Anular" in detalle.text
