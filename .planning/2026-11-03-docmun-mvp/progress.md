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

---

## Session: 2026-11-04

### Current Status
- **Phase:** 3 - Fase 1 del producto, en curso
- **Plan ID:** 2026-11-03-docmun-mvp
- **Plan detallado:** `fase-1-plan.md` (6 subfases)
- **Subfase completada:** Phase 1 — Sincronización y cimientos

### Actions Taken

1. Se aclaró el flujo del QR: el papel llega sin QR, ventanilla lo registra, el
   sistema genera `DOC-AAAA-NNNNNN` y se imprime un cargo/etiqueta con el QR.
2. Se ejecutó la Phase 1 del plan detallado:
   - `public_base_url` en `app/core/config.py` y `PUBLIC_BASE_URL` en `.env.example`.
   - `openpyxl==3.1.5` en `requirements.txt`.
   - `app/web/flash.py` + inyección de `flash_messages` y `now` en `render()`.
   - Mensajes flash, navegación activa y footer “Fase 1” en `base.html`.
   - Rutas placeholder `/documentos` y `/admin` (admin) con `en_construccion.html`.
   - `app/services/eventos.py` (auditoría con IP, sin PII).
   - `app/services/ciudadanos.py` (get-or-create por tipo y número).
   - `app/services/documentos.py` (alta con reintento de código, búsqueda y detalle).
   - `tests/test_servicios_base.py` y pruebas de rutas en `tests/test_auth.py`.
   - `AGENTS.md` reescrito con estructura real, comandos y flujo del QR.
3. Se copió el plan detallado a `.planning/2026-11-03-docmun-mvp/fase-1-plan.md` y se
   marcó su Phase 1 como completa.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | 17 tests | 17 passed | OK |
| `ruff check app tests scripts` | 0 errores | All checks passed | OK |
| Reintento de código | 5 intentos y error claro | Cubierto por tests | OK |
| Duplicado `(tipo, número, año)` | `DocumentoDuplicado` | Cubierto por test | OK |
| Mismo número en otro tipo | Permitido | Cubierto por test | OK |
| Evento `CREADO` sin movimiento | 1 evento, 0 movimientos | Cubierto por test | OK |
| Rutas placeholder | 200 admin, 303 sin sesión | Cubierto por test | OK |

### Errors
| Error | Resolution |
|-------|------------|
| Contenedor `app` detenido y sin `argon2` en el host | `docker compose up -d db app` y ejecutar los comandos dentro del contenedor |
| Imagen sin reconstruir tras añadir `openpyxl` | Tests no lo importan; reconstruir con `--build` antes de usar el importador (Phase 5) |

### Notes
- `app/data/catalogos.py` tiene una línea de comentario eliminada por el usuario; no se
  revirtió.
- No hubo migración nueva: el esquema de 11 tablas ya cubre la Fase 1.
- El alta de documentos no crea `Movimiento`; la primera derivación es de la Fase 2.

### Next
Phase 2 del plan detallado: CRUD de catálogos en `/admin` (oficinas, tipos de trámite,
plazos, feriados y configuración) con autorización de admin y CSRF.

---

## Session: 2026-11-04 — CRUD de catálogos

### Current Status
- **Phase:** 3 - Fase 1 del producto, en curso
- **Subfase completada:** Phase 2 — CRUD de catálogos

### Actions Taken

1. Se creó `app/web/admin_routes.py` con el router `/admin`:
   - Resumen con contadores.
   - Oficinas: listado con búsqueda, alta, edición y activación/desactivación.
   - Tipos de trámite: listado con búsqueda, alta, edición y activación.
   - Plazos: filtro por tipo, upsert tipo × oficina, edición inline y eliminación.
   - Feriados: alta con fecha única y eliminación.
   - Configuración: alta de claves y edición de valor/descripción con validación de
     JSON para `horario_laboral`.
2. Se crearon las plantillas `app/web/templates/admin/*.html` con una navegación común
   (`base_admin.html`), Tailwind y token CSRF en todos los formularios.
