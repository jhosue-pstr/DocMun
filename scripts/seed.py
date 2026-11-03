"""Carga inicial de catálogos y usuario administrador.

Uso:
    python -m scripts.seed
"""

from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.data.catalogos import AREAS, FERIADOS_2026
from app.db.session import SessionLocal
from app.models.catalogos import Configuracion, Feriado, Oficina, Plazo, TipoTramite
from app.models.enums import Rol
from app.models.usuarios import Usuario

CONFIGURACION_INICIAL = {
    "municipio_nombre": ("Municipalidad", "Nombre oficial de la entidad"),
    "horario_laboral": (
        '{"lunes_viernes": ["08:00", "16:00"], "sabado": null, "domingo": null}',
        "Horario de atención usado para calcular plazos en horas hábiles",
    ),
    "umbral_por_vencer": ("0.8", "Fracción del plazo que marca por vencer"),
    "umbral_atrasado": ("1.0", "Fracción del plazo que marca atrasado"),
    "umbral_muy_atrasado": ("1.5", "Fracción del plazo que marca muy atrasado"),
    "zona_horaria": (settings.timezone, "Zona horaria de presentación"),
}


def seed_oficinas_y_tipos(db) -> None:
    for area in AREAS:
        oficina = db.scalar(select(Oficina).where(Oficina.codigo == area.codigo))
        if oficina is None:
            oficina = Oficina(
                codigo=area.codigo,
                nombre=area.nombre,
                siglas=area.siglas,
                activa=True,
            )
            db.add(oficina)
            db.flush()
            print(f"  + oficina {area.codigo} {area.nombre}")

        tipo = db.scalar(
            select(TipoTramite).where(TipoTramite.codigo == f"TR-{area.codigo[-2:]}")
        )
        if tipo is None:
            tipo = TipoTramite(
                codigo=f"TR-{area.codigo[-2:]}",
                nombre=area.nombre,
                area="Desarrollo Urbano",
                oficina_responsable_id=oficina.id,
                sla_horas=area.sla_horas,
                activo=True,
            )
            db.add(tipo)
            db.flush()
            print(f"  + tipo TR-{area.codigo[-2:]} ({area.sla_horas} h)")

        for oficina_destino in db.scalars(select(Oficina)).all():
            existe = db.scalar(
                select(Plazo).where(
                    Plazo.tipo_tramite_id == tipo.id,
                    Plazo.oficina_id == oficina_destino.id,
                )
            )
            if existe is None:
                db.add(
                    Plazo(
                        tipo_tramite_id=tipo.id,
                        oficina_id=oficina_destino.id,
                        horas=area.sla_horas,
                    )
                )


def seed_feriados(db) -> None:
    for fecha_iso, descripcion in FERIADOS_2026:
        fecha = date.fromisoformat(fecha_iso)
        if db.scalar(select(Feriado).where(Feriado.fecha == fecha)) is None:
            db.add(Feriado(fecha=fecha, descripcion=descripcion))
            print(f"  + feriado {fecha_iso} {descripcion}")


def seed_configuracion(db) -> None:
    for clave, (valor, descripcion) in CONFIGURACION_INICIAL.items():
        actual = db.get(Configuracion, clave)
        if actual is None:
            db.add(Configuracion(clave=clave, valor=valor, descripcion=descripcion))


def seed_admin(db) -> None:
    email = settings.admin_email.strip().lower()
    admin = db.scalar(select(Usuario).where(Usuario.email == email))
    if admin is None:
        admin = Usuario(
            nombre=settings.admin_nombre,
            email=email,
            password_hash=hash_password(settings.admin_password),
            rol=Rol.ADMIN,
            activo=True,
        )
        db.add(admin)
        print(f"  + admin {email}")
    else:
        print(f"  = admin {email} ya existe (no se cambia la contraseña)")


def main() -> None:
    print("Cargando datos iniciales de DocMun...")
    db = SessionLocal()
    try:
        seed_oficinas_y_tipos(db)
        seed_feriados(db)
        seed_configuracion(db)
        seed_admin(db)
        db.commit()
        print("Listo.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
