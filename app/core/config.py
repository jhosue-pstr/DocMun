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

    database_url: str = "postgresql+psycopg://docmun:docmun@localhost:5433/docmun"

    session_cookie: str = "docmun_session"
    session_max_age: int = 60 * 60 * 8

    admin_email: str = "admin@docmun.local"
    admin_password: str = "admin1234"
    admin_nombre: str = "Administrador DocMun"

    sla_por_vencer: float = 0.8
    sla_atrasado: float = 1.0
    sla_muy_atrasado: float = 1.5

    timezone: str = "America/Lima"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
