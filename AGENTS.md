# AGENTS.md — DocMun

Sistema de registro y seguimiento de documentos de la Municipalidad. El expediente es
físico; el sistema lo virtualiza con un código interno y un QR. Al finalizar se notifica
al ciudadano por WhatsApp (correo opcional).

## Flujo del documento y del QR

1. El papel llega a ventanilla **sin QR**.
2. Ventanilla registra el documento en el sistema; ahí nace el código interno
   `DOC-AAAA-NNNNNN`.
3. Se imprime el cargo o una etiqueta con el QR y se adhiere al expediente físico.
4. Escanear el QR abre `/documentos/{codigo}` (detalle interno por ahora).

No se “escanea el documento” para crear el QR: el identificador nace del registro y el
QR solo codifica la URL de seguimiento. El portal público sigue pendiente de aprobación.

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Base de datos | PostgreSQL 16 |
| Frontend | Jinja2 + HTMX + Alpine.js + Tailwind (CDN, sin build) |
| Auth | Sesión en cookie firmada (`SessionMiddleware`), contraseñas Argon2 |
| IA local (opcional) | Tesseract OCR + LayoutLMv3 (torch CPU) en el contenedor `ia` |
| Contenedores | Docker Compose: app + db + adminer + scheduler + ia |

## Arranque y comandos

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec app alembic upgrade head    # no hay auto-migración al arrancar
docker compose exec app python -m scripts.seed
docker compose exec app python -m scripts.importar_historico --dry-run   # revisar sin escribir
docker compose exec app python -m scripts.importar_historico             # importar todo
docker compose exec app python -m scripts.importar_muestras --limite 20  # muestra rápida
```

```bash
docker compose logs -f app
docker compose exec app pytest -q
docker compose exec app pytest -q tests/test_auth.py::test_login_correcto
docker compose exec app ruff check .
docker compose exec app ruff format .
docker compose exec app python -m scripts.evaluar_plazos --dry-run  # alertas sin escribir
docker compose exec app python -m scripts.scheduler --una-vez      # una pasada del job
docker compose exec app python -m scripts.simular_flujo --dry-run  # flujo de demostración
docker compose exec app alembic revision --autogenerate -m "descripcion"
docker compose down          # añade -v para borrar la base
```

```bash
docker compose logs -f ia                                    # worker de análisis
docker compose exec ia python -m scripts.worker_ia --una-vez  # una pasada de la cola
docker compose exec ia python -m scripts.analizar_documento --archivo storage/.../x.pdf
docker compose exec app python -m scripts.exportar_dataset_ia --salida dataset/ia
```

- App: <http://localhost:8000>. Adminer: <http://localhost:8080> (sistema
  `PostgreSQL`, servidor `db`, usuario/clave `docmun`/`docmun`).
- Postgres se publica en el host en **5433**, no en 5432.
- Sin Docker para la app: `docker compose up -d db`, instala `requirements-dev.txt`,
  `alembic upgrade head` y `uvicorn app.main:app --reload`.
- `pytest` no necesita Postgres: usa SQLite en memoria y `dependency_overrides`.
- `docker compose` inyecta `DATABASE_URL=...@db:5432`; `.env.example` apunta a
  `localhost:5433` para correr fuera de Docker. El entorno del compose gana sobre `.env`.
- `scripts.seed` es idempotente; si el admin ya existe **no** actualiza su contraseña.

## Estructura real

```
app/main.py          # app FastAPI, middleware de sesión, /health, /static
app/core/            # settings, deps de auth, CSRF, Argon2
app/db/              # engine, SessionLocal, get_db, Base/TimestampMixin
app/models/          # tablas SQLAlchemy (registrar en __init__.py)
app/services/        # folio, documentos, ciudadanos, eventos, archivos, QR, cargo PDF,
                     # sla (horas hábiles), alertas (job de semáforo), circulacion
                     # (máquina de estados), metricas (dashboard por oficina) e
                     # ia/ (motor OCR+LayoutLMv3, reglas `extraccion` y cola)
