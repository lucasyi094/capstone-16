# Heurística de microplanning por eventos

Esta carpeta es una implementación independiente de `heur_eventos` para las
instancias AC-01, AC-02 y AC-03. Incluye el algoritmo, los datos de entrada, la
evaluación de factibilidad y el ejecutor. No contiene ni llama a
CP-SAT, LNS u otros métodos de optimización.

La metodología ejecutada es siempre:

1. SSGS consciente del costo con candidatos por eventos.
2. Recocido simulado sobre prioridades, disyunciones, holgura y `T_ref`.
3. Búsqueda local.
4. Verificación independiente de factibilidad y exportación de resultados.

## Requisitos e instalación

La versión comprobada es **Python 3.11**. No se necesita ningún archivo del
repositorio original, una base de datos ni un optimizador externo.

Después de copiar o descomprimir la entrega debe existir una carpeta llamada
`microplanning/`. Todos los comandos se ejecutan desde su directorio padre:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r microplanning/requirements.txt
```

En Windows, la activación equivalente es `.venv\Scripts\activate`.

## Ejecución

Una instancia durante 30 minutos:

```bash
python -m microplanning --instance ac03 --seed 5 --time 1800
```

Varias instancias, con 600 segundos para cada una:

```bash
python -m microplanning --instances ac01 ac02 ac03 --seed 1 --time 600
```

Sin `--instance` ni `--instances` se ejecutan las tres. Las opciones principales
se consultan con:

```bash
python -m microplanning --help
```

Cada ejecución genera por defecto `resultados_heur_eventos/` en el directorio
desde el que se ejecutó el comando. La carpeta contiene:

- la solución NumPy (`.npy`);
- el resumen y las métricas (`_resumen.json` y `resumen.csv`);
- los calendarios de hitos y grupos (`_hitos.csv` y `_grupos.csv`);
- la carga por especialidad y turno (`_carga.csv`);

`--data` permite reemplazar los CSV incluidos en `microplanning/datos/` y
`--out` permite escoger otra carpeta de salida. `--time` se aplica por separado
a cada instancia seleccionada.

## Semillas y reproducción de las mejores corridas

La opción `--seed` fija la semilla del generador aleatorio utilizado por el
recocido simulado. Esto controla la secuencia de decisiones aleatorias y permite
comparar corridas ejecutadas con la misma configuración.

El protocolo final evaluó diez semillas por instancia (1--10), con un presupuesto
de 1.800 segundos para cada corrida. Las mejores soluciones factibles observadas
fueron:

| Instancia | Semilla | Costo total | TAT | Violaciones |
|---|---:|---:|---:|---:|
| AC-01 | 5 | 822.595,0 | 456,00 h | 0 |
| AC-02 | 9 | 259.250,0 | 144,00 h | 0 |
| AC-03 | 5 | 205.712,5 | 172,75 h | 0 |

Para repetir su configuración desde el directorio que contiene
`microplanning/`:

```bash
python -m microplanning --instance ac01 --seed 5 --time 1800 --out resultados_mejores/ac01
python -m microplanning --instance ac02 --seed 9 --time 1800 --out resultados_mejores/ac02
python -m microplanning --instance ac03 --seed 5 --time 1800 --out resultados_mejores/ac03
```

La semilla reproduce la secuencia aleatoria, pero el término de la búsqueda se
basa en tiempo de reloj. Una máquina distinta puede completar otro número de
iteraciones y no necesariamente alcanzar exactamente el mismo costo. Por ello,
los comandos reproducen la configuración experimental; los valores de la tabla
corresponden a las corridas originales. Esta carpeta entrega las semillas y los
comandos necesarios para repetir su configuración; una repetición puede variar
ligeramente por depender de un límite de tiempo de reloj.

Desde Python, el flujo principal queda expuesto en una sola función:

```python
from microplanning.p07_ejecutar import ConfiguracionHeuristica, resolver
from microplanning.p02_instancia import cargar_instancia

instancia = cargar_instancia("ac03")
resultado = resolver(
    instancia,
    ConfiguracionHeuristica(semilla=5, tiempo_s=1800),
)
inicios = resultado.inicios
```

## Contenido

- `p01_configuracion.py`: unidades, turnos y costos globales.
- `p02_instancia.py`: carga de datos y construcción del grafo.
- `p03_operaciones_calendario.py`: operaciones numéricas compartidas por el SSGS y la evaluación.
- `p04_decodificador_eventos.py`: SSGS rápido por eventos.
- `p05_evaluacion.py`: métricas, factibilidad y exportación.
- `p06_SA.py`: recocido simulado (SA) y búsqueda local.
- `p07_ejecutar.py`: coordinación, interfaz de línea de comandos y resultados.
- `datos/`: todos los CSV necesarios para construir las tres instancias.
- `requirements.txt`: versiones exactas de las dependencias comprobadas.

## Portabilidad

La carpeta puede copiarse completa a otra ubicación manteniendo el nombre
`microplanning`. Se ejecuta desde su directorio padre con `python -m microplanning`.
Los directorios `__pycache__` no son parte de la entrega: Python y Numba los
regeneran automáticamente cuando hacen falta.
