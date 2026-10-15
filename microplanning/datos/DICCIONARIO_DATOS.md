# Diccionario de datos — Instancias de checks de mantenimiento (AC-01, AC-02, AC-03)

Cada instancia (AC-01, AC-02, AC-03) tiene 7 archivos propios, identificados con el
sufijo `_ac01`, `_ac02` o `_ac03`. Hay además un archivo `costs.csv` común a las tres.
Todos los archivos están verificados contra las cifras reportadas en el informe (sección 2.4);
las cantidades de filas indicadas abajo corresponden a los datos reales.

## Convenciones generales

- **Clave de un hito**: usar siempre `id` (UUID), no `task`. El campo `task` (ej. `T-0392`)
  se repite entre hitos porque agrupa sub-actividades del mismo trabajo (ej. `-OP`, `-LUB`);
  `id` es el único identificador verdaderamente único.
- **Clave de un grupo**: el nombre en `group` / `name` (texto), no hay id numérico.
  Ojo: hay inconsistencias de nombre entre instancias para el mismo concepto de grupo
  (ej. `ENTRADA AC HANGAR` en AC-02 vs `ENTRADA A/C HANGAR` en AC-03) — normalizar antes
  de cruzar instancias.
- **Turnos**: `M` (mañana), `T` (tarde), `N` (noche). Bloques de 8h sin hora de reloj asociada;
  el modelo trabaja en tiempo relativo desde el inicio de la check.
- **Especialidades**: `Z100`, `Z200`, `Z350`, `Z400`, `Z600`, `Z700` — las mismas seis en
  las tres instancias.
- **Archivos vacíos**: algunos archivos tienen 0 filas de datos (solo encabezado) en ciertas
  instancias — esto es esperado y no un error. Ver detalle por archivo más abajo.

---

## 1. `costs.csv` (único, compartido por las 3 instancias)

| Columna | Tipo | Descripción |
|---|---|---|
| `concept` | string | Identificador del concepto de costo (`over_capacity_hh`, `idle_capacity_hh`, `over_tat`) |
| `unit` | string | Unidad del costo (`USD/hora-hombre` o `USD/dia`) |
| `cost_usd` | float | Valor del costo |
| `description` | string | Descripción en texto |

**Valores**: sobrecarga = 100 USD/h-h · ociosidad = 50 USD/h-h · exceso de TAT = 8.500 USD/día.
(100/50 = 2× → sobrecarga cuesta el doble que la ociosidad. 8.500/50 = 170 h-h → un día extra
de TAT equivale a 170 horas-hombre ociosas.)

---

## 2. `task_milestones_acXX.csv` — Hitos (el archivo principal)

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | UUID | **Clave única del hito.** Usar esta columna para joins con los archivos de precedencia/no-paralelismo entre hitos. |
| `task` | string | Código de tarea (ej. `T-0392`). Se repite entre hitos — NO es único. |
| `name` | string | Nombre corto, combina `task` + tipo de operación (ej. `T-0392-LUB(JIC)`) |
| `description` | string | Descripción libre de la actividad |
| `group` | string | Nombre del grupo al que pertenece — join con `groups_acXX.csv` |
| `skill` | string | Especialidad requerida (Z100–Z700) |
| `tat` | float | Duración del hito en horas |
| `rate` | float | Carga en horas-hombre = `tat × worker_demand` (verificado: se cumple en el 100% de los 2.542 hitos de las tres instancias) |
| `worker_demand` | int | Nº de trabajadores de `skill` requeridos simultáneamente durante todo el hito |

**Cómo encontrar cosas típicas:**
- Carga total de una instancia → `sum(rate)`
- Hitos que exceden un turno (>8h) → filtrar `tat > 8`
- Cantidad de tareas distintas (no hitos) → `nunique(task)`

**Filas**: AC-01 = 1.194 · AC-02 = 668 · AC-03 = 680

---

## 3. `groups_acXX.csv` — Listado de grupos

| Columna | Tipo | Descripción |
|---|---|---|
| `name` | string | Nombre del grupo (clave) |

Incluye grupos **sin hitos** (puntos de control, ej. `INICIO`, `POWER ON/OFF`, `SUBIR/BAJAR AVION
EN GATAS`) que no aparecen en `task_milestones_acXX.csv` pero sí deben programarse explícitamente.
Para identificarlos: `set(groups.name) − set(task_milestones.group)`.

**Filas**: AC-01 = 38 (5 sin hitos) · AC-02 = 44 (12 sin hitos) · AC-03 = 44 (12 sin hitos)

---

## 4. `task_milestones_sequence_requirements_acXX.csv` — Precedencias entre hitos

