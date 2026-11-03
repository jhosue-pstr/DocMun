# DocMun

Sistema de registro y seguimiento de documentos de la Municipalidad.

Un documento se registra en ventanilla, circula por un conjunto de oficinas con un
plazo máximo de permanencia y, al finalizar, se notifica al ciudadano por WhatsApp
(correo opcional). El expediente físico se virtualiza con un código interno y un QR.

## Requisitos

- Docker y Docker Compose
- (Opcional para desarrollo sin Docker) Python 3.12 + PostgreSQL 16

## Arranque rápido

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec app alembic upgrade head
docker compose exec app python -m scripts.seed
```

Luego abrir:

- App: <http://localhost:8000>
- Adminer: <http://localhost:8080> (sistema `PostgreSQL`, servidor `db`, usuario `docmun`, clave `docmun`)

Credenciales iniciales (cambiar en producción):

```
admin@docmun.local / admin1234
```

## Comandos útiles

```bash
docker compose logs -f app          # logs
docker compose exec app pytest -q   # tests
docker compose down                 # detener
docker compose down -v              # detener y borrar la base
```

## Estado

Fase 0: esqueleto, Docker, modelos, migración inicial, seed y login.
Ver `.planning/2026-11-03-docmun-mvp/task_plan.md` para el plan completo.

## Estructura

Ver `AGENTS.md`.
