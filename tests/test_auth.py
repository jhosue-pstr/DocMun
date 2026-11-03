"""Pruebas de autenticación y sesión."""

import re

from fastapi.testclient import TestClient

HTML = {"accept": "text/html"}


def _csrf(client: TestClient) -> str:
    response = client.get("/login", headers=HTML)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "El formulario de login debe incluir el token CSRF"
    return match.group(1)


def test_login_correcto(client: TestClient, admin_user):
    token = _csrf(client)
    response = client.post(
        "/login",
        data={
            "email": "admin@test.local",
            "password": "secreto123",
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/panel"

    panel = client.get("/panel", headers=HTML)
    assert panel.status_code == 200
    assert "Panel de control" in panel.text
    assert "Admin de Prueba" in panel.text


def test_login_con_password_incorrecta(client: TestClient, admin_user):
    token = _csrf(client)
    response = client.post(
        "/login",
        data={
            "email": "admin@test.local",
            "password": "incorrecta",
            "csrf_token": token,
        },
        headers=HTML,
    )
    assert response.status_code == 401
    assert "Correo o contraseña incorrectos" in response.text


def test_panel_sin_sesion_redirige_a_login(client: TestClient):
    response = client.get("/panel", headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_sin_csrf_es_rechazado(client: TestClient, admin_user):
    response = client.post(
        "/login",
        data={
            "email": "admin@test.local",
            "password": "secreto123",
            "csrf_token": "falso",
        },
        headers=HTML,
    )
    assert response.status_code == 400
