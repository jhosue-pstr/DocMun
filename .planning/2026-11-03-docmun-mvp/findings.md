# Findings & Decisions

## Requirements

- Registrar documentos municipales y hacer seguimiento de su recorrido.
- 16 oficinas; un documento puede pasar de una a otra hasta ser revisado y finalizado.
- Cada oficina tiene un tiempo máximo de permanencia (SLA).
- Estados del trámite: registrado, derivado, en oficina, observado, finalizado, archivado.
- Estado de plazo: en plazo, por vencer, atrasado, muy atrasado.
- Ver cuánto tiempo lleva el documento en cada oficina y si está atrasado.
- Al finalizar, notificar al ciudadano (WhatsApp; correo opcional).
- El expediente es físico: el sistema lo virtualiza con código y QR.
- Portal ciudadano: pendiente de más requerimientos; no bloquea el MVP.

## Research Findings

### Fuente de datos

Archivo: `docs/fuentes/Consolidado_certificados_municipales_todos_los_ZIP.xlsx`
(original en `~/Descargas/`). Generado a partir de 16 ZIP, uno por tipo de trámite.

| Hoja | Filas | Contenido |
|---|---|---|
| Base consolidada | 2483 | Registro principal (18 columnas) |
| Resumen | 42 | Totales del procesamiento |
| Revisión de datos | 1884 | Registros con observaciones |
| Evidencia de fechas | 2483 | Texto que sustenta cada fecha |

### Columnas de "Base consolidada" y % de llenado

| # | Columna | Llenado |
|---|---|---|
| 0 | N.º | 100% |
| 1 | Área / tipo de trámite | 100% |
| 2 | Número de certificado / visado / autorización | 100% |
| 3 | Fuente del número | 100% |
| 4 | RUT | 9.9% |
| 5 | Expediente administrativo | 33.6% |
| 6 | RUC | 1.6% |
| 7 | Referencia de carta / expediente | 2.1% |
| 8 | Administrado / solicitante | 65.5% |
| 9 | Fecha de inicio (según documento) | 59.3% |
| 10 | Fecha de salida / emisión | 100% |
| 11 | Días transcurridos | 41.0% |
| 12 | Tiempo calendario | 41.0% |
| 13 | Criterio / evidencia de fecha de inicio | 100% |
| 14 | Criterio / evidencia de fecha de salida | 100% |
| 15 | Estado / observaciones | 100% |
| 16 | Tipo de archivo | 100% (PDF 2182, Word 301) |
| 17 | Archivo de origen | 100% |

### Tipos de trámite (16, coinciden con las 16 carpetas/ZIP)

| Registros | Tipo de trámite |
|---:|---|
| 704 | CERTIFICADO DE NUMERACIÓN MUNICIPAL |
| 483 | CERTIFICADO DE PARÁMETROS URBANÍSTICOS Y EDIFICATORIOS |
| 461 | VISADO DE PLANOS DE UBICACIÓN, PERIMÉTRICO Y MEMORIA DESCRIPTIVA DE PREDIO URBANO |
| 345 | CERTIFICADO NEGATIVO DE CATASTRO |
| 160 | CONSTANCIA DE POSESIÓN PARA SERVICIOS BÁSICOS |
| 87 | CERTIFICADO DE ZONIFICACIÓN Y VÍAS |
| 76 | LICENCIAS DE EDIFICACIÓN |
| 62 | AUTORIZACIÓN PARA APERTURA DE ZANJA EN PREDIOS URBANOS |
| 36 | CERTIFICADO DE COMPATIBILIDAD DE USO |
| 31 | AUTORIZACIÓN PARA OCUPACIÓN DE VÍA PÚBLICA CON MATERIALES DE CONSTRUCCIÓN |
| 15 | CERTIFICADO DE HABITABILIDAD |
| 8 | CERTIFICADO DE JURISDICCIÓN |
| 6 | AUTORIZACIÓN PARA TELECOMUNICACIONES |
| 5 | CONSTANCIA DE ALINEAMIENTO |
| 2 | CERTIFICADO DE CONFORMIDAD DE OBRA Y DECLARATORIA DE EDIFICACIÓN |
| 2 | CONSTANCIA DE PREDIO RÚSTICO |

### Hallazgos que afectan el modelo de datos

1. **El número de certificado NO es único global.** Hay 441 números repetidos
   (`042-2026` aparece 4 veces) porque se repiten entre tipos de trámite.
   → Restricción `UNIQUE (tipo_tramite_id, numero_certificado, anio)`.
2. **Formatos de número:** `NNN-AAAA` (1495), sin año (954), `N-AAAA` (34).
   → Guardar `numero` como texto y `anio` como entero separado.
3. **Fechas:** emisión siempre presente (2017-08-24 a 2026-12-31);
   inicio solo en 1473 registros y con conflictos en 443 casos.
   → El histórico se importa como `origen = historico`, sin forzar SLA.
4. **Días transcurridos** solo calculable en 41% de los registros.
   → No usar ese campo como fuente de verdad; recalcular con el motor de SLA.
5. **Observaciones de calidad** en 1884 filas (faltan fechas, identificadores dudosos,
   duplicados textuales). → Importar a una columna `observaciones_importacion`,
   no bloquear la carga.
6. **No hay columna de oficina**, solo tipo de trámite. Falta confirmar si las 16
   oficinas son las mismas 16 áreas de trámite o una dimensión distinta.
7. **Administrado** presente en 65.5%; es texto libre, no un DNI normalizado.
   → Tabla `ciudadanos` separada, con importación tolerante a nulos.

### Entidades propuestas

- `oficinas` (16, configurables)
- `usuarios` + roles: admin, ventanilla, oficina, direccion
- `ciudadanos` (tipo_doc, numero_doc, nombres, email, telefono)
- `tipos_tramite` (16, con SLA por defecto)
- `documentos` (código interno, número, año, tipo, administrado, estado, oficina actual)
- `movimientos` (origen, destino, envío, recepción, salida, SLA, fecha límite)
- `plazos` (tipo_tramite × oficina → horas)
- `feriados` y `configuracion` (horario laboral, umbrales)
- `eventos` (auditoría inmutable)
- `notificaciones` (canal, destinatario, estado, error, proveedor)
- `adjuntos` (fase posterior, para expediente digital)

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Postgres, no SQLite | Mismo motor en desarrollo y producción; soporta concurrencia |
| `numero` + `anio` separados | El formato del Excel es inconsistente |
| Unicidad por tipo + número + año | Evita falsos duplicados entre áreas |
| Importación tolerante | El Excel tiene 1884 filas con observaciones de calidad |
| Histórico no genera SLA | No hay fechas de inicio confiables en el 41% |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| `pandas` ausente | Se usó `openpyxl` 3.1.5 |
| Encoding de nombres de carpeta (`╠ü`) | Normalizar a UTF-8 al importar |

## Open Questions

1. ¿Las 16 oficinas son las 16 áreas de trámite del Excel o son distintas?
2. Plazos (horas) por tipo de trámite/oficina.
3. Horario de atención y feriados.
4. ¿Una cuenta por oficina o por funcionario?
5. ¿Portal público aprobado o solo notificación?

## Resources

- Excel fuente: `docs/fuentes/Consolidado_certificados_municipales_todos_los_ZIP.xlsx`
- Plan: `.planning/2026-11-03-docmun-mvp/task_plan.md`
