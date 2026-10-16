# Paso 2: carga de datos, DAG unificado, disyunciones y CPM.
#
# Nodos del grafo:
#   0 .. n_hitos-1                  -> hitos que consumen recursos
#   n_hitos + 2g, n_hitos + 2g + 1 -> inicio y fin sintético del grupo g
# Un arco (u, v) significa: S_v >= S_u + dur_u.
from __future__ import annotations
import os
from dataclasses import dataclass, field
from heapq import heappush, heappop
import numpy as np
import pandas as pd
from .p01_configuracion import (DIAS_EXTRA, INTERVALOS_POR_DIA, INTERVALOS_POR_HORA, INTERVALOS_POR_TURNO, ORDEN_TURNOS)


# --- Normalización de identificadores ---
# Evita que variaciones de espacios, mayúsculas o "A/C" creen grupos distintos.
def _norm(nombre: str) -> str:
    return " ".join(str(nombre).upper().replace("A/C", "AC").split())


@dataclass
class Instancia:
    # Representación numérica completa de una instancia. Los arreglos incluyen hitos reales y nodos sintéticos; solo los primeros n_hitos consumen recursos.
    nombre: str
    hitos: pd.DataFrame
    grupos: list
    especialidades: list
    n_hitos: int
    n_nodos: int
    duracion: np.ndarray       # intervalos
    especialidad: np.ndarray   # índice de especialidad, -1 si es nodo sintético
    trabajadores: np.ndarray   # trabajadores requeridos
    arcos_base: np.ndarray     # (m, 2)
    disyunciones: np.ndarray   # (p, 4): las dos orientaciones posibles
    etiquetas_disyunciones: list
    miembros: dict             # grupo -> lista de hitos
    capacidad: np.ndarray      # trabajadores por especialidad y turno
    n_turnos_ventana: int
    tat_objetivo: int          # largo de la ventana, en intervalos
    estadisticas: dict = field(default_factory=dict)
    directorio_datos: str = ""

    # ---------- utilidades ----------
    # Devuelve el nodo sintético que representa el inicio del grupo g.
    def sigma(self, g):
        return self.n_hitos + 2 * g

    # Devuelve el nodo sintético que representa el fin del grupo g.
    def phi(self, g):
        return self.n_hitos + 2 * g + 1

    # Horizonte total evaluable, medido en slots de tres minutos.
    @property
    def horizonte(self):
        return self.capacidad.shape[1] * INTERVALOS_POR_TURNO

    # Combina los arcos obligatorios con una orientación para cada disyunción.
    def arcos_para(self, orientaciones: np.ndarray | None) -> np.ndarray:
        if orientaciones is None or len(self.disyunciones) == 0:
            return self.arcos_base
        elegidos = np.where(orientaciones[:, None] == 0, self.disyunciones[:, 0:2], self.disyunciones[:, 2:4])
        return np.vstack([self.arcos_base, elegidos])


# --- Conversión del grafo a formato CSR ---
# Construye listas compactas de predecesores o sucesores. ptr[u]:ptr[u+1]
# delimita en val los vecinos de u, sin usar listas de Python dentro de Numba.
def csr(n, arcs, by="pred"):
    # ptr[u]:ptr[u+1] delimita en val los vecinos de u. Este formato evita
    # listas de Python dentro de los kernels numéricos compilados con Numba.
    # Para predecesores se agrupa por destino; para sucesores, por origen.
    key, val = (arcs[:, 1], arcs[:, 0]) if by == "pred" else (arcs[:, 0], arcs[:, 1])
    # El orden estable conserva el orden relativo cuando dos arcos comparten clave.
    order = np.argsort(key, kind="stable")
    # El acumulado transforma conteos por nodo en límites de cada segmento.
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(ptr, key + 1, 1)
    return np.cumsum(ptr), val[order].astype(np.int64)


