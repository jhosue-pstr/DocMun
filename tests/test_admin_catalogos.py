"""Pruebas del CRUD de catálogos en /admin."""

import re
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models.catalogos import Configuracion, Feriado, Oficina, Plazo, TipoTramite
from app.models.enums import Rol
from app.models.usuarios import Usuario

HTML = {"accept": "text/html"}


def _csrf(client: TestClient, path: str = "/login") -> str:
    response = client.get(path, headers=HTML)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, f"La página {path} debe incluir el token CSRF"
    return match.group(1)


def _login(client: TestClient, email: str, password: str) -> str:
    token = _csrf(client)
    response = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _csrf(client, "/panel")


def _login_admin(client: TestClient, admin_user) -> str:
    return _login(client, "admin@test.local", "secreto123")


def _crear_oficina(db, codigo: str = "OF-99") -> Oficina:
    oficina = Oficina(codigo=codigo, nombre=f"Oficina {codigo}", activa=True)
    db.add(oficina)
    db.commit()
    db.refresh(oficina)
    return oficina


def _crear_tipo(db, oficina: Oficina, codigo: str = "TR-99") -> TipoTramite:
    tipo = TipoTramite(
        codigo=codigo,
        nombre=f"Tipo {codigo}",
        area="Pruebas",
        oficina_responsable_id=oficina.id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)
    return tipo


def test_admin_resumen_lista_secciones(client: TestClient, admin_user):
    _login_admin(client, admin_user)
    response = client.get("/admin", headers=HTML)
    assert response.status_code == 200
    for texto in ("Oficinas", "Tipos de trámite", "Plazos", "Feriados", "Configuración"):
        assert texto in response.text


