"""Paso 2: interfaz de línea de comandos del caso base estricto."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cargar_componentes():
    """Admite tanto ``-m`` como la ejecución directa de este archivo."""
    if __package__:
        from .p01_constructor import (
            guardar_resultado,
            resolver_caso_base_estricto,
        )
        from ..p02_instancia import cargar_instancia
    else:
        raiz_paquete = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(raiz_paquete))
        from microplanning.caso_base.p01_constructor import (
            guardar_resultado,
            resolver_caso_base_estricto,
        )
        from microplanning.p02_instancia import cargar_instancia

    return cargar_instancia, guardar_resultado, resolver_caso_base_estricto


def crear_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        description="Caso base determinista con capacidad estricta."
    )
    analizador.add_argument(
        "--instance",
        choices=("ac01", "ac02", "ac03"),
        required=True,
        help="Instancia que se resolverá.",
    )
    analizador.add_argument(
        "--out",
        type=Path,
        default=Path("resultados_caso_base"),
        help="Directorio de salida (predeterminado: resultados_caso_base).",
    )
    return analizador


def main(argv: list[str] | None = None) -> None:
    opciones = crear_analizador().parse_args(argv)
    cargar_instancia, guardar_resultado, resolver = _cargar_componentes()
    instancia = cargar_instancia(opciones.instance)
    resultado = resolver(instancia)
    reporte = guardar_resultado(
        instancia, resultado, opciones.out, "caso_base_estricto"
    )
    print(
        f"{opciones.instance.upper()} | "
        f"costo={reporte['costo_total']:,.0f} | "
        f"TAT={reporte['TAT_h']:.2f} h | "
        f"violaciones técnicas={reporte['num_violaciones_tecnicas']} | "
        "violaciones capacidad="
        f"{reporte['num_violaciones_capacidad_estricta']}"
    )


if __name__ == "__main__":
    main()
