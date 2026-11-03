# Progress Log

## Session: 2026-11-03

### Current Status
- **Phase:** 3 - Fase 1 del producto (registro y cargo con QR)
- **Started:** 2026-11-03
- **Plan ID:** 2026-11-03-docmun-mvp
- **Último commit:** `51f59b0 Fase 0: esqueleto FastAPI, Docker, modelos, migración, seed y login`

### Actions Taken

1. Se creó el directorio `DocMun/` y se copió el Excel fuente a `docs/fuentes/`.
2. Se inicializaron los archivos de planificación con `init-session.sh "DocMun MVP"`.
3. Se analizó el Excel con `openpyxl`:
   - 2483 registros, 18 columnas, 4 hojas.
   - 16 tipos de trámite (coinciden con los 16 ZIP de origen).
   - 441 números de certificado repetidos entre tipos.
   - Fechas de emisión completas; fechas de inicio solo en 59.3%.
4. Se documentaron hallazgos y modelo de datos en `findings.md`.
5. Se escribió el plan completo en `task_plan.md`.
6. El usuario confirmó que **las 16 áreas del Excel son las 16 oficinas**.
7. Se escribió `AGENTS.md` (equivalente a `/init`).
8. Se implementó la Fase 0:
   - `docker-compose.yml` con app, PostgreSQL 16 y Adminer.
   - FastAPI con `SessionMiddleware`, CSRF y Argon2.
   - 10 tablas modeladas con SQLAlchemy 2.0.
   - Migración inicial `8f52ff33f364_esquema_inicial.py`.
   - Seed de 16 oficinas, 16 tipos, 256 plazos, 15 feriados y admin.
   - Login, logout, panel y página 403.
   - Tests de health, login, CSRF y redirecciones.
9. Se documentó el esquema en `docs/db/schema.md` y el mapeo Excel → DB.
10. Se inicializó git y se hizo el primer commit.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Lectura del Excel | 2483 filas | 2483 filas | OK |
| Detección de tipos | 16 | 16 | OK |
| Detección de duplicados | Reportar | 441 números repetidos | OK |
| `pytest` | 6 tests | 6 passed | OK |
| `ruff check .` | 0 errores | All checks passed | OK |
| `GET /health` | 200 ok | `{"status":"ok","app":"DocMun"}` | OK |
| Login real (curl) | 303 a /panel | 303 `location: /panel` | OK |
| Panel autenticado | Renderiza | Muestra admin y 16 oficinas | OK |
| Panel sin sesión | 401 / redirect | 401 JSON, 303 con `Accept: text/html` | OK |

### Errors
| Error | Resolution |
|-------|------------|
| `pandas` no instalado | Se usó `openpyxl` 3.1.5 |
| `workdir` DocMun no existía al crear | Se creó desde el directorio padre |
| Índice duplicado `ix_documentos_fecha_registro` en SQLite | Se quitó el `Index` explícito; queda `index=True` en la columna |
| Ruff I001 en `alembic/env.py` | `ruff check --fix` |

### Next
Fase 1 del producto: CRUD de catálogos, alta de documentos, cargo PDF con QR e
importador del histórico.
