# Análisis de documentos con IA — DocMun

Módulo local de OCR + LayoutLMv3 que **sugiere** el tipo de trámite y algunos datos
del oficio para que ventanilla los confirme. Nunca escribe en el expediente por su
cuenta: el registro, la circulación y el semáforo siguen siendo los mismos.

## Por qué existe

Ventanilla recibe un oficio en papel y tiene que teclearlo. El módulo lee el escaneo,
propone `tipo_tramite`, número, año, asunto, referencia, administrado, DNI y RUC, y
ventanilla aplica solo lo que corresponde. El objetivo real es reunir etiquetas
confirmadas para entrenar un modelo municipal; hoy el modelo incluido es genérico.

## Arquitectura

```
app web (sin torch)                     contenedor `ia` (Dockerfile.ia)
────────────────────                    ──────────────────────────────
POST /documentos/nuevo  ──┐
POST /documentos/…/analizar ─┐          python -m scripts.worker_ia
  INSERT documento_analisis │  ───────▶  SELECT … FOR UPDATE SKIP LOCKED
  estado = pendiente        │             OCR (Tesseract) + LayoutLMv3
                            │             INSERT resultado (listo | error)
GET /documentos/… (HTMX) ◀──┘             modelos montados en /app/models/ia (ro)
```

- La app nunca importa `torch`: solo `app.services.ia.cola` y `extraccion`, que son
  Python puro y SQLAlchemy.
- El worker es un proceso aparte (como `scheduler`) y comparte la misma base de
  datos y `storage/`.
- Con `IA_HABILITADA=false` no se encola nada y la app funciona igual. Es el valor
  por defecto en `app/core/config.py`; `docker compose` lo activa por defecto.
- La inferencia es **local**: no se envía ningún documento a un servicio externo.

## Flujo de ventanilla

1. Ventanilla elige el archivo en `/documentos/nuevo`: el análisis arranca solo
   (también hay un botón *Analizar con IA* por si se quiere repetir). El archivo se
   sube a `/documentos/analizar-borrador` y queda en
   `storage/borradores/{sha256}.{ext}` mientras se procesa.
2. La página consulta `/documentos/analizar-borrador/{id}` cada 2 s (HTMX) hasta
   que el estado es `listo` o `error`.
3. Al quedar listo, el navegador **prellena solo los campos vacíos** con las
   sugerencias y los resalta; nunca pisa lo que ventanilla ya escribió. Hay botones
   para *Aplicar todas* o *Quitar sugerencias*, y cada campo muestra su confianza y
   evidencia. Nada se guarda hasta pulsar *Registrar*.
4. Al registrar, el formulario envía `analisis_id`. El servidor vuelve a validar
   el hash: si el archivo cambió, descarta el borrador y usa el subido. Si coincide,
   reutiliza el binario del borrador, lo mueve al expediente y guarda
   `tipo_tramite_confirmado_id` como etiqueta para el futuro dataset. Si el OCR aún
   estaba pendiente, se enlaza igual y el worker lo termina ya dentro del expediente.
5. Si el archivo se adjunta después (detalle del documento), se encola el análisis
   y el resultado aparece bajo el archivo con el texto OCR plegado.

## Qué hace el motor

`app/services/ia/motor.py`

1. Convierte el PDF (primeras `IA_MAX_PAGINAS`) o la imagen a RGB con PyMuPDF/Pillow.
2. OCR con Tesseract (`IA_OCR_IDIOMAS`, por defecto `spa+eng`, `--psm 3`), agrupando
   palabras por línea y normalizando cajas a 0–1000.
3. Si existen los modelos, clasifica la primera página (LayoutLMv3) y ejecuta KIE
   (HEADER/QUESTION/ANSWER). Ambas salidas son secundarias.
4. Si falta un modelo, devuelve igualmente el texto OCR para que las reglas sugieran.

`app/services/ia/extraccion.py` (Python puro, testeable sin torch):

- **Tipo**: reglas de palabras clave en español que mapean a `TR-01`…`TR-16`.
  Es la señal principal; el clasificador genérico NO decide el tipo.
