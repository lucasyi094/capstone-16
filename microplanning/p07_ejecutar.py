# Paso 7: coordinación y ejecución independiente de la metodología heur_eventos.

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .p02_instancia import DIRECTORIO_DATOS_PREDETERMINADO, Instancia, cargar_instancia
from .p05_evaluacion import evaluar, exportar, verificar_factibilidad
from .p06_SA import SolucionadorHeurEventos


# --- Configuración y resultado de una corrida ---
@dataclass(frozen=True)
class ConfiguracionHeuristica:
    semilla: int = 1
    tiempo_s: float = 1800.0
    mostrar_progreso: bool = True
    intervalo_progreso_s: float = 60.0


@dataclass(frozen=True)
class ResultadoHeuristica:
    inicios: np.ndarray
    orientaciones: np.ndarray
    tiempo_s: float
    iteraciones: int
    tat_objetivo_h: float


# --- Ejecución del SSGS, recocido y búsqueda local ---
def resolver(instancia: Instancia, configuracion: ConfiguracionHeuristica) -> ResultadoHeuristica:
    _calentar_numba(instancia)
    solucionador = SolucionadorHeurEventos(instancia, semilla=configuracion.semilla)
    inicio = time.time()
    limite = inicio + configuracion.tiempo_s
    _, orientaciones, _, inicios = solucionador.optimizar_con_recocido(
        configuracion.tiempo_s,
        mostrar_progreso=configuracion.mostrar_progreso,
        intervalo_log=configuracion.intervalo_progreso_s,
        optimizar_tat_referencia=True,
        limite=limite,
    )
    inicios = solucionador.mejorar_localmente(
        inicios, orientaciones, mostrar_progreso=False, limite=limite,
    )
    return ResultadoHeuristica(
        inicios=inicios,
        orientaciones=orientaciones,
        tiempo_s=time.time() - inicio,
        iteraciones=solucionador.sa_info["iteraciones"],
        tat_objetivo_h=solucionador.sa_info["T_ref_h"],
    )


# Compila los kernels de Numba antes de comenzar a medir el tiempo de búsqueda.
def _calentar_numba(instancia: Instancia) -> None:
    calentamiento = SolucionadorHeurEventos(instancia, semilla=0)
    calentamiento.optimizar_con_recocido(
        0.01, mostrar_progreso=False, optimizar_tat_referencia=True,
    )


# --- Configuración de la interfaz de línea de comandos ---
# Declara las instancias disponibles, el presupuesto de búsqueda y las rutas de
# entrada/salida. Esta función solo construye el parser; no inicia una corrida.
def crear_parser():
    analizador = argparse.ArgumentParser(
        prog="python -m microplanning",
        description="Ejecuta heur_eventos de forma independiente.",
    )
    objetivos = analizador.add_mutually_exclusive_group()
    objetivos.add_argument("--instance", choices=("ac01", "ac02", "ac03"))
    objetivos.add_argument("--instances", nargs="+", choices=("ac01", "ac02", "ac03"))
    analizador.add_argument("--seed", type=int, default=1)
    analizador.add_argument(
        "--time", "--sa-time", dest="time", type=float, default=1800,
        help="presupuesto total por instancia en segundos (por defecto: 1800)",
    )
    analizador.add_argument("--data", default=DIRECTORIO_DATOS_PREDETERMINADO, help="carpeta de CSV; por defecto usa los datos incluidos")
    analizador.add_argument("--out", default="resultados_heur_eventos")
    analizador.add_argument(
        "--progress-every", type=float, default=60,
        help="segundos entre mensajes de avance; 0 los desactiva",
    )
    return analizador


# --- Validación previa ---
# Rechaza argumentos inválidos antes de cargar datos o crear archivos parciales.
def _validar_argumentos(analizador, argumentos):
    if argumentos.time <= 0:
        analizador.error("--time debe ser mayor que cero")
    if argumentos.progress_every < 0:
        analizador.error("--progress-every no puede ser negativo")
    if not os.path.isdir(argumentos.data):
        analizador.error(f"no existe la carpeta de datos: {argumentos.data}")


