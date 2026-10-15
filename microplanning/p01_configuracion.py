# Paso 1: parámetros globales del proyecto, unidades de tiempo, turnos y costos.

# Unidad de tiempo: 3 minutos. Es el máximo común divisor de las duraciones de
# los datos (hay hitos de 0,25 h, 0,30 h, 0,50 h, ...), así que no se redondea nada.
INTERVALOS_POR_HORA = 20
HORAS_POR_TURNO = 8
INTERVALOS_POR_TURNO = HORAS_POR_TURNO * INTERVALOS_POR_HORA  # 160
INTERVALOS_POR_DIA = 24 * INTERVALOS_POR_HORA                 # 480

# Orden de turnos dentro del día (supuesto del informe: mañana, tarde, noche)
ORDEN_TURNOS = {"M": 0, "T": 1, "N": 2}

# Costos (costs.csv)
COSTO_SOBRECARGA_HORA = 100.0  # USD por hora-hombre de sobrecarga
COSTO_OCIOSIDAD_HORA = 50.0    # USD por hora-hombre ociosa
COSTO_DIA_EXCESO_TAT = 8500.0  # USD por día de exceso sobre el TAT objetivo

# Regla del profesor: la sobrecarga se contrata por turno completo.
COSTO_TRABAJADOR_TURNO_EXTRA = COSTO_SOBRECARGA_HORA * HORAS_POR_TURNO  # 800 USD

# Días de capacidad agregados después de la ventana, por si el plan se pasa del
# TAT objetivo. SUPUESTO: se repite el patrón de los últimos 7 días de la ventana.
DIAS_EXTRA = 21
