# Caso base estricto

Este subpaquete implementa un calendario greedy, determinista y sin
backtracking para comparar la heurística principal con una política
constructiva conservadora.

El caso base reutiliza exclusivamente los módulos y datos incluidos en la
carpeta `microplanning/`. No depende de archivos ubicados en otras carpetas del
repositorio.

## Ejecución

Desde el directorio que contiene la carpeta `microplanning/`:

```bash
python -m pip install -r microplanning/requirements.txt
python -m microplanning.caso_base --instance ac01
```

También se puede ejecutar directamente:

```bash
python microplanning/caso_base/p02_ejecutar.py --instance ac01
```

Las instancias válidas son `ac01`, `ac02` y `ac03`. Para cambiar la carpeta de
salida:

```bash
python -m microplanning.caso_base --instance ac03 --out resultados/ac03
```

La ejecución produce un vector NumPy de inicios, calendarios CSV de hitos y
grupos, un perfil de carga y un resumen JSON con costos, TAT y validaciones.

## Regla constructiva

1. Orienta una sola vez cada restricción de no paralelismo sin crear ciclos.
2. Fija un orden topológico, con desempate por ID del hito.
3. Prueba inicios habilitados, bordes de turno y eventos de la especialidad.
4. Escoge la colocación factible de menor costo incremental y no vuelve atrás.
5. Permite únicamente el exceso de capacidad inevitable de un hito cuya
   demanda individual supera toda la capacidad regular disponible.

No usa semillas, reinicios, búsqueda local ni módulos externos a
`microplanning/`.

## Archivos

- `p01_constructor.py`: fija las disyunciones, construye el calendario greedy,
  evalúa la solución y exporta sus resultados.
- `p02_ejecutar.py`: procesa los argumentos, carga la instancia y coordina la
  ejecución.
- `__main__.py`: habilita el comando abreviado `python -m
  microplanning.caso_base`.
