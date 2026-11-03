# Esquema de base de datos — DocMun

Motor: PostgreSQL 16. Migración inicial: `alembic/versions/8f52ff33f364_esquema_inicial.py`.

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
                       └────N notificaciones              │
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
                            ▲                    │
                            └────derivar─────────┤
                                                 ├──observar──> observado
                                                 └──finalizar─> finalizado ──> archivado
```

El **semáforo** (`en_plazo`, `por_vencer`, `atrasado`, `muy_atrasado`) no es una
columna: se calcula desde `fecha_limite` y el reloj, con umbrales configurables
(80%, 100%, 150%).

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

Los 2483 registros se importan con `origen = historico` y `estado = archivado`,
sin generar movimientos ni SLA, porque el 41% no tiene fechas confiables.