# --- Ejecución de una instancia ---
# Carga datos, ejecuta la heurística, comprueba factibilidad y exporta todos los
# artefactos asociados a una instancia y semilla.
def _ejecutar_instancia(nombre_instancia, argumentos):
    # Cada instancia se carga desde cero para que una ejecución no reutilice el
    # estado, la capacidad ni la semilla aleatoria de la instancia anterior.
    instancia = cargar_instancia(nombre_instancia, argumentos.data)
    print(
        f"\n=== {nombre_instancia.upper()} | heur_eventos | {instancia.n_hitos} hitos | "
        f"{instancia.estadisticas['effective_disjunctions']} disyunciones efectivas"
    )

    # La configuración de línea de comandos se traduce a la interfaz del solucionador.
    resultado = resolver(
        instancia,
        ConfiguracionHeuristica(
            semilla=argumentos.seed,
            tiempo_s=argumentos.time,
            mostrar_progreso=argumentos.progress_every > 0,
            intervalo_progreso_s=argumentos.progress_every,
        ),
    )
    # El vector contiene tiempos para hitos reales y para nodos sintéticos de grupo.
    inicios = resultado.inicios

    # La evaluación y la revisión de restricciones se hacen después de buscar la
    # solución. Así se comprueba el calendario final con una ruta independiente.
    metricas = evaluar(instancia, inicios)
    violaciones = verificar_factibilidad(instancia, inicios)
    prefijo = os.path.join(
        argumentos.out,
        f"{nombre_instancia}_s{argumentos.seed}_heur_eventos",
    )

    # Se guarda tanto el vector exacto de inicios como salidas legibles. El NPY
    # permite reanudar análisis sin reconstruir el calendario desde los CSV.
    np.save(prefijo + ".npy", inicios)
    exportar(instancia, inicios, prefijo)
    # El JSON combina metadatos de búsqueda con las métricas de la solución final.
    informe = {
        "instancia": nombre_instancia,
        "metodo": "heur_eventos",
        "semilla": argumentos.seed,
        "tiempo_s": round(resultado.tiempo_s, 3),
        "iteraciones_sa": resultado.iteraciones,
        "T_ref_h": resultado.tat_objetivo_h,
        "num_violaciones": len(violaciones),
        "violaciones": violaciones,
        **metricas,
    }
    with open(prefijo + "_resumen.json", "w", encoding="utf-8") as file:
        json.dump(informe, file, ensure_ascii=False, indent=2, default=float)

    print(
        f"Listo: costo={metricas['costo_total']:,.0f} | TAT={metricas['TAT_h']:.1f} h | "
        f"violaciones={len(violaciones)} | tiempo={resultado.tiempo_s:.1f} s"
    )
    return informe


# --- Flujo principal ---
# Puede recibir argv desde pruebas o usar directamente los argumentos de la CLI.
def main(argv=None):
    analizador = crear_parser()
    argumentos = analizador.parse_args(argv)
    _validar_argumentos(analizador, argumentos)
    os.makedirs(argumentos.out, exist_ok=True)

    # Una selección individual tiene prioridad; sin selección se ejecutan las tres.
    nombres_instancias = (
        [argumentos.instance]
        if argumentos.instance
        else (argumentos.instances or ["ac01", "ac02", "ac03"])
    )
    # Las instancias se ejecutan secuencialmente para respetar el presupuesto de cada una.
    informes = [
        _ejecutar_instancia(nombre, argumentos)
        for nombre in nombres_instancias
    ]

    # El CSV agregado excluye la lista de textos de violación para mantener una
    # fila tabular por instancia. El detalle completo permanece en cada JSON.
    filas = []
    for informe in informes:
        fila = {clave: valor for clave, valor in informe.items() if clave != "violaciones"}
        filas.append(fila)
    ruta_resumen = os.path.join(argumentos.out, "resumen.csv")
    pd.DataFrame(filas).to_csv(ruta_resumen, index=False)
    print(f"\nResumen: {ruta_resumen}")

    if any(informe["num_violaciones"] for informe in informes):
        raise SystemExit("Una o más soluciones contienen violaciones")


if __name__ == "__main__":
    main()
