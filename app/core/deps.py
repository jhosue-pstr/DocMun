"""Dependencias de autenticación y autorización."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import Rol
from app.models.usuarios import Usuario


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Usuario | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(Usuario, user_id)
    if user is None or not user.activo:
        request.session.clear()
        return None
    return user


def require_user(
    request: Request, db: Session = Depends(get_db)
) -> Usuario:
    user = get_optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


def require_roles(*roles: Rol) -> Callable[..., Usuario]:
    """Dependencia que exige uno de los roles indicados. Admin siempre pasa."""

    def dependency(user: Usuario = Depends(require_user)) -> Usuario:
        if user.rol == Rol.ADMIN or user.rol in roles:
            return user
        raise HTTPException(status_code=403, detail="No tiene permisos para esta sección")

    return dependency
