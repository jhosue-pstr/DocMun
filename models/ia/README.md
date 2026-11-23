# Modelos de IA (no versionados)

Esta carpeta es el punto de montaje de los modelos LayoutLMv3 que usa el contenedor
`ia` (ver `Dockerfile.ia` y `docs/ia.md`). Los pesos **no** se guardan en git ni se
cuecen en la imagen de la app.

```
models/ia/
├── clasificacion/modelo.pt          # 16 clases genéricas (RVL-CDIP); solo diagnóstico
├── kie/modelo.pt                    # activo: HEADER / QUESTION / ANSWER
└── versiones/
    ├── kie-generico-anterior.pt     # respaldo del KIE genérico
    └── kie-municipal-2026-11.pt     # KIE entrenado con certificados municipales
```

Sin estos archivos el worker sigue funcionando en modo solo-OCR: Tesseract extrae el
texto y `app/services/ia/extraccion.py` sugiere el tipo y los campos.

Para instalar los modelos:

1. Copia `clasificacion/modelo.pt` y `kie/modelo.pt` desde el paquete de inferencia
   (`paquete_prueba/modelos/`).
2. Verifica con
   `docker compose exec ia python -c "from app.services.ia import motor; print(motor.estado_motor())"`.
3. Para usar otro modelo, déjalo en `versiones/` y apunta la variable
   `IA_MODELO_KIE=versiones/mi-modelo.pt` (o cópialo a `kie/modelo.pt`). Reinicia
   `ia` después del cambio.
4. Para usar un clasificador municipal reentrenado, reemplaza
   `clasificacion/modelo.pt` por el que genera `scripts/entrenar_ia.py` y reinicia
   el servicio (`docker compose restart ia`).

> El KIE municipal actual se entrenó con 32 documentos y etiquetas genéricas
> `HEADER/QUESTION/ANSWER`; se guarda como diagnóstico y no prellena el formulario.
> El prellenado sale de las reglas de `extraccion.py`.

Licencias: el checkpoint base `microsoft/layoutlmv3-base` es CC BY-NC-SA 4.0.
Revísala antes de un despliegue comercial.
