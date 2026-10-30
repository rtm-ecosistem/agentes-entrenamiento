"""Parámetros del experimento. Las claves vienen de variables de entorno (secrets de GitHub)."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"
ESTADO = DATOS / "estado.json"
DIARIOS = DATOS / "diarios"
OPERACIONES = DATOS / "operaciones.csv"
EVOLUCION = DATOS / "evolucion.json"  # las últimas sesiones minuto a minuto, para la ventana de gráficas
NOTICIAS = DATOS / "noticias.json"
FUENTES = DATOS / "fuentes.json"
FUNDAMENTALES = DATOS / "fundamentales.json"
ESTRATEGIAS = RAIZ / "estrategias"
PANEL_MD = RAIZ / "PANEL.md"
PANEL_DIR = RAIZ / "panel"
WEB_DIR = RAIZ / "web"

# Reglas del experimento (acordadas en la especificación).
# La plantilla de agentes está en estrategias/personas.json: 6 del equipo diario y 4 del intradía.
EQUIPOS = {"diario": "Equipo diario", "intradia": "Equipo intradía"}
CAPITAL_INICIAL = 10_000.0
MAX_PESO_POSICION = 0.20        # ninguna posición puede superar el 20 % del valor del agente
MAX_CAIDA = 0.10                # despido inmediato si cae más de un 10 % desde su máximo
DIAS_EVALUACION = 28            # cada 4 semanas debe superar al S&P 500


MARGEN_AVISO = 0.03             # quedar por detrás del S&P 500 por menos de 3 puntos es un aviso, no un despido
AVISOS_PARA_DESPIDO = 2         # dos avisos seguidos: despido
DIAS_EXTRA_POR_RACHA = 7        # cada evaluación superada seguida alarga el siguiente periodo una semana...
MAX_DIAS_EXTRA = 28             # ...hasta cuatro semanas más (un "contrato" más largo)


def dias_evaluacion(agente: dict) -> int:
    """El plazo de evaluación del agente: el suyo propio (los que copian fuentes lentas, como el 13F o los
    congresistas, tienen uno más largo) más el premio por evaluaciones superadas seguidas."""
    base = int(agente.get("dias_evaluacion") or DIAS_EVALUACION)
    return base + min(MAX_DIAS_EXTRA, DIAS_EXTRA_POR_RACHA * int(agente.get("racha", 0)))
MINIMO_ORDEN_USD = 50.0
# Radar de oportunidades: empresas fuera de la lista que los analistas ven en las noticias
MAX_RADAR = 5                   # como mucho 5 por informe
MAX_PESO_RADAR = 0.05           # y como mucho un 5 % de la cartera en cada una
MIN_PRECIO_RADAR = 5.0          # nada de "chicharros" de menos de 5 $
MIN_VOLUMEN_RADAR_USD = 500_000  # negociado medio diario en IEX (una parte pequeña del total) en los últimos 30 días
DESLIZAMIENTO = 0.0005          # 0,05 % de coste por operación, para ser realistas
REFERENCIA = "SPY"
# Control de riesgo (además de los anteriores)
MAX_ORDENES_DIA = 8             # órdenes como máximo en cada decisión
MAX_COMPRAS_DIA = 0.50          # no puede comprar en un día más del 50 % del valor de su cartera
EFECTIVO_AVISO = 0.30           # al equipo diario con más de un 30 % en efectivo se le recuerda lo que cuesta esperar
# Estrategias de la plantilla revisadas: los agentes que ya trabajan con ellas pasan una vez a la versión nueva
# del archivo (la fecha distingue una revisión de la siguiente)
ESTRATEGIAS_REVISADAS = {"rebote": "2026-10-29", "noticias_rapidas": "2026-10-29"}
MAX_PERDIDA_DIA_INTRADIA = 0.03  # un intradía que pierde un 3 % en el día lo cierra todo y para hasta mañana
MINUTOS_SIN_COMPRAS = 75        # el intradía no abre posiciones nuevas en la última hora y cuarto
MINUTOS_CIERRE_INTRADIA = 45    # y lo cierra todo cuando quedan 45 minutos o menos
MINUTOS_ENTRE_DECISIONES = 50   # el intradía decide más o menos una vez por hora
# Media hora antes del cierre, el agente diario con una acción que presenta resultados esa noche (tras el cierre o
# antes de abrir el siguiente día de bolsa) decide si la mantiene, la reduce o la vende. Va en la ejecución del
# intradía de las 15:30 de Nueva York y, si esa falla, en la de las 15:45. Más tarde no: la orden llegaría cerrada la bolsa
MINUTOS_ANTES_RESULTADOS = 35
MINUTOS_ULTIMA_DECISION_RESULTADOS = 8
# Eventos que mueven el mercado (hora de Nueva York): desde la hora del evento (o desde la primera ronda, si es antes
# de abrir) y durante MINUTOS_EVENTO, el intradía decide en cada ronda (cada 15 minutos) en vez de una vez por hora.
# Calendario oficial (federalreserve.gov y bls.gov); los analistas pueden añadir otros grandes que vean en las noticias.
_FED = "decisión de tipos de la Reserva Federal"
_IPC = "dato de inflación (IPC) de EE. UU."
_EMPLEO = "dato de empleo de EE. UU."
_PPI = "precios al productor (PPI) de EE. UU."
_VENTAS = "ventas minoristas de EE. UU."
_PCE = "inflación PCE y PIB de EE. UU."
EVENTOS = [
    ("2026-10-28T14:00", _FED),
    ("2026-11-06T08:30", _EMPLEO), ("2026-11-10T08:30", _IPC), ("2026-11-13T08:30", _PPI),
    ("2026-11-17T08:30", _VENTAS), ("2026-11-25T08:30", _PCE),
    ("2026-12-04T08:30", _EMPLEO), ("2026-12-09T14:00", _FED), ("2026-12-10T08:30", _IPC),
    ("2026-12-15T08:30", _PPI), ("2026-12-16T08:30", _VENTAS), ("2026-12-23T08:30", _PCE),
    # 2027: fechas provisionales de la Reserva Federal (las de IPC y empleo, cuando el BLS publique su calendario)
    ("2027-01-27T14:00", _FED), ("2027-03-17T14:00", _FED), ("2027-04-28T14:00", _FED), ("2027-06-09T14:00", _FED),
    ("2027-07-28T14:00", _FED), ("2027-09-15T14:00", _FED), ("2027-10-27T14:00", _FED), ("2027-12-08T14:00", _FED),
]
MAX_EVENTOS_ANALISTAS = 3       # eventos que pueden añadir los analistas en cada informe
# Los resultados de estas empresas mueven todo el mercado: con fecha confirmada (calendario de Nasdaq) son un evento,
# el mismo día si es antes de abrir o el siguiente día de bolsa si es tras el cierre
GIGANTES = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "GOOGL": "Google", "AMZN": "Amazon",
            "META": "Meta", "TSLA": "Tesla", "AVGO": "Broadcom"}
MINUTOS_EVENTO = 60
MINUTOS_ENTRE_DECISIONES_EVENTO = 10
MAX_RIESGO_OPERACION = 0.01     # el intradía no puede arriesgar más del 1 % de su cartera hasta su stop
MIN_BENEFICIO_RIESGO = 1.0      # y su objetivo debe estar al menos tan lejos como su stop
# Si los agentes leen su ánimo antes de decidir (False: solo se calcula y se ve en la web)
EMOCIONES_EN_DECISION = os.environ.get("EMOCIONES_EN_DECISION", "1").strip() != "0"
# «Si fuera real»: dinero de verdad repartido entre los puestos y comisión de un bróker barato por operación
CAPITAL_REAL = 1000.0
COMISION_REAL = 1.0
DIAS_INACTIVIDAD_DIARIO = 5     # decisiones seguidas sin operar antes del aviso
DIAS_INACTIVIDAD_INTRADIA = 1   # días de bolsa enteros sin comprar antes del aviso
STOP_INTRADIA_DEFECTO = 0.015   # si una compra del intradía llega sin stop válido, se le pone a un 1,5 %
# Stop del equipo diario: lo elige el agente en cada compra; lo vigila la ronda intradía de cada media hora
STOP_DIARIO_MIN = 0.04
STOP_DIARIO_MAX = 0.15
STOP_DIARIO_DEFECTO = 0.08
DIAS_NOTICIAS = 30               # cuántos días de noticias se guardan
MAX_NOTICIAS_CONSULTA = 200      # máximo de noticias nuevas por consulta

# Lo que pueden operar: acciones grandes y ETFs muy líquidos de EE. UU.
UNIVERSO = [
    # Fondos (ETF) amplios, de tamaño y de bonos
    "SPY", "QQQ", "DIA", "VTI", "IWM", "MDY", "IJR", "TLT", "IEF", "HYG",
    # Fondos de sectores y temáticos
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC", "SMH", "XBI", "ITA", "KRE",
    "ARKK", "TAN", "URA", "LIT",
    # Fondos de otros países y regiones
    "VEA", "VGK", "EWG", "EWJ", "VWO", "FXI", "INDA", "EWZ",
    # Materias primas y bitcoin
    "GLD", "SLV", "USO", "UNG", "IBIT",
    # Tecnología, software y semiconductores
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "AVGO", "ORCL", "CRM", "ADBE", "NOW", "INTU", "IBM", "CSCO",
    "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "TSM", "ASML", "ARM", "MRVL", "ANET", "DELL",
    "SMCI", "PANW", "CRWD", "ZS", "NET", "DDOG", "SNOW", "SHOP", "TTD",
    # Internet, ocio y comunicación
    "NFLX", "DIS", "SPOT", "UBER", "ABNB", "BKNG", "RBLX", "DUOL", "T", "VZ", "TMUS",
    # Bancos, pagos y fintech
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "AXP", "V", "MA", "PYPL", "COIN", "HOOD", "SOFI",
    "AFRM", "NU", "BRK.B",
    # Salud, farmacéuticas y biotecnología
    "LLY", "NVO", "UNH", "JNJ", "ABBV", "MRK", "PFE", "AMGN", "GILD", "VRTX", "REGN", "BMY", "ISRG", "TMO",
    "DHR", "CVS", "MRNA", "HIMS",
    # Energía (petróleo, gas, renovables y nuclear) y eléctricas
    "XOM", "CVX", "COP", "OXY", "SLB", "EOG", "FSLR", "ENPH", "NEE", "CEG", "VST", "CCJ",
    # Industria, transporte y defensa
    "CAT", "DE", "GE", "HON", "BA", "LMT", "RTX", "NOC", "GD", "ETN", "UPS", "FDX",
    # Consumo
    "TSLA", "WMT", "COST", "HD", "LOW", "TGT", "PG", "KO", "PEP", "MCD", "SBUX", "CMG", "NKE", "LULU", "MAR",
    "RIVN",
    # Materiales y minería
    "LIN", "FCX", "NEM", "ALB", "MP",
    # Jóvenes de alto crecimiento (salieron a bolsa hace poco o crecen muy rápido): IA, espacio, cuántica, nuclear
    "PLTR", "APP", "CRWV", "TEM", "SOUN", "RKLB", "ASTS", "LUNR", "JOBY", "ACHR", "IONQ", "RGTI", "QBTS",
    "OKLO", "SMR", "CAVA", "RDDT", "CRCL",
]

# Proveedor de IA: "gemini" (gratis) o "claude" (de pago)
PROVEEDOR_IA = os.environ.get("PROVEEDOR_IA", "gemini").strip() or "gemini"
MODELO_GEMINI = os.environ.get("MODELO_GEMINI", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
MODELO_GEMINI_LIGERO = os.environ.get("MODELO_GEMINI_LIGERO", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
MODELO_CLAUDE = os.environ.get("MODELO_CLAUDE", "claude-haiku-5-5").strip() or "claude-haiku-5-5"

# Las fuentes del Copión (13F de la SEC) y de la del Congreso piden identificarse
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip() or \
    "agentes-inversion 340233035+rtm-ecosistem@users.noreply.github.com"

# Si es "1", las órdenes también se envían a la cuenta de práctica de Alpaca (para verlas allí)
ESPEJO_ALPACA = os.environ.get("ESPEJO_ALPACA", "1") == "1"


def secreto(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"Falta la variable {nombre}. Añádela en GitHub → Settings → Secrets and variables → Actions.")
    return valor
