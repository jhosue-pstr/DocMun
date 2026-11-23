# Esquema de base de datos — DocMun

Motor: PostgreSQL 16. Migración inicial: `alembic/versions/8f52ff33f364_esquema_inicial.py`.
Archivos digitalizados: `alembic/versions/cf01505205fe_tabla_documento_archivos.py`.

## Diagrama lógico

```
oficinas 1─────N usuarios
    │ 1
    ├──────N plazos N──────1 tipos_tramite 1────N documentos
    │                              │ 1                    │
    └──────N tipos_responsable     └─────────────────────┤
                                                          │
ciudadanos 1────N documentos ────N movimientos            │
                       │ 1                                 │
                       ├────N eventos                     │
                       ├────N notificaciones              │
                       └────N documento_archivos          │
                                                          │
feriados (independiente)      configuracion (independiente)
```

## Tablas

### `oficinas` — las 16 áreas municipales
| Columna | Tipo | Notas |
|---|---|---|
| id | serial | PK |
| codigo | varchar(20) | único (`OF-01`…`OF-16`) |
| nombre | varchar(150) | nombre del área |
| siglas | varchar(30) | iniciales para UI |
| ubicacion, email, telefono | varchar | opcionales |
| activa | boolean | baja lógica |

Las 16 oficinas provienen de las 16 carpetas del Excel consolidado.

### `tipos_tramite` — los 16 tipos de certificado
| Columna | Tipo | Notas |
|---|---|---|
| id | serial | PK |
| codigo | varchar(30) | único (`TR-01`…`TR-16`) |
| nombre | varchar(200) | nombre del certificado |
| area | varchar(150) | agrupación |
| oficina_responsable_id | FK oficinas | área que lo emite |
| sla_horas | integer | plazo por defecto |
| activo | boolean | |

### `plazos` — SLA por tipo y oficina
| Columna | Tipo | Notas |
|---|---|---|
| tipo_tramite_id | FK | |
| oficina_id | FK | |
| horas | integer | plazo máximo de permanencia |

Único por `(tipo_tramite_id, oficina_id)`. Permite que el mismo trámite tenga
distinto plazo en cada oficina.

### `usuarios`
| Columna | Tipo | Notas |
|---|---|---|
| id | serial | PK |
| nombre | varchar(150) | |
| email | varchar(150) | único |
| password_hash | varchar(255) | Argon2 |
| rol | varchar(30) | `admin`, `ventanilla`, `oficina`, `direccion` |
| oficina_id | FK oficinas | nulo para admin/dirección |
| activo | boolean | |
| ultimo_acceso | timestamptz | |

### `ciudadanos`
| Columna | Tipo | Notas |
|---|---|---|
| tipo_documento | varchar(30) | `dni`, `ruc`, `ce`, `pasaporte` |
| numero_documento | varchar(20) | |
| nombres | varchar(200) | |
| email, telefono, direccion | varchar | opcionales |

Único por `(tipo_documento, numero_documento)`.

### `documentos`
| Columna | Tipo | Notas |
|---|---|---|
| id | serial | PK |
| codigo | varchar(30) | único, `DOC-2026-000001` |
| tipo_tramite_id | FK | |
| ciudadano_id | FK | opcional |
| numero_certificado | varchar(50) | número del papel |
| anio | integer | año del certificado |
| fuente_numero | varchar(50) | texto del documento / nombre de archivo |
| rut, expediente_administrativo, ruc, referencia | varchar | del Excel |
| administrado | varchar(200) | solicitante tal como figura |
| asunto | varchar(300) | |
| prioridad | varchar(30) | `baja`, `normal`, `alta`, `urgente` |
| estado | varchar(30) | ver máquina de estados |
| origen | varchar(30) | `manual`, `historico`, `importado` |
| oficina_actual_id | FK oficinas | dónde está hoy |
| creado_por_id | FK usuarios | |
| fecha_registro | timestamptz | |
| fecha_finalizacion | timestamptz | |
| observaciones, observaciones_importacion | text | |
| archivo_origen | varchar(500) | ruta en el ZIP |

**Restricción clave:** `UNIQUE (tipo_tramite_id, numero_certificado, anio)`.
En el Excel hay 441 números repetidos entre tipos; no son duplicados reales.

