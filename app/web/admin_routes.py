"""Rutas de administración de catálogos. Solo el rol admin tiene acceso."""

from __future__ import annotations

import json
import re
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.csrf import verify_csrf
from app.core.deps import require_roles
from app.db.session import get_db
from app.models.catalogos import Configuracion, Feriado, Oficina, Plazo, TipoTramite
from app.models.enums import OrigenDocumento, Rol
from app.models.usuarios import Usuario
from app.services.metricas import (
    PERIODOS,
    FiltrosMetricas,
    calcular_dashboard,
    resolver_periodo,
)
from app.services.sla import HorarioInvalido, normalizar_horario
from app.web.flash import flash
from app.web.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])

admin_required = require_roles(Rol.ADMIN)
CLAVE_CONFIG = re.compile(r"^[a-z0-9_]+$")


def _limpiar(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpio = valor.strip()
    return limpio or None


def _texto(valor: str | None, defecto: str = "") -> str:
    return (valor or defecto).strip()


def _oficinas_para_select(db: Session) -> list[Oficina]:
    return list(db.scalars(select(Oficina).order_by(Oficina.codigo)).all())


def _tipos_para_select(db: Session) -> list[TipoTramite]:
    return list(db.scalars(select(TipoTramite).order_by(TipoTramite.codigo)).all())


def _contar(db: Session, modelo, *filtros) -> int:
    stmt = select(func.count()).select_from(modelo)
    if filtros:
        stmt = stmt.where(*filtros)
    return db.scalar(stmt) or 0


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------


@router.get("")
def admin_resumen(
    request: Request,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return render(
        request,
        "admin/index.html",
        {
            "current_user": user,
            "seccion": "resumen",
            "titulo": "Administración",
            "subtitulo": "Catálogos y parámetros del sistema.",
            "oficinas_activas": _contar(db, Oficina, Oficina.activa.is_(True)),
            "oficinas_total": _contar(db, Oficina),
            "tipos_activos": _contar(db, TipoTramite, TipoTramite.activo.is_(True)),
            "tipos_total": _contar(db, TipoTramite),
            "plazos": _contar(db, Plazo),
            "feriados": _contar(db, Feriado),
            "parametros": _contar(db, Configuracion),
        },
    )


# ---------------------------------------------------------------------------
# Métricas del semáforo
# ---------------------------------------------------------------------------


@router.get("/metricas")
def admin_metricas(
    request: Request,
    periodo: str = "30",
    oficina_id: str = "",
    tipo_tramite_id: str = "",
    origen: str = "",
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    origen_enum = OrigenDocumento(origen) if origen in {o.value for o in OrigenDocumento} else None
    filtros = FiltrosMetricas(
        periodo=resolver_periodo(periodo),
        oficina_id=int(oficina_id) if oficina_id.isdigit() else None,
        tipo_tramite_id=int(tipo_tramite_id) if tipo_tramite_id.isdigit() else None,
        origen=origen_enum,
    )
    dashboard = calcular_dashboard(db, filtros)
    return render(
        request,
        "admin/metricas.html",
        {
            "current_user": user,
            "seccion": "metricas",
            "titulo": "Métricas de atención",
            "subtitulo": "Semáforo, carga y desempeño por oficina.",
            "panel": dashboard,
            "filtros": filtros,
            "periodos": [
                (clave, datos[2]) for clave, datos in PERIODOS.items()
            ],
            "oficinas": _oficinas_para_select(db),
            "tipos": list(db.scalars(select(TipoTramite).order_by(TipoTramite.codigo)).all()),
            "origenes": list(OrigenDocumento),
        },
    )


# ---------------------------------------------------------------------------
# Oficinas
# ---------------------------------------------------------------------------


@router.get("/oficinas")
def listar_oficinas(
    request: Request,
    q: str | None = None,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    stmt = select(Oficina).order_by(Oficina.codigo)
    if q and q.strip():
        patron = f"%{q.strip()}%"
        stmt = stmt.where(or_(Oficina.codigo.ilike(patron), Oficina.nombre.ilike(patron)))
    return render(
        request,
        "admin/oficinas.html",
        {
            "current_user": user,
            "seccion": "oficinas",
            "titulo": "Oficinas",
            "subtitulo": "Las 16 áreas municipales y sus datos de contacto.",
            "oficinas": list(db.scalars(stmt).all()),
            "q": q or "",
        },
    )


@router.get("/oficinas/nueva")
def formulario_oficina(
    request: Request,
    user: Usuario = Depends(admin_required),
):
    return render(
        request,
        "admin/oficina_form.html",
        {
            "current_user": user,
            "seccion": "oficinas",
            "titulo": "Nueva oficina",
            "subtitulo": "Registra un área municipal.",
            "oficina": None,
            "valores": {},
            "errores": [],
        },
    )


@router.post("/oficinas/nueva")
def crear_oficina(
    request: Request,
    codigo: str = Form(""),
    nombre: str = Form(""),
    siglas: str = Form(""),
    ubicacion: str = Form(""),
    email: str = Form(""),
    telefono: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    valores = {
        "codigo": _texto(codigo).upper(),
        "nombre": _texto(nombre),
        "siglas": _texto(siglas).upper(),
        "ubicacion": _texto(ubicacion),
        "email": _texto(email).lower(),
        "telefono": _texto(telefono),
    }
    errores = _validar_oficina(db, valores, oficina_id=None)
    if errores:
        return _form_oficina(request, user, valores, errores, status_code=400)

    db.add(Oficina(**valores))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        errores.append("Ya existe una oficina con ese código.")
        return _form_oficina(request, user, valores, errores, status_code=400)

    flash(request, f"Oficina {valores['codigo']} creada.")
    return RedirectResponse("/admin/oficinas", status_code=303)


@router.get("/oficinas/{oficina_id}/editar")
def formulario_editar_oficina(
    oficina_id: int,
    request: Request,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    oficina = _obtener_oficina(db, oficina_id)
    return render(
        request,
        "admin/oficina_form.html",
        {
            "current_user": user,
            "seccion": "oficinas",
            "titulo": f"Editar {oficina.codigo}",
            "subtitulo": oficina.nombre,
            "oficina": oficina,
            "valores": {
                "codigo": oficina.codigo,
                "nombre": oficina.nombre,
                "siglas": oficina.siglas or "",
                "ubicacion": oficina.ubicacion or "",
                "email": oficina.email or "",
                "telefono": oficina.telefono or "",
            },
            "errores": [],
        },
    )


@router.post("/oficinas/{oficina_id}/editar")
def editar_oficina(
    oficina_id: int,
    request: Request,
    codigo: str = Form(""),
    nombre: str = Form(""),
    siglas: str = Form(""),
    ubicacion: str = Form(""),
    email: str = Form(""),
    telefono: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    oficina = _obtener_oficina(db, oficina_id)
    valores = {
        "codigo": _texto(codigo).upper(),
        "nombre": _texto(nombre),
        "siglas": _texto(siglas).upper(),
        "ubicacion": _texto(ubicacion),
        "email": _texto(email).lower(),
        "telefono": _texto(telefono),
    }
    errores = _validar_oficina(db, valores, oficina_id=oficina.id)
    if errores:
        return _form_oficina(request, user, valores, errores, oficina=oficina, status_code=400)

    for campo, valor in valores.items():
        setattr(oficina, campo, valor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        errores.append("Ya existe una oficina con ese código.")
        return _form_oficina(request, user, valores, errores, oficina=oficina, status_code=400)

    flash(request, f"Oficina {oficina.codigo} actualizada.")
    return RedirectResponse("/admin/oficinas", status_code=303)


@router.post("/oficinas/{oficina_id}/estado")
def cambiar_estado_oficina(
    oficina_id: int,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    oficina = _obtener_oficina(db, oficina_id)
    oficina.activa = not oficina.activa
    db.commit()
    flash(
        request,
        f"Oficina {oficina.codigo} {'activada' if oficina.activa else 'desactivada'}.",
    )
    return RedirectResponse("/admin/oficinas", status_code=303)


# ---------------------------------------------------------------------------
# Tipos de trámite
# ---------------------------------------------------------------------------


@router.get("/tipos-tramite")
def listar_tipos(
    request: Request,
    q: str | None = None,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    stmt = (
        select(TipoTramite)
        .options(selectinload(TipoTramite.oficina_responsable))
        .order_by(TipoTramite.codigo)
    )
    if q and q.strip():
        patron = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                TipoTramite.codigo.ilike(patron),
                TipoTramite.nombre.ilike(patron),
                TipoTramite.area.ilike(patron),
            )
        )
    return render(
        request,
        "admin/tipos.html",
        {
            "current_user": user,
            "seccion": "tipos",
            "titulo": "Tipos de trámite",
            "subtitulo": "Cada tipo define el plazo base y la oficina responsable.",
            "tipos": list(db.scalars(stmt).all()),
            "q": q or "",
        },
    )


@router.get("/tipos-tramite/nuevo")
def formulario_tipo(
    request: Request,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return render(
        request,
        "admin/tipo_form.html",
        {
            "current_user": user,
            "seccion": "tipos",
            "titulo": "Nuevo tipo de trámite",
            "subtitulo": "Define el trámite y su plazo inicial.",
            "tipo": None,
            "valores": {"sla_horas": 72},
            "errores": [],
            "oficinas": _oficinas_para_select(db),
        },
    )


@router.post("/tipos-tramite/nuevo")
def crear_tipo(
    request: Request,
    codigo: str = Form(""),
    nombre: str = Form(""),
    area: str = Form(""),
    oficina_responsable_id: str = Form(""),
    sla_horas: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    valores, errores = _validar_tipo(
        db,
        {
            "codigo": _texto(codigo).upper(),
            "nombre": _texto(nombre),
            "area": _texto(area),
            "oficina_responsable_id": _texto(oficina_responsable_id),
            "sla_horas": _texto(sla_horas),
        },
        tipo_id=None,
    )
    if errores:
        return _form_tipo(request, user, db, valores, errores, status_code=400)

    db.add(
        TipoTramite(
            codigo=valores["codigo"],
            nombre=valores["nombre"],
            area=valores["area"],
            oficina_responsable_id=valores["oficina_responsable_id"],
            sla_horas=valores["sla_horas"],
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        errores.append("Ya existe un tipo de trámite con ese código.")
        return _form_tipo(request, user, db, valores, errores, status_code=400)

    flash(request, f"Tipo {valores['codigo']} creado.")
    return RedirectResponse("/admin/tipos-tramite", status_code=303)


@router.get("/tipos-tramite/{tipo_id}/editar")
def formulario_editar_tipo(
    tipo_id: int,
    request: Request,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    tipo = _obtener_tipo(db, tipo_id)
    return render(
        request,
        "admin/tipo_form.html",
        {
            "current_user": user,
            "seccion": "tipos",
            "titulo": f"Editar {tipo.codigo}",
            "subtitulo": tipo.nombre,
            "tipo": tipo,
            "valores": {
                "codigo": tipo.codigo,
                "nombre": tipo.nombre,
                "area": tipo.area or "",
                "oficina_responsable_id": tipo.oficina_responsable_id or "",
                "sla_horas": tipo.sla_horas,
            },
            "errores": [],
            "oficinas": _oficinas_para_select(db),
        },
    )


@router.post("/tipos-tramite/{tipo_id}/editar")
def editar_tipo(
    tipo_id: int,
    request: Request,
    codigo: str = Form(""),
    nombre: str = Form(""),
    area: str = Form(""),
    oficina_responsable_id: str = Form(""),
    sla_horas: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    tipo = _obtener_tipo(db, tipo_id)
    valores, errores = _validar_tipo(
        db,
        {
            "codigo": _texto(codigo).upper(),
            "nombre": _texto(nombre),
            "area": _texto(area),
            "oficina_responsable_id": _texto(oficina_responsable_id),
            "sla_horas": _texto(sla_horas),
        },
        tipo_id=tipo.id,
    )
    if errores:
        return _form_tipo(request, user, db, valores, errores, tipo=tipo, status_code=400)

    tipo.codigo = valores["codigo"]
    tipo.nombre = valores["nombre"]
    tipo.area = valores["area"]
    tipo.oficina_responsable_id = valores["oficina_responsable_id"]
    tipo.sla_horas = valores["sla_horas"]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        errores.append("Ya existe un tipo de trámite con ese código.")
        return _form_tipo(request, user, db, valores, errores, tipo=tipo, status_code=400)

    flash(request, f"Tipo {tipo.codigo} actualizado.")
    return RedirectResponse("/admin/tipos-tramite", status_code=303)


@router.post("/tipos-tramite/{tipo_id}/estado")
def cambiar_estado_tipo(
    tipo_id: int,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    tipo = _obtener_tipo(db, tipo_id)
    tipo.activo = not tipo.activo
    db.commit()
    flash(
        request,
        f"Tipo {tipo.codigo} {'activado' if tipo.activo else 'desactivado'}.",
    )
    return RedirectResponse("/admin/tipos-tramite", status_code=303)


# ---------------------------------------------------------------------------
# Plazos
# ---------------------------------------------------------------------------


@router.get("/plazos")
def listar_plazos(
    request: Request,
    tipo_tramite_id: int | None = None,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Plazo)
        .options(
            selectinload(Plazo.tipo_tramite),
            selectinload(Plazo.oficina),
        )
        .join(Plazo.tipo_tramite)
        .join(Plazo.oficina)
        .order_by(TipoTramite.codigo, Oficina.codigo)
    )
    if tipo_tramite_id is not None:
        stmt = stmt.where(Plazo.tipo_tramite_id == tipo_tramite_id)

    return render(
        request,
        "admin/plazos.html",
        {
            "current_user": user,
            "seccion": "plazos",
            "titulo": "Plazos por oficina",
            "subtitulo": "Horas máximas de permanencia de cada trámite en cada oficina.",
            "plazos": list(db.scalars(stmt).all()),
            "tipos": _tipos_para_select(db),
            "oficinas": _oficinas_para_select(db),
            "filtro_tipo": tipo_tramite_id,
        },
    )


@router.post("/plazos")
def guardar_plazo(
    request: Request,
    tipo_tramite_id: str = Form(""),
    oficina_id: str = Form(""),
    horas: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    errores: list[str] = []
    tipo_id = _entero(tipo_tramite_id, "tipo de trámite", errores)
    oficina_pk = _entero(oficina_id, "oficina", errores)
    horas_valor = _entero(horas, "horas", errores)

    if tipo_id is not None and db.get(TipoTramite, tipo_id) is None:
        errores.append("El tipo de trámite no existe.")
    if oficina_pk is not None and db.get(Oficina, oficina_pk) is None:
        errores.append("La oficina no existe.")
    if horas_valor is not None and horas_valor <= 0:
        errores.append("Las horas deben ser mayores que cero.")

    if errores:
        for error in errores:
            flash(request, error, "error")
        return RedirectResponse("/admin/plazos", status_code=303)

    existente = db.scalar(
        select(Plazo).where(Plazo.tipo_tramite_id == tipo_id, Plazo.oficina_id == oficina_pk)
    )
    if existente is None:
        db.add(Plazo(tipo_tramite_id=tipo_id, oficina_id=oficina_pk, horas=horas_valor))
        mensaje = "Plazo creado."
    else:
        existente.horas = horas_valor
        mensaje = "Plazo actualizado."

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "No se pudo guardar el plazo: ya existe.", "error")
        return RedirectResponse("/admin/plazos", status_code=303)

    flash(request, mensaje)
    return RedirectResponse("/admin/plazos", status_code=303)


@router.post("/plazos/{plazo_id}/editar")
def editar_plazo(
    plazo_id: int,
    request: Request,
    horas: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    plazo = db.get(Plazo, plazo_id)
    if plazo is None:
        raise HTTPException(status_code=404, detail="Plazo no encontrado")

    errores: list[str] = []
    horas_valor = _entero(horas, "horas", errores)
    if horas_valor is not None and horas_valor <= 0:
        errores.append("Las horas deben ser mayores que cero.")
    if errores:
        for error in errores:
            flash(request, error, "error")
        return RedirectResponse("/admin/plazos", status_code=303)

    plazo.horas = horas_valor
    db.commit()
    flash(request, "Plazo actualizado.")
    return RedirectResponse("/admin/plazos", status_code=303)


@router.post("/plazos/{plazo_id}/eliminar")
def eliminar_plazo(
    plazo_id: int,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    plazo = db.get(Plazo, plazo_id)
    if plazo is None:
        raise HTTPException(status_code=404, detail="Plazo no encontrado")
    db.delete(plazo)
    db.commit()
    flash(request, "Plazo eliminado.")
    return RedirectResponse("/admin/plazos", status_code=303)


# ---------------------------------------------------------------------------
# Feriados
# ---------------------------------------------------------------------------


@router.get("/feriados")
def listar_feriados(
    request: Request,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return render(
        request,
        "admin/feriados.html",
        {
            "current_user": user,
            "seccion": "feriados",
            "titulo": "Feriados",
            "subtitulo": "Días no laborables usados para calcular plazos en horas hábiles.",
            "feriados": list(db.scalars(select(Feriado).order_by(Feriado.fecha.desc())).all()),
        },
    )


@router.post("/feriados")
def crear_feriado(
    request: Request,
    fecha: date = Form(...),
    descripcion: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    descripcion_limpia = _texto(descripcion)
    errores: list[str] = []
    if not descripcion_limpia:
        errores.append("La descripción es obligatoria.")
    if db.scalar(select(Feriado.id).where(Feriado.fecha == fecha)) is not None:
        errores.append("Ya existe un feriado en esa fecha.")

    if errores:
        for error in errores:
            flash(request, error, "error")
        return RedirectResponse("/admin/feriados", status_code=303)

    db.add(Feriado(fecha=fecha, descripcion=descripcion_limpia))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Ya existe un feriado en esa fecha.", "error")
        return RedirectResponse("/admin/feriados", status_code=303)

    flash(request, f"Feriado {fecha.isoformat()} agregado.")
    return RedirectResponse("/admin/feriados", status_code=303)


@router.post("/feriados/{feriado_id}/eliminar")
def eliminar_feriado(
    feriado_id: int,
    request: Request,
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    feriado = db.get(Feriado, feriado_id)
    if feriado is None:
        raise HTTPException(status_code=404, detail="Feriado no encontrado")
    db.delete(feriado)
    db.commit()
    flash(request, "Feriado eliminado.")
    return RedirectResponse("/admin/feriados", status_code=303)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------


@router.get("/configuracion")
def listar_configuracion(
    request: Request,
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    return render(
        request,
        "admin/configuracion.html",
        {
            "current_user": user,
            "seccion": "configuracion",
            "titulo": "Configuración",
            "subtitulo": "Parámetros editables del sistema.",
            "parametros": list(
                db.scalars(select(Configuracion).order_by(Configuracion.clave)).all()
            ),
        },
    )


@router.post("/configuracion")
def crear_parametro(
    request: Request,
    clave: str = Form(""),
    valor: str = Form(""),
    descripcion: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    clave_limpia = _texto(clave).lower()
    errores: list[str] = []
    if not clave_limpia:
        errores.append("La clave es obligatoria.")
    elif not CLAVE_CONFIG.match(clave_limpia):
        errores.append("La clave solo admite minúsculas, números y guion bajo.")
    if not valor.strip():
        errores.append("El valor es obligatorio.")
    if clave_limpia and db.get(Configuracion, clave_limpia) is not None:
        errores.append("Ya existe un parámetro con esa clave.")

    if errores:
        for error in errores:
            flash(request, error, "error")
        return RedirectResponse("/admin/configuracion", status_code=303)

    db.add(
        Configuracion(
            clave=clave_limpia,
            valor=valor.strip(),
            descripcion=_limpiar(descripcion),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Ya existe un parámetro con esa clave.", "error")
        return RedirectResponse("/admin/configuracion", status_code=303)

    flash(request, f"Parámetro {clave_limpia} creado.")
    return RedirectResponse("/admin/configuracion", status_code=303)


@router.post("/configuracion/{clave}")
def editar_parametro(
    clave: str,
    request: Request,
    valor: str = Form(""),
    descripcion: str = Form(""),
    csrf_token: str = Form(...),
    user: Usuario = Depends(admin_required),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    parametro = db.get(Configuracion, clave)
    if parametro is None:
        raise HTTPException(status_code=404, detail="Parámetro no encontrado")

    errores: list[str] = []
    if not valor.strip():
        errores.append("El valor es obligatorio.")
    if clave == "horario_laboral" and valor.strip():
        try:
            horario = json.loads(valor)
        except json.JSONDecodeError:
            errores.append("El horario laboral debe ser JSON válido.")
        else:
            if not isinstance(horario, dict):
                errores.append("El horario laboral debe ser un objeto JSON.")
            else:
                try:
                    normalizar_horario(horario)
                except HorarioInvalido as exc:
                    errores.append(str(exc))

    if errores:
        for error in errores:
            flash(request, error, "error")
        return RedirectResponse("/admin/configuracion", status_code=303)

    parametro.valor = valor.strip()
    parametro.descripcion = _limpiar(descripcion)
    db.commit()
    flash(request, f"Parámetro {clave} actualizado.")
    return RedirectResponse("/admin/configuracion", status_code=303)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _entero(valor: str, nombre: str, errores: list[str]) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        errores.append(f"El campo {nombre} debe ser un número entero.")
        return None


def _obtener_oficina(db: Session, oficina_id: int) -> Oficina:
    oficina = db.get(Oficina, oficina_id)
    if oficina is None:
        raise HTTPException(status_code=404, detail="Oficina no encontrada")
    return oficina


def _obtener_tipo(db: Session, tipo_id: int) -> TipoTramite:
    tipo = db.get(TipoTramite, tipo_id)
    if tipo is None:
        raise HTTPException(status_code=404, detail="Tipo de trámite no encontrado")
    return tipo


def _validar_oficina(db: Session, valores: dict, *, oficina_id: int | None) -> list[str]:
    errores: list[str] = []
    if not valores["codigo"]:
        errores.append("El código es obligatorio.")
    elif len(valores["codigo"]) > 20:
        errores.append("El código no puede superar los 20 caracteres.")
    if not valores["nombre"]:
        errores.append("El nombre es obligatorio.")
    elif len(valores["nombre"]) > 150:
        errores.append("El nombre no puede superar los 150 caracteres.")
    if valores["email"] and "@" not in valores["email"]:
        errores.append("El correo no tiene un formato válido.")

    if valores["codigo"]:
        stmt = select(Oficina.id).where(func.upper(Oficina.codigo) == valores["codigo"])
        if oficina_id is not None:
            stmt = stmt.where(Oficina.id != oficina_id)
        if db.scalar(stmt) is not None:
            errores.append("Ya existe una oficina con ese código.")
    return errores


def _validar_tipo(db: Session, valores: dict, *, tipo_id: int | None) -> tuple[dict, list[str]]:
    errores: list[str] = []
    if not valores["codigo"]:
        errores.append("El código es obligatorio.")
    elif len(valores["codigo"]) > 30:
        errores.append("El código no puede superar los 30 caracteres.")
    if not valores["nombre"]:
        errores.append("El nombre es obligatorio.")
    elif len(valores["nombre"]) > 200:
        errores.append("El nombre no puede superar los 200 caracteres.")

    sla_horas = _entero(valores["sla_horas"], "SLA (horas)", errores)
    if sla_horas is not None and sla_horas <= 0:
        errores.append("El SLA debe ser mayor que cero.")

    oficina_id: int | None = None
    if valores["oficina_responsable_id"]:
        oficina_id = _entero(valores["oficina_responsable_id"], "oficina responsable", errores)
        if oficina_id is not None and db.get(Oficina, oficina_id) is None:
            errores.append("La oficina responsable no existe.")

    if valores["codigo"]:
        stmt = select(TipoTramite.id).where(func.upper(TipoTramite.codigo) == valores["codigo"])
        if tipo_id is not None:
            stmt = stmt.where(TipoTramite.id != tipo_id)
        if db.scalar(stmt) is not None:
            errores.append("Ya existe un tipo de trámite con ese código.")

    valores["sla_horas"] = sla_horas
    valores["oficina_responsable_id"] = oficina_id
    return valores, errores


def _form_oficina(
    request: Request,
    user: Usuario,
    valores: dict,
    errores: list[str],
    *,
    oficina: Oficina | None = None,
    status_code: int = 200,
):
    return render(
        request,
        "admin/oficina_form.html",
        {
            "current_user": user,
            "seccion": "oficinas",
            "titulo": f"Editar {oficina.codigo}" if oficina else "Nueva oficina",
            "subtitulo": oficina.nombre if oficina else "Registra un área municipal.",
            "oficina": oficina,
            "valores": valores,
            "errores": errores,
        },
        status_code=status_code,
    )


def _form_tipo(
    request: Request,
    user: Usuario,
    db: Session,
    valores: dict,
    errores: list[str],
    *,
    tipo: TipoTramite | None = None,
    status_code: int = 200,
):
    return render(
        request,
        "admin/tipo_form.html",
        {
            "current_user": user,
            "seccion": "tipos",
            "titulo": (f"Editar {tipo.codigo}" if tipo else "Nuevo tipo de trámite"),
            "subtitulo": (tipo.nombre if tipo else "Define el trámite y su plazo inicial."),
            "tipo": tipo,
            "valores": valores,
            "errores": errores,
            "oficinas": _oficinas_para_select(db),
        },
        status_code=status_code,
    )