3. Se conectó el router en `app/main.py` y se retiró el placeholder `/admin` de
   `app/web/routes.py`.
4. Se agregó `tests/test_admin_catalogos.py` con 10 pruebas de CRUD, duplicados,
   validaciones, permisos y CSRF.
5. Se actualizó el badge del panel a “Fase 1 · Catálogos activos”.
6. Smoke test HTTP contra Postgres: login admin y las 5 secciones responden 200.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | 27 tests | 27 passed | OK |
| `ruff check app tests scripts` | 0 errores | All checks passed | OK |
| `/admin` como admin | 200 con secciones | 200 | OK |
| `/admin/*` | 200 | oficinas, tipos, plazos, feriados, configuración | OK |
| `/admin` como ventanilla | 403 | 403 “Acceso denegado” | OK |
| Duplicado de oficina/tipo | Error de formulario | 400 con mensaje | OK |
| Feriado duplicado | Error de formulario | Flash de error | OK |
| JSON de horario inválido | Error de formulario | Flash de error | OK |

### Notes
- No hubo migración nueva: los catálogos ya existían en el esquema.
- Plazos muestra las 256 combinaciones del seed; el filtro por tipo reduce la lista.
- La baja de oficinas y tipos es lógica (`activa`/`activo`); los plazos y feriados se
  pueden eliminar porque no tienen referencias históricas.

### Next
Phase 3 del plan detallado: registro y consulta de documentos (`/documentos`,
`/documentos/nuevo`, detalle y evento de creación).

---

## Session: 2026-11-05 — Registro, adjuntos y muestra histórica

### Current Status
- **Phase:** 3 - Fase 1 del producto, en curso
- **Subfases completadas:** Phase 3 (registro y consulta) y Phase 4 (QR/cargo)
- **Subfase en curso:** Phase 5 (importador histórico; muestra de 20 cargada)

### Actions Taken

1. Se agregó la tabla `documento_archivos` y los enums `OrigenArchivo` y
   `TipoEvento.ADJUNTO_AGREGADO`; migración `cf01505205fe` aplicada en Postgres.
2. Se implementó `app/services/archivos.py`:
   - Validación por *magic bytes* para PDF, JPEG, PNG y TIFF.
   - Límite `MAX_UPLOAD_MB`, deduplicación por SHA-256 y rutas seguras dentro de
     `STORAGE_DIR`.
   - Guardado en `storage/documentos/{codigo}/{uuid}.{ext}` con limpieza si falla.
3. Se implementaron `app/services/qr.py`, `app/services/cargo_pdf.py` y las rutas
   `/documentos`, `/nuevo`, `/{codigo}`, `/{codigo}/adjuntar`, `/{codigo}/archivos/{id}`,
   `qr.png`, `cargo.pdf` y `etiqueta.pdf`.
4. Se crearon las plantillas `documentos/lista.html`, `nuevo.html` y `detalle.html`;
   el registro crea `Documento` + `Evento CREADO` y no crea `Movimiento`.
5. Se agregaron 11 pruebas en `tests/test_documentos_routes.py` y se actualizó el
   esquema documentado con la tabla de archivos y las reglas de `storage/`.
6. Se creó `scripts/importar_muestras.py` y se cargaron 20 registros del Excel:
   - 16 áreas representadas, más repeticiones en las áreas de mayor volumen.
   - `origen=historico`, `estado=archivado`, sin movimientos ni PDFs.
   - Reejecución: 0 creados, 20 duplicados, 0 inválidos.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | Suite completa | 38 passed | OK |
| `ruff check app tests scripts` | 0 errores | All checks passed | OK |
| Registro con PDF | 303 + archivo en disco | 303, metadatos y evento | OK |
| Archivo inválido | 400 sin documento | 400, sin registros parciales | OK |
| Adjunto duplicado | No duplicar | 1 solo archivo | OK |
| QR/cargo/etiqueta | 200 y firmas correctas | PNG y PDF válidos | OK |
| Smoke HTTP contra Postgres | Lista, detalle y adjuntos | 200 en detalle, QR, cargo y etiqueta | OK |
| Muestra histórica | 20 creados | 20 creados; 20 duplicados al repetir | OK |