### `movimientos` — cada derivación (inmutable)
| Columna | Tipo | Notas |
|---|---|---|
| documento_id | FK | |
| oficina_origen_id | FK | nulo en el primer movimiento |
| oficina_destino_id | FK | |
| usuario_envia_id, usuario_recibe_id | FK usuarios | |
| fecha_envio | timestamptz | |
| fecha_recepcion | timestamptz | arranca el reloj del SLA |
| fecha_salida | timestamptz | cierra el tramo |
| sla_horas | integer | copia del plazo aplicable |
| fecha_limite | timestamptz | calculada en horas hábiles |
| observacion | text | |

No se actualiza ni se borra: una corrección es otro movimiento.

### `eventos` — auditoría inmutable
`documento_id`, `usuario_id`, `tipo`, `detalle` (JSON), `ip`, `creado_en`.

Tipos: `creado`, `derivado`, `recibido`, `observado`, `subsanado`, `finalizado`,
`archivado`, `anulado`, `analizado` (además de los de notificación). Cada acción de
circulación registra un evento con usuario, IP y timestamp; el detalle guarda el
`movimiento_id` o el motivo/nota cuando aplica. El evento `analizado` solo guarda
ids, motor, versión, páginas, tipo sugerido y confianza: nunca texto OCR ni datos
personales.

### `documento_archivos` — PDFs e imágenes adjuntos
| Columna | Tipo | Notas |
|---|---|---|
| documento_id | FK documentos | un trámite puede tener varios archivos |
| nombre_original | varchar(255) | nombre del archivo subido |
| ruta | varchar(500) | ruta relativa a `storage/` |
| mime | varchar(100) | `application/pdf`, `image/jpeg`, etc. |
| tamano_bytes | integer | tamaño validado contra `MAX_UPLOAD_MB` |
| hash_sha256 | varchar(64) | deduplicación por documento |
| paginas | integer | opcional; lo llena el OCR |
| origen | varchar(20) | `escaneo`, `camara`, `upload` |
| texto_ocr | text | texto OCR del worker de IA (búsqueda y vista) |
| subido_por_id | FK usuarios | autor del adjunto |

Único por `(documento_id, hash_sha256)`. El contenido **no** se guarda en
PostgreSQL: vive en `storage/documentos/{codigo}/{uuid}.{ext}`. El tipo real se
valida por *magic bytes*, no solo por la extensión o el `Content-Type`. Las rutas
se resuelven dentro de `STORAGE_DIR` para evitar traversal.

`texto_ocr` y `paginas` los llena el worker de IA (`scripts/worker_ia.py`) y se
usan solo para buscar y mostrar; nunca reemplazan lo que registró ventanilla.

### `documento_analisis` — OCR y sugerencias de IA
Migración: `alembic/versions/d4c9a1b7e2f0_tabla_documento_analisis.py`.

| Columna | Tipo | Notas |
|---|---|---|
| documento_id | FK documentos (NULL) | NULL mientras es un borrador sin registrar |
| archivo_id | FK documento_archivos (NULL) | único (`uq_analisis_archivo`); NULL en borradores |
| hash_sha256 | varchar(64) | índice; permite reutilizar el análisis del mismo archivo |
| nombre_original, mime | varchar | copia del archivo analizado |
| ruta_borrador | varchar(500) | archivo temporal en `storage/borradores/` antes del registro |
| estado | varchar(20) | `pendiente`, `procesando`, `listo`, `error` |
| texto_ocr | text | texto de las primeras `IA_MAX_PAGINAS` páginas |
| paginas | integer | páginas procesadas |
| campos | JSON | sugerencias estructuradas (tipo, campos y clase genérica) |
| tipo_clase, tipo_confianza | varchar/float | clasificador genérico RVL-CDIP; **solo diagnóstico** |
| tipo_tramite_sugerido_id | FK tipos_tramite | sugerencia por reglas de español |
| tipo_tramite_confirmado_id | FK tipos_tramite | lo que confirmó ventanilla; alimenta el dataset |
| sugerencia_confianza | float | confianza de la sugerencia de tipo |
| motor, version_modelo | varchar | `tesseract`, `tesseract+layoutlmv3`, hash del modelo |
| duracion_ms | integer | tiempo de inferencia |
| intentos, error | integer/text | reintentos del worker y último error |

