# Paso 5: evaluación exacta, KPIs y verificación independiente de factibilidad.
import math
import os
import pandas as pd
from .p01_configuracion import (COSTO_DIA_EXCESO_TAT, COSTO_OCIOSIDAD_HORA,
                                COSTO_TRABAJADOR_TURNO_EXTRA, INTERVALOS_POR_DIA,
                                INTERVALOS_POR_HORA, INTERVALOS_POR_TURNO)
from .p03_operaciones_calendario import construir_estado, terminos_recursos


# --- Evaluación económica y operacional ---
# Calcula los KPIs y el costo final del calendario completo. Con idle_mode="window"
# se cobra ociosidad en toda la ventana contractual; con idle_mode="tat", solo en
# los turnos transcurridos hasta el TAT. La sobrecarga siempre se contrata por
# trabajador-turno completo.
def evaluar(instancia, inicios):
    # El último final de cualquier nodo define el TAT del calendario.
    cantidad_especialidades, cantidad_turnos = instancia.capacidad.shape
    finales = inicios + instancia.duracion
    tat = int(finales.max())
    if tat > instancia.horizonte:
        raise ValueError("El calendario se sale del horizonte extendido; aumentar DIAS_EXTRA")
    # Se reconstruye el perfil completo para no depender del estado del solucionador.
    demanda, maximos = construir_estado(
        inicios, instancia.duracion, instancia.especialidad, instancia.trabajadores,
        cantidad_especialidades, cantidad_turnos, INTERVALOS_POR_TURNO,
    )
    # Un TAT parcial consume igualmente el turno completo que contiene su final.
    turnos_tat = math.ceil(tat / INTERVALOS_POR_TURNO)
    xws, sobrecarga, uso_ventana, uso_tat, concurrencia = terminos_recursos(
        demanda, maximos, instancia.capacidad, INTERVALOS_POR_TURNO,
        instancia.n_turnos_ventana, turnos_tat,
    )
    # La capacidad está en trabajadores-turno; multiplicar por ocho la lleva a HH.
    oferta_ventana = 8.0 * instancia.capacidad[:, : instancia.n_turnos_ventana].sum()
    oferta_tat = 8.0 * instancia.capacidad[:, :turnos_tat].sum()
    ocio_ventana = oferta_ventana - uso_ventana / INTERVALOS_POR_HORA
    ocio_tat = oferta_tat - uso_tat / INTERVALOS_POR_HORA
    # Cualquier fracción de día fuera de la ventana activa un día completo de
    # penalización, según la definición del costo TAT del problema.
    dias_exceso = math.ceil(max(0, tat - instancia.tat_objetivo) / INTERVALOS_POR_DIA)
    costo_sobrecarga = COSTO_TRABAJADOR_TURNO_EXTRA * xws
    costo_tat = COSTO_DIA_EXCESO_TAT * dias_exceso
    costo_ociosidad = COSTO_OCIOSIDAD_HORA * ocio_tat
    cota_inferior = instancia.estadisticas["lb_precedences_h"] * INTERVALOS_POR_HORA
    return {
        "costo_total": costo_sobrecarga + costo_ociosidad + costo_tat,
        "costo_sobrecarga": costo_sobrecarga,
        "costo_ociosidad": costo_ociosidad,
        "costo_tat": costo_tat,
        "TAT_h": tat / INTERVALOS_POR_HORA,
        "dias_exceso": dias_exceso,
        "brecha_cota": tat / cota_inferior,
        "trabajadores_turno_extra": xws,
        "sobrecarga_hh_horaria": sobrecarga / INTERVALOS_POR_HORA,
        "ocio_hh_ventana": ocio_ventana,
        "ocio_hh_hasta_tat": ocio_tat,
        "concurrencia_max": concurrencia,
        "idle_mode": "tat",
    }


