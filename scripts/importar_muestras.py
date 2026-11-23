"""Compatibilidad: importa una muestra del histórico (20 filas por defecto).

El importador completo vive en `scripts.importar_historico`. Este módulo se
mantiene para no romper los comandos y notas anteriores.

Uso:
    python -m scripts.importar_muestras --limite 20
    python -m scripts.importar_muestras --limite 20 --dry-run
"""

from scripts.importar_historico import main

if __name__ == "__main__":
    main(limite_por_defecto=20)
