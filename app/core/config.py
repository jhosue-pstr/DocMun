"""Configuración de la aplicación basada en variables de entorno."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DocMun"
    environment: str = "development"
    debug: bool = True
    secret_key: str = "dev-secret-change-me"

    # URL pública usada para construir el enlace del QR del cargo.
    public_base_url: str = "http://localhost:8000"

    # Almacenamiento de los PDFs escaneados (fuera de la base de datos).
    storage_dir: str = "storage"
    max_upload_mb: int = 25

    database_url: str = "postgresql+psycopg://docmun:docmun@localhost:5433/docmun"

    session_cookie: str = "docmun_session"
    session_max_age: int = 60 * 60 * 8

    admin_email: str = "admin@docmun.local"
    admin_password: str = "admin1234"
    admin_nombre: str = "Administrador DocMun"

    sla_por_vencer: float = 0.8
    sla_atrasado: float = 1.0
    sla_muy_atrasado: float = 1.5

    # Cada cuántos segundos el job evalúa plazos y registra alertas.
    sla_intervalo_segundos: int = 900

    # ------------------------------------------------------------------
    # Análisis de documentos con IA (OCR + LayoutLMv3)
    # ------------------------------------------------------------------
    # La app web solo encola; el trabajo pesado lo hace el contenedor "ia".
    ia_habilitada: bool = False
    ia_modelos_dir: str = "models/ia"
    ia_modelo_clasificacion: str = "clasificacion/modelo.pt"
    ia_modelo_kie: str = "kie/modelo.pt"
    ia_dispositivo: str = "cpu"
    ia_hilos: int = 2
    ia_max_paginas: int = 3
    ia_dpi: int = 200
    ia_ocr_idiomas: str = "spa+eng"
    ia_ocr_psm: int = 3
    ia_umbral_sugerencia: float = 0.65
    ia_max_intentos: int = 3
    ia_intervalo_segundos: int = 5
    ia_borrador_horas: int = 24

    timezone: str = "America/Lima"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
