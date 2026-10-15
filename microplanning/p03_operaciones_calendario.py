# Paso 3: operaciones numéricas del calendario compiladas con Numba.
#
# Estado de recursos:
#   D[k, t] = demanda de trabajadores de la especialidad k en el slot t.
#   M[k, s] = máximo de D dentro del turno s.
#   X[k, s] = max(0, M[k, s] - cap[k, s]) trabajadores extra contratados.
import numpy as np
try:
    from numba import njit
except ImportError:                      # funciona sin numba, pero ~100x más lento
    def njit(*a, **k):
        return (lambda f: f) if not a or not callable(a[0]) else a[0]


# --- Costo de insertar un hito ---
# Calcula, sin modificar el estado, cuánto aumenta la contratación por turno y
# la demanda sobre capacidad al poner r trabajadores de k durante [t, t+d).
# Retorna ambos deltas para que el decodificador construya su función de puntaje.
@njit(cache=True)
def costo_colocacion(demanda, maximos, capacidad, k, inicio, duracion, trabajadores, intervalos_turno):
    # No modifica D ni M: calcula el delta respecto del estado actual. ``dws``
    # cobra el máximo del turno; ``dslot`` mide el exceso efectivo slot a slot.
    dws = 0.0
    dslot = 0.0
    s0 = inicio // intervalos_turno
    s1 = (inicio + duracion - 1) // intervalos_turno
    for s in range(s0, s1 + 1):
        # a:b es la porción del hito que pertenece al turno actual.
        a = max(inicio, s * intervalos_turno)
        b = min(inicio + duracion, (s + 1) * intervalos_turno)
        c = capacidad[k, s]
        seg = 0
        for h in range(a, b):
            # Se suma únicamente el exceso nuevo provocado por este hito.
            v = demanda[k, h]
            if v > seg:
                seg = v
            dslot += max(v + trabajadores - c, 0) - max(v - c, 0)
        # La contratación se calcula con el máximo simultáneo del turno completo.
        mx = maximos[k, s]
        new = max(mx, seg + trabajadores)
        dws += max(new - c, 0) - max(mx - c, 0)
    return dws, dslot


# --- Actualización del estado de recursos ---
# r positivo agrega un hito y r negativo lo retira. Solo recalcula los máximos M
# de los turnos tocados por el intervalo.
@njit(cache=True)
def aplicar(demanda, maximos, k, inicio, duracion, trabajadores, intervalos_turno):
    # Primero cambia la demanda de cada intervalo cubierto por el hito.
    for h in range(inicio, inicio + duracion):
        demanda[k, h] += trabajadores
    # Después recalcula únicamente los máximos de los turnos afectados.
    for s in range(inicio // intervalos_turno, (inicio + duracion - 1) // intervalos_turno + 1):
        mx = 0
        for h in range(s * intervalos_turno, (s + 1) * intervalos_turno):
            if demanda[k, h] > mx:
                mx = demanda[k, h]
        maximos[k, s] = mx


# --- Reconstrucción completa del estado ---
# Genera D y M desde un calendario terminado. Se usa para evaluar o pulir una
# solución; durante decode estos arreglos se actualizan incrementalmente.
@njit(cache=True)
def construir_estado(inicios, duraciones, especialidades, trabajadores, K, n_turnos, intervalos_turno):
    demanda = np.zeros((K, n_turnos * intervalos_turno), dtype=np.int64)
    for u in range(inicios.shape[0]):
        # Especialidad negativa identifica nodos sintéticos sin carga de trabajo.
        if especialidades[u] >= 0 and duraciones[u] > 0:
            k = especialidades[u]
            for h in range(inicios[u], inicios[u] + duraciones[u]):
                demanda[k, h] += trabajadores[u]
    maximos = np.zeros((K, n_turnos), dtype=np.int64)
    for k in range(K):
        for s in range(n_turnos):
            mx = 0
            for h in range(s * intervalos_turno, (s + 1) * intervalos_turno):
                if demanda[k, h] > mx:
                    mx = demanda[k, h]
            maximos[k, s] = mx
    return demanda, maximos


# --- Agregación de indicadores de recursos ---
# Retorna trabajadores-turno extra, sobrecarga horaria, oferta utilizada dentro
# de la ventana, oferta utilizada hasta el TAT y máxima razón demanda/oferta.
@njit(cache=True)
def terminos_recursos(demanda, maximos, capacidad, intervalos_turno, n_turnos_ventana, turnos_tat):
    K = capacidad.shape[0]
    xws = 0.0
    ov = 0.0
    uw = 0.0
    ut = 0.0
    mr = 0.0
    for k in range(K):
        for s in range(capacidad.shape[1]):
            # El máximo del turno determina cuántos trabajadores extra contratar.
            c = capacidad[k, s]
            xws += max(maximos[k, s] - c, 0)
            if c > 0 and maximos[k, s] / c > mr:
                mr = maximos[k, s] / c
            for h in range(s * intervalos_turno, (s + 1) * intervalos_turno):
                v = demanda[k, h]
                ov += max(v - c, 0)
                # La utilización reconocida no puede superar la oferta disponible.
                m = min(v, c)
                if s < n_turnos_ventana:
                    uw += m
                if s < turnos_tat:
                    ut += m
    return xws, ov, uw, ut, mr


# --- Cotas para nodos sintéticos ---
# Con los hitos reales fijos, propaga el inicio más temprano ET y el más tardío LT
# de cada inicio/fin de grupo. Estas cotas permiten mover un hito en búsqueda local
# sin romper indirectamente las precedencias entre grupos.
@njit(cache=True)
def cotas_sinteticas(topo, inicios, duraciones, especialidades, pptr, pidx, sptr, sidx, horizonte):
    n = topo.shape[0]
    ET = np.zeros(n, dtype=np.int64)
    LT = np.full(n, horizonte, dtype=np.int64)
    # Pase hacia adelante para propagar cotas desde predecesores fijos.
    for idx in range(n):
        u = topo[idx]
        if especialidades[u] >= 0:
            continue
        e = 0
        for q in range(pptr[u], pptr[u + 1]):
            p = pidx[q]
            v = (inicios[p] + duraciones[p]) if especialidades[p] >= 0 else ET[p]
            if v > e:
                e = v
        ET[u] = e
    # Pase inverso para propagar cotas desde sucesores fijos.
    for idx in range(n - 1, -1, -1):
        u = topo[idx]
        if especialidades[u] >= 0:
            continue
        l = horizonte
        for q in range(sptr[u], sptr[u + 1]):
            v = sidx[q]
            w = inicios[v] if especialidades[v] >= 0 else LT[v]
            if w < l:
                l = w
        LT[u] = l
    return ET, LT
