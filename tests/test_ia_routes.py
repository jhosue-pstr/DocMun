"""Pruebas de las rutas de análisis IA (borrador, detalle y reanálisis)."""

import hashlib
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.analisis import DocumentoAnalisis
from app.models.archivos import DocumentoArchivo
from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento, Evento
from app.models.enums import EstadoAnalisis, Rol, TipoEvento
from app.models.usuarios import Usuario
from app.services.archivos import ArchivoValidado, guardar_validado
from app.services.documentos import DatosDocumento, crear_documento
from app.services.ia import cola, motor

HTML = {"accept": "text/html"}
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
TEXTO = (
    "MUNICIPALIDAD PROVINCIAL\nCARTA N° 045-2026-MP\n"
    "ASUNTO: Solicitud de certificado de numeración municipal\n"
    "SEÑOR ALCALDE:\nYo, Ana Pérez, identificada con DNI: 12345678, solicito.\n"
)


def _csrf(client: TestClient, path: str) -> str:
    response = client.get(path, headers=HTML)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, f"La página {path} debe incluir el token CSRF"
    return match.group(1)


def _login(client: TestClient, email: str, password: str = "secreto123") -> str:
    token = _csrf(client, "/login")
    response = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _csrf(client, "/panel")


@pytest.fixture()
def ventanilla(db) -> Usuario:
    usuario = Usuario(
        nombre="Ventanilla de Prueba",
        email="ventanilla@test.local",
        password_hash=hash_password("secreto123"),
        rol=Rol.VENTANILLA,
        activo=True,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture()
def ia_activa(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ia_habilitada", True)
    monkeypatch.setattr(motor, "analizar_archivo", lambda ruta, **kwargs: _resultado())
    return tmp_path


def _resultado() -> motor.ResultadoMotor:
    return motor.ResultadoMotor(
        texto_ocr=TEXTO,
        paginas=1,
        tipo_clase="letter",
        tipo_confianza=0.35,
        top_clases=[{"clase": "letter", "probabilidad": 0.35}],
        motor="tesseract+layoutlmv3",
        version_modelo="layoutlmv3-test",
        duracion_ms=900,
    )


def _catalogo(db) -> tuple[Oficina, TipoTramite]:
    oficina = Oficina(codigo="OF-01", nombre="Obras", activa=True)
    db.add(oficina)
    db.commit()
    tipo = TipoTramite(
        codigo="TR-01",
        nombre="Numeración municipal",
        area="Obras",
        oficina_responsable_id=oficina.id,
        sla_horas=48,
        activo=True,
    )
    db.add(tipo)
    db.commit()
    return oficina, tipo


def _documento(db, tipo) -> Documento:
    documento = crear_documento(
        db, DatosDocumento(tipo_tramite_id=tipo.id, asunto="Solicitud de prueba"), anio_codigo=2026
    )
    db.commit()
    db.refresh(documento)
    return documento


def _validado(contenido: bytes = PDF) -> ArchivoValidado:
    return ArchivoValidado(
        contenido=contenido,
        nombre_original="oficio.pdf",
        mime="application/pdf",
        extension=".pdf",
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
    )


def _datos_registro(tipo: TipoTramite, **extra) -> dict[str, str]:
    datos = {
        "tipo_tramite_id": str(tipo.id),
        "numero_certificado": "",
        "anio": "",
        "prioridad": "normal",
        "asunto": "Certificado de numeración",
        "referencia": "",
        "rut": "",
        "expediente_administrativo": "",
        "ruc": "",
        "administrado": "",
        "observaciones": "",
        "ciudadano_tipo_documento": "dni",
        "ciudadano_numero_documento": "",
        "ciudadano_nombres": "",
        "ciudadano_email": "",
        "ciudadano_telefono": "",
        "ciudadano_direccion": "",
        "analisis_id": "",
    }
    datos.update(extra)
    return datos


def test_analizar_borrador_deshabilitado(client, ventanilla, monkeypatch):
    monkeypatch.setattr(settings, "ia_habilitada", False)
    token = _login(client, "ventanilla@test.local")

    response = client.post(
        "/documentos/analizar-borrador",
        data={"csrf_token": token},
        files={"archivo": ("oficio.pdf", PDF, "application/pdf")},
        headers=HTML,
    )
    assert response.status_code == 200
    assert "deshabilitado" in response.text
    assert "Sugerencias de IA" not in response.text


def test_analizar_borrador_y_ver_sugerencias(client, ventanilla, db, ia_activa):
    _catalogo(db)
    token = _login(client, "ventanilla@test.local")


    response = client.post(
        "/documentos/analizar-borrador",
        data={"csrf_token": token},
        files={"archivo": ("oficio.pdf", PDF, "application/pdf")},
        headers=HTML,
    )
    assert response.status_code == 200
    assert "Analizando el documento" in response.text

    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    assert analisis is not None
    assert analisis.documento_id is None
    assert analisis.estado == EstadoAnalisis.PENDIENTE
    assert (ia_activa / analisis.ruta_borrador).is_file()

    assert cola.procesar_pendientes(db).procesados == 1

    response = client.get(f"/documentos/analizar-borrador/{analisis.id}", headers=HTML)
    assert response.status_code == 200
    assert "Sugerencias de IA" in response.text
    assert "12345678" in response.text
    assert "TR-01" in response.text
    assert f"'{analisis.id}'" in response.text or f'"{analisis.id}"' in response.text
    assert "no se usa para asignar el trámite" not in response.text

    # El endpoint de detalle de archivo no debe exponer un borrador ajeno.
    response = client.get(f"/documentos/analizar-borrador/{analisis.id + 999}", headers=HTML)
    assert response.status_code == 404


def test_registrar_reutilizando_borrador(client, ventanilla, db, ia_activa):
    _, tipo = _catalogo(db)
    token = _login(client, "ventanilla@test.local")

    client.post(
        "/documentos/analizar-borrador",
        data={"csrf_token": token},
        files={"archivo": ("oficio.pdf", PDF, "application/pdf")},
        headers=HTML,
    )
    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    cola.procesar_pendientes(db)

    token = _csrf(client, "/documentos/nuevo")
    response = client.post(
        "/documentos/nuevo",
        data={
            **_datos_registro(tipo, analisis_id=str(analisis.id), administrado="Ana Pérez"),
            "csrf_token": token,
        },
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    documento = db.scalar(select(Documento))
    assert documento is not None
    archivo = db.scalar(select(DocumentoArchivo))
    assert archivo is not None
    assert archivo.documento_id == documento.id
    assert archivo.hash_sha256 == hashlib.sha256(PDF).hexdigest()

    actualizado = db.get(DocumentoAnalisis, analisis.id)
    assert actualizado.documento_id == documento.id
    assert actualizado.archivo_id == archivo.id
    assert actualizado.ruta_borrador is None
    assert actualizado.tipo_tramite_confirmado_id == tipo.id
    assert archivo.texto_ocr.startswith("MUNICIPALIDAD")
    assert not (ia_activa / "borradores").joinpath(
        f"{archivo.hash_sha256}.pdf"
    ).exists()


def test_registrar_con_borrador_pendiente_lo_procesa_en_el_expediente(
    client, ventanilla, db, ia_activa
):
    _, tipo = _catalogo(db)
    token = _login(client, "ventanilla@test.local")

    client.post(
        "/documentos/analizar-borrador",
        data={"csrf_token": token},
        files={"archivo": ("oficio.pdf", PDF, "application/pdf")},
        headers=HTML,
    )
    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    assert analisis.estado == EstadoAnalisis.PENDIENTE

    # Registra sin esperar el OCR: el borrador se enlaza igual.
    token = _csrf(client, "/documentos/nuevo")
    response = client.post(
        "/documentos/nuevo",
        data={**_datos_registro(tipo, analisis_id=str(analisis.id)), "csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    enlazado = db.get(DocumentoAnalisis, analisis.id)
    assert enlazado.documento_id is not None
    assert enlazado.archivo_id is not None
    assert enlazado.ruta_borrador is None

    assert cola.procesar_pendientes(db).procesados == 1
    db.expire_all()
    actualizado = db.get(DocumentoAnalisis, analisis.id)
    assert actualizado.estado == EstadoAnalisis.LISTO
    assert actualizado.texto_ocr.startswith("MUNICIPALIDAD")
    assert actualizado.archivo.texto_ocr.startswith("MUNICIPALIDAD")

    eventos = db.scalars(
        select(Evento).where(Evento.documento_id == enlazado.documento_id)
    ).all()
    assert any(evento.tipo == TipoEvento.ANALIZADO for evento in eventos)


def test_borrador_de_otro_archivo_no_se_reutiliza(client, ventanilla, db, ia_activa):
    _, tipo = _catalogo(db)
    token = _login(client, "ventanilla@test.local")

    client.post(
        "/documentos/analizar-borrador",
        data={"csrf_token": token},
        files={"archivo": ("oficio.pdf", PDF, "application/pdf")},
        headers=HTML,
    )
    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    cola.procesar_pendientes(db)

    otro_pdf = PDF + b"%" * 8
    token = _csrf(client, "/documentos/nuevo")
    response = client.post(
        "/documentos/nuevo",
        data={**_datos_registro(tipo, analisis_id=str(analisis.id)), "csrf_token": token},
        files={"archivo": ("otro.pdf", otro_pdf, "application/pdf")},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    archivo = db.scalar(select(DocumentoArchivo))
    assert archivo is not None
    assert archivo.hash_sha256 == hashlib.sha256(otro_pdf).hexdigest()
    # El borrador sigue sin dueño y su archivo sigue en storage.
    borrador = db.get(DocumentoAnalisis, analisis.id)
    assert borrador.documento_id is None
    assert borrador.ruta_borrador is not None
    assert (ia_activa / borrador.ruta_borrador).is_file()


def test_detalle_muestra_el_analisis_del_archivo(client, admin_user, db, ia_activa):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    guardar_validado(db, documento, _validado())
    db.commit()
    cola.procesar_pendientes(db)

    token = _login(client, "admin@test.local")
    response = client.get(f"/documentos/{documento.codigo}", headers=HTML)
    assert response.status_code == 200
    assert "Análisis listo" in response.text
    assert "12345678" in response.text
    assert token


def test_reanalizar_archivo_lo_vuelve_a_encolar(client, admin_user, db, ia_activa):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    archivo = guardar_validado(db, documento, _validado())
    db.commit()
    cola.procesar_pendientes(db)

    token = _login(client, "admin@test.local")
    token = _csrf(client, f"/documentos/{documento.codigo}")
    response = client.post(
        f"/documentos/{documento.codigo}/archivos/{archivo.id}/analizar",
        data={"csrf_token": token},
        headers=HTML,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db.expire_all()
    analisis = db.scalar(select(DocumentoAnalisis))
    assert analisis.estado == EstadoAnalisis.PENDIENTE
    assert analisis.intentos == 0
    assert analisis.hash_sha256 == archivo.hash_sha256


def test_analisis_requiere_sesion(client, db, ia_activa):
    _, tipo = _catalogo(db)
    documento = _documento(db, tipo)
    archivo = guardar_validado(db, documento, _validado())
    db.commit()

    response = client.get(f"/documentos/{documento.codigo}/archivos/{archivo.id}/analisis")
    assert response.status_code == 401
