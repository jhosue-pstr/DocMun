# Reproducibilidad

Esta guía describe cómo reconstruir el entorno de DocMun y cómo registrar las
versiones de sus dependencias y datos sin exponer información personal.

## Requisitos

- Docker y Docker Compose, o Python 3.12 y PostgreSQL 16 para ejecutar sin Docker.
- El archivo `.env`, creado a partir de `.env.example`.
- Para importar el histórico, el Excel fuente autorizado en `docs/fuentes/`.

## Dependencias

`requirements.txt` contiene las dependencias de la aplicación con versiones
fijadas. Para instalar también las herramientas de desarrollo y pruebas:

```bash
pip install -r requirements-dev.txt
```

`requirements-ia.txt` y `requirements-entrenamiento.txt` son opcionales: se usan
para inferencia y entrenamiento, respectivamente. Algunas de sus dependencias
usan rangos de versión; para repetir exactamente esos entornos, registra además
la versión de Python, el sistema operativo, el hardware y las versiones
efectivamente instaladas. No instales esas dependencias para ejecutar solo la
aplicación web.

Para reconstruir el entorno principal con Docker:

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec app alembic upgrade head
docker compose exec app python -m scripts.seed
```

No compartas ni versionés `.env`. Usa credenciales seguras fuera del entorno
local de desarrollo.

## Base de datos y migraciones

Las migraciones de Alembic en `alembic/versions/` son la fuente versionada de los
cambios de esquema. Al preparar una instalación nueva, aplica las migraciones
con `alembic upgrade head`; no dependas de una base de datos local preexistente.
El seed (`scripts/seed.py`) carga los catálogos y la configuración inicial, y es
idempotente.

Para ejecutar las pruebas:

```bash
docker compose exec app pytest -q
```

Las pruebas usan SQLite en memoria; las diferencias relevantes de PostgreSQL,
como constraints y migraciones, deben validarse también con PostgreSQL.

## Versionado de datos

El Excel histórico puede contener datos personales. Por ese motivo, el archivo
fuente de `docs/fuentes/` está excluido de Git, igual que `.env`, la base de datos
local y los archivos subidos a `storage/`. No quites esas exclusiones ni subas
copias del Excel, exportaciones de la base o archivos con DNI, RUC, teléfonos,
correos, nombres o expedientes.

Para poder identificar una fuente sin publicarla:

1. Conserva el original en un almacenamiento privado con acceso controlado y
   evita sobrescribirlo.
2. Registra fuera del repositorio quién autorizó su uso y la fecha/versión de
   recepción, conforme a las reglas de protección de datos de la Municipalidad.
3. En el repositorio puedes documentar solo metadatos no personales, como el
   nombre lógico de la fuente, fecha de corte, número de filas y suma SHA-256.
   No incluyas filas de ejemplo ni datos identificables.
4. Antes de importar, ejecuta el modo de prueba y revisa el resumen:

   ```bash
   docker compose exec app python -m scripts.importar_historico --dry-run
   ```

5. Importa únicamente después de validar la fuente y el resumen:

   ```bash
   docker compose exec app python -m scripts.importar_historico
   ```

La suma SHA-256 permite comprobar que se recibió el mismo archivo, pero no
reemplaza el almacenamiento seguro ni constituye por sí sola una copia de
respaldo. No versionamos actualmente datasets con DVC o Git LFS; si se adopta una
herramienta de ese tipo, debe configurarse en almacenamiento privado y con
controles de acceso adecuados para datos personales.

Los modelos entrenados y datasets de IA tampoco deben añadirse al repositorio:
sus ubicaciones están excluidas en `.gitignore`. Versiona el código de
preprocesamiento y entrenamiento, y conserva los artefactos en almacenamiento
privado con una identificación de versión no sensible.
