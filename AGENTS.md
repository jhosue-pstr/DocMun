# AGENTS.md — DocMun

Guía para agentes y desarrolladores que trabajen en este repositorio.

## Qué es DocMun

Sistema de registro y seguimiento de documentos de la Municipalidad. Un documento
se registra en ventanilla, circula por un conjunto de oficinas con un plazo máximo
de permanencia, y al finalizar se notifica al ciudadano por WhatsApp (correo opcional).

El expediente real es **físico**. El sistema lo virtualiza: cada documento tiene un
código interno y un QR que abre su línea de tiempo.

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Base de datos | PostgreSQL 16 |
| Frontend | Jinja2 + HTMX + Alpine.js + Tailwind (CDN, sin build) |
| Auth | Sesión en cookie firmada, contraseñas con Argon2 |
| Tareas | Jobs CLI (`python -m app.jobs.*`) ejecutados por cron/systemd |
| Contenedores | Docker Compose (app + db + adminer) |

## Comandos

```bash
# Levantar todo
docker compose up --build

# Solo base de datos (desarrollo local)
docker compose up -d db

# Migraciones
docker compose exec app alembic upgrade head
docker compose exec app alembic revision --autogenerate -m "descripcion"

# Seed de catálogos y admin
docker compose exec app python -m scripts.seed

# Tests
docker compose exec app pytest -q

# Logs
docker compose logs -f app
```

La app corre en `http://localhost:8000`. Adminer en `http://localhost:8080`.

## Estructura

```
app/
├── main.py            # creación de la app y routers
├── core/              # config, seguridad, CSRF, dependencias
├── db/                # engine, sesión, Base declarativa
├── models/            # tablas SQLAlchemy
├── schemas/           # modelos Pydantic (API)
├── services/          # reglas de negocio (SLA, folios, notificaciones)
├── api/v1/            # endpoints REST
├── web/               # rutas y templates Jinja2
└── jobs/              # tareas programadas
alembic/               # migraciones
scripts/               # seed e importador del Excel
tests/                 # pytest
docs/                  # fuentes y documentación
.planning/             # plan de trabajo (planning-with-files)
```

## Reglas de negocio que no se negocian

1. **El semáforo de plazo es calculado, nunca almacenado ni editable.** Se deriva de
   `fecha_limite` y del reloj. Nadie puede marcar un documento como "en plazo".
2. **Los movimientos son inmutables.** Corregir un error = movimiento de reversión,
   nunca `DELETE` ni `UPDATE` sobre un movimiento cerrado.
3. **Todo cambio de estado genera un evento de auditoría** con usuario, IP y timestamp.
4. **El plazo corre desde la recepción confirmada** por la oficina (configurable).
5. **Los plazos se calculan en horas hábiles**, con horario y feriados en base de datos.
6. **Un documento puede estar en una sola oficina a la vez.**
7. **La finalización dispara la notificación** al ciudadano; el envío se registra en
   `notificaciones` con estado y error, y se reintenta si falla.

## Modelo de datos (resumen)

- `oficinas` — 16 áreas municipales.
- `usuarios` — roles: `admin`, `ventanilla`, `oficina`, `direccion`.
- `ciudadanos` — DNI/RUC, nombre, email, teléfono.
- `tipos_tramite` — 16 tipos extraídos del Excel consolidado, con SLA por defecto.
- `documentos` — código interno, número y año, estado, oficina actual.
- `movimientos` — cada derivación con envío, recepción, salida, SLA y fecha límite.
- `plazos` — horas máximas por tipo de trámite y oficina.
- `eventos` — auditoría inmutable.
- `notificaciones` — cola y log de envíos.
- `feriados` y `configuracion` — calendario y parámetros.

**Unicidad importante:** el número de certificado se repite entre tipos de trámite
(441 casos en el Excel). La restricción es `(tipo_tramite_id, numero, anio)`.

## Convenciones de código

- Python con type hints; `ruff` para lint y formato.
- Modelos SQLAlchemy 2.0 con `Mapped` / `mapped_column`.
- Names en español para el dominio (`documento`, `oficina`, `movimiento`); inglés para
  infraestructura (`settings`, `session`, `router`).
- Fechas siempre con zona horaria `America/Lima`.
- Montos/fechas en UTC en base de datos, presentación en hora local.
- Nunca commitear `.env`, `storage/` ni archivos del Excel con datos personales.
- Tests con pytest + SQLite en memoria para lógica; Postgres para integración.

## Seguridad y datos personales

- DNI, teléfono y correo son datos personales (Ley 29733). Solo visibles para roles
  autorizados; nunca en logs.
- Formularios POST con token CSRF de sesión.
- Contraseñas con Argon2; nunca en texto plano ni en logs.
- El servidor de producción usa HTTPS detrás de Caddy/Nginx.

## Plan de trabajo

El plan vive en `.planning/2026-11-03-docmun-mvp/task_plan.md`.
Antes de una tarea compleja, leer `task_plan.md`, `findings.md` y `progress.md`.
Actualizar el estado de la fase y `progress.md` al terminar cada bloque.

## Estado actual

Fase 0 técnica: esqueleto, Docker, auth, migración inicial y seed.
Las fases 1–4 (registro, circulación, SLA, notificaciones) están pendientes.
