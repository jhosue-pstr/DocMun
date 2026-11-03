# Task Plan: DocMun — Seguimiento de documentos municipales

## Goal

Construir un sistema web (FastAPI + PostgreSQL + Jinja2/HTMX) que registre documentos
municipales, controle su paso por las 16 oficinas con plazos en horas hábiles,
calcule el semáforo de retraso y notifique al ciudadano por WhatsApp/correo al finalizar.

## Next Step

Comenzar la Fase 3 (Fase 1 del producto): CRUD de catálogos y alta de documentos
con cargo PDF y QR.

## Current Phase

Phase 3

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
- [ ] CRUD de catálogos (oficinas, tipos, feriados, horario)
- [ ] Alta de ciudadano y documento con código interno
- [ ] Generación de cargo PDF con QR
- [ ] Importador del Excel histórico (2483 registros)
- **Status:** pending

### Phase 4: Fase 2 — circulación entre oficinas
- [ ] Derivar, recibir, observar, finalizar y anular
- [ ] Bandejas por oficina y timeline del documento
- [ ] Auditoría inmutable de cada movimiento
- **Status:** pending

### Phase 5: Fase 3 — motor de SLA y alertas
- [ ] Cálculo en horas hábiles con feriados
- [ ] Semáforo en_plazo / por_vencer / atrasado / muy_atrasado
- [ ] Job `evaluar_plazos` y alertas internas
- **Status:** pending

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
- [ ] Dashboard de cuellos de botella y promedios por oficina
- [ ] Portal público de seguimiento (sujeto a aprobación de requerimientos)
- **Status:** pending

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

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `pandas` no instalado | 1 | Se usó `openpyxl` 3.1.5, ya disponible |
| `workdir` DocMun no existía | 1 | Se creó el directorio desde el padre antes de ejecutar |