### Errors
| Error | Resolution |
|-------|------------|
| Contenedores detenidos al inspeccionar el Excel | `docker compose up -d db app` |
| `psycopg2` no instalado en el contenedor | Se usó SQLAlchemy/psycopg, ya configurado en la app |
| Código de folio empezó en `DOC-2026-000002` | Ya existía un documento manual previo; no se borró |

### Notes
- Los registros históricos no tienen PDF porque el Excel no incluye los archivos;
  se pueden adjuntar manualmente desde el detalle.
- En 2 filas la fecha de inicio era posterior a la emisión; se corrigió la fecha de
  registro a la de emisión y el importador ya aplica esa regla.
- El código interno agrupa todos los archivos de un mismo trámite.
- El QR solo contiene la URL interna `/documentos/{codigo}`; el portal público sigue
  pendiente de aprobación.
- No se implementaron circulación, SLA ni notificaciones.
- Los cambios quedan sin commit; no se deben versionar `.env`, `storage/` ni el Excel.

### Next
Completar el importador de los 2483 registros con pruebas y reporte de no mapeadas,
o iniciar la Phase 4 del producto (circulación entre oficinas).

---

## Session: 2026-11-10 — Importador histórico completo

### Current Status
- **Phase:** 3 - Fase 1 del producto, completa
- **Siguiente:** Phase 4 - Fase 2 del producto (circulación entre oficinas)
- **Plan ID:** 2026-11-03-docmun-mvp

### Actions Taken

1. Se creó `scripts/importar_historico.py` a partir de la muestra, con:
   - `--limite` (`0` = todas), `--lote`, `--dry-run` y `--reporte`.
   - Commits por lote con savepoints: una fila mala no aborta la importación.
   - Reporte JSON en `storage/reportes/` con conteos y filas descartadas; sin PII.
   - Inferencia del año desde `archivo_origen` cuando el número trae solo el
     correlativo (92 filas recuperadas).
   - Detección de claves repetidas en el archivo y ya existentes en la base.
2. `scripts/importar_muestras.py` quedó como wrapper de compatibilidad con 20 filas
   por defecto.
3. Se agregó `tests/test_importacion_historica.py` (10 pruebas): mapeo, descartes,
   inferencia de año, alta histórica, idempotencia, duplicados por lote, fechas
   corregidas, resumen del dry-run y reporte JSON.
4. Respaldo previo de PostgreSQL en `/tmp/docmun-pre-import-20261110.sql`.
5. Dry-run real: 2483 filas válidas, 0 descartadas, 2371 claves únicas, 112 repetidas
   en el archivo, 20 ya en la base, 2351 por crear.
6. Importación completa: 2351 creados en ~18 s con `--lote 200`.
7. Reejecución: 0 creados, 2483 duplicados, 0 inválidos (idempotencia confirmada).

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | Suite completa | 48 passed | OK |
| `ruff check app tests scripts` | 0 errores | All checks passed | OK |
| Dry-run del Excel real | 2483 válidas | 2483 válidas, 0 descartadas | OK |
| Importación completa | 2351 creados | 2351 creados | OK |
| Documentos en BD | 2372 (2371 históricos + 1 manual) | 2372 | OK |
| Históricos archivados | 2371 | 2371 | OK |
| Eventos `CREADO` | Uno por documento | 2372 | OK |
| Movimientos creados | 0 | 0 | OK |
| Claves duplicadas en BD | 0 | 0 | OK |
| Reejecución | 0 creados | 0 creados, 2483 duplicados | OK |