# --- Verificación independiente de restricciones ---
# Relee los CSV originales y comprueba precedencias y no-paralelismos sin confiar
# en el DAG construido por el solucionador. Así también detecta errores de carga.
def verificar_factibilidad(instancia, inicios):
    # Esta función vuelve a leer las restricciones. No confía en los arcos que
    # usó el solucionador, por lo que también detecta errores de preprocesamiento.
    errores = []
    hit = instancia.hitos
    hid = {u: i for i, u in enumerate(hit["id"])}
    st = inicios[: instancia.n_hitos]
    en = st + instancia.duracion[: instancia.n_hitos]
    if (inicios < 0).any():
        errores.append("inicios negativos")
    # Primero se revisan las relaciones directas entre hitos.
    seq = pd.read_csv(os.path.join(instancia.directorio_datos, f"task_milestones_sequence_requirements_{instancia.nombre}.csv"))
    for a, b in zip(seq.enabler, seq.enabled):
        if en[hid[a]] > st[hid[b]]:
            errores.append(f"prec hito {a}->{b}")
    # El intervalo de un grupo es la envolvente de todos sus hitos. Para grupos
    # vacíos se toman directamente sus nodos sintéticos sigma/phi.
    gint = {}
    for g in range(len(instancia.grupos)):
        m = instancia.miembros[g]
        if m:
            gint[g] = (st[m].min(), en[m].max())
        else:
            gint[g] = (inicios[instancia.sigma(g)], inicios[instancia.phi(g)])
    gi = {g: i for i, g in enumerate(instancia.grupos)}
    from .p02_instancia import _norm
    # Después se valida el orden de las envolventes temporales de los grupos.
    gs = pd.read_csv(os.path.join(instancia.directorio_datos, f"groups_sequence_requirements_{instancia.nombre}.csv"))
    for a, b in zip(gs.enabler.map(_norm), gs.enabled.map(_norm)):
        if gint[gi[a]][1] > gint[gi[b]][0]:
            errores.append(f"prec grupo {a}->{b}")
    # Dos intervalos se solapan si cada uno comienza antes de que termine el otro.
    gn = pd.read_csv(os.path.join(instancia.directorio_datos, f"groups_no_parallelism_requirements_{instancia.nombre}.csv"))
    for a, b in zip(gn.iloc[:, 0].map(_norm), gn.iloc[:, 1].map(_norm)):
        (s1, e1), (s2, e2) = gint[gi[a]], gint[gi[b]]
        if s1 < e2 and s2 < e1:
            errores.append(f"no-par grupo {a}|{b}")
    hn = pd.read_csv(os.path.join(instancia.directorio_datos, f"task_milestones_no_parallelism_requirements_{instancia.nombre}.csv"))
    for a, b in zip(hn.iloc[:, 0], hn.iloc[:, 1]):
        i, j = hid[a], hid[b]
        if st[i] < en[j] and st[j] < en[i]:
            errores.append(f"no-par hito {a}|{b}")
    return errores


# --- Exportación de resultados ---
# Genera tres vistas de una misma solución: hitos, intervalos de grupos y carga
# de trabajadores por especialidad y turno.
def exportar(instancia, inicios, prefijo):
    # Los tres archivos describen la misma solución desde perspectivas distintas:
    # tareas, intervalos agregados por grupo y utilización de trabajadores.
    n = instancia.n_hitos
    # Vista 1: una fila por hito con sus tiempos convertidos nuevamente a horas.
    h = instancia.hitos[["id", "name", "group", "skill", "tat", "worker_demand"]].copy()
    h["inicio_h"] = inicios[:n] / INTERVALOS_POR_HORA
    h["fin_h"] = (inicios[:n] + instancia.duracion[:n]) / INTERVALOS_POR_HORA
    h.sort_values("inicio_h").to_csv(prefijo + "_hitos.csv", index=False)
    # Vista 2: una fila por grupo usando la envolvente de todos sus miembros.
    filas = []
    for g, nombre in enumerate(instancia.grupos):
        miembros = instancia.miembros[g]
        a, b = ((inicios[miembros].min(), (inicios[miembros] + instancia.duracion[miembros]).max())
                if miembros else (inicios[instancia.sigma(g)], inicios[instancia.phi(g)]))
        filas.append((nombre, len(miembros), a / INTERVALOS_POR_HORA, b / INTERVALOS_POR_HORA))
    pd.DataFrame(filas, columns=["grupo", "n_hitos", "inicio_h", "fin_h"]).sort_values("inicio_h").to_csv(prefijo + "_grupos.csv", index=False)
    # Vista 3: demanda máxima, oferta y contratación por turno y especialidad.
    K, n_turnos = instancia.capacidad.shape
    demanda, maximos = construir_estado(inicios, instancia.duracion, instancia.especialidad,
                                         instancia.trabajadores, K, n_turnos, INTERVALOS_POR_TURNO)
    tat_turnos = math.ceil((inicios + instancia.duracion).max() / INTERVALOS_POR_TURNO)
    # Tres turnos por día: el cociente identifica el día y el residuo M/T/N.
    carga = [(instancia.especialidades[k], s // 3, "MTN"[s % 3], instancia.capacidad[k, s],
              maximos[k, s], max(maximos[k, s] - instancia.capacidad[k, s], 0),
              demanda[k, s * INTERVALOS_POR_TURNO:(s + 1) * INTERVALOS_POR_TURNO].sum() / INTERVALOS_POR_HORA)
             for k in range(K) for s in range(tat_turnos)]
    pd.DataFrame(carga, columns=["skill", "dia", "turno", "oferta", "demanda_max", "extra_contratado", "hh_demandadas"]).to_csv(prefijo + "_carga.csv", index=False)
    return demanda, maximos
