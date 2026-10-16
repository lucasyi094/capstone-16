# Decodificador SSGS por eventos, optimizado y equivalente al decodificador base.
#

import numpy as np

from .operaciones_calendario import njit, place_cost


# --- Índice de nodos por especialidad 
# Construye un CSR donde cada segmento contiene, en orden ascendente, únicamente
# los nodos de una especialidad. Evita revisar todos los nodos para cada hito.
def csr_por_especialidad(skill, K):
    skill = np.asarray(skill)
    ptr = np.zeros(K + 1, dtype=np.int64)
    for k in range(K):
        ptr[k + 1] = ptr[k] + int(np.count_nonzero(skill == k))
    idx = np.empty(int(ptr[K]), dtype=np.int64)
    cursor = ptr[:K].copy()
    for j in range(skill.shape[0]):
        k = int(skill[j])
        if k >= 0:
            idx[cursor[k]] = j
            cursor[k] += 1
    return ptr, idx


# --- Decodificación optimizada por eventos ---
# Implementa el mismo SSGS que decode, pero usa el índice por especialidad y una
# marca por slot para no calcular dos veces el costo del mismo inicio candidato.
@njit(cache=True)
def decode_rapido(order, dur, skill, req, pptr, pidx, LS, cap, SH, delta, lam, w_slot, eps,horizon, sk_ptr, sk_idx):
    K = cap.shape[0]
    n = order.shape[0]
    # D guarda la demanda por slot y M su máximo por turno. Mantener ambos hace
    # barato evaluar el costo de sobrecarga de cada posición candidata.
    D = np.zeros((K, horizon), dtype=np.int64)
    M = np.zeros((K, cap.shape[1]), dtype=np.int64)
    S = np.full(n, -1, dtype=np.int64)
    # marca de version por slot: evita reevaluar un candidato ya probado para este hito
    marca = np.zeros(horizon + 1, dtype=np.int64)
    version = 0

    for idx in range(n):
        u = order[idx]
        es = 0
        for q in range(pptr[u], pptr[u + 1]):
            p = pidx[q]
            e = S[p] + dur[p]
            if e > es:
                es = e
        d = dur[u]
        if skill[u] < 0 or d == 0:
            S[u] = es
            continue
        k = skill[u]
        r = req[u]
        best_t = es
        best = 1e30
        # En vez de limpiar ``marca`` para cada nodo, se incrementa la versión.
        # Un slot con la versión actual ya fue evaluado para este mismo hito.
        version += 1
        lim = es + delta
        b0 = (es // SH) * SH
        b = b0

        # --- candidatos alineados a borde de turno (mismo orden que el original) ---
        while True:
            for c in range(3):
                if c == 0:
                    t = es
                elif c == 1:
                    t = b
                else:
                    t = b - d
                if t < es or t > lim or t + d > horizon:
                    continue
                if c == 0 and b > b0:
                    continue
                if marca[t] == version:
                    continue
                marca[t] = version
                dws, dsl = place_cost(D, M, cap, k, t, d, r, SH)
                phi = 800.0 * dws + w_slot * dsl + eps * t
                late = t - LS[u]
                if late > 0:
                    phi += lam * late
                if phi < best - 1e-9:
                    best = phi
                    best_t = t
            b += SH
            if b - d > lim or b > horizon:
                break

        # --- candidatos por evento: solo los nodos de la especialidad k, indice ascendente ---
        for q in range(sk_ptr[k], sk_ptr[k + 1]):
            j = sk_idx[q]
            if S[j] < 0:
                continue
            for c in range(4):
                if c == 0:
                    t = S[j]
                elif c == 1:
                    t = S[j] + dur[j]
                elif c == 2:
                    t = S[j] - d
                else:
                    t = S[j] + dur[j] - d
                if t < es or t > lim or t + d > horizon:
                    continue
                if marca[t] == version:
                    continue
                marca[t] = version
                dws, dsl = place_cost(D, M, cap, k, t, d, r, SH)
                phi = 800.0 * dws + w_slot * dsl + eps * t
                late = t - LS[u]
                if late > 0:
                    phi += lam * late
                if phi < best - 1e-9:
                    best = phi
                    best_t = t

        # Esta actualización está escrita explícitamente (en lugar de llamar a
        # apply) para conservar un kernel autocontenido y eficiente en Numba.
        S[u] = best_t
        for h in range(best_t, best_t + d):
            D[k, h] += r
        for s in range(best_t // SH, (best_t + d - 1) // SH + 1):
            mx = 0
            for h in range(s * SH, (s + 1) * SH):
                if D[k, h] > mx:
                    mx = D[k, h]
            M[k, s] = mx
    return S