### Notes
- 90 grupos (112 filas) repiten `(tipo_tramite_id, numero_certificado, anio)` dentro
  del mismo tipo. No se pueden insertar sin violar la restricción única; quedan
  listadas por índice en el reporte. En 53 grupos el administrado difiere, lo que
  apunta a colisiones de numeración en el Excel y requiere revisión manual.
- 1466 filas usaron la fecha de emisión como fecha de registro porque no tenían
  inicio o este era posterior a la emisión.
- 92 filas tenían solo el correlativo; el año se infirió desde `archivo_origen`.
- Los documentos históricos siguen sin PDFs y sin movimientos/SLA.
- No hubo migración nueva.

### Next
Phase 4 del producto: circulación entre oficinas (derivar, recibir, observar,
subsanar, finalizar, archivar y anular) con movimientos inmutables, bandejas y
timeline. El cálculo de `fecha_limite` al recibir se apoya en un helper de horas
hábiles; el semáforo y el job quedan para la fase de SLA.

---

## Session: 2026-11-10 — Fase 2: circulación entre oficinas

### Current Status
- **Phase:** 5 - Fase 2 del producto, completa
- **Siguiente:** Phase 6 / Fase 3 del producto (motor de SLA y alertas)
- **Plan ID:** 2026-11-03-docmun-mvp

### Actions Taken

1. `app/services/sla.py`: cálculo determinista de `fecha_limite` en horas hábiles.
   - Lee `horario_laboral` y `feriados`; si el horario falta o es inválido, usa el
     horario por defecto (lunes–viernes 08:00–13:00 y 14:00–17:00; cada día
     acepta uno o varios tramos).
   - Salta fines de semana, feriados y días cerrados; arranca en la próxima apertura
     si el inicio cae fuera de horario. Devuelve UTC.
2. `app/services/circulacion.py`: máquina de estados y permisos.
   - `derivar`, `recibir`, `observar`, `subsanar`, `finalizar`, `archivar`, `anular`.
   - Permisos: `admin` todo; `ventanilla` deriva solo `registrado`; `oficina` opera
     solo su oficina; `direccion` consulta y anula.
   - `observar`/`subsanar` no crean movimiento, solo evento; derivar/finalizar/anular
     cierran el tramo abierto.
   - `recibir` fija `fecha_recepcion` y calcula `fecha_limite` con el plazo de
     `plazos` (tipo + oficina) o `tipos_tramite.sla_horas`.
   - `obtener_para_actualizar` usa `SELECT ... FOR UPDATE` en Postgres.
3. `app/web/circulacion_routes.py`: acciones POST con CSRF, bloqueo, flash + redirect
   y `/bandejas` por rol. Routers registrados en `app/main.py`.
4. Enums: `TipoEvento.SUBSANADO` y `TipoEvento.ARCHIVADO`.
5. UI: panel de circulación y recorrido por oficinas en el detalle, bandeja nueva,
   navegación y footer actualizados.
6. Tests: `tests/test_sla.py` (11) y `tests/test_circulacion.py` (12), incluyendo
   permisos, CSRF, SLA por oficina, bandejas y estados inválidos.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | Suite completa | 71 passed | OK |
| `ruff check app tests scripts` | 0 errores | All checks passed | OK |
| Smoke test real (Postgres) | Flujo completo | login → alta → derivar → recibir → observar → subsanar → finalizar → archivar | OK |
| `fecha_limite` real | Calculada al recibir | 48 h hábiles → 2026-11-18 | OK |
| Documento de prueba | Eliminado | `DOC-2026-002373` borrado | OK |

### Notes
- El semáforo (`en_plazo`, `por_vencer`, `atrasado`, `muy_atrasado`) todavía no se
  calcula ni se muestra: queda para la fase 3.
- No hay job de evaluación de plazos ni notificaciones.
- No hubo migración nueva: los enums viven como VARCHAR y no requieren cambio de
  esquema.
- El smoke test contra Postgres confirmó el orden de eventos
  `creado → derivado → recibido → observado → subsanado → finalizado → archivado`.

