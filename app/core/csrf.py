"""Protección CSRF para formularios HTML."""

import secrets

from fastapi import HTTPException, Request


def get_csrf_token(request: Request) -> str:
    """Devuelve el token de la sesión, creándolo si no existe."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=400, detail="Token CSRF inválido o expirado")