- **Campos**: regex tolerantes al OCR (por ejemplo `CERTIFICADO | N°629-2026`), año
  tomado del número o de la línea de fecha (nunca del aniversario `1926 - 2026`),
  asunto, referencia, administrado (`Doña:`, `A petición de:`, `Yo, …`) y DNI/RUC/
  expediente, cada uno con evidencia y confianza. Si el documento no trae una línea
  `ASUNTO`, el asunto se respalda con el nombre del tipo de trámite detectado.
- **Versión de reglas**: `extraccion.VERSION_REGLAS` se guarda en `campos.reglas`. La
  caché por hash solo reutiliza resultados de esa misma versión: al ajustar las
  reglas, el siguiente análisis se reprocesa solo (no queda tapado por la caché).

El clasificador incluido es RVL-CDIP (16 clases en inglés). En una carta municipal
real predijo `resume` con 99.96 %: por eso se guarda como diagnóstico
(`tipo_clase`) y nunca se usa para asignar el trámite.

## Modelos

```
models/ia/
├── clasificacion/modelo.pt          # activo: secuencia, 16 clases RVL-CDIP
├── kie/modelo.pt                    # activo: token classification (HEADER/QUESTION/ANSWER)
└── versiones/
    ├── kie-generico-anterior.pt     # respaldo del KIE genérico
    └── kie-municipal-2026-11.pt     # KIE municipal entrenado por el usuario
```

- El KIE municipal se entrenó con 32 certificados y etiquetas genéricas
  `HEADER/QUESTION/ANSWER`. Es útil como diagnóstico y sus pares se guardan en
  `documento_analisis.campos.kie`, pero **no** prellenan el formulario: el prellenado
  sale de las reglas de `extraccion.py`.
- Para cambiar de modelo sin editar archivos, usa
  `IA_MODELO_KIE=versiones/kie-municipal-2026-11.pt` (o el que corresponda) y reinicia
  `ia`. Para fijarlo como activo:
  `cp models/ia/versiones/kie-municipal-2026-11.pt models/ia/kie/modelo.pt`.
- Formato: paquete `torch.save` con `config`, `state_dict`, `etiquetas`,
  `tokenizer_json`, `image_processor_config`, `max_length` y `modelo_base`.
  `motor.py` lo carga con `weights_only=True` y cae a `weights_only=False` solo si
  el archivo es antiguo (queda un aviso en el log).
- `models/ia/` está en `.gitignore` y `.dockerignore`: no se versiona ni se cuece en
  la imagen de la app; se monta de solo lectura en el contenedor `ia`.
- Los pesos base son de Hugging Face (`microsoft/layoutlmv3-base`) con licencia
  **CC BY-NC-SA 4.0** (uso no comercial). Antes de un despliegue comercial hay que
  revisar licencias o reentrenar desde un checkpoint compatible.

## Puesta en marcha

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec app alembic upgrade head
docker compose exec app python -m scripts.seed

docker compose logs -f ia          # ver el worker
docker compose exec ia python -m scripts.worker_ia --una-vez
```

Comprobaciones y utilidades:

```bash
# Diagnóstico: tesseract, modelos, dispositivo
docker compose exec ia python -c "from app.services.ia import motor; print(motor.estado_motor())"