# --- Orden topológico ---
# Aplica Kahn usando un heap: entre nodos disponibles escoge la menor prioridad.
# Devuelve None cuando no puede procesar todos los nodos, es decir, si hay ciclo.
def topo_order(n, arcs, priority=None):
    # Los sucesores permiten actualizar quién queda disponible al retirar un nodo.
    sptr, sidx = csr(n, arcs, "succ")
    indeg = np.bincount(arcs[:, 1], minlength=n) if len(arcs) else np.zeros(n, int)
    # Sin prioridad explícita, el índice del nodo resuelve los empates.
    pr = np.arange(n) if priority is None else priority
    # El heap contiene solo nodos sin predecesores pendientes.
    heap = [(pr[u], u) for u in range(n) if indeg[u] == 0]
    heap.sort()
    out = []
    indeg = indeg.copy()
    while heap:
        # Siempre se procesa primero el nodo disponible con menor prioridad.
        _, u = heappop(heap)
        out.append(u)
        for v in sidx[sptr[u]:sptr[u + 1]]:
            indeg[v] -= 1
            if indeg[v] == 0:
                # El sucesor entra al heap cuando se satisface su último predecesor.
                heappush(heap, (pr[v], v))
    # Procesar menos de n nodos indica que el grafo contiene un ciclo.
    return np.array(out, dtype=np.int64) if len(out) == n else None


# --- Critical Path Method ---
# Calcula inicio temprano (ES), camino restante (tail), inicio tardío (LS) y la
# cota inferior de duración. No considera capacidades de trabajadores.
def cpm(instancia: Instancia, arcos, tat_referencia=None):
    n = instancia.n_nodos
    order = topo_order(n, arcos)
    pptr, pidx = csr(n, arcos, "pred")
    sptr, sidx = csr(n, arcos, "succ")
    # Barrido hacia adelante: inicio más temprano impuesto únicamente por el DAG.
    es = np.zeros(n, dtype=np.int64)
    for u in order:
        ps = pidx[pptr[u]:pptr[u + 1]]
        if len(ps):
            es[u] = np.max(es[ps] + instancia.duracion[ps])
    # Barrido hacia atrás: duración del camino más largo desde cada nodo. Por eso
    # T_ref - tail es el último inicio compatible con terminar antes de T_ref.
    tail = instancia.duracion.astype(np.int64).copy()
    for u in order[::-1]:
        ss = sidx[sptr[u]:sptr[u + 1]]
        if len(ss):
            tail[u] = instancia.duracion[u] + np.max(tail[ss])
    cota_inferior = int(np.max(es + instancia.duracion))
    tat_referencia = instancia.tat_objetivo if tat_referencia is None else tat_referencia
    return es, tail, tat_referencia - tail, cota_inferior


# --- Clausura transitiva compacta ---
# reach[u] es un entero usado como bitset de todos los nodos alcanzables desde u.
def _reach_bitsets(n, arcs):
    order = topo_order(n, arcs)
    sptr, sidx = csr(n, arcs, "succ")
    reach = [0] * n
    for u in order[::-1]:
        r = 0
        for v in sidx[sptr[u]:sptr[u + 1]]:
            r |= reach[v] | (1 << int(v))
        reach[u] = r
    return reach


RAIZ_PAQUETE = os.path.dirname(os.path.abspath(__file__))
DIRECTORIO_DATOS_PREDETERMINADO = os.path.join(RAIZ_PAQUETE, "datos")


