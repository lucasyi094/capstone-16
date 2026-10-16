"""Paso 1: construcción greedy y determinista del caso base estricto."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ..p01_configuracion import (
    COSTO_DIA_EXCESO_TAT,
    COSTO_OCIOSIDAD_HORA,
    COSTO_TRABAJADOR_TURNO_EXTRA,
    INTERVALOS_POR_DIA,
    INTERVALOS_POR_HORA,
    INTERVALOS_POR_TURNO,
)
from ..p02_instancia import cpm, csr, topo_order
from ..p03_operaciones_calendario import aplicar, construir_estado
from ..p05_evaluacion import evaluar, exportar, verificar_factibilidad


def fijar_orden_de_incompatibilidades(instancia):
    """Orienta cada no-paralelismo una vez, sin introducir ciclos."""
    inicios_tempranos, _, _, _ = cpm(instancia, instancia.arcos_base)
    decisiones = np.zeros(len(instancia.disyunciones), dtype=np.int64)
    arcos_actuales = instancia.arcos_base
    orden_pares = np.argsort(
        [
            min(inicios_tempranos[a_primero], inicios_tempranos[b_primero])
            for a_primero, _, b_primero, _ in instancia.disyunciones
        ]
    )

    for numero_par in orden_pares:
        a_primero, b_despues, b_primero, a_despues = instancia.disyunciones[
            numero_par
        ]
        opcion_preferida = (
            0
            if inicios_tempranos[a_primero] <= inicios_tempranos[b_primero]
            else 1
        )
        for opcion in (opcion_preferida, 1 - opcion_preferida):
            arco_nuevo = (
                np.array([[a_primero, b_despues]])
                if opcion == 0
                else np.array([[b_primero, a_despues]])
            )
            arcos_de_prueba = np.vstack([arcos_actuales, arco_nuevo])
            if topo_order(instancia.n_nodos, arcos_de_prueba) is not None:
                decisiones[numero_par] = opcion
                arcos_actuales = arcos_de_prueba
                break
        else:
            raise ValueError(
                "No se pudo ordenar una incompatibilidad sin crear un ciclo"
            )

    return decisiones


def crear_orden_fijo(instancia, arcos):
    """Crea un orden topológico fijo con desempate por ID de hito."""
    prioridad = np.arange(instancia.n_nodos, dtype=np.int64) + instancia.n_hitos
    posiciones = np.argsort(instancia.hitos["id"].to_numpy(), kind="stable")
    prioridad[posiciones] = np.arange(instancia.n_hitos)
    orden = topo_order(instancia.n_nodos, arcos, priority=prioridad)
    if orden is None:
        raise ValueError("Las restricciones forman un ciclo")
    punteros, predecesores = csr(instancia.n_nodos, arcos, "pred")
    return orden, punteros, predecesores


def calcular_extras_inevitables(instancia):
    """Calcula el exceso mínimo de hitos que no caben ni estando solos."""
    extras = np.zeros(instancia.n_nodos, dtype=np.int64)
    for hito in range(instancia.n_hitos):
        especialidad = instancia.especialidad[hito]
        demanda = instancia.trabajadores[hito]
        extras[hito] = max(0, demanda - instancia.capacidad[especialidad].max())
    return extras


def capacidad_en_intervalo(instancia, especialidad, inicio, duracion):
    turnos = np.arange(inicio, inicio + duracion) // INTERVALOS_POR_TURNO
    return instancia.capacidad[especialidad, turnos]


def respeta_capacidad(demanda_actual, instancia, hito, inicio, extra_permitido):
    especialidad = instancia.especialidad[hito]
    duracion = instancia.duracion[hito]
    trabajadores = instancia.trabajadores[hito]
    capacidad = capacidad_en_intervalo(
        instancia, especialidad, inicio, duracion
    )
    demanda_nueva = (
        demanda_actual[especialidad, inicio : inicio + duracion] + trabajadores
    )
    return bool(np.all(demanda_nueva <= capacidad + extra_permitido))


def calcular_costo_local(
    demanda_actual, maximos_actuales, instancia, hito, inicio, tat_actual
):
    """Calcula el costo incremental de una colocación en el plan parcial."""
    especialidad = instancia.especialidad[hito]
    duracion = instancia.duracion[hito]
    trabajadores = instancia.trabajadores[hito]
    fin = inicio + duracion
    primer_turno = inicio // INTERVALOS_POR_TURNO
    ultimo_turno = (fin - 1) // INTERVALOS_POR_TURNO
    extras_nuevos = 0
    uso_regular_nuevo = 0

    for turno in range(primer_turno, ultimo_turno + 1):
        comienzo_turno = turno * INTERVALOS_POR_TURNO
        fin_turno = comienzo_turno + INTERVALOS_POR_TURNO
        desde = max(inicio, comienzo_turno)
        hasta = min(fin, fin_turno)
        capacidad = instancia.capacidad[especialidad, turno]
        demanda_con_hito = (
            demanda_actual[especialidad, desde:hasta] + trabajadores
        )
        maximo_nuevo = max(
            maximos_actuales[especialidad, turno],
            int(demanda_con_hito.max()),
        )
        extras_nuevos += max(maximo_nuevo - capacidad, 0) - max(
            maximos_actuales[especialidad, turno] - capacidad, 0
        )
        uso_antes = np.minimum(
            demanda_actual[especialidad, desde:hasta], capacidad
        ).sum()
        uso_despues = np.minimum(demanda_con_hito, capacidad).sum()
        uso_regular_nuevo += int(uso_despues - uso_antes)

    turnos_antes = math.ceil(tat_actual / INTERVALOS_POR_TURNO)
    tat_nuevo = max(tat_actual, fin)
    turnos_despues = math.ceil(tat_nuevo / INTERVALOS_POR_TURNO)
    capacidad_nueva_hh = (
        instancia.capacidad[:, turnos_antes:turnos_despues].sum() * 8
    )
    costo_ociosidad = COSTO_OCIOSIDAD_HORA * (
        capacidad_nueva_hh - uso_regular_nuevo / INTERVALOS_POR_HORA
    )
    dias_antes = math.ceil(
        max(0, tat_actual - instancia.tat_objetivo) / INTERVALOS_POR_DIA
    )
    dias_despues = math.ceil(
        max(0, tat_nuevo - instancia.tat_objetivo) / INTERVALOS_POR_DIA
    )
    costo_tat = COSTO_DIA_EXCESO_TAT * (dias_despues - dias_antes)
    return (
        COSTO_TRABAJADOR_TURNO_EXTRA * extras_nuevos
        + costo_ociosidad
        + costo_tat
    )


def generar_candidatos(inicio_minimo, duracion, horizonte, tat_objetivo, eventos):
    candidatos = {inicio_minimo, tat_objetivo - duracion}
    for borde_turno in range(0, horizonte + 1, INTERVALOS_POR_TURNO):
        candidatos.add(borde_turno)
        candidatos.add(borde_turno - duracion)
    for evento in eventos:
        candidatos.add(evento)
        candidatos.add(evento - duracion)
    return sorted(
        candidato
        for candidato in candidatos
        if inicio_minimo <= candidato
        and candidato + duracion <= horizonte
    )


def buscar_primer_inicio_factible(
    demanda_actual, instancia, hito, inicio_minimo, extra_permitido
):
    ultimo_inicio = instancia.horizonte - instancia.duracion[hito]
    for inicio in range(inicio_minimo, ultimo_inicio + 1):
        if respeta_capacidad(
            demanda_actual, instancia, hito, inicio, extra_permitido
        ):
            return inicio
    nombre_hito = instancia.hitos.at[hito, "id"]
    raise ValueError(f"No existe un inicio factible para el hito {nombre_hito}")


def resolver_caso_base_estricto(instancia):
    """Construye, evalúa y valida el calendario greedy estricto."""
    decisiones = fijar_orden_de_incompatibilidades(instancia)
    arcos = instancia.arcos_para(decisiones)
    orden, punteros, predecesores = crear_orden_fijo(instancia, arcos)
    extras_inevitables = calcular_extras_inevitables(instancia)
    cantidad_especialidades, cantidad_turnos = instancia.capacidad.shape
    demanda_actual = np.zeros(
        (
            cantidad_especialidades,
            cantidad_turnos * INTERVALOS_POR_TURNO,
        ),
        dtype=np.int64,
    )
    maximos_actuales = np.zeros(
        (cantidad_especialidades, cantidad_turnos), dtype=np.int64
    )
    inicios = np.full(instancia.n_nodos, -1, dtype=np.int64)
    eventos_por_especialidad = [[] for _ in range(cantidad_especialidades)]
    tat_actual = 0

    for hito in orden:
        pred = predecesores[punteros[hito] : punteros[hito + 1]]
        inicio_minimo = max(
            (
                inicios[predecesor] + instancia.duracion[predecesor]
                for predecesor in pred
            ),
            default=0,
        )
        if instancia.especialidad[hito] < 0 or instancia.duracion[hito] == 0:
            inicios[hito] = inicio_minimo
            continue

        especialidad = instancia.especialidad[hito]
        duracion = instancia.duracion[hito]
        extra_permitido = extras_inevitables[hito]
        candidatos = generar_candidatos(
            inicio_minimo,
            duracion,
            instancia.horizonte,
            instancia.tat_objetivo,
            eventos_por_especialidad[especialidad],
        )
        factibles = [
            candidato
            for candidato in candidatos
            if respeta_capacidad(
                demanda_actual,
                instancia,
                hito,
                candidato,
                extra_permitido,
            )
        ]
        if not factibles:
            factibles = [
                buscar_primer_inicio_factible(
                    demanda_actual,
                    instancia,
                    hito,
                    inicio_minimo,
                    extra_permitido,
                )
            ]
        mejor_inicio = min(
            factibles,
            key=lambda candidato: (
                calcular_costo_local(
                    demanda_actual,
                    maximos_actuales,
                    instancia,
                    hito,
                    candidato,
                    tat_actual,
                ),
                candidato,
            ),
        )
        inicios[hito] = mejor_inicio
        aplicar(
            demanda_actual,
            maximos_actuales,
            especialidad,
            mejor_inicio,
            duracion,
            instancia.trabajadores[hito],
            INTERVALOS_POR_TURNO,
        )
        eventos_por_especialidad[especialidad].extend(
            [mejor_inicio, mejor_inicio + duracion]
        )
        tat_actual = max(tat_actual, mejor_inicio + duracion)

    metricas = evaluar(instancia, inicios)
    demanda_final, _ = construir_estado(
        inicios,
        instancia.duracion,
        instancia.especialidad,
        instancia.trabajadores,
        cantidad_especialidades,
        cantidad_turnos,
        INTERVALOS_POR_TURNO,
    )
    capacidad_extra = np.zeros_like(demanda_final)
    excepciones = []
    for hito in range(instancia.n_hitos):
        if extras_inevitables[hito] > 0:
            especialidad = instancia.especialidad[hito]
            inicio = inicios[hito]
            fin = inicio + instancia.duracion[hito]
            capacidad_extra[especialidad, inicio:fin] += extras_inevitables[hito]
            excepciones.append(
                {
                    "id": instancia.hitos.at[hito, "id"],
                    "nombre": instancia.hitos.at[hito, "name"],
                    "especialidad": instancia.especialidades[especialidad],
                    "trabajadores_extra_minimos": int(
                        extras_inevitables[hito]
                    ),
                }
            )
    capacidad_slots = np.repeat(
        instancia.capacidad, INTERVALOS_POR_TURNO, axis=1
    )
    violaciones_capacidad = int(
        np.count_nonzero(demanda_final > capacidad_slots + capacidad_extra)
    )
    return {
        "inicios": inicios,
        "metricas": metricas,
        "decisiones": decisiones,
        "excepciones": excepciones,
        "violaciones_capacidad": violaciones_capacidad,
    }


def guardar_resultado(instancia, resultado, carpeta_salida, nombre_metodo):
    """Exporta el calendario, sus KPI y las validaciones."""
    carpeta_salida = Path(carpeta_salida)
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    prefijo = carpeta_salida / f"{instancia.nombre}_{nombre_metodo}"
    np.save(str(prefijo) + ".npy", resultado["inicios"])
    exportar(instancia, resultado["inicios"], str(prefijo))
    reporte = {
        "instancia": instancia.nombre,
        "metodo": nombre_metodo,
        "num_violaciones_tecnicas": len(
            verificar_factibilidad(instancia, resultado["inicios"])
        ),
        "num_violaciones_capacidad_estricta": resultado[
            "violaciones_capacidad"
        ],
        "excepciones_intrinsecas": resultado["excepciones"],
        **resultado["metricas"],
    }
    with open(str(prefijo) + "_resumen.json", "w", encoding="utf-8") as archivo:
        json.dump(reporte, archivo, ensure_ascii=False, indent=2, default=float)
    return reporte
