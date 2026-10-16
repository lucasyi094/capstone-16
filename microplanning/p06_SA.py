# Paso 6: motor de optimización mediante SA y búsqueda local.
# El flujo público está en p07_ejecutar.py; este módulo contiene los vecindarios y
# la administración detallada del estado de búsqueda.
import math
import time
import numpy as np
from .p01_configuracion import (COSTO_DIA_EXCESO_TAT, COSTO_OCIOSIDAD_HORA,
                                INTERVALOS_POR_DIA, INTERVALOS_POR_HORA,
                                INTERVALOS_POR_TURNO)
from .p02_instancia import cpm, csr, topo_order
from .p03_operaciones_calendario import (aplicar, construir_estado, costo_colocacion,
                                         cotas_sinteticas)
from .p04_decodificador_eventos import csr_por_especialidad, decodificar_rapido
from .p05_evaluacion import evaluar


class SolucionadorHeurEventos:
    # Optimiza las decisiones que alimentan al SSGS. Una solución contiene:
    # pi (orden topológico), y (orientación de disyunciones), delta (holgura de
    # colocación) y, opcionalmente, T_ref (TAT guía). El calendario S se obtiene
    # de forma determinista a partir de esos componentes.
    def __init__(self, instancia, semilla=0):
        # El generador queda ligado a la semilla para reproducir una corrida completa.
        self.instancia = instancia
        self.aleatorio = np.random.default_rng(semilla)
        # Evita recorrer hitos de especialidades irrelevantes al decodificar.
        self._event_skill_index = csr_por_especialidad(
            instancia.especialidad, instancia.capacidad.shape[0]
        )
        per_slot = 1.0 / INTERVALOS_POR_HORA
        # w_slot: costo de ociosidad por slot-trabajador de demanda sobre la oferta
        self.w_slot = COSTO_OCIOSIDAD_HORA * per_slot
        # lam: costo marginal de una hora de TAT, llevado a slots (sección C4)
        cap_hour = instancia.capacidad[:, : instancia.n_turnos_ventana].sum(axis=0).mean()
        self.lam = (COSTO_DIA_EXCESO_TAT / 24.0 + COSTO_OCIOSIDAD_HORA * cap_hour) * per_slot
        self.T_ref = int(instancia.estadisticas["lb_precedences_h"] * INTERVALOS_POR_HORA)
        self.eps = 1e-4
        self._cache = {}

    # ---------- grafo según orientación y (C2) ----------
    def graph(self, y):
        # Muchas propuestas del SA repiten una orientación ya visitada. La caché
        # evita reconstruir su DAG y sus cotas temporales en cada repetición.
        # La orientación completa funciona como clave inmutable de caché.
        key = y.tobytes()
        if key not in self._cache:
            # Una orientación cíclica se guarda como None y se rechaza al reutilizarla.
            arcs = self.instancia.arcos_para(y)
            topo = topo_order(self.instancia.n_nodos, arcs)
            if topo is None:
                self._cache[key] = None
            else:
                pptr, pidx = csr(self.instancia.n_nodos, arcs, "pred")
                sptr, sidx = csr(self.instancia.n_nodos, arcs, "succ")
                ES, tail, LS, lbk = cpm(self.instancia, arcs, self.T_ref)
                self._cache[key] = dict(arcs=arcs, topo=topo, pptr=pptr, pidx=pidx,
                                        sptr=sptr, sidx=sidx, ES=ES, LS=LS, tail=tail, lb=lbk)
            # El límite evita acumular indefinidamente arreglos de grafos antiguos.
            if len(self._cache) > 200:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    # --- Solución inicial ---
    # Orienta las disyunciones sin crear ciclos y ordena los nodos por último
    # inicio posible (LST) o por criticidad, según la configuración.
    def crear_solucion_inicial(self):
        inst = self.instancia
        es, _, _, _ = cpm(inst, inst.arcos_base)
        # Orientación inicial: se agregan las disyunciones de a una, prefiriendo que vaya
        # primero el grupo que puede empezar antes y evitando crear ciclos.
        y = np.zeros(len(inst.disyunciones), dtype=np.int64)
        arcs = inst.arcos_base
        # Se procesan primero las incompatibilidades que podrían comenzar antes.
        for p in np.argsort([min(es[a0], es[a1]) for a0, _, a1, _ in inst.disyunciones]):
            a0, b0, a1, b1 = inst.disyunciones[p]
            pref = 0 if es[a0] <= es[a1] else 1
            # Si la dirección preferida crea ciclo, se intenta la opuesta.
            for opt in (pref, 1 - pref):
                arc = np.array([[a0, b0]] if opt == 0 else [[a1, b1]])
                if topo_order(inst.n_nodos, np.vstack([arcs, arc])) is not None:
                    y[p] = opt
                    arcs = np.vstack([arcs, arc])
                    break
            else:
                raise ValueError(f"Disyunción imposible de orientar: {inst.etiquetas_disyunciones[p]}")
        g = self.graph(y)
        priority = g["LS"]
        pi = topo_order(inst.n_nodos, g["arcs"], priority=priority)
        return pi, y, 48 * INTERVALOS_POR_HORA

    # ---------- decodificación (C4) ----------
    # --- Construcción de calendario ---
    # Convierte pi, y y delta en tiempos de inicio mediante el SSGS por eventos.
    def decodificar(self, pi, y, delta, T_ref=None):
        g = self.graph(y)
        inst = self.instancia
        # Cambiar T_ref desplaza los inicios tardíos sin reconstruir el DAG.
        LS = g["LS"] if T_ref is None else (int(T_ref) - g["tail"])
        args = (pi, inst.duracion, inst.especialidad, inst.trabajadores, g["pptr"], g["pidx"], LS,
                inst.capacidad, INTERVALOS_POR_TURNO, int(delta), self.lam, self.w_slot,
                self.eps, inst.horizonte)
        return decodificar_rapido(*args, *self._event_skill_index)

    def calcular_costo(self, S):
        return evaluar(self.instancia, S)["costo_total"]

    # ---------- vecindarios (C5) ----------
    # Mueve un hito únicamente dentro del intervalo que conserva el orden del DAG.
    def _move_in_list(self, pi, y):
        g = self.graph(y)
        n = len(pi)
        # pos convierte el índice de nodo en su ubicación actual dentro de pi.
        pos = np.empty(n, dtype=np.int64); pos[pi] = np.arange(n)
        hitos = self.instancia.n_hitos
        u = int(pi[self.aleatorio.integers(n)])
        if u >= hitos:
            u = int(self.aleatorio.integers(hitos))
        preds = g["pidx"][g["pptr"][u]:g["pptr"][u + 1]]
        succs = g["sidx"][g["sptr"][u]:g["sptr"][u + 1]]
        # El nodo debe quedar después de todo predecesor y antes de todo sucesor.
        lo = pos[preds].max() + 1 if len(preds) else 0
        hi = pos[succs].min() - 1 if len(succs) else n - 1
        # Sin más de una posición factible no existe un vecino distinto.
        if hi <= lo:
            return None
        new = int(self.aleatorio.integers(lo, hi + 1))
        lst = list(pi); lst.pop(pos[u])
        lst.insert(new if new < pos[u] else new, u)
        return np.array(lst, dtype=np.int64)

    # Invierte una disyunción y reconstruye el orden si el grafo sigue siendo DAG.
    def _flip(self, pi, y):
        if len(y) == 0:
            return None
        y2 = y.copy(); y2[self.aleatorio.integers(len(y))] ^= 1
        # La inversión solo sirve si las precedencias continúan siendo acíclicas.
        g = self.graph(y2)
        if g is None:
            return None
        pos = np.empty(len(pi), dtype=np.int64); pos[pi] = np.arange(len(pi))
        return topo_order(self.instancia.n_nodos, g["arcs"], priority=pos), y2

    # ---------- Simulated Annealing (C5) ----------
    # --- Bucle principal de Simulated Annealing ---
    # Explora orden, disyunciones, holgura y opcionalmente T_ref. Acepta siempre
    # mejoras y acepta algunos empeoramientos según la temperatura. Cada nuevo
    # mejor puede pulirse mediante búsqueda local sin superar deadline.
    def optimizar_con_recocido(self, presupuesto_tiempo=60.0, intervalo_log=5.0,
                               mostrar_progreso=True, periodo_busqueda_local=5.0,
                               optimizar_tat_referencia=False, limite=None):
        # Estado actual: orden, orientaciones y holgura de colocación.
        pi, y, delta = self.crear_solucion_inicial()
        lb = self.graph(y)["lb"]
        tmax = self.instancia.tat_objetivo + 2 * INTERVALOS_POR_DIA
        tref = self.T_ref if not optimizar_tat_referencia else int(lb + 0.5 * (self.instancia.tat_objetivo - lb))
        dec = lambda p_, y_, d_, tr_: self.decodificar(p_, y_, d_, tr_ if optimizar_tat_referencia else None)
        # Toda propuesta se decodifica a un calendario antes de evaluarse.
        S = dec(pi, y, delta, tref)
        cur = best = self.calcular_costo(S)
        best_sol = (pi, y, delta, S)
        best_tref = tref
        ups = []
        # Muestra vecinos para calibrar la temperatura a la escala de la instancia.
        for _ in range(40):
            cand = self._move_in_list(pi, y)
            if cand is None:
                continue
            c = self.calcular_costo(dec(cand, y, delta, tref))
            if c > cur:
                ups.append(c - cur)
        # La temperatura inicial se calibra con empeoramientos observados en la
        # instancia, evitando una escala fija que se comporte distinto por avión.
        T0 = 0.1 * (np.mean(ups) if ups else max(1.0, 1e-3 * cur))
        t0 = time.time(); last = t0; it = acc = n_ls = 0
        hist = [(0.0, best)]
        last_ls = -1e9
        # ``best`` sigue al mejor individuo del SA; ``pol`` guarda una solución
        # pulida por búsqueda local. Se comparan al finalizar y se retorna la mejor.
        pol = (best, y, S)
        while (el := time.time() - t0) < presupuesto_tiempo and (limite is None or time.time() < limite):
            # Enfriamiento geométrico continuo: al agotar el presupuesto queda
            # aproximadamente en 0,5 % de la temperatura inicial.
            T = T0 * (0.005 ** (el / presupuesto_tiempo))
            r = self.aleatorio.random()
            # La propuesta parte del estado actual y una sola rama la modifica.
            npi, ny, nd, nt = pi, y, delta, tref
            # El modo por defecto conserva la mezcla histórica. Las dos variantes
            # experimentales concentran 50 % de propuestas en su vecindario
            # disyuntivo, sin eliminar del todo lista, delta y T_ref.
            list_p, disj_p = 0.65, 0.13
            if r < list_p:
                npi = self._move_in_list(pi, y)
                if npi is None:
                    continue
            elif r < list_p + disj_p and len(y):
                res = self._flip(pi, y)
                if res is None:
                    continue
                npi, ny = res
            elif r < 0.88 or not optimizar_tat_referencia:
                nd = int(np.clip(delta + self.aleatorio.choice([-1, 1]) * INTERVALOS_POR_TURNO, 0, 7 * INTERVALOS_POR_DIA))
            else:
                step = self.aleatorio.choice([-2, -1, 1, 2]) * INTERVALOS_POR_TURNO if self.aleatorio.random() < 0.8 else \
                    int(self.aleatorio.integers(lb, tmax)) - tref
                nt = int(np.clip(tref + step, lb, tmax))
            # Se decodifica desde cero para no heredar carga residual del estado previo.
            S2 = dec(npi, ny, nd, nt)
            c = self.calcular_costo(S2)
            it += 1
            # Las mejoras siempre se aceptan. Un empeoramiento puede aceptarse al
            # comienzo para escapar de mínimos locales; esa probabilidad cae con T.
            if c <= cur or self.aleatorio.random() < math.exp(-(c - cur) / max(T, 1e-9)):
                pi, y, delta, tref, cur = npi, ny, nd, nt, c
                acc += 1
                # Solo una mejora estricta reemplaza el mejor individuo conocido.
                if c < best - 1e-6:
                    best, best_sol, best_tref = c, (pi, y, delta, S2), tref
                    if periodo_busqueda_local is not None and el - last_ls > periodo_busqueda_local:
                        S3 = self.mejorar_localmente(S2, y, max_pasadas=2, mostrar_progreso=False, limite=limite)
                        c3 = self.calcular_costo(S3)
                        n_ls += 1
                        last_ls = time.time() - t0
                        if c3 < pol[0]:
                            pol = (c3, y, S3)
                    hist.append((el, min(best, pol[0])))
            if mostrar_progreso and time.time() - last > intervalo_log:
                last = time.time()
                print(f"  SA t={el:5.0f}s it={it:6d} acept={acc/it:4.0%} actual={cur:,.0f} mejor={min(best, pol[0]):,.0f} "
                      f"delta={delta/INTERVALOS_POR_HORA:.0f}h T_ref={tref/INTERVALOS_POR_HORA:.0f}h", flush=True)
        self.sa_info = dict(iteraciones=it, aceptadas=acc, historia=hist, busquedas_locales=n_ls,
                            T_ref_h=best_tref / INTERVALOS_POR_HORA)
        if pol[0] < best:
            return best_sol[0], pol[1], best_sol[2], pol[2]
        return best_sol

    # ---------- búsqueda local (C6) ----------
    # --- Pulido local de un calendario ---
    # Mantiene fija la orientación y prueba nuevas posiciones para cada hito entre
    # sus cotas factibles. Alterna el sentido de recorrido entre pasadas.
    def mejorar_localmente(self, S, y, max_pasadas=5, mostrar_progreso=True, limite=None):
        inst = self.instancia
        g = self.graph(y)
        S = S.copy()
        K, ns = inst.capacidad.shape
        # Aquí se modifica un calendario existente, por eso se reconstruye su carga.
        D, M = construir_estado(S, inst.duracion, inst.especialidad, inst.trabajadores,
                                K, ns, INTERVALOS_POR_TURNO)
        # Prefijo de horas ofrecidas para actualizar el costo de ociosidad cuando
        # un movimiento cambia el turno en que termina el calendario completo.
        offered_prefix = np.concatenate([[0], np.cumsum(8.0 * inst.capacidad.sum(axis=0))])

        def tat_cost(tat):
            c = COSTO_DIA_EXCESO_TAT * math.ceil(max(0, tat - inst.tat_objetivo) / INTERVALOS_POR_DIA)
            c += COSTO_OCIOSIDAD_HORA * offered_prefix[math.ceil(tat / INTERVALOS_POR_TURNO)]
            return c

        hitos = np.where(inst.especialidad >= 0)[0]
        for ps in range(max_pasadas):
            if limite is not None and time.time() >= limite:
                break
            moved = 0
            gain = 0.0
            # Alternar fin->inicio e inicio->fin reduce el sesgo de actualizar los
            # hitos siempre en el mismo orden dentro de una búsqueda tipo descenso.
            for i in hitos[np.argsort(-S[hitos])] if ps % 2 == 0 else hitos[np.argsort(S[hitos])]:
                if limite is not None and time.time() >= limite:
                    break
                ET, LT = cotas_sinteticas(g["topo"], S, inst.duracion, inst.especialidad, g["pptr"], g["pidx"],
                                          g["sptr"], g["sidx"], inst.horizonte)
                d, k, r = inst.duracion[i], inst.especialidad[i], inst.trabajadores[i]
                # Predecesores y sucesores delimitan el intervalo factible [lo, hi].
                preds = g["pidx"][g["pptr"][i]:g["pptr"][i + 1]]
                succs = g["sidx"][g["sptr"][i]:g["sptr"][i + 1]]
                lo = max([(S[p] + inst.duracion[p]) if inst.especialidad[p] >= 0 else ET[p] for p in preds], default=0)
                hi = min([S[v] if inst.especialidad[v] >= 0 else LT[v] for v in succs], default=inst.horizonte) - d
                if hi <= lo:
                    continue
                ends = S[hitos] + inst.duracion[hitos]
                other = np.max(np.where(hitos == i, -1, ends))
                cur_t = S[i]
                # Se retira temporalmente i para que cada posición se valore sin
                # contar simultáneamente su ubicación antigua y la candidata.
                aplicar(D, M, k, cur_t, d, -r, INTERVALOS_POR_TURNO)
                dws0, ds0 = costo_colocacion(D, M, inst.capacidad, k, cur_t, d, r, INTERVALOS_POR_TURNO)
                base = 800 * dws0 + self.w_slot * ds0 + tat_cost(max(other, cur_t + d))
                # Se prueban siempre los extremos y después los bordes interiores.
                cands = {lo, hi}
                for b in range((lo // INTERVALOS_POR_TURNO) * INTERVALOS_POR_TURNO,
                               hi + d + INTERVALOS_POR_TURNO, INTERVALOS_POR_TURNO):
                    for t in (b, b - d):
                        if lo <= t <= hi:
                            cands.add(t)
                best_t, best_c = cur_t, base
                for t in cands:
                    dws, ds = costo_colocacion(D, M, inst.capacidad, k, t, d, r, INTERVALOS_POR_TURNO)
                    c = 800 * dws + self.w_slot * ds + tat_cost(max(other, t + d))
                    if c < best_c - 1e-6:
                        best_t, best_c = t, c
                # La mejor posición se incorpora al estado usado por el siguiente hito.
                aplicar(D, M, k, best_t, d, r, INTERVALOS_POR_TURNO)
                if best_t != cur_t:
                    S[i] = best_t; moved += 1; gain += base - best_c
            # Tras mover hitos, los nodos sigma/phi se vuelven a ajustar a las
            # nuevas cotas tempranas; no representan trabajo ejecutable.
            ET, _ = cotas_sinteticas(g["topo"], S, inst.duracion, inst.especialidad, g["pptr"], g["pidx"],
                                     g["sptr"], g["sidx"], inst.horizonte)
            syn = inst.especialidad < 0
            S[syn] = ET[syn]
            if mostrar_progreso:
                print(f"  LS pasada {ps+1}: {moved} hitos movidos, ahorro estimado {gain:,.0f} -> costo {self.calcular_costo(S):,.0f}")
            if moved == 0:
                break
            if limite is not None and time.time() >= limite:
                break
        return S