# --- Carga y preprocesamiento de una instancia ---
# Convierte los CSV de un avión a arreglos enteros, construye el DAG, elimina
# disyunciones redundantes y extiende el calendario de capacidades.
def cargar_instancia(ac: str, directorio_datos: str = DIRECTORIO_DATOS_PREDETERMINADO) -> Instancia:
    # Resuelve un nombre lógico al CSV correspondiente de la aeronave elegida.
    f = lambda k: pd.read_csv(os.path.join(directorio_datos, f"{k}_{ac}.csv"))
    hitos = f("task_milestones").reset_index(drop=True)
    grupos_csv = f("groups")
    secuencia_hitos = f("task_milestones_sequence_requirements")
    secuencia_grupos = f("groups_sequence_requirements")
    no_paralelos_hitos = f("task_milestones_no_parallelism_requirements")
    no_paralelos_grupos = f("groups_no_parallelism_requirements")
    capacidades = f("worker_capacities")
    estadisticas = {}

    # --- Normalización y catálogos internos ---
    # Los textos pasan a índices enteros antes de entrar al núcleo numérico.
    hitos["group"] = hitos["group"].map(_norm)
    grupos = sorted(set(grupos_csv["name"].map(_norm)) | set(hitos["group"]))
    gidx = {g: i for i, g in enumerate(grupos)}
    especialidades = sorted(set(capacidades["skill"]) | set(hitos["skill"]))
    kidx = {k: i for i, k in enumerate(especialidades)}
    hid = {u: i for i, u in enumerate(hitos["id"])}
    n_h, n_g = len(hitos), len(grupos)
    # Cada grupo agrega dos nodos sintéticos: sigma y phi.
    n = n_h + 2 * n_g

    estadisticas["group_prec_rows"] = len(secuencia_grupos)
    secuencia_grupos = secuencia_grupos.assign(enabler=secuencia_grupos.enabler.map(_norm), enabled=secuencia_grupos.enabled.map(_norm)).drop_duplicates()
    estadisticas["group_prec_unique"] = len(secuencia_grupos)

    # --- Arreglos por nodo ---
    # Los CSV expresan horas. Desde aquí todo el motor usa intervalos enteros para
    # evitar errores de punto flotante al comparar precedencias y solapamientos.
    dur = np.zeros(n, dtype=np.int64)
    dur[:n_h] = np.round(hitos["tat"].to_numpy() * INTERVALOS_POR_HORA).astype(np.int64)
    skill = -np.ones(n, dtype=np.int64)
    skill[:n_h] = hitos["skill"].map(kidx).to_numpy()
    req = np.zeros(n, dtype=np.int64)
    req[:n_h] = hitos["worker_demand"].to_numpy()

    # Índice inverso para recuperar rápidamente los hitos de cada grupo.
    miembros = {g: [] for g in range(n_g)}
    for i, g in enumerate(hitos["group"].map(gidx)):
        miembros[g].append(i)

    # --- DAG de precedencias obligatorias ---
    arcs = []
    sig = lambda g: n_h + 2 * g
    ph = lambda g: n_h + 2 * g + 1
    # Los nodos sigma y phi encierran todos los hitos de un grupo. Gracias a
    # ellos una precedencia entre grupos se representa con un solo arco.
    for g in range(n_g):                       # span (C1)
        if miembros[g]:
            # sigma precede a todos los hitos; todos los hitos preceden a phi.
            for i in miembros[g]:
                arcs += [(sig(g), i), (i, ph(g))]
        else:
            # Un grupo vacío se representa como un intervalo de duración cero.
            arcs.append((sig(g), ph(g)))
    for a, b in zip(secuencia_hitos.enabler, secuencia_hitos.enabled):
        arcs.append((hid[a], hid[b]))
    for a, b in zip(secuencia_grupos.enabler, secuencia_grupos.enabled):
        arcs.append((ph(gidx[a]), sig(gidx[b])))
    base = np.array(sorted(set(arcs)), dtype=np.int64)
    # Una instancia cíclica es imposible y no debe llegar al optimizador.
    if topo_order(n, base) is None:
        raise ValueError("El grafo de precedencias tiene un ciclo")

    # Una incompatibilidad ya impuesta por una cadena de precedencias no agrega
    # decisiones al problema. Quitarla reduce el espacio que recorre el SA.
    # --- disyunciones (C2): se eliminan las ya implicadas por precedencias ---
    reach = _reach_bitsets(n, base)

    def implied(u_end, targets):
        return all((reach[u_end] >> t) & 1 for t in targets)

    disj, labels = [], []
    # Cada no-paralelismo se convierte en una decisión binaria: i antes que j o
    # j antes que i. ``sorted`` elimina duplicados y diferencias de orientación.
    pairs_h = {tuple(sorted((hid[a], hid[b]))) for a, b in zip(no_paralelos_hitos.iloc[:, 0], no_paralelos_hitos.iloc[:, 1])} if len(no_paralelos_hitos) else set()
    red_h = 0
    for i, j in pairs_h:
        # Se descarta el par si el DAG ya fija cuál hito ocurre primero.
        if implied(i, [j]) or implied(j, [i]):
            red_h += 1
        else:
            # Se almacenan juntas las dos orientaciones posibles del par.
            disj.append((i, j, j, i))
            labels.append(("hito", hitos.at[i, "name"], hitos.at[j, "name"]))
    pairs_g = {tuple(sorted((gidx[_norm(a)], gidx[_norm(b)]))) for a, b in zip(no_paralelos_grupos.iloc[:, 0], no_paralelos_grupos.iloc[:, 1])} if len(no_paralelos_grupos) else set()
    red_g = 0
    for a, b in pairs_g:
        # Los grupos vacíos usan sigma como su representante alcanzable.
        ta = miembros[a] or [sig(a)]
        tb = miembros[b] or [sig(b)]
        if implied(ph(a), tb) or implied(ph(b), ta):
            red_g += 1
        else:
            disj.append((ph(a), sig(b), ph(b), sig(a)))
            labels.append(("grupo", grupos[a], grupos[b]))
    estadisticas.update(hito_np_pairs=len(pairs_h), hito_np_redundant=red_h,
                 group_np_pairs=len(pairs_g), group_np_redundant=red_g,
                 effective_disjunctions=len(disj))

    # La capacidad original termina al cerrar la ventana. Se repite su última
    # semana para poder evaluar calendarios que se extiendan más allá del TAT.
    # --- capacidad por turno ---
    # El turno s cubre [s*INTERVALOS_POR_TURNO, (s+1)*INTERVALOS_POR_TURNO).
    # --- Capacidad por especialidad y turno ---
    capacidades = capacidades.assign(date=pd.to_datetime(capacidades["date"]))
    d0 = capacidades["date"].min()
    dias = int((capacidades["date"].max() - d0).days) + 1
    capacidades["s"] = (capacidades["date"] - d0).dt.days * 3 + capacidades["shift"].map(ORDEN_TURNOS)
    capw = np.zeros((len(especialidades), 3 * dias), dtype=np.int64)
    for k, s, a in zip(capacidades["skill"].map(kidx), capacidades["s"], capacidades["amount"]):
        # Cada celda guarda personas disponibles, no horas-hombre.
        capw[k, s] = a
    # Hay tres turnos diarios, por eso una semana corresponde a 21 columnas.
    ultima_semana = capw[:, -21:] if dias >= 7 else capw
    repeticiones = int(np.ceil(3 * DIAS_EXTRA / ultima_semana.shape[1]))
    cap = np.hstack([capw, np.tile(ultima_semana, repeticiones)[:, : 3 * DIAS_EXTRA]])

    # La instancia se crea antes del CPM porque sus duraciones son necesarias.
    inst = Instancia(ac, hitos, grupos, especialidades, n_h, n, dur, skill, req, base,
                    np.array(disj, dtype=np.int64).reshape(-1, 4), labels, miembros,
                    cap, 3 * dias, dias * INTERVALOS_POR_DIA, estadisticas, directorio_datos)
    _, _, _, lb = cpm(inst, base)
    estadisticas["lb_precedences_h"] = lb / INTERVALOS_POR_HORA
    estadisticas["window_days"] = dias
    estadisticas["workload_hh"] = float(hitos["rate"].sum())
    estadisticas["offered_hh_window"] = float(8 * capw.sum())
    return inst


if __name__ == "__main__":
    for ac in ["ac01", "ac02", "ac03"]:
        inst = cargar_instancia(ac)
        print(ac, inst.n_hitos, "hitos,", len(inst.grupos), "grupos |", inst.estadisticas)