# Probar un archivo sin base de datos (no expone datos personales en logs)
docker compose exec ia python -m scripts.analizar_documento --archivo storage/documentos/DOC-2026-000001/xxx.pdf
docker compose exec ia python -m scripts.analizar_documento --archivo oficio.png --solo ocr --json
```

Tiempos medidos en CPU (2 hilos, sin GPU): PDF de 3 páginas ≈ 27 s y ~2 GB de RAM.
El contenedor `ia` limita a `cpus: 2.0` y `mem_limit: 4g` para no ahogar a la app.

## Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `IA_HABILITADA` | `false` (compose: `true`) | enciende el encolado y el worker |
| `IA_MODELOS_DIR` | `models/ia` | carpeta de modelos (en Docker `/app/models/ia`) |
| `IA_MODELO_CLASIFICACION` | `clasificacion/modelo.pt` | relativo a `IA_MODELOS_DIR` |
| `IA_MODELO_KIE` | `kie/modelo.pt` | relativo a `IA_MODELOS_DIR` |
| `IA_DISPOSITIVO` | `cpu` | `cuda:0` si hay GPU disponible |
| `IA_HILOS` | `2` | hilos de torch |
| `IA_MAX_PAGINAS` | `3` | páginas que se convierten y se OCR-ean |
| `IA_DPI` | `200` | resolución de rasterizado |
| `IA_OCR_IDIOMAS` | `spa+eng` | idiomas de Tesseract (cae a `eng` si falta `spa`) |
| `IA_OCR_PSM` | `3` | modo de segmentación de Tesseract |
| `IA_UMBRAL_SUGERENCIA` | `0.65` | reservado para filtrar sugerencias |
| `IA_MAX_INTENTOS` | `3` | reintentos antes de marcar `error` |
| `IA_INTERVALO_SEGUNDOS` | `5` | espera del worker cuando no hay trabajo |
| `IA_BORRADOR_HORAS` | `24` | antigüedad para borrar borradores huérfanos |

## Operación

- La cola es la tabla `documento_analisis`. En Postgres el worker usa
  `FOR UPDATE SKIP LOCKED`; en SQLite (tests) no.
- Idempotencia: reanalizar un archivo reusa la fila existente y reinicia intentos.
- Caché por hash: si el mismo binario ya se analizó, el resultado se copia sin
  volver a ejecutar OCR.
- Limpieza: cada pasada borra borradores sin documento con más de
  `IA_BORRADOR_HORAS` horas y marca error tras `IA_MAX_INTENTOS`.
- El evento `analizado` guarda ids, motor, versión, páginas, tipo sugerido y
  confianza. No guarda texto, DNI, RUC, teléfono ni correo.
- Un fallo del motor no tumba el registro: el archivo se guarda y el análisis
  queda en `error` con el mensaje visible en el detalle.

## Dataset y reentrenamiento

```bash
# 1. Exportar lo analizado (dataset/ está en .gitignore: contiene PII)
docker compose exec app python -m scripts.exportar_dataset_ia --salida dataset/ia
docker compose exec app python -m scripts.exportar_dataset_ia --solo-confirmados

# 2. (Otra máquina) validar el manifiesto sin torch
python -m scripts.entrenar_ia --dataset dataset/ia --dry-run

# 3. Entrenar con GPU
pip install -r requirements-entrenamiento.txt
python -m scripts.entrenar_ia --dataset dataset/ia --salida models/ia/v2 \
    --epocas 5 --batch 4 --balancear --fp16

# 4. Publicar el modelo
cp models/ia/v2/modelo.pt models/ia/clasificacion/modelo.pt
docker compose restart ia
```

- El manifiesto (`manifest.jsonl`) lista ruta, `tipo` y hash. El script excluye
  duplicados por hash y avisa de clases con menos de `--minimo-por-tipo` ejemplos
  (50 por defecto): con menos, el modelo no generaliza.
- `scripts/entrenar_ia.py` divide train/val/test por clase, cachea el OCR en
  `cache_ocr/`, permite balanceo por pesos y exporta el mismo formato `.pt` más un
  `informe.json` con métricas (accuracy y F1 macro).
- No entrenes en el servidor de DocMun: usa una máquina con GPU. Este equipo no
  tiene GPU y el entrenamiento no se validó aquí.
- Las etiquetas de entrenamiento son `tipo_tramite_confirmado_id`: si nadie confirma
  el tipo al registrar, ese ejemplo no aporta a la clase real.

## Privacidad y licencias

- El texto OCR y los campos sugeridos son datos personales (Ley 29733). No se
  escriben en logs, ni en el QR, ni en `eventos`. La app solo muestra el OCR a
  usuarios con sesión.
- No se usa `Accept`/extensión como prueba de formato: se validan *magic bytes* y
  tamaño igual que en el resto de adjuntos.
- PyMuPDF es **AGPL-3.0**; Tesseract es Apache-2.0; LayoutLMv3 base es
  **CC BY-NC-SA 4.0**. Revisa estas licencias antes de distribuir el sistema.

## Pruebas

```bash
docker compose exec app pytest -q tests/test_ia_extraccion.py
docker compose exec app pytest -q tests/test_ia_cola.py
docker compose exec app pytest -q tests/test_ia_routes.py
```

No necesitan torch ni Tesseract: el motor se sustituye con un resultado simulado.
Para validar la inferencia real usa `scripts.analizar_documento` dentro del
contenedor `ia`.