app/web/             # rutas de documentos, circulación, bandejas, admin, flash y templates
app/data/            # catálogos del seed: 16 áreas y feriados
scripts/seed.py      # seed de catálogos, configuración y admin
scripts/importar_historico.py  # importador completo del Excel histórico
scripts/importar_muestras.py   # wrapper de compatibilidad (20 filas por defecto)
scripts/evaluar_plazos.py      # una pasada del job de semáforo y alertas
scripts/scheduler.py           # bucle continuo del job (servicio `scheduler`)
scripts/worker_ia.py           # bucle de la cola de análisis (servicio `ia`)
scripts/analizar_documento.py  # prueba el motor con un PDF/imagen, sin DB
scripts/exportar_dataset_ia.py # exporta análisis confirmados a dataset/
scripts/entrenar_ia.py         # entrena un clasificador municipal (otra máquina)
scripts/simular_flujo.py       # flujo de demostración para documentos históricos
Dockerfile.ia        # imagen del worker: Tesseract + torch CPU + modelos
models/ia/           # pesos LayoutLMv3 (gitignoreado; montado solo lectura)
storage/             # PDFs/imágenes subidos (gitignoreado; solo .gitkeep)
alembic/             # migraciones; env.py toma DATABASE_URL de settings
tests/               # pytest + SQLite en memoria
docs/db/schema.md    # esquema y mapeo Excel → DB
docs/ia.md           # arquitectura, operación y entrenamiento del módulo IA
.planning/...        # plan de trabajo
```

Todavía **no existen** `app/api/v1/`, `app/schemas/` ni `app/jobs/`. Hay rutas web
(`/`, `/login`, `/logout`, `/panel`, `/health`) y tres grupos:

- `/documentos`: lista con filtros y paginación, `/documentos/nuevo`, detalle
  `/documentos/{codigo}`, adjuntos, `qr.png`, `cargo.pdf`, `etiqueta.pdf`, las
  acciones POST de circulación (`/derivar`, `/recibir`, `/observar`, `/subsanar`,
  `/finalizar`, `/archivar`, `/anular`) y el análisis IA de borradores/archivos
  (`/analizar-borrador`, `/analizar-borrador/{id}`,
  `/{codigo}/archivos/{id}/analizar` y `.../analisis`).
- `/bandejas`: bandeja por rol (por recibir, en oficina, registrados por derivar).
- `/admin`: CRUD de catálogos (`/admin/oficinas`, `/admin/tipos-tramite`,
  `/admin/plazos`, `/admin/feriados`, `/admin/configuracion`) y `/admin/metricas`
  (dashboard del semáforo con gráficos y filtros por periodo, oficina, tipo y origen).

No asumas endpoints REST ni notificaciones.

## Convenciones que no son obvias

- Dominio en español (`documento`, `oficina`, `movimiento`); infraestructura en inglés.
- `render()` inyecta `csrf_token`, `current_user=None`, `flash_messages` y `now`. En
  páginas autenticadas pasa `current_user` explícitamente; en todo POST incluye el
  hidden `csrf_token` y llama `verify_csrf(request, token)`.
- Usa `flash(request, mensaje, categoria)` antes de redirigir; `render()` consume y
  limpia los mensajes.
- Protege rutas con `Depends(require_user)` o `require_roles(Rol.X)`. `admin` pasa
  siempre. Un 401 solo redirige a `/login` si `Accept` contiene `text/html`; un 403
  renderiza `403.html`. Si no, la respuesta es JSON. En tests usa
  `headers={"accept": "text/html"}`.
- Guarda datetimes con zona (columnas `DateTime(timezone=True)`, UTC) y presenta con el
  filtro `|fecha`, que convierte a `America/Lima`. No uses datetimes naive.
- `enum_col` guarda el *value* del enum como VARCHAR; en plantillas se compara con el
  string (`rol == 'admin'`). No hay CHECK de Postgres que valide los valores.
- `app/models/__init__.py` debe importar todo modelo nuevo; de ahí leen Alembic y el
  `create_all` de los tests.
- `app/web/admin_routes.py` concentra `/admin/*`: exige `require_roles(Rol.ADMIN)`,
  valida en servidor, maneja `IntegrityError` con error de formulario y responde con
  flash + redirect. La baja de oficinas y tipos es lógica (`activa`/`activo`).
- `app/web/documentos_routes.py` concentra `/documentos*`. Registrar exige
  `Rol.VENTANILLA`; adjuntar permite `Rol.VENTANILLA` y `Rol.OFICINA`; leer exige
  sesión. El orden importa: `/documentos/nuevo` va antes de `/{codigo}`.
- `app/web/circulacion_routes.py` concentra las acciones POST de circulación y
  `/bandejas`. Cada acción bloquea el documento con `obtener_para_actualizar`
  (`SELECT ... FOR UPDATE` en Postgres), ejecuta la función de `app/services/circulacion.py`,
  hace `commit` y redirige; un permiso inválido responde 403 y un estado inválido
  redirige con flash de error.
- `app/services/circulacion.py` es la única puerta de la máquina de estados. Permisos:
  `admin` opera todo; `ventanilla` deriva solo `registrado`; `oficina` opera solo los
  documentos de su oficina (derivar, recibir, observar, subsanar, finalizar, archivar);
  `direccion` solo consulta y anula. Observar/subsanar solo cambian estado y generan
  evento (no crean movimiento); derivar/finalizar/anular cierran el tramo abierto.
- `app/services/sla.py` calcula el semáforo desde `fecha_limite` y el reloj, y
  `fecha_limite` al confirmar recepción usando `horario_laboral` y `feriados`. Es
  determinista y no guarda estado; el job que genera alertas vive en
  `app/services/alertas.py` y `scripts/scheduler.py`.
- `app/services/archivos.py` valida *magic bytes* y tamaño antes de crear el
  documento, deduplica por SHA-256 y guarda fuera de la base de datos en
  `storage/documentos/{codigo}/{uuid}.{ext}`. Nunca aceptes la extensión o el
  `Content-Type` como prueba del formato.
- El QR solo codifica `PUBLIC_BASE_URL + /documentos/{codigo}`; no incluye PII. El
  cargo y la etiqueta se generan al vuelo con reportlab (`app/services/cargo_pdf.py`).
- El módulo IA es **opcional y local**: la app web nunca importa `torch`. El motor
  vive en `app/services/ia/motor.py`, importa torch dentro de las funciones y cae a
  solo-OCR si falta un modelo; `extraccion.py` es Python puro con reglas `TR-xx`.
  La cola es la tabla `documento_analisis` y la procesa `scripts/worker_ia.py`
  (servicio `ia`). El clasificador RVL-CDIP solo se guarda como `tipo_clase`: no
  asigna el trámite. Al elegir un archivo el análisis arranca solo y el navegador
  prellena únicamente los campos vacíos (nunca pisa lo escrito); el registro exige
  confirmación humana y enlaza el borrador con `analisis_id`, revalidando el hash.
  Los modelos viven en `models/ia/` (activos) y `models/ia/versiones/` (respaldos);
  `IA_MODELO_KIE` elige cuál usar y `extraccion.VERSION_REGLAS` invalida la caché por
  hash cuando cambian las reglas (súbela al tocar `extraccion.py`).
- `alembic/env.py` fija la URL desde `settings.database_url`; `sqlalchemy.url` de
  `alembic.ini` se ignora. No edites esa URL.
- Frontend sin build: se editan directamente las plantillas; Tailwind/HTMX/Alpine vienen
  por CDN en `base.html`. No agregues npm/Vite.
- `ruff` usa line-length 100 y excluye `alembic/versions`; no reformatees migraciones.

## Tests

- `tests/conftest.py` crea las tablas desde `Base.metadata` en SQLite y sustituye
  `get_db`. Usa las fixtures `db`, `client` y `admin_user`.
- SQLite no reproduce diferencias de Postgres (constraints, timezone, JSON). Valida
  migraciones y constraints contra Postgres cuando importe.
- `crear_documento` reintenta el código interno ante colisión y no crea movimientos;
  el primer movimiento pertenece a la fase de circulación.

## Reglas de negocio (contrato del dominio)

El registro, los adjuntos, la circulación y el motor de semáforo/job ya están
implementados. Las notificaciones siguen pendientes de la fase 4 del producto.

1. **El semáforo es calculado**, desde `fecha_limite` y el reloj; nunca se almacena ni
   se edita.
2. **Los movimientos son inmutables.** Corregir un error = movimiento de reversión;
   nunca `DELETE` ni `UPDATE`.
3. **Todo cambio de estado genera un `Evento`** con usuario, IP y timestamp.
4. **El plazo corre desde la recepción confirmada** por la oficina (configurable).
5. **Los plazos se calculan en horas hábiles**; horario y feriados viven en
   `configuracion` y `feriados`.
6. **Un documento está en una sola oficina a la vez.**
7. **Finalizar dispara la notificación** al ciudadano; el envío se registra en
   `notificaciones` con estado y error, y se reintenta si falla.

## Modelo de datos (lo mínimo)

- 13 tablas; el detalle autoritativo está en `docs/db/schema.md`. La trece es
  `documento_analisis` (OCR + sugerencias IA, opcional).
- Las 16 áreas del Excel son las 16 oficinas (`OF-01`…`OF-16`); el seed genera un tipo
  de trámite por área (`TR-01`…`TR-16`).
- `documentos.codigo` es único (`DOC-AAAA-NNNNNN`). `generar_codigo()` en
  `app/services/folio.py` no evita colisiones por sí solo; `crear_documento()` reintenta.
- Cada trámite tiene un solo código y puede tener varios `documento_archivos`. Los
  archivos binarios viven en `storage/`; la base de datos solo guarda metadatos.
- Unicidad clave: `(tipo_tramite_id, numero_certificado, anio)`. El número de
  certificado se repite entre tipos (441 casos en el Excel); no es único global.
- `movimientos.fecha_recepcion` inicia el SLA; `fecha_limite` se guarda al recibir,
  calculada en horas hábiles, pero el semáforo se deriva de ella.
- Roles: `admin`, `ventanilla`, `oficina`, `direccion`.
- Estados: `registrado`, `derivado`, `en_oficina`, `observado`, `finalizado`,
  `archivado`, `anulado`. Semáforo: `en_plazo`, `por_vencer`, `atrasado`,
  `muy_atrasado`.

## Datos, seguridad y entorno

- El Excel fuente vive en `docs/fuentes/` y está **gitignoreado** por contener datos
  personales. No lo fuerces a git ni lo subas.
- Nunca commitear `.env`, `storage/`, `models/`, `dataset/` ni datos personales.
- DNI, teléfono y correo son datos personales (Ley 29733): no escribirlos en logs.
  El texto OCR y los campos sugeridos también lo son; el evento `analizado` solo
  guarda ids, motor, versión, páginas, tipo sugerido y confianza.
- Contraseñas con Argon2; nunca en texto plano ni en logs.
- Producción va detrás de HTTPS (Caddy/Nginx).
- `PUBLIC_BASE_URL` define la URL que se codifica en el QR; en producción debe ser el
  dominio público real.
- Licencias de la IA: LayoutLMv3 base es CC BY-NC-SA 4.0, PyMuPDF es AGPL-3.0 y
  Tesseract Apache-2.0. Revísalas antes de un despliegue comercial.

## Plan y estado

- Plan activo: `.planning/2026-11-03-docmun-mvp/task_plan.md`. Antes de una tarea
  compleja, lee también `findings.md` y `progress.md`; al terminar un bloque actualiza
  el estado de la fase y `progress.md`.
- Estado: Fase 0 técnica completa. Fase 1 del producto completa: catálogos, registro,
  adjuntos, QR, cargo/etiqueta y los 2483 registros del Excel procesados (2371
  documentos únicos; 112 filas repetidas por la clave `(tipo, número, año)`).
- Fase 2 del producto completa: circulación (derivar, recibir, observar, subsanar,
  finalizar, archivar, anular), bandejas por rol, movimientos inmutables y
  `fecha_limite` en horas hábiles al confirmar recepción.
- Fase 3 del producto completa: semáforo calculado, job idempotente de alertas
  (`scripts/scheduler.py`) y dashboard `/admin/metricas` con gráficos y filtros.
- Extra completo: asistente local de IA (OCR Tesseract + LayoutLMv3) que sugiere
  tipo y campos en ventanilla con confirmación humana, worker `ia`, borradores y
  scripts de dataset/entrenamiento. Ver `docs/ia.md`. Los modelos incluidos son
  genéricos; el reentrenamiento municipal queda para otra máquina con GPU.
- La fase 4 del producto (notificaciones WhatsApp/correo) sigue pendiente.
