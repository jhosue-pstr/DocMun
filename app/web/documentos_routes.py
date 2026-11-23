"""Rutas de registro, consulta y adjuntos de documentos."""

from __future__ import annotations

import math
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.csrf import verify_csrf
from app.core.deps import require_roles, require_user
from app.db.session import get_db
from app.models.analisis import DocumentoAnalisis
from app.models.archivos import DocumentoArchivo
from app.models.catalogos import Oficina, TipoTramite
from app.models.documentos import Documento
from app.models.enums import (
    EstadoAnalisis,
    EstadoDocumento,
    Prioridad,
    Rol,
    TipoDocumentoIdentidad,
)
from app.models.usuarios import Usuario
from app.services.archivos import (
    ArchivoInvalido,
    ArchivoValidado,
    guardar_archivo,
    guardar_validado,
    ruta_absoluta,
    validar_archivo,
)
from app.services.cargo_pdf import cargo_pdf, etiqueta_pdf
from app.services.circulacion import acciones_disponibles
from app.services.ciudadanos import (
    DatosCiudadano,
    normalizar_numero_documento,
    obtener_ciudadano,
    obtener_o_crear,
)
from app.services.documentos import (
    CodigoNoDisponible,
    DatosDocumento,
    DocumentoInvalido,
    buscar_documentos,
    crear_documento,
    obtener_por_codigo,
)
from app.services.ia.cola import (
    contexto_analisis,
    eliminar_borrador,
    encolar_analisis,
    encolar_borrador,
    ruta_borrador_absoluta,
    vincular_analisis,
)
from app.services.qr import qr_png
from app.services.sla import estado_por_documento
from app.web.flash import flash
from app.web.templating import render

router = APIRouter(prefix="/documentos", tags=["documentos"])

puede_registrar = require_roles(Rol.VENTANILLA)
puede_adjuntar = require_roles(Rol.VENTANILLA, Rol.OFICINA)
PER_PAGE = 25


def _texto(valor: str | None) -> str:
    return (valor or "").strip()


def _entero(valor: str | None) -> int | None:
    valor = _texto(valor)
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        return None


def _estado(valor: str | None) -> EstadoDocumento | None:
    valor = _texto(valor)
    if not valor:
        return None
    try:
        return EstadoDocumento(valor)
    except ValueError:
        return None


def _prioridad(valor: str | None) -> Prioridad:
    try:
        return Prioridad(_texto(valor) or Prioridad.NORMAL.value)
    except ValueError:
        return Prioridad.NORMAL


def _tipo_documento(valor: str | None) -> TipoDocumentoIdentidad:
    try:
        return TipoDocumentoIdentidad(_texto(valor) or TipoDocumentoIdentidad.DNI.value)
    except ValueError:
        return TipoDocumentoIdentidad.DNI


def _documento_o_404(db: Session, codigo: str) -> Documento:
    documento = obtener_por_codigo(db, codigo)
    if documento is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return documento


def _tipos_activos(db: Session) -> list[TipoTramite]:
    return list(
        db.scalars(
            select(TipoTramite).where(TipoTramite.activo.is_(True)).order_by(TipoTramite.codigo)
        ).all()
    )


def _oficinas_activas(db: Session) -> list[Oficina]:
    return list(
        db.scalars(select(Oficina).where(Oficina.activa.is_(True)).order_by(Oficina.codigo)).all()
    )


# ---------------------------------------------------------------------------
# Listado y búsqueda
# ---------------------------------------------------------------------------