def test_oficina_crear_editar_y_desactivar(client: TestClient, admin_user, db):
    token = _login_admin(client, admin_user)

    response = client.post(
        "/admin/oficinas/nueva",
        data={
            "codigo": "of-99",
            "nombre": "Oficina Nueva",
            "siglas": "new",
            "ubicacion": "Piso 2",
            "email": "oficina99@muni.gob.pe",
            "telefono": "999888777",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    oficina = db.scalar(select(Oficina).where(Oficina.codigo == "OF-99"))
    assert oficina is not None
    assert oficina.nombre == "Oficina Nueva"
    assert oficina.siglas == "NEW"

    response = client.post(
        f"/admin/oficinas/{oficina.id}/editar",
        data={
            "codigo": "OF-99",
            "nombre": "Oficina Renombrada",
            "siglas": "NEW",
            "ubicacion": "",
            "email": "",
            "telefono": "",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(oficina)
    assert oficina.nombre == "Oficina Renombrada"

    response = client.post(
        f"/admin/oficinas/{oficina.id}/estado",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(oficina)
    assert oficina.activa is False


def test_oficina_duplicada_muestra_error(client: TestClient, admin_user, db):
    _crear_oficina(db, "OF-98")
    token = _login_admin(client, admin_user)

    response = client.post(
        "/admin/oficinas/nueva",
        data={
            "codigo": "OF-98",
            "nombre": "Otra oficina",
            "siglas": "",
            "ubicacion": "",
            "email": "",
            "telefono": "",
            "csrf_token": token,
        },
        headers=HTML,
    )
    assert response.status_code == 400
    assert "Ya existe una oficina con ese código." in response.text


def test_tipo_tramite_crear_y_editar(client: TestClient, admin_user, db):
    oficina = _crear_oficina(db)
    token = _login_admin(client, admin_user)

    response = client.post(
        "/admin/tipos-tramite/nuevo",
        data={
            "codigo": "tr-99",
            "nombre": "Trámite Nuevo",
            "area": "Desarrollo Urbano",
            "oficina_responsable_id": str(oficina.id),
            "sla_horas": "72",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    tipo = db.scalar(select(TipoTramite).where(TipoTramite.codigo == "TR-99"))
    assert tipo is not None
    assert tipo.oficina_responsable_id == oficina.id
    assert tipo.sla_horas == 72

    response = client.post(
        f"/admin/tipos-tramite/{tipo.id}/editar",
        data={
            "codigo": "TR-99",
            "nombre": "Trámite Renombrado",
            "area": "",
            "oficina_responsable_id": "",
            "sla_horas": "96",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(tipo)
    assert tipo.nombre == "Trámite Renombrado"
    assert tipo.sla_horas == 96
    assert tipo.oficina_responsable_id is None


def test_tipo_sla_invalido_muestra_error(client: TestClient, admin_user, db):
    token = _login_admin(client, admin_user)
    response = client.post(
        "/admin/tipos-tramite/nuevo",
        data={
            "codigo": "TR-97",
            "nombre": "Trámite inválido",
            "area": "",
            "oficina_responsable_id": "",
            "sla_horas": "0",
            "csrf_token": token,
        },
        headers=HTML,
    )
    assert response.status_code == 400
    assert "El SLA debe ser mayor que cero." in response.text


def test_plazo_upsert_y_eliminar(client: TestClient, admin_user, db):
    oficina = _crear_oficina(db)
    tipo = _crear_tipo(db, oficina)
    token = _login_admin(client, admin_user)

    response = client.post(
        "/admin/plazos",
        data={
            "tipo_tramite_id": str(tipo.id),
            "oficina_id": str(oficina.id),
            "horas": "48",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    plazo = db.scalar(
        select(Plazo).where(Plazo.tipo_tramite_id == tipo.id, Plazo.oficina_id == oficina.id)
    )
    assert plazo is not None
    assert plazo.horas == 48

    client.post(
        "/admin/plazos",
        data={
            "tipo_tramite_id": str(tipo.id),
            "oficina_id": str(oficina.id),
            "horas": "24",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    db.refresh(plazo)
    assert plazo.horas == 24
    assert db.scalar(select(Plazo).where(Plazo.id == plazo.id)) is not None

    response = client.post(
        f"/admin/plazos/{plazo.id}/eliminar",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert db.scalar(select(Plazo).where(Plazo.id == plazo.id)) is None


def test_feriado_duplicado_muestra_error(client: TestClient, admin_user, db):
    token = _login_admin(client, admin_user)
    datos = {
        "fecha": "2026-01-01",
        "descripcion": "Año Nuevo",
        "csrf_token": token,
    }

    response = client.post("/admin/feriados", data=datos, headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert db.scalar(select(Feriado).where(Feriado.fecha == date(2026, 1, 1))) is not None

    response = client.post("/admin/feriados", data=datos, headers=HTML)
    assert response.status_code == 200
    assert "Ya existe un feriado en esa fecha." in response.text


def test_configuracion_valida_json_y_actualiza(client: TestClient, admin_user, db):
    db.add(
        Configuracion(
            clave="horario_laboral",
            valor='{"lunes_viernes": ["08:00", "16:00"]}',
            descripcion="Horario",
        )
    )
    db.commit()
    token = _login_admin(client, admin_user)

    response = client.post(
        "/admin/configuracion/horario_laboral",
        data={
            "valor": "no es json",
            "descripcion": "Horario",
            "csrf_token": token,
        },
        headers=HTML,
    )
    assert response.status_code == 200
    assert "JSON válido" in response.text

    response = client.post(
        "/admin/configuracion/horario_laboral",
        data={
            "valor": '{"lunes_viernes": ["07:00", "15:00"]}',
            "descripcion": "Horario de atención",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=True,
    )
    assert response.status_code == 200
    db.expire_all()
    parametro = db.get(Configuracion, "horario_laboral")
    assert "07:00" in parametro.valor
    assert parametro.descripcion == "Horario de atención"


def test_configuracion_rechaza_tramos_superpuestos(client: TestClient, admin_user, db):
    valor_original = '{"lunes_viernes": ["08:00", "16:00"]}'
    db.add(
        Configuracion(
            clave="horario_laboral",
            valor=valor_original,
            descripcion="Horario",
        )
    )
    db.commit()
    token = _login_admin(client, admin_user)

    response = client.post(
        "/admin/configuracion/horario_laboral",
        data={
            "valor": '{"lunes_viernes": [["08:00", "13:00"], ["12:00", "17:00"]]}',
            "descripcion": "Horario",
            "csrf_token": token,
        },
        headers=HTML,
    )

    assert response.status_code == 200
    assert "no pueden superponerse" in response.text
    db.expire_all()
    assert db.get(Configuracion, "horario_laboral").valor == valor_original


def test_no_admin_recibe_403(client: TestClient, db):
    db.add(
        Usuario(
            nombre="Ventanilla",
            email="ventanilla@test.local",
            password_hash=hash_password("secreto123"),
            rol=Rol.VENTANILLA,
            activo=True,
        )
    )
    db.commit()

    _login(client, "ventanilla@test.local", "secreto123")
    response = client.get("/admin", headers=HTML)
    assert response.status_code == 403
    assert "Acceso denegado" in response.text


def test_admin_post_sin_csrf_es_rechazado(client: TestClient, admin_user):
    _login_admin(client, admin_user)
    response = client.post(
        "/admin/oficinas/nueva",
        data={
            "codigo": "OF-96",
            "nombre": "Sin CSRF",
            "siglas": "",
            "ubicacion": "",
            "email": "",
            "telefono": "",
            "csrf_token": "falso",
        },
        headers=HTML,
    )
    assert response.status_code == 400