| Columna | Tipo | Descripción |
|---|---|---|
| `enabler` | UUID | `id` del hito que debe completarse primero |
| `enabled` | UUID | `id` del hito que depende del anterior |

Join con `task_milestones_acXX.csv` por `id`. Grafo acíclico, sin arcos redundantes (verificado
por el equipo). Solo cerca de la mitad de los hitos participa en alguna precedencia.

**Filas**: AC-01 = 410 · AC-02 = 207 · AC-03 = 202

---

## 5. `groups_sequence_requirements_acXX.csv` — Precedencias entre grupos

| Columna | Tipo | Descripción |
|---|---|---|
| `enabler` | string | Nombre del grupo que debe completarse primero |
| `enabled` | string | Nombre del grupo que depende del anterior |

**Advertencia:** contiene filas duplicadas en las tres instancias, de forma extrema en AC-03: de 216 filas,
112 son duplicados exactos (solo 104 pares únicos). Depurar en preprocesamiento — no cambia las
soluciones posibles pero infla el tamaño del modelo.

**Filas (total / únicas)**: AC-01 = 69 / 67 · AC-02 = 78 / 70 · AC-03 = 216 / 104

---

## 6. `task_milestones_no_parallelism_requirements_acXX.csv` — Incompatibilidad entre hitos

| Columna | Tipo | Descripción |
|---|---|---|
| `task_milestone_i` | UUID | `id` de un hito del par incompatible |
| `task_milestone_j` | UUID | `id` del otro hito del par |

Pares que no pueden ejecutarse en paralelo (independiente de la disponibilidad de recursos),
típicamente por ocupar la misma zona física. Join con `task_milestones_acXX.csv` por `id`.

**Advertencia:** solo AC-01 declara esta restricción a nivel de hito (5.545 pares, muy densa: 209 hitos
distintos involucrados, uno de ellos bloqueando a 96 actividades). En AC-02 y AC-03 el archivo
existe pero está **vacío** (0 filas) — en esas dos instancias la restricción se expresa a nivel
de grupo (ver archivo 7).

**Filas**: AC-01 = 5.545 · AC-02 = 0 · AC-03 = 0

---

## 7. `groups_no_parallelism_requirements_acXX.csv` — Incompatibilidad entre grupos

| Columna | Tipo | Descripción |
|---|---|---|
| `group_i` | string | Nombre de un grupo del par incompatible |
| `group_j` | string | Nombre del otro grupo del par |

Misma restricción que el archivo 6 pero expresada a nivel de grupo. Complementaria: donde una
instancia usa este archivo con datos, el archivo 6 está vacío, y viceversa.

**Advertencia:** AC-01 tiene este archivo vacío (0 filas); ahí la restricción vive solo en el archivo 6.

**Filas**: AC-01 = 0 · AC-02 = 41 · AC-03 = 113 (en AC-03, 39 de los 44 grupos participan en al
menos un par; el grupo `PESAJE` concentra 33 de los 113 pares)

---

## 8. `worker_capacities_acXX.csv` — Oferta de trabajadores

| Columna | Tipo | Descripción |
|---|---|---|
| `date` | date | Día calendario dentro de la ventana de la instancia |
| `skill` | string | Especialidad (Z100–Z700) |
| `shift` | string | Turno (`M`, `T`, `N`) |
| `amount` | int | Nº de trabajadores disponibles de esa especialidad en ese turno/día |

Filas = días de la ventana × 3 turnos × 6 especialidades (incluye filas con `amount = 0`, ej.
domingos, que tienen capacidad nula en las tres instancias; los sábados operan con dotación
reducida pero no nula).

**Cómo encontrar cosas típicas:**
- Capacidad total de una especialidad en horas-hombre → `sum(amount) × 8`
- Comparar contra demanda → `sum(rate donde skill=X)` de `task_milestones_acXX.csv` dividido por
  lo anterior (esto da la Tabla 2 del informe)
- % de capacidad en turno noche → `sum(amount donde shift=N) / sum(amount)` (≈14–16% en las tres)

**Filas**: AC-01 = 432 (24 días) · AC-02 = 180 (10 días) · AC-03 = 216 (12 días)

---

## Joins más comunes

```
task_milestones_acXX.id  ──┬── task_milestones_sequence_requirements_acXX.{enabler, enabled}
                            └── task_milestones_no_parallelism_requirements_acXX.{task_milestone_i, task_milestone_j}

task_milestones_acXX.group ──── groups_acXX.name ──┬── groups_sequence_requirements_acXX.{enabler, enabled}
                                                     └── groups_no_parallelism_requirements_acXX.{group_i, group_j}

task_milestones_acXX.skill ──── worker_capacities_acXX.skill  (+ ventana de fechas de la instancia)
```
