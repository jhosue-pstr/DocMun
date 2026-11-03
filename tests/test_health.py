"""Pruebas de infraestructura básica."""

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "DocMun"


def test_raiz_redirige_a_login_sin_sesion(client: TestClient):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