Nunca guarda el binario. El texto OCR y los campos son datos personales: no se
exponen en el QR, los eventos ni los logs. Un borrador no pertenece a ningún
expediente hasta que ventanilla registra el documento y lo confirma.



### `notificaciones`
`documento_id`, `canal` (`whatsapp`/`email`), `destinatario`, `plantilla`,
`estado` (`pendiente`/`enviado`/`fallido`), `intentos`, `proveedor_id`, `error`,
`enviado_en`.

### `feriados`
`fecha` (única), `descripcion`. Alimenta el cálculo de horas hábiles.

### `configuracion`
Clave/valor para horario laboral, umbrales de semáforo, nombre de la entidad.

## Máquina de estados

```
registrado ──derivar──> derivado ──recibir──> en_oficina
                            ▲                    │  ▲
                            └────derivar─────────┤  │
                                                 │  └──subsanar── observado
                                                 │                    ▲
                                                 │                    │
                                                 └──observar──────────┘
                                                 │
                                                 ├──finalizar──> finalizado ──archivar──> archivado
                                                 └──────────────anular──────────────────> anulado
```

- `derivar` y `finalizar` cierran el tramo abierto (`fecha_salida`); `recibir` fija
  `fecha_recepcion` y calcula `fecha_limite` en horas hábiles.
- `observar` y `subsanar` no crean movimiento: solo cambian el estado y generan evento.
- `anular` (admin/dirección) cierra el tramo abierto y deja el documento en `anulado`.
- Un documento `archivado` o `anulado` queda fuera de circulación.

El **semáforo** (`en_plazo`, `por_vencer`, `atrasado`, `muy_atrasado`) no es una
columna: se calcula desde `fecha_limite` y el reloj, con umbrales configurables
(80%, 100%, 150%). El job que lo evalúa queda para la fase de SLA.

## Circulación implementada

- `app/services/circulacion.py` concentra las transiciones y permisos; las rutas de
  `app/web/circulacion_routes.py` bloquean el documento con `SELECT ... FOR UPDATE`
  antes de operar (en SQLite el bloqueo se ignora).
- Permisos: `admin` todo; `ventanilla` deriva solo `registrado`; `oficina` opera solo
  su bandeja; `direccion` consulta y anula.
- El plazo de cada tramo sale de `plazos` (tipo + oficina) o, si falta, de
  `tipos_tramite.sla_horas`.
- `/bandejas` muestra por recibir, en oficina y registrados por derivar según el rol.

## Mapeo Excel → base de datos

| Columna del Excel | Destino |
|---|---|
| Área / tipo de trámite | `tipos_tramite.nombre` y `oficinas.nombre` |
| Número de certificado | `documentos.numero_certificado` |
| Fuente del número | `documentos.fuente_numero` |
| RUT | `documentos.rut` |
| Expediente administrativo | `documentos.expediente_administrativo` |
| RUC | `documentos.ruc` |
| Referencia de carta / expediente | `documentos.referencia` |
| Administrado / solicitante | `documentos.administrado` |
| Fecha de inicio | `documentos.fecha_registro` (solo si es confiable) |
| Fecha de salida / emisión | `documentos.fecha_finalizacion` |
| Días transcurridos | No se importa: se recalcula |
| Estado / observaciones | `documentos.observaciones_importacion` |
| Tipo de archivo | Se registra en la importación |
| Archivo de origen | `documentos.archivo_origen` |

El importador completo está en `scripts/importar_historico.py` y ya procesó las
2483 filas del Excel:

- 2371 claves únicas `(tipo_tramite_id, numero_certificado, anio)`.
- 112 filas repetidas dentro del archivo: se omiten y quedan listadas por índice en
  el reporte para revisión manual.
- 92 filas traían solo el correlativo; el año se infirió desde `archivo_origen`.
- `1466` filas usaron la fecha de emisión como fecha de registro porque no tenían
  inicio o este era posterior a la emisión.
- Todas quedan con `origen = historico` y `estado = archivado`, sin movimientos ni SLA.

El reporte se guarda en `storage/reportes/importacion-*.json` (ignorado por git) y no
incluye nombres, DNI, RUC, teléfono ni correo. La reejecución es idempotente: reporta
0 creados y todas las claves como duplicadas. Los documentos históricos no llevan
PDFs: los adjuntos se agregan manualmente desde el detalle.
