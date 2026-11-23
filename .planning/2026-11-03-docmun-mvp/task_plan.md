# Task Plan: DocMun — Seguimiento de documentos municipales

## Goal

Construir un sistema web (FastAPI + PostgreSQL + Jinja2/HTMX) que registre documentos
municipales, controle su paso por las 16 oficinas con plazos en horas hábiles,
calcule el semáforo de retraso y notifique al ciudadano por WhatsApp/correo al finalizar.

## Next Step

Implementar la Fase 4 del producto (notificaciones): interfaz `Notifier` con
adaptadores consola/correo/WhatsApp Cloud, registro de `Notificacion` al finalizar y
job de envío con reintentos. El registro, los adjuntos, el QR, el cargo, la
circulación, el motor de SLA y el asistente local de IA ya están operativos.

## Current Phase

Phase 6

## Phases

### Phase 1: Descubrimiento y modelo de datos
- [x] Analizar el Excel consolidado de certificados municipales
- [x] Extraer tipos de trámite, campos y reglas de unicidad
- [x] Definir entidades, relaciones y estados
- [x] Documentar hallazgos en findings.md
- **Status:** complete

### Phase 2: Fase 0 técnica — esqueleto y base
- [x] Crear estructura del proyecto y repositorio git
- [x] Docker Compose con app + PostgreSQL
- [x] FastAPI con configuración, sesión y roles
- [x] Modelos SQLAlchemy y migración inicial Alembic
- [x] Seed de oficinas, tipos de trámite y usuario admin
- [x] Login y layout base con HTMX/Tailwind
- [x] Tests de health, login y permisos
- **Status:** complete

### Phase 3: Fase 1 — registro y cargo con QR
- [x] Cimientos: `openpyxl`, `PUBLIC_BASE_URL`, flash, navegación, auditoría y
      servicios base (`ciudadanos`, `documentos`)
- [x] CRUD de catálogos (oficinas, tipos, feriados, horario)
- [x] Alta de ciudadano y documento con código interno
- [x] Lista con filtros, detalle, adjuntos y visualización autenticada
- [x] Generación de cargo PDF, QR y etiqueta 100×70 mm
- [x] Muestra idempotente de 20 registros históricos
- [x] Importador completo del Excel histórico (2483 filas → 2371 documentos únicos)
- **Status:** complete

### Phase 4: Fase 2 — circulación entre oficinas
- [x] Derivar, recibir, observar, subsanar, finalizar, archivar y anular
- [x] Bandejas por rol y recorrido del documento con timeline inmutable
- [x] Auditoría inmutable de cada movimiento y evento
- [x] `fecha_limite` en horas hábiles al confirmar recepción
- **Status:** complete

### Phase 5: Fase 3 — motor de SLA y alertas
- [x] Cálculo en horas hábiles con feriados
- [x] Semáforo en_plazo / por_vencer / atrasado / muy_atrasado
- [x] Job `evaluar_plazos` y alertas internas
- [x] Scheduler continuo y datos de demostración con flujo real
- **Status:** complete

### Phase 6: Fase 4 — notificaciones
- [ ] Interfaz Notifier con adaptadores consola, correo y WhatsApp Cloud
- [ ] Job de envío con reintentos y log
- [ ] Disparo automático al finalizar
- **Status:** pending

### Phase 7: Verificación y entrega del MVP
- [ ] Pruebas de flujo completo
- [ ] Documentación de despliegue en el servidor municipal
- [ ] Respaldos y checklist de seguridad
- **Status:** pending

### Phase 8: Post-MVP — dashboard y portal ciudadano
- [x] Dashboard de cuellos de botella y promedios por oficina (`/admin/metricas`)
- [ ] Portal público de seguimiento (sujeto a aprobación de requerimientos)
- **Status:** partial

### Phase 9: Asistente local de IA (extra, opcional)
- [x] Tabla `documento_analisis` y cola de trabajo con worker propio
- [x] OCR con Tesseract y reglas en español para tipo y campos
- [x] LayoutLMv3 local (clasificación y KIE) como señal secundaria
- [x] Prellenado del registro con confirmación humana y borradores
- [x] Scripts de dataset y entrenamiento (`exportar_dataset_ia`, `entrenar_ia`)
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Las 16 áreas del Excel son las 16 oficinas | Confirmado por el usuario |
| FastAPI + SQLAlchemy 2 + Alembic | Validación automática, OpenAPI y webhooks de WhatsApp |
| Jinja2 + HTMX + Tailwind | Sin build de JS; API queda lista para React/Flutter futuro |
| PostgreSQL en Docker | Mismo entorno en PC de desarrollo y servidor municipal |
| Plazo por oficina desde recepción confirmada | Más justo y auditable; configurable si cambia el criterio |
| Semáforo calculado, no editable | Evita que se oculte un retraso cambiando el estado |
| Movimientos inmutables | Auditoría municipal; correcciones por reversión |
| Proveedor WhatsApp por interfaz | Permite desarrollar sin cuenta Meta y conectar después |
| Importar el Excel como histórico | 2483 documentos de referencia y base para reportes |
| Archivos en disco, metadatos en `documento_archivos` | Evita blobs en PostgreSQL y permite varios escaneos por trámite |
| Un código interno agrupa todos los archivos del mismo trámite | El expediente físico es uno solo; los anexos se adjuntan después |
| Muestra de 20 registros antes del importador completo | Permite validar la UI y el mapeo sin cargar datos personales masivos |
| Año inferido desde `archivo_origen` | 92 filas traían solo el correlativo; el nombre del archivo sí incluía el año |
| Las 112 filas con clave repetida se omiten y se reportan | No se puede violar `UNIQUE (tipo_tramite_id, numero_certificado, anio)`; quedan para revisión manual |
| El job de SLA corre en un servicio `scheduler` de Compose | Evita depender de cron/systemd del host y funciona igual en desarrollo y en el servidor |
| Las alertas internas se registran como `Evento ALERTA_PLAZO` una sola vez por tramo y nivel | El semáforo no se guarda y el job queda idempotente |
| La base de demostración se reconstruye con un flujo simulado | Permite probar bandejas, timeline y semáforo con los 2371 documentos históricos sin inventar datos personales |
| La IA corre en un contenedor aparte (`ia`) con cola en base de datos | La app web no instala torch; un modelo pesado no bloquea el registro ni la circulación |
| El tipo de trámite se sugiere con reglas en español, no con RVL-CDIP | El clasificador incluido es genérico y en inglés; en una carta real predijo `resume` al 99.96 % |
| Toda sugerencia requiere confirmación de ventanilla | El expediente es un acto administrativo; la IA solo prellena y deja la etiqueta para reentrenar |
| El binario del borrador vive en `storage/borradores/` hasta registrar | Permite analizar antes de crear el documento y evita adjuntos huérfanos |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `pandas` no instalado | 1 | Se usó `openpyxl` 3.1.5, ya disponible |
| `workdir` DocMun no existía | 1 | Se creó el directorio desde el padre antes de ejecutar |
