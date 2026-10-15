# Paso 4: decodificador SSGS por eventos, optimizado y equivalente al base.
#
# Conserva la semántica de decode(..., event_candidates=True). Sus mejoras son:
#   1. Recorrer en CSR solo los nodos de la especialidad necesaria.
#   2. Evitar candidatos repetidos mediante marcas de versión.
#
# El orden de evaluación no cambia: el CSR conserva los índices ascendentes y el
# desempate sigue siendo estricto. Por eso el resultado es idéntico, no aproximado.
import numpy as np

from .p03_operaciones_calendario import costo_colocacion, njit


# --- Índice de nodos por especialidad ---
# Construye un CSR donde cada segmento contiene, en orden ascendente, únicamente
# los nodos de una especialidad. Evita revisar todos los nodos para cada hito.
def csr_por_especialidad(especialidades, cantidad_especialidades):
    especialidades = np.asarray(especialidades)
    # Primero se cuenta cuántos nodos tendrá el segmento de cada especialidad.
    ptr = np.zeros(cantidad_especialidades + 1, dtype=np.int64)
    for k in range(cantidad_especialidades):
        ptr[k + 1] = ptr[k] + int(np.count_nonzero(especialidades == k))
    # idx concatena los segmentos; cursor señala la siguiente celda libre de cada uno.
    idx = np.empty(int(ptr[cantidad_especialidades]), dtype=np.int64)
    cursor = ptr[:cantidad_especialidades].copy()
    for j in range(especialidades.shape[0]):
        # Recorrer j en orden ascendente conserva exactamente los desempates del original.
        k = int(especialidades[j])
        if k >= 0:
            idx[cursor[k]] = j
            cursor[k] += 1
    return ptr, idx


# --- Decodificación optimizada por eventos ---
# Implementa el mismo SSGS que decode, pero usa el índice por especialidad y una
# marca por slot para no calcular dos veces el costo del mismo inicio candidato.
@njit(cache=True)
def decodificar_rapido(orden, duraciones, especialidades, trabajadores, pptr, pidx,
                       inicios_tardios, capacidad, intervalos_turno, holgura,
                       penalizacion_tardia, costo_intervalo, desempate, horizonte,
                       especialidad_ptr, especialidad_idx):
    K = capacidad.shape[0]
    n = orden.shape[0]
    # D guarda la demanda por slot y M su máximo por turno. Mantener ambos hace
    # barato evaluar el costo de sobrecarga de cada posición candidata.
    demanda = np.zeros((K, horizonte), dtype=np.int64)
    maximos = np.zeros((K, capacidad.shape[1]), dtype=np.int64)
    inicios = np.full(n, -1, dtype=np.int64)
    # marca de version por slot: evita reevaluar un candidato ya probado para este hito
    marca = np.zeros(horizonte + 1, dtype=np.int64)
    version = 0

    for idx in range(n):
        u = orden[idx]
        # Calcula el primer inicio permitido por todos los predecesores ya ubicados.
        es = 0
        for q in range(pptr[u], pptr[u + 1]):
            p = pidx[q]
            e = inicios[p] + duraciones[p]
            if e > es:
                es = e
        d = duraciones[u]
        if especialidades[u] < 0 or d == 0:
            # Los nodos de grupo solo transmiten precedencias y no generan candidatos.
            inicios[u] = es
            continue
        k = especialidades[u]
        r = trabajadores[u]
        best_t = es
        mejor_costo = 1e30
        # En vez de limpiar ``marca`` para cada nodo, se incrementa la versión.
        # Un slot con la versión actual ya fue evaluado para este mismo hito.
        version += 1
        lim = es + holgura
        b0 = (es // intervalos_turno) * intervalos_turno
        b = b0

        # --- candidatos alineados a borde de turno (mismo orden que el original) ---
        while True:
            # Primer conjunto: inicio temprano y bordes de turno cercanos.
            for c in range(3):
                if c == 0:
                    t = es
                elif c == 1:
                    t = b
                else:
                    t = b - d
                if t < es or t > lim or t + d > horizonte:
                    continue
                if c == 0 and b > b0:
                    continue
                if marca[t] == version:
                    # El mismo inicio puede provenir de más de una regla de generación.
                    continue
                marca[t] = version
                dws, dsl = costo_colocacion(demanda, maximos, capacidad, k, t, d, r, intervalos_turno)
                phi = 800.0 * dws + costo_intervalo * dsl + desempate * t
                late = t - inicios_tardios[u]
                if late > 0:
                    phi += penalizacion_tardia * late
                if phi < mejor_costo - 1e-9:
                    mejor_costo = phi
                    best_t = t
            b += intervalos_turno
            if b - d > lim or b > horizonte:
                break

        # --- candidatos por evento: solo los nodos de la especialidad k, indice ascendente ---
        for q in range(especialidad_ptr[k], especialidad_ptr[k + 1]):
            # Segundo conjunto: comienzos y finales de trabajo de igual especialidad.
            j = especialidad_idx[q]
            if inicios[j] < 0:
                continue
            for c in range(4):
                if c == 0:
                    t = inicios[j]
                elif c == 1:
                    t = inicios[j] + duraciones[j]
                elif c == 2:
                    t = inicios[j] - d
                else:
                    t = inicios[j] + duraciones[j] - d
                if t < es or t > lim or t + d > horizonte:
                    continue
                if marca[t] == version:
                    # No se vuelve a calcular un inicio ya evaluado en este hito.
                    continue
                marca[t] = version
                dws, dsl = costo_colocacion(demanda, maximos, capacidad, k, t, d, r, intervalos_turno)
                phi = 800.0 * dws + costo_intervalo * dsl + desempate * t
                late = t - inicios_tardios[u]
                if late > 0:
                    phi += penalizacion_tardia * late
                if phi < mejor_costo - 1e-9:
                    mejor_costo = phi
                    best_t = t

        # Esta actualización está escrita explícitamente (en lugar de llamar a
        # apply) para conservar un kernel autocontenido y eficiente en Numba.
        inicios[u] = best_t
        # Se incorpora la elección al estado que verán los hitos siguientes.
        for h in range(best_t, best_t + d):
            demanda[k, h] += r
        for s in range(best_t // intervalos_turno, (best_t + d - 1) // intervalos_turno + 1):
            mx = 0
            for h in range(s * intervalos_turno, (s + 1) * intervalos_turno):
                if demanda[k, h] > mx:
                    mx = demanda[k, h]
            maximos[k, s] = mx
    return inicios