### Next
Fase 3 del producto: semáforo calculado desde `fecha_limite`, umbrales en
`configuracion` y job `evaluar_plazos` con alertas internas.

---

## Session: 2026-11-11 — Cierre de Fase 3 y flujo histórico simulado

### Current Status
- **Phase:** 5 - Fase 3 del producto, completa
- **Siguiente:** Phase 6 / Fase 4 del producto (notificaciones)
- **Plan ID:** 2026-11-03-docmun-mvp
- **Commit:** ninguno; los cambios quedan en el árbol de trabajo

### Actions Taken

1. Se cerró la Fase 3 del producto:
   - `scripts/scheduler.py` ejecuta `generar_alertas` en bucle, aguanta SIGTERM y
     admite `--una-vez`, `--intervalo` y `--limite`.
   - `docker-compose.yml` agrega el servicio `scheduler` con el mismo entorno que la
     app; el intervalo se controla con `SLA_INTERVALO_SEGUNDOS` (900 s por defecto).
   - `app/core/config.py` y `.env.example` documentan el intervalo.
2. Se habilitó la reconstrucción de flujos con fechas históricas:
   - `registrar_evento` acepta `creado_en`.
   - Las acciones de `circulacion.py` aceptan `ahora` y lo propagan a movimientos y
     eventos; el valor por defecto sigue siendo `datetime.now(UTC)`.
3. Se creó `scripts/simular_flujo.py`:
   - Solo procesa documentos sin movimientos y omite los que ya tienen flujo.
   - Reconstruye derivación, recepción, observación, subsanación, finalización,
     archivo y anulación con fechas coherentes con `fecha_registro`.
   - Reparte los documentos abiertos entre los cuatro niveles del semáforo.
   - Ajusta el `CREADO` de los históricos a su fecha de registro para que el timeline
     sea coherente; no modifica documentos ni archivos.
4. Respaldo previo de PostgreSQL en `/tmp/docmun-pre-simulacion-20261111.sql`.
5. Simulación completa sobre Postgres:
   - 2371 documentos procesados, 1 omitido (documento manual con movimientos), 0 errores.
   - 2820 movimientos y 699 tramos abiertos.
6. Job de alertas ejecutado dos veces: 473 alertas nuevas en la primera pasada y 0 en
   la segunda (idempotencia confirmada).