@router.get("")
def listar_documentos(
    request: Request,
    q: str | None = None,
    estado: str | None = None,
    tipo_tramite_id: str | None = None,
    oficina_id: str | None = None,
    page: str | None = None,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    estado_enum = _estado(estado)
    tipo_pk = _entero(tipo_tramite_id)
    oficina_pk = _entero(oficina_id)
    pagina = max(_entero(page) or 1, 1)
    documentos, total = buscar_documentos(
        db,
        q=q,
        estado=estado_enum,
        tipo_tramite_id=tipo_pk,
        oficina_id=oficina_pk,
        page=pagina,
        per_page=PER_PAGE,
    )
    total_paginas = max(1, math.ceil(total / PER_PAGE))
    plazos = estado_por_documento(db, documentos)

    filtros = {
        "q": _texto(q),
        "estado": estado_enum.value if estado_enum else "",
        "tipo_tramite_id": tipo_pk,
        "oficina_id": oficina_pk,
    }
    base_query = urlencode(
        {clave: valor for clave, valor in filtros.items() if valor not in ("", None)}
    )

    return render(
        request,
        "documentos/lista.html",
        {
            "current_user": user,
            "documentos": documentos,
            "plazos": plazos,
            "total": total,
            "page": pagina,
            "total_paginas": total_paginas,
            "per_page": PER_PAGE,
            "base_query": base_query,
            "q": _texto(q),
            "estado": estado_enum.value if estado_enum else "",
            "tipo_tramite_id": tipo_pk,
            "oficina_id": oficina_pk,
            "tipos": _tipos_activos(db),
            "oficinas": _oficinas_activas(db),
            "estados": list(EstadoDocumento),
            "puede_registrar": user.rol == Rol.ADMIN or user.rol == Rol.VENTANILLA,
        },
    )


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------


@router.get("/nuevo")
def formulario_nuevo(
    request: Request,
    user: Usuario = Depends(puede_registrar),
    db: Session = Depends(get_db),
):
    return _form_nuevo(request, user, db, _valores_iniciales(), [])


@router.post("/nuevo")
async def registrar_documento(
    request: Request,
    tipo_tramite_id: str = Form(""),
    numero_certificado: str = Form(""),
    anio: str = Form(""),
    prioridad: str = Form("normal"),
    asunto: str = Form(""),
    referencia: str = Form(""),
    rut: str = Form(""),
    expediente_administrativo: str = Form(""),
    ruc: str = Form(""),
    administrado: str = Form(""),
    observaciones: str = Form(""),
    ciudadano_tipo_documento: str = Form("dni"),
    ciudadano_numero_documento: str = Form(""),
    ciudadano_nombres: str = Form(""),
    ciudadano_email: str = Form(""),
    ciudadano_telefono: str = Form(""),
    ciudadano_direccion: str = Form(""),
    csrf_token: str = Form(...),
    archivo: UploadFile | None = File(None),
    analisis_id: str = Form(""),
    user: Usuario = Depends(puede_registrar),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    valores = {
        "analisis_id": _texto(analisis_id),
        "tipo_tramite_id": _texto(tipo_tramite_id),
        "numero_certificado": _texto(numero_certificado),
        "anio": _texto(anio),
        "prioridad": _prioridad(prioridad).value,
        "asunto": _texto(asunto),
        "referencia": _texto(referencia),
        "rut": _texto(rut),
        "expediente_administrativo": _texto(expediente_administrativo),
        "ruc": _texto(ruc),
        "administrado": _texto(administrado),
        "observaciones": _texto(observaciones),
        "ciudadano_tipo_documento": _tipo_documento(ciudadano_tipo_documento).value,
        "ciudadano_numero_documento": normalizar_numero_documento(ciudadano_numero_documento),
        "ciudadano_nombres": _texto(ciudadano_nombres),
        "ciudadano_email": _texto(ciudadano_email),
        "ciudadano_telefono": _texto(ciudadano_telefono),
        "ciudadano_direccion": _texto(ciudadano_direccion),
    }

    errores = _validar_registro(db, valores)
    if errores:
        return _form_nuevo(request, user, db, valores, errores, status_code=400)

    tipo_id = int(valores["tipo_tramite_id"])
    anio_documento = _entero(valores["anio"])
    numero = valores["numero_certificado"] or None

    validado = None
    if archivo is not None and archivo.filename:
        try:
            validado = await validar_archivo(archivo)
        except ArchivoInvalido as exc:
            return _form_nuevo(request, user, db, valores, [str(exc)], status_code=400)

    # Si ventanilla analizó un borrador, reutilizamos su archivo y su resultado.
    # También sirve si el análisis sigue pendiente: el worker lo procesará ya
    # enlazado al expediente, sin repetir el OCR.
    analisis_borrador = None
    analisis_pk = _entero(analisis_id)
    if analisis_pk is not None:
        candidato = db.get(DocumentoAnalisis, analisis_pk)
        if (
            candidato is not None
            and candidato.documento_id is None
            and candidato.estado
            in {EstadoAnalisis.PENDIENTE, EstadoAnalisis.LISTO}
            and (validado is None or validado.hash_sha256 == candidato.hash_sha256)
        ):
            analisis_borrador = candidato

    if validado is None and analisis_borrador is not None:
        try:
            ruta_draft = ruta_borrador_absoluta(analisis_borrador)
            contenido = ruta_draft.read_bytes()
        except (OSError, ValueError):
            analisis_borrador = None
        else:
            validado = ArchivoValidado(
                contenido=contenido,
                nombre_original=analisis_borrador.nombre_original or ruta_draft.name,
                mime=analisis_borrador.mime or "application/pdf",
                extension=ruta_draft.suffix.lower(),
                hash_sha256=analisis_borrador.hash_sha256,
            )

    ruta_borrador = None
    try:
        ciudadano = None
        if valores["ciudadano_numero_documento"]:
            tipo_doc = _tipo_documento(valores["ciudadano_tipo_documento"])
            if valores["ciudadano_nombres"]:
                ciudadano, _ = obtener_o_crear(
                    db,
                    DatosCiudadano(
                        tipo_documento=tipo_doc,
                        numero_documento=valores["ciudadano_numero_documento"],
                        nombres=valores["ciudadano_nombres"],
                        email=valores["ciudadano_email"],
                        telefono=valores["ciudadano_telefono"],
                        direccion=valores["ciudadano_direccion"],
                    ),
                )
            else:
                ciudadano = obtener_ciudadano(db, tipo_doc, valores["ciudadano_numero_documento"])
                if ciudadano is None:
                    raise DocumentoInvalido(
                        "No existe un ciudadano con ese documento. " "Registre también sus nombres."
                    )

        documento = crear_documento(
            db,
            DatosDocumento(
                tipo_tramite_id=tipo_id,
                numero_certificado=numero,
                anio=anio_documento,
                prioridad=_prioridad(valores["prioridad"]),
                ciudadano_id=ciudadano.id if ciudadano is not None else None,
                referencia=valores["referencia"] or None,
                rut=valores["rut"] or None,
                expediente_administrativo=valores["expediente_administrativo"] or None,
                ruc=valores["ruc"] or None,
                administrado=valores["administrado"] or None,
                asunto=valores["asunto"] or None,
                observaciones=valores["observaciones"] or None,
            ),
            usuario=user,
            request=request,
        )

        archivo_guardado = None
        if validado is not None:
            archivo_guardado = guardar_validado(
                db,
                documento,
                validado,
                usuario=user,
                request=request,
                encolar=analisis_borrador is None,
            )

        ruta_borrador = None
        if analisis_borrador is not None and archivo_guardado is not None:
            ruta_borrador = vincular_analisis(
                db,
                analisis_borrador,
                documento,
                archivo_guardado,
                tipo_confirmado_id=tipo_id,
            )

        db.commit()
    except (DocumentoInvalido, ArchivoInvalido) as exc:
        db.rollback()
        return _form_nuevo(request, user, db, valores, [str(exc)], status_code=400)
    except CodigoNoDisponible as exc:
        db.rollback()
        return _form_nuevo(request, user, db, valores, [str(exc)], status_code=503)

    if ruta_borrador:
        eliminar_borrador(ruta_borrador)

    flash(
        request,
        f"Documento {documento.codigo} registrado correctamente.",
    )
    return RedirectResponse(f"/documentos/{documento.codigo}", status_code=303)


# ---------------------------------------------------------------------------
# Análisis IA de borradores (antes de registrar)
# ---------------------------------------------------------------------------


@router.post("/analizar-borrador")
async def analizar_borrador(
    request: Request,
    archivo: UploadFile = File(...),
    csrf_token: str = Form(...),
    user: Usuario = Depends(puede_registrar),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)

    if not settings.ia_habilitada:
        return _render_borrador(
            request, None, error="El análisis con IA está deshabilitado."
        )
    if not archivo.filename:
        return _render_borrador(request, None, error="Selecciona un archivo.")

    try:
        validado = await validar_archivo(archivo)
    except ArchivoInvalido as exc:
        return _render_borrador(request, None, error=str(exc))

    analisis = encolar_borrador(
        db,
        contenido=validado.contenido,
        nombre_original=validado.nombre_original,
        mime=validado.mime,
        extension=validado.extension,
        hash_sha256=validado.hash_sha256,
    )
    db.commit()
    return _render_borrador(request, analisis)


@router.get("/analizar-borrador/{analisis_id}")
def estado_borrador(
    analisis_id: int,
    request: Request,
    user: Usuario = Depends(puede_registrar),
    db: Session = Depends(get_db),
):
    analisis = db.get(DocumentoAnalisis, analisis_id)
    if analisis is None or analisis.documento_id is not None:
        raise HTTPException(status_code=404, detail="Análisis no encontrado")
    return _render_borrador(request, analisis)


# ---------------------------------------------------------------------------
# Detalle y adjuntos
# ---------------------------------------------------------------------------


@router.get("/{codigo}")
def detalle_documento(
    codigo: str,
    request: Request,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    documento = _documento_o_404(db, codigo)
    plazos = estado_por_documento(db, [documento])

    analisis_por_archivo = {
        archivo.id: contexto_analisis(None) for archivo in documento.archivos
    }
    if documento.archivos:
        analisis = db.scalars(
            select(DocumentoAnalisis)
            .options(selectinload(DocumentoAnalisis.tipo_tramite_sugerido))
            .where(
                DocumentoAnalisis.archivo_id.in_(
                    [archivo.id for archivo in documento.archivos]
                )
            )
        ).all()
        for item in analisis:
            if item.archivo_id is not None:
                analisis_por_archivo[item.archivo_id] = contexto_analisis(item)

    return render(
        request,
        "documentos/detalle.html",
        {
            "current_user": user,
            "documento": documento,
            "plazo": plazos.get(documento.id),
            "puede_adjuntar": user.rol in {Rol.ADMIN, Rol.VENTANILLA, Rol.OFICINA},
            "acciones": acciones_disponibles(db, documento, user),
            "analisis_por_archivo": analisis_por_archivo,
            "oficinas_destino": [
                oficina
                for oficina in _oficinas_activas(db)
                if oficina.id != documento.oficina_actual_id
            ],
        },
    )


@router.post("/{codigo}/adjuntar")
async def adjuntar_archivo(
    codigo: str,
    request: Request,
    archivo: UploadFile = File(...),
    csrf_token: str = Form(...),
    user: Usuario = Depends(puede_adjuntar),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    documento = _documento_o_404(db, codigo)

    if not archivo.filename:
        flash(request, "Selecciona un archivo para adjuntar.", "error")
        return RedirectResponse(f"/documentos/{documento.codigo}", status_code=303)

    try:
        await guardar_archivo(db, documento, archivo, usuario=user, request=request)
        db.commit()
    except ArchivoInvalido as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(f"/documentos/{documento.codigo}", status_code=303)

    flash(request, "Archivo adjuntado al expediente.")
    return RedirectResponse(f"/documentos/{documento.codigo}", status_code=303)


@router.post("/{codigo}/archivos/{archivo_id}/analizar")
def solicitar_analisis(
    codigo: str,
    archivo_id: int,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(puede_adjuntar),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    documento = _documento_o_404(db, codigo)
    archivo = _archivo_o_404(db, documento, archivo_id)

    if not settings.ia_habilitada:
        flash(request, "El análisis con IA está deshabilitado.", "error")
        return RedirectResponse(f"/documentos/{documento.codigo}", status_code=303)

    analisis = db.scalar(
        select(DocumentoAnalisis).where(DocumentoAnalisis.archivo_id == archivo.id)
    )
    if analisis is None:
        encolar_analisis(db, archivo, documento=documento)
    elif analisis.estado != EstadoAnalisis.PROCESANDO:
        analisis.estado = EstadoAnalisis.PENDIENTE
        analisis.intentos = 0
        analisis.error = None
    db.commit()

    if request.headers.get("HX-Request"):
        return _render_analisis_archivo(request, db, documento, archivo, user)

    flash(request, "Análisis encolado. El resultado aparecerá en unos segundos.")
    return RedirectResponse(f"/documentos/{documento.codigo}", status_code=303)


@router.get("/{codigo}/archivos/{archivo_id}/analisis")
def ver_analisis_archivo(
    codigo: str,
    archivo_id: int,
    request: Request,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    documento = _documento_o_404(db, codigo)
    archivo = _archivo_o_404(db, documento, archivo_id)
    return _render_analisis_archivo(request, db, documento, archivo, user)


@router.get("/{codigo}/archivos/{archivo_id}")
def ver_archivo(
    codigo: str,
    archivo_id: int,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    documento = _documento_o_404(db, codigo)
    archivo = db.scalar(
        select(DocumentoArchivo).where(
            DocumentoArchivo.id == archivo_id,
            DocumentoArchivo.documento_id == documento.id,
        )
    )
    if archivo is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    try:
        ruta = ruta_absoluta(archivo)
    except ArchivoInvalido as exc:
        raise HTTPException(status_code=404, detail="Archivo no encontrado") from exc
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    return FileResponse(
        ruta,
        media_type=archivo.mime,
        filename=archivo.nombre_original,
        content_disposition_type="inline",
    )


# ---------------------------------------------------------------------------
# QR, cargo y etiqueta
# ---------------------------------------------------------------------------


@router.get("/{codigo}/qr.png")
def qr_documento(
    codigo: str,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    documento = _documento_o_404(db, codigo)
    return Response(
        content=qr_png(documento.codigo),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/{codigo}/cargo.pdf")
def cargo_documento(
    codigo: str,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    documento = _documento_o_404(db, codigo)
    return Response(
        content=cargo_pdf(documento),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="cargo-{documento.codigo}.pdf"'},
    )


@router.get("/{codigo}/etiqueta.pdf")
def etiqueta_documento(
    codigo: str,
    user: Usuario = Depends(require_user),
    db: Session = Depends(get_db),
):
    documento = _documento_o_404(db, codigo)
    return Response(
        content=etiqueta_pdf(documento),
        media_type="application/pdf",
        headers={"Content-Disposition": (f'inline; filename="etiqueta-{documento.codigo}.pdf"')},
    )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _archivo_o_404(db: Session, documento: Documento, archivo_id: int) -> DocumentoArchivo:
    archivo = db.scalar(
        select(DocumentoArchivo).where(
            DocumentoArchivo.id == archivo_id,
            DocumentoArchivo.documento_id == documento.id,
        )
    )
    if archivo is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return archivo


def _render_analisis_archivo(
    request: Request,
    db: Session,
    documento: Documento,
    archivo: DocumentoArchivo,
    user: Usuario,
):
    analisis = db.scalar(
        select(DocumentoAnalisis)
        .options(selectinload(DocumentoAnalisis.tipo_tramite_sugerido))
        .where(DocumentoAnalisis.archivo_id == archivo.id)
    )
    ctx = contexto_analisis(analisis)
    return render(
        request,
        "documentos/_analisis.html",
        {
            "current_user": user,
            "documento": documento,
            "archivo": archivo,
            "puede_adjuntar": user.rol in {Rol.ADMIN, Rol.VENTANILLA, Rol.OFICINA},
            **ctx,
        },
    )


def _render_borrador(
    request: Request,
    analisis: DocumentoAnalisis | None,
    *,
    error: str | None = None,
):
    ctx = contexto_analisis(analisis)
    return render(
        request,
        "documentos/_analisis_borrador.html",
        {"error": error, **ctx},
    )


def _valores_iniciales() -> dict[str, str]:
    return {
        "analisis_id": "",
        "tipo_tramite_id": "",
        "numero_certificado": "",
        "anio": "",
        "prioridad": Prioridad.NORMAL.value,
        "asunto": "",
        "referencia": "",
        "rut": "",
        "expediente_administrativo": "",
        "ruc": "",
        "administrado": "",
        "observaciones": "",
        "ciudadano_tipo_documento": TipoDocumentoIdentidad.DNI.value,
        "ciudadano_numero_documento": "",
        "ciudadano_nombres": "",
        "ciudadano_email": "",
        "ciudadano_telefono": "",
        "ciudadano_direccion": "",
    }


def _validar_registro(db: Session, valores: dict[str, str]) -> list[str]:
    errores: list[str] = []

    tipo_id = _entero(valores["tipo_tramite_id"])
    if tipo_id is None:
        errores.append("Selecciona un tipo de trámite.")
    else:
        tipo = db.get(TipoTramite, tipo_id)
        if tipo is None:
            errores.append("El tipo de trámite no existe.")
        elif not tipo.activo:
            errores.append("El tipo de trámite está inactivo.")

    anio = _entero(valores["anio"])
    if valores["anio"] and anio is None:
        errores.append("El año debe ser un número entero.")
    elif anio is not None and not 1900 <= anio <= 2100:
        errores.append("El año debe estar entre 1900 y 2100.")

    if valores["numero_certificado"] and anio is None:
        errores.append("Si registra el número de certificado, indica también el año.")

    if not valores["asunto"]:
        errores.append("El asunto es obligatorio.")
    elif len(valores["asunto"]) > 300:
        errores.append("El asunto no puede superar los 300 caracteres.")

    if valores["administrado"] and len(valores["administrado"]) > 200:
        errores.append("El administrado no puede superar los 200 caracteres.")

    if valores["ciudadano_nombres"] and not valores["ciudadano_numero_documento"]:
        errores.append("Para registrar al ciudadano, indica su número de documento.")
    if valores["ciudadano_numero_documento"] and not valores["ciudadano_nombres"]:
        ciudadano = obtener_ciudadano(
            db,
            _tipo_documento(valores["ciudadano_tipo_documento"]),
            valores["ciudadano_numero_documento"],
        )
        if ciudadano is None:
            errores.append(
                "No existe un ciudadano con ese documento. " "Registre también sus nombres."
            )

    return errores


def _form_nuevo(
    request: Request,
    user: Usuario,
    db: Session,
    valores: dict[str, str],
    errores: list[str],
    *,
    status_code: int = 200,
):
    return render(
        request,
        "documentos/nuevo.html",
        {
            "current_user": user,
            "valores": valores,
            "errores": errores,
            "tipos": _tipos_activos(db),
            "prioridades": list(Prioridad),
            "tipos_documento": list(TipoDocumentoIdentidad),
        },
        status_code=status_code,
    )
