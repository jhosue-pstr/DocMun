"""Pruebas de registro, adjuntos y QR de documentos."""

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.archivos import DocumentoArchivo
from app.models.catalogos import Oficina, TipoTramite
from app.models.ciudadanos import Ciudadano
from app.models.documentos import Documento, Evento
from app.models.enums import (
    Rol,
    TipoEvento,
)
from app.models.usuarios import Usuario
from app.services.documentos import DatosDocumento, crear_documento

HTML = {"accept": "text/html"}
PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"%%EOF\n"
)


def _csrf(client: TestClient, path: str = "/login") -> str:
    response = client.get(path, headers=HTML)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, f"La página {path} debe incluir el token CSRF"
    return match.group(1)


def _login(client: TestClient, email: str, password: str = "secreto123") -> str:
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


def _crear_catalogo(db) -> tuple[Oficina, TipoTramite]:
    oficina = Oficina(codigo="OF-DOC", nombre="Oficina de Documentos", activa=True)
    db.add(oficina)
    db.commit()
    db.refresh(oficina)

    tipo = TipoTramite(
        codigo="TR-DOC",
        nombre="Certificado de prueba",
        area="Pruebas",
        oficina_responsable_id=oficina.id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    db.refresh(tipo)
    db.commit()
    return oficina, tipo


def _crear_documento(db, tipo: TipoTramite, **extra) -> Documento:
    campos = {
        "tipo_tramite_id": tipo.id,
        "numero_certificado": "001-2026",
        "anio": 2026,
        "asunto": "Solicitud de prueba",
    }
    campos.update(extra)
    documento = crear_documento(
        db,
        DatosDocumento(**campos),
        anio_codigo=2026,
    )
    db.commit()
    db.refresh(documento)
    db.commit()
    return documento


def _datos_registro(tipo: TipoTramite, **extra) -> dict[str, str]:
    datos = {
        "tipo_tramite_id": str(tipo.id),
        "numero_certificado": "123-2026",
        "anio": "2026",
        "prioridad": "normal",
        "asunto": "Licencia de funcionamiento",
        "referencia": "Expediente 45",
        "rut": "RUT-001",
        "expediente_administrativo": "EXP-001",
        "ruc": "",
        "administrado": "",
        "observaciones": "",
        "ciudadano_tipo_documento": "dni",
        "ciudadano_numero_documento": "12345678",
        "ciudadano_nombres": "Ana Pérez",
        "ciudadano_email": "ana@example.com",
        "ciudadano_telefono": "999888777",
        "ciudadano_direccion": "",
    }
    datos.update(extra)
    return datos


def test_registrar_documento_con_pdf_y_ciudadano(
    client: TestClient, admin_user, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    _, tipo = _crear_catalogo(db)
    token = _login(client, "admin@test.local")

    response = client.post(
        "/documentos/nuevo",
        data={**_datos_registro(tipo), "csrf_token": token},
        files={"archivo": ("escaneo.pdf", PDF, "application/pdf")},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento))
    assert documento is not None
    assert documento.codigo.startswith("DOC-2026-")
    assert response.headers["location"] == f"/documentos/{documento.codigo}"
    assert documento.ciudadano is not None
    assert documento.ciudadano.nombres == "Ana Pérez"
    assert documento.ciudadano.numero_documento == "12345678"
    assert documento.numero_certificado == "123-2026"

    ciudadano = db.scalar(select(Ciudadano))
    assert ciudadano is not None
    assert ciudadano.nombres == "Ana Pérez"

    archivo = db.scalar(select(DocumentoArchivo))
    assert archivo is not None
    assert archivo.documento_id == documento.id
    assert archivo.mime == "application/pdf"
    assert archivo.tamano_bytes == len(PDF)
    assert (tmp_path / archivo.ruta).is_file()

    tipos = set(db.scalars(select(Evento.tipo).where(Evento.documento_id == documento.id)).all())
    assert TipoEvento.CREADO in tipos
    assert TipoEvento.ADJUNTO_AGREGADO in tipos


def test_registrar_sin_archivo_queda_pendiente_de_escaneo(
    client: TestClient, admin_user, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    _, tipo = _crear_catalogo(db)
    token = _login(client, "admin@test.local")

    response = client.post(
        "/documentos/nuevo",
        data={
            **_datos_registro(
                tipo,
                numero_certificado="",
                anio="",
                ciudadano_numero_documento="",
                ciudadano_nombres="",
                ciudadano_email="",
                ciudadano_telefono="",
            ),
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento))
    assert documento is not None
    assert documento.archivos == []
    assert db.scalar(select(DocumentoArchivo)) is None


def test_numero_sin_anio_muestra_error(client: TestClient, admin_user, db):
    _, tipo = _crear_catalogo(db)
    token = _login(client, "admin@test.local")

    response = client.post(
        "/documentos/nuevo",
        data={**_datos_registro(tipo, anio=""), "csrf_token": token},
        headers=HTML,
    )
    assert response.status_code == 400
    assert "indica también el año" in response.text
    db.expire_all()
    assert db.scalar(select(Documento)) is None


def test_certificado_duplicado_muestra_error(client: TestClient, admin_user, db):
    _, tipo = _crear_catalogo(db)
    _crear_documento(db, tipo, numero_certificado="123-2026", anio=2026)
    token = _login(client, "admin@test.local")

    response = client.post(
        "/documentos/nuevo",
        data={**_datos_registro(tipo), "csrf_token": token},
        headers=HTML,
    )
    assert response.status_code == 400
    assert "Ya existe el documento 123-2026" in response.text
    db.expire_all()
    assert len(db.scalars(select(Documento)).all()) == 1


def test_archivo_invalido_no_crea_documento(
    client: TestClient, admin_user, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    _, tipo = _crear_catalogo(db)
    token = _login(client, "admin@test.local")

    response = client.post(
        "/documentos/nuevo",
        data={**_datos_registro(tipo), "csrf_token": token},
        files={"archivo": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")},
        headers=HTML,
    )
    assert response.status_code == 400
    assert "Formato no permitido" in response.text
    db.expire_all()
    assert db.scalar(select(Documento)) is None
    assert db.scalar(select(DocumentoArchivo)) is None


def test_oficina_puede_adjuntar_pero_no_registrar(client: TestClient, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    oficina, tipo = _crear_catalogo(db)
    _crear_usuario(db, Rol.OFICINA, "oficina@test.local", oficina.id)
    documento = _crear_documento(db, tipo)
    token = _login(client, "oficina@test.local")

    response = client.get("/documentos/nuevo", headers=HTML)
    assert response.status_code == 403
    assert "Acceso denegado" in response.text

    response = client.post(
        f"/documentos/{documento.codigo}/adjuntar",
        data={"csrf_token": token},
        files={"archivo": ("anexo.pdf", PDF, "application/pdf")},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    archivo = db.scalar(select(DocumentoArchivo))
    assert archivo is not None
    assert archivo.documento_id == documento.id
    assert archivo.subido_por_id is not None


def test_adjunto_duplicado_no_se_guarda_dos_veces(
    client: TestClient, admin_user, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    _, tipo = _crear_catalogo(db)
    documento = _crear_documento(db, tipo)
    token = _login(client, "admin@test.local")

    for _ in range(2):
        client.post(
            f"/documentos/{documento.codigo}/adjuntar",
            data={"csrf_token": token},
            files={"archivo": ("anexo.pdf", PDF, "application/pdf")},
            headers=HTML,
            follow_redirects=False,
        )

    db.expire_all()
    assert len(db.scalars(select(DocumentoArchivo)).all()) == 1


def test_descarga_archivo_autenticado(client: TestClient, admin_user, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    _, tipo = _crear_catalogo(db)
    documento = _crear_documento(db, tipo)
    token = _login(client, "admin@test.local")

    client.post(
        f"/documentos/{documento.codigo}/adjuntar",
        data={"csrf_token": token},
        files={"archivo": ("anexo.pdf", PDF, "application/pdf")},
        headers=HTML,
        follow_redirects=False,
    )
    db.expire_all()
    archivo = db.scalar(select(DocumentoArchivo))
    assert archivo is not None

    response = client.get(f"/documentos/{documento.codigo}/archivos/{archivo.id}", headers=HTML)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == PDF

    client.cookies.clear()
    response = client.get(
        f"/documentos/{documento.codigo}/archivos/{archivo.id}",
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_qr_cargo_y_etiqueta(client: TestClient, admin_user, db):
    _, tipo = _crear_catalogo(db)
    documento = _crear_documento(db, tipo)
    _login(client, "admin@test.local")

    qr = client.get(f"/documentos/{documento.codigo}/qr.png", headers=HTML)
    assert qr.status_code == 200
    assert qr.headers["content-type"] == "image/png"
    assert qr.content.startswith(b"\x89PNG\r\n\x1a\n")

    cargo = client.get(f"/documentos/{documento.codigo}/cargo.pdf", headers=HTML)
    assert cargo.status_code == 200
    assert cargo.headers["content-type"] == "application/pdf"
    assert cargo.content.startswith(b"%PDF")

    etiqueta = client.get(f"/documentos/{documento.codigo}/etiqueta.pdf", headers=HTML)
    assert etiqueta.status_code == 200
    assert etiqueta.headers["content-type"] == "application/pdf"
    assert etiqueta.content.startswith(b"%PDF")


def test_detalle_y_lista_filtran(client: TestClient, admin_user, db):
    _, tipo = _crear_catalogo(db)
    primero = _crear_documento(
        db, tipo, asunto="Licencia de edificación", numero_certificado="111-2026"
    )
    segundo = _crear_documento(
        db, tipo, asunto="Certificado de catastro", numero_certificado="222-2026"
    )
    _login(client, "admin@test.local")

    detalle = client.get(f"/documentos/{primero.codigo}", headers=HTML)
    assert detalle.status_code == 200
    assert primero.codigo in detalle.text
    assert "Licencia de edificación" in detalle.text
    assert "Imprimir cargo" in detalle.text

    lista = client.get("/documentos?q=Licencia", headers=HTML)
    assert lista.status_code == 200
    assert primero.codigo in lista.text
    assert segundo.codigo not in lista.text

    por_ciudadano = client.get("/documentos?q=12345678", headers=HTML)
    assert por_ciudadano.status_code == 200


def test_filtros_vacios_no_rompen_la_lista(client: TestClient, admin_user, db):
    _, tipo = _crear_catalogo(db)
    documento = _crear_documento(db, tipo, asunto="Licencia de edificación")
    _login(client, "admin@test.local")

    respuesta = client.get(
        "/documentos?q=&estado=&tipo_tramite_id=&oficina_id=&page=",
        headers=HTML,
    )

    assert respuesta.status_code == 200
    assert documento.codigo in respuesta.text
    assert "documento(s)" in respuesta.text


def test_detalle_inexistente_da_404(client: TestClient, admin_user):
    _login(client, "admin@test.local")
    response = client.get("/documentos/DOC-2026-999999", headers=HTML)
    assert response.status_code == 404