7. Servicio `scheduler` levantado con `docker compose up -d scheduler`.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest` | Suite completa | 92 passed | OK |
| `ruff check app scripts tests` | 0 errores | All checks passed | OK |
| Simulación dry-run | Sin cambios | Rollback correcto | OK |
| Simulación completa | Todos los históricos con flujo | 2371 procesados, 0 errores | OK |
| Reejecución de la simulación | 0 procesados | 0 procesados, 2372 omitidos | OK |
| Distribución de estados | Varios estados | 756 archivado, 726 finalizado, 565 en oficina, 134 observado, 109 anulado, 82 derivado | OK |
| Semáforo de abiertos | Cuatro niveles | en_plazo 226, por_vencer 185, atrasado 159, muy_atrasado 129 | OK |
| Alertas internas | Una por tramo y nivel | 473, sin duplicados al repetir | OK |

### Notes
- Las fechas de los movimientos simulados son anteriores a hoy y respetan el orden
  `creado → derivado → recibido → observado → subsanado → finalizado → archivado`.
- Los documentos abiertos tienen `fecha_recepcion` y `fecha_limite`; los derivados
  pendientes de recepción no tienen SLA todavía.
- El semáforo se sigue calculando en cada consulta y no se guarda.
- No hubo migración nueva.

### Next
Fase 4 del producto: interfaz `Notifier` (consola, correo y WhatsApp Cloud), registro
de `Notificacion` al finalizar y job de envío con reintentos e idempotencia.

---

## Session: 2026-11-11 — Dashboard de métricas por oficina

### Current Status
Fase 3 completa, incluido el dashboard del semáforo. Fase 4 (notificaciones) pendiente.

### Actions Taken
1. Se creó `app/services/metricas.py`:
   - `calcular_dashboard()` reutiliza `ContextoSla`, `evaluar_movimiento` y
     `horas_habiles_entre`; no agregó tablas ni migraciones.
   - Calcula KPIs globales, métricas por oficina (abiertos, por recibir, semáforo,
     antigüedad, cerrados, mediana de horas, mediana del SLA, cumplimiento,
     observaciones), documentos críticos, tipos y serie semanal.
   - Atribuye observaciones y alertas a la oficina del tramo abierto del momento.
2. Se agregó `/admin/metricas` en `app/web/admin_routes.py` (solo `admin`) y
   `admin/metricas.html` con filtros por periodo, oficina, tipo y origen.
3. Gráficos Chart.js por CDN: doughnut del semáforo, doughnut de estados, barras
   apiladas por oficina, barras de cumplimiento y consumo de SLA, barras por tipo,
   torta de carga y línea de tendencia semanal. Todos responden a los filtros.
4. Tablas de “Ahora por oficina”, “Desempeño por oficina” (con aviso de muestra
   insuficiente bajo 20 cierres) y documentos más atrasados.
5. Se agregó el tab “Métricas” a `admin/base_admin.html` y un acceso desde `/panel`.
6. Se actualizaron `AGENTS.md` y `README.md` al estado de Fase 3.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | Suite completa | 104 passed | OK |
| `ruff check app scripts tests` | 0 errores | All checks passed | OK |
| `/admin/metricas` | 200 con gráficos | 200; 7 canvas y tablas presentes | OK |
| Filtros | Cambian las cifras | periodo/oficina/tipo/origen aplicados | OK |

### Notes
- El dashboard no guarda el semáforo: se calcula por request desde los movimientos.
- Los tramos simulados cierran casi siempre en 16 h, así que el cumplimiento histórico
  de la demo sale 100 % y las medianas son planas. Es un límite de los datos de
  demostración, no del dashboard.
- No hubo migración nueva ni commits.

### Next
Fase 4: notificaciones (WhatsApp/correo) al finalizar.

---

## Session: 2026-11-20 — Asistente local de IA para ventanilla

### Current Status
Fase 3 completa. Se agregó el módulo opcional de IA (OCR + LayoutLMv3) que prellena
el registro con confirmación humana. Fase 4 (notificaciones) sigue pendiente.

### Actions Taken
1. Se copiaron los modelos del paquete local a `models/ia/clasificacion/modelo.pt` y
   `models/ia/kie/modelo.pt` (484 MB cada uno). `models/`, `dataset/` y `storage/`
   quedan fuera de git y de la imagen por `.dockerignore`.
2. Tabla nueva `documento_analisis` (migración `d4c9a1b7e2f0`, después de
   `cf01505205fe`) con `EstadoAnalisis` y `TipoEvento.ANALIZADO`.
   Un borrador tiene `documento_id` y `archivo_id` en NULL y su binario vive en
   `storage/borradores/{sha256}.{ext}`.
3. `app/services/ia/motor.py`: OCR Tesseract (`spa+eng`), rasterizado con PyMuPDF y
   clasificación/KIE con LayoutLMv3. Importa torch solo dentro de las funciones y
   degrada a solo-OCR si falta un modelo.
4. `app/services/ia/extraccion.py` (puro): reglas de palabras clave `TR-01`…`TR-16`
   y regex de DNI, RUC, expediente, número, año, asunto, referencia y administrado,
   con evidencia y confianza.
5. `app/services/ia/cola.py`: encolado, caché por hash, `FOR UPDATE SKIP LOCKED` en
   Postgres, reintentos, limpieza de borradores, evento `analizado` y enlace del
   borrador al registrar. `guardar_validado(..., encolar=...)` evita duplicados.
6. Rutas nuevas: `POST /documentos/analizar-borrador`,
   `GET /documentos/analizar-borrador/{id}`,
   `POST|GET /documentos/{codigo}/archivos/{id}/analizar|analisis`.
   `POST /documentos/nuevo` acepta `analisis_id` y revalida el hash del archivo.
7. Plantillas `_analisis.html` (detalle del archivo) y `_analisis_borrador.html`
   (sugerencias con botón *Aplicar* por campo); el registro muestra el botón
   *Analizar con IA* si `IA_HABILITADA`.
8. Servicio `ia` en `docker-compose.yml` (`Dockerfile.ia`, Tesseract, modelos de solo
   lectura, `cpus: 2.0`, `mem_limit: 4g`) y variables IA en `.env.example`.
9. Scripts `worker_ia`, `analizar_documento`, `exportar_dataset_ia` y `entrenar_ia`.
10. Documentación en `docs/ia.md` y `docs/db/schema.md`; `AGENTS.md` actualizado.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | Suite completa | 130 passed | OK |
| `ruff check .` | 0 errores | All checks passed | OK |
| `scripts/analizar_documento` (PDF real, 3 págs, CPU) | OCR + modelos | 26.6 s, 1000 palabras, `tesseract+layoutlmv3` | OK |
| Tipo por reglas en ese PDF | Sin falsos positivos | Sin coincidencia (carta de internet) | OK |
| Campos en ese PDF | Número, año, asunto, administrado | `207-2026-MPSRJ/GEMU/OGTI`, 2026, asunto y administrado | OK |
| E2E en Docker (login, subida, worker, registro) | Documento con análisis enlazado | `DOC-2026-002374`, OCR listo en 29 s | OK |
| Reutilización por hash | Sin reejecutar OCR | Segundo archivo igual quedó listo al instante | OK |
| Auto-prefill en `/documentos/nuevo` | Rellena solo campos vacíos | Markup con `data-campo`/`data-valor` y `Aplicar todas` | OK |

### Notes
- El clasificador RVL-CDIP del paquete predijo `resume` al 99.96 % en una carta
  municipal: se guarda solo como `tipo_clase` y nunca asigna el trámite.
- El KIE aporta HEADER/QUESTION/ANSWER pero no se usa para prellenar campos.
- Sin GPU: ~2 GB RAM y 27 s por PDF de 3 páginas. El entrenamiento no se validó en
  este equipo (no hay GPU); `entrenar_ia` quedó listo para correr en otra máquina.
- Pruebas con el venv del paquete de inferencia; la app sigue sin torch.
- El análisis arranca al elegir el archivo (htmx `change`) y el navegador prellena
  solo los campos vacíos; el registro sigue siendo humano y revalida el hash.
- Si se registra antes de que termine el OCR, el borrador se enlaza igual y el worker
  lo procesa ya dentro del expediente (sin repetir trabajo).
- Se verificó el flujo completo contra Postgres y el contenedor `ia` reales.

### Next
Fase 4 del producto: interfaz `Notifier` (consola, correo y WhatsApp Cloud), registro
de `Notificacion` al finalizar y job de envío con reintentos e idempotencia.

## Session: 2026-11-22 — Extracción tolerante al OCR y modelos consolidados

### Objetivo
Corregir el prellenado que devolvía `numero_certificado = "NO"` y `anio = 1926` en un
Certificado de Numeración real, sin reentrenar, y dejar todos los modelos juntos.

### Diagnóstico
- `CERTIFICADO | N°629-2026`: la regex no aceptaba `|` como separador y terminaba
  capturando el `NO` de "ESTE CERTIFICADO NO ACREDITA".
- El año salía del primer año del texto (`1926 - 2026`), no del número ni de la fecha.
- `administrado` vacío: el documento usa `A petición de: — Doña: …`.
- El KIE municipal no se usaba para prellenar: `cola.py` solo pasaba el texto a las
  reglas. Se confirmó que reentrenar no arreglaba estos campos.

### Cambios
1. `extraccion.py`: número tolerante a `|`, `N°`, `Nº`, espacios y guiones; descarte de
   coincidencias sin dígitos; año desde el número → fecha → año plausible reciente;
   administrado con `Doña/Don/A petición de`; respaldo de asunto con el nombre del tipo.
2. `VERSION_REGLAS`: se guarda en `campos.reglas` y la caché por hash solo reutiliza
   resultados de la misma versión (un cambio de reglas invalida la caché sola).
3. `cola.py`: el mapa `{codigo: nombre}` alimenta el respaldo de asunto y los pares KIE
   se conservan en `campos.kie` como diagnóstico/dataset.
4. `models/ia/versiones/`: `kie-generico-anterior.pt` (paquete original) y
   `kie-municipal-2026-11.pt` (modelo entrenado por el usuario). El activo
   `kie/modelo.pt` ya era el municipal; se puede cambiar con `IA_MODELO_KIE`.
5. Tests nuevos de regresión con el OCR real y de invalidación de caché.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `pytest -q` | Suite completa | 136 passed | OK |
| `ruff check .` | 0 errores | All checks passed | OK |
| `analizar_documento` en el PDF real | Campos correctos | `629-2026`, 2026, `CANAZA CAMARGO, Jessica` | OK |
| E2E en Docker (subida → worker → registro) | Prellenado correcto | Sugerencias correctas en 23 s, `analisis_id` enlazado | OK |
| Caché con la misma versión de reglas | Reutiliza al instante | Segundo envío listo en 0 s | OK |
| Caché de reglas viejas | Se reprocesa | Nueva fila `pendiente` | OK |

### Notes
- El KIE municipal (32 documentos, etiquetas genéricas) sigue sin prellenar campos; el
  prellenado sale de reglas. Para que el KIE aporte habría que etiquetar campos
  municipales y juntar ~300 escaneos.
- `IA_DPI=200` se mantiene; el OCR real ya funciona con las reglas tolerantes. Subir a
  300 es una opción si aparece letra muy pequeña (más lento).

## Session: 2026-11-23 — Artículo académico en LaTeX y Markdown

### Current Status
- **Fase:** entrega de artículo (documento, no cambio de producto)
- **Entregable:** `docs/Paquete_articulo_resultados_codigo/`

### Actions Taken
1. Se creó `latex/Articulo_redes_neuronales.tex`: artículo a dos columnas, A4,
   10 pt, con Resumen, Introducción, Trabajos relacionados, Materiales y métodos,
   Resultados, Discusión, Conclusiones, Trabajo futuro, Disponibilidad,
   Referencias y Apéndice de reproducibilidad.
2. Se agregaron `latex/referencias.bib` (11 fuentes IEEE), la figura TikZ de
   arquitectura y `latex/figuras/clasificacion_matriz_confusion.png`.
3. Se compiló con `pdflatex → bibtex → pdflatex ×2`: PDF de 6 páginas, sin
   referencias indefinidas ni errores.
4. Se actualizó `Articulo_borrador_redes_neuronales.md` para alinearlo con el
   artículo: autores UPeU, trabajos relacionados, resultados del prototipo
   integrado, análisis de errores, ética, disponibilidad y apéndice.
5. Se anonimizó el número de certificado del caso funcional (`123-2026`) y se
   verificó que no haya nombres, DNI, correos ni teléfonos en `.tex`, `.bib` o `.md`.
6. Se creó `latex/README.md` con los pasos para subir y compilar en Overleaf.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Compilación LaTeX | 0 errores | 6 páginas, exit 0 | OK |
| Referencias | Sin `?` | 11 referencias resueltas | OK |
| Búsqueda de PII en el artículo | Sin datos personales | Sin coincidencias | OK |
| `pytest -q` | Suite completa | 136 passed | OK |

### Notes
- Las métricas del artículo son las reales del paquete: clasificación 0,830/0,832;
  XFUND 0,605/0,282/0,304; municipal 0,622/0,108/0,039.
- El artículo no afirma beneficio operativo: presenta el sistema como asistencia
  con confirmación humana y documenta las amenazas a la validez.
