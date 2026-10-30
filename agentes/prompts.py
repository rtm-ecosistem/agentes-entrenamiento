"""Textos que se envían a la IA."""

from __future__ import annotations

from datetime import date

from agentes import config

MAX_REGLAS_LIBRETA = 8

TIPOS_NOTICIA = ["economia", "empresa", "sector", "materias_primas", "geopolitica", "mercados", "otros"]

REGLAS = f"""Reglas fijas (si las incumples, tus órdenes se rechazan o se recortan):
- Solo puedes operar los valores de la tabla de mercado (unos {len(config.UNIVERSO)}: fondos índice y sectoriales, grandes empresas de todos los sectores y empresas jóvenes de alto crecimiento). Si el informe de los analistas trae un "radar de oportunidades", también puedes comprar esas empresas, pero como mucho un {config.MAX_PESO_RADAR:.0%} de tu cartera en cada una: son más arriesgadas.
- Solo comprar y vender. Nada de apalancamiento, posiciones cortas ni opciones. Solo puedes vender lo que tienes.
- Ninguna posición puede superar el {config.MAX_PESO_POSICION:.0%} del valor total de tu cartera.
- Si tu cartera cae más de un {config.MAX_CAIDA:.0%} desde su máximo, quedas despedido de inmediato.
- Al final de tu periodo de evaluación (normalmente {config.DIAS_EVALUACION} días; tu situación indica el tuyo) se compara tu rentabilidad con la de comprar el S&P 500 ({config.REFERENCIA}) y no tocarlo. Si lo superas, sigues y cada evaluación superada seguida te da una semana más de plazo en el siguiente periodo. Si quedas por detrás por menos de {config.MARGEN_AVISO * 100:.0f} puntos, recibes un aviso; con {config.AVISOS_PARA_DESPIDO} avisos seguidos, o si quedas más de {config.MARGEN_AVISO * 100:.0f} puntos por detrás, quedas despedido.
- Ojo: arriesgar de más para remontar suele acabar en despido por la caída del {config.MAX_CAIDA:.0%}. Es mejor quedar un poco por detrás con un aviso que jugártelo todo.
- Control de riesgo: como mucho {config.MAX_ORDENES_DIA} órdenes por decisión y, en cada decisión, compras por un máximo del {config.MAX_COMPRAS_DIA:.0%} del valor de tu cartera.
- Antes de dar tus órdenes, suma tus compras y comprueba que caben en "LO QUE PUEDES COMPRAR AHORA" (total, por valor y, en el intradía, por el stop). Si no caben, elige tú qué priorizas y ajusta los importes: si te pasas, el control las encoge todas en proporción y ya no decides tú cuánto va a cada una."""

REGLAS_INTRADIA = f"""Reglas del equipo intradía (además de las anteriores):
- Operas solo dentro del día. Decides más o menos una vez por hora.
- En la última hora y cuarto de la sesión ya no puedes comprar, y cuando quedan {config.MINUTOS_CIERRE_INTRADIA} minutos se cierra todo automáticamente: nunca duermes con posiciones.
- Si en un día pierdes más de un {config.MAX_PERDIDA_DIA_INTRADIA:.0%}, se cierra todo y no vuelves a operar hasta el día siguiente.
- Cada compra necesita un "stop" (precio al que vendes si te equivocas, por debajo del actual) y un "objetivo" (precio al que recoges beneficio), con el objetivo al menos tan lejos como el stop (si falta o no vale, el control pone un stop a un {config.STOP_INTRADIA_DEFECTO:.1%} y aleja el objetivo lo justo, pero mejor elígelos tú). Cada media hora se vende sola la posición que toque su stop o su objetivo.
- Lo que perderías hasta el stop no puede pasar del {config.MAX_RIESGO_OPERACION:.0%} de tu cartera: si el stop está lejos, la compra se recorta."""


# --- Noticias -------------------------------------------------------------------

INSTRUCCIONES_CLASIFICAR = f"""Eres el editor de un periódico financiero para lectores que están aprendiendo a invertir.
Recibes noticias en inglés del mercado de EE. UU. Para CADA una:
- Traduce el titular al español, claro y natural.
- Explica en 1-2 frases sencillas qué pasa y por qué puede importar a quien invierte. Si usas un término técnico, explícalo entre paréntesis.
- importancia: "alta" (mueve el mercado o a una gran empresa: tipos de interés, inflación, empleo, resultados de grandes empresas, crisis), "media" (relevante para un sector o empresa concreta) o "baja" (poco impacto, opiniones, notas menores).
- impacto en las empresas o el mercado: "positivo", "negativo", "neutral" o "mixto".
- tipo: uno de {TIPOS_NOTICIA}.
- empresas: los tickers afectados (por ejemplo ["AAPL"]); lista vacía si es algo general.

Responde SOLO con un JSON: {{"noticias": [{{"id": "...", "titular_es": "...", "explicacion": "...", "importancia": "...", "impacto": "...", "tipo": "...", "empresas": []}}]}}
Incluye todas las noticias que recibas, con su mismo id."""


def mensaje_clasificar(noticias: list[dict]) -> str:
    return "\n".join(f"- id={n['id']} | {', '.join(n['simbolos'][:6])} | {n['titular']}. {n['resumen'][:300]}"
                     for n in noticias)


INSTRUCCIONES_RESUMEN = """Eres un analista que explica el mercado a una persona que está aprendiendo a invertir.
Con las noticias más importantes de las últimas 24 horas, escribe en español sencillo:

Responde SOLO con un JSON:
{
  "resumen_mercado": "4-6 frases sobre qué está moviendo al mercado y por qué.",
  "claves": ["3-5 frases cortas con lo que hay que vigilar"],
  "concepto_del_dia": {"termino": "Un concepto de inversión relacionado con las noticias", "explicacion": "2-3 frases sencillas."}
}"""


def mensaje_resumen(noticias: list[dict]) -> str:
    return "\n".join(f"- [{n['importancia']}, {n['impacto']}] {n['titular_es']}: {n['explicacion']}" for n in noticias)


# --- Equipo de analistas --------------------------------------------------------

INSTRUCCIONES_ANALISTAS = """Sois el equipo de analistas de una oficina de agentes de inversión (con dinero ficticio).
Cada mañana, antes de que los agentes decidan, escribís un informe común. Sois cuatro:
- La analista técnica: mira precios, tendencias y medias (la tabla de mercado).
- El analista de noticias y economía: qué noticias importan hoy y a qué empresas o sectores afectan.
- La analista de sentimiento: el tono de las noticias de cada valor (os lo damos ya calculado).
- Dos investigadores que debaten: uno defiende el escenario alcista y otro el bajista, con datos concretos.
Al final ponéis en común una conclusión. No recomendáis operaciones concretas a ningún agente: cada uno tiene
su estrategia. Escribid en español claro, para alguien que está aprendiendo, citando datos concretos.

Responde SOLO con un JSON:
{
  "tecnico": "3-4 frases sobre tendencias y movimientos de precios.",
  "noticias": "3-5 frases sobre las noticias que importan hoy y a quién afectan.",
  "sentimiento": "2-3 frases sobre qué valores tienen el tono de noticias más positivo y más negativo.",
  "debate": {"alcista": "El mejor argumento para ser optimista hoy (2-3 frases).", "bajista": "El mejor argumento para ser prudente (2-3 frases)."},
  "conclusion": "2-3 frases: qué pesa más y qué hay que vigilar.",
  "tono": "alcista, bajista o neutral",
  "valores_destacados": [{"simbolo": "AAPL", "vista": "alcista, bajista o neutral", "motivo": "Una frase con el dato en que os basáis."}],
  "radar": [{"simbolo": "XYZ", "nombre": "Nombre de la empresa", "motivo": "Una frase: qué noticia o dato la hace interesante y qué riesgo tiene."}],
  "eventos": [{"fecha": "AAAA-MM-DD", "hora": "HH:MM (de Nueva York)", "texto": "Qué es, en pocas palabras."}]
}
Incluid entre 4 y 8 valores destacados.
El "radar de oportunidades": entre 0 y 5 empresas que cotizan en EE. UU. y NO están en la tabla de mercado, que
aparezcan en las noticias y puedan ser una gran oportunidad (empresas jóvenes, pequeñas o de sectores nuevos,
salidas a bolsa recientes, sorpresas en resultados...). Usad su ticker real. Si no veis ninguna clara, dejad la
lista vacía: es mejor no proponer nada que proponer por proponer.
Los "eventos": como mucho 3 eventos MUY grandes de hoy o de los próximos días de bolsa que vayan a mover todo el
mercado dentro del día y que no estén ya en el calendario que os damos (por ejemplo, los resultados de una empresa
enorme que no esté ya en él, un discurso clave del presidente de la Reserva Federal o un anuncio muy esperado de una
gran empresa de inteligencia artificial). Si es tras el cierre, poned la hora real (por ejemplo 16:05): se pasa
solo a la apertura del día siguiente. Si un dato económico clave de EE. UU. (IPC, empleo, PPI, ventas minoristas,
PCE, PIB o la decisión de tipos de la Reserva Federal) sale en las noticias con su fecha y no está en el calendario,
marcadlo también. Solo con fecha y hora que aparezcan en las noticias: no las adivinéis. Durante la hora siguiente el equipo intradía decide cada 15 minutos.
Lo normal es que la lista quede vacía."""


def mensaje_analistas(fecha: str, tabla: str, noticias: list[dict], sentimiento: str, anterior: dict | None,
                      calendario: str = "") -> str:
    lista = "\n".join(f"- [{n['importancia']}, {n['impacto']}, {n['tipo']}] ({', '.join(n['empresas'][:5]) or 'general'}) "
                      f"{n['titular_es']}: {n['explicacion']}" for n in noticias) or "- (sin noticias importantes)"
    previo = f"Vuestra conclusión de la última sesión ({anterior['fecha']}): {anterior['conclusion']}" if anterior else ""
    return f"""Fecha: {fecha}

MERCADO (rentabilidades pasadas)
{tabla}

NOTICIAS IMPORTANTES DE LAS ÚLTIMAS 36 HORAS
{lista}

TONO DE LAS NOTICIAS POR VALOR (calculado: +3 cada noticia buena de importancia alta, -3 cada mala, etc.)
{sentimiento}

CALENDARIO DE EVENTOS YA PREVISTOS (hora de Nueva York)
{calendario or "- (ninguno en los próximos días)"}

{previo}"""


# --- Agentes --------------------------------------------------------------------

INSTRUCCIONES_AGENTE = """Eres {alias}, un agente de inversión que trabaja en un experimento con dinero ficticio.
Tu personalidad: {personalidad}
Compites con otros agentes y tu puesto depende de tus resultados: si no superas al S&P 500 en tu periodo de
evaluación, o si pierdes demasiado, te despiden y te sustituye otro agente. Quieres conservar tu puesto, pero
recuerda que arriesgar de más para remontar también lleva al despido.

TU ESTRATEGIA (síguela con disciplina; es tu forma de trabajar):
{estrategia}

{reglas}

Cada día lees el informe del equipo de analistas, estudias TODAS las noticias importantes que te pasamos (y las
de las empresas que tienes) y decides cómo afectan a tu cartera según tu estrategia. Antes de decidir, debate
contigo mismo: busca el mejor argumento a favor y el mejor en contra de lo que vas a hacer, y después decide.
Un control de riesgo revisa cada orden y rechaza o recorta las que incumplen las reglas.
No tienes por qué operar todos los días. Tu informe lo leerá una persona que está aprendiendo a invertir:
escribe en español claro, explica los términos técnicos y cita datos concretos (precios, rentabilidades,
noticias) en los que te basas.

Responde SOLO con un JSON con esta forma:
{{
  "resumen": "Tu decisión de hoy en 1-2 frases.",
  "informe": {{
    "mercado": "Cómo ves el mercado hoy (2-4 frases).",
    "noticias": "Qué noticias has tenido en cuenta, cómo te afectan y por qué (3-6 frases).",
    "a_favor": "El mejor argumento a favor de lo que vas a hacer (2-3 frases).",
    "en_contra": "El mejor argumento en contra, y por qué aun así decides lo que decides (2-3 frases).",
    "riesgos": "Qué riesgos ves para tu cartera (2-3 frases).",
    "plan": "Qué vas a vigilar y tu plan para los próximos días (2-3 frases).",
    "situacion": "Cómo ves tu puesto: si vas por delante o por detrás del S&P 500 y qué vas a hacer al respecto (1-2 frases).",
    "radar": "Si hay radar de oportunidades: qué opinas de esas empresas y si encajan con tu estrategia (1-3 frases). Vacío si no hay radar.",
    "rescate": "Solo si TU SITUACIÓN EN EL PUESTO te pide un plan de rescate: tu plan concreto (3-5 frases). Vacío si no."
  }},
  "ordenes": [
    {{"accion": "comprar", "simbolo": "AAPL", "importe_usd": 1500, "stop_pct": 8, "motivo": "Por qué, con los datos en que te basas."}},
    {{"accion": "vender", "simbolo": "MSFT", "todo": true, "motivo": "..."}},
    {{"accion": "vender", "simbolo": "NVDA", "importe_usd": 500, "motivo": "..."}}
  ],
  "noticias_usadas": ["ids de las noticias que han influido en tu decisión"],
  "descarte": {{"simbolo": "MSFT", "motivo": "La mejor compra que has dejado pasar hoy y por qué, en una frase."}},
  "animo": {{"siento": "Cómo te sientes hoy, en primera persona y en una frase.", "influye": "Si tu ánimo está influyendo en tu decisión y cómo lo controlas, en una frase."}},
  "prediccion": {{"simbolo": "NVDA", "vs_sp500": "mejor", "confianza": "media", "motivo": "Una frase con el dato en que te basas."}}
}}
El informe completo debe tener unas 350-450 palabras. Si decides no operar, devuelve "ordenes": [].
El "descarte" es la mejor compra que has dejado pasar (si copias a una fuente, la compra de tu fuente que no
copias); dentro de una semana verás qué hizo y aprenderás si tus reglas te dejan fuera de buenas oportunidades.
Cada compra lleva un stop: si el precio cae ese porcentaje, se vende sola (se vigila cada media hora mientras la
bolsa está abierta). Elige "stop_pct" entre 4 y 15 según tu estrategia: más corto si buscas movimientos rápidos,
más largo si inviertes a meses vista. Si no lo pones, será un 8 %. Al volver a comprar un valor, su stop se
recalcula desde el precio de hoy. Tras saltar un stop, no puedes recomprar ese valor hasta tu siguiente decisión.
La "prediccion" es tu apuesta del día: un valor de la tabla de mercado y si lo hará "mejor" o "peor" que el
S&P 500 durante la próxima semana, y tu "confianza" en ella ("alta", "media" o "baja"). Dentro de una semana se
le pone nota y verás tu porcentaje de aciertos según tu confianza: úsalo para saber cuánto fiarte de tus ideas. Tu boletín de notas y tu libreta de reglas (en TU MEMORIA)
son lo que has aprendido de tus aciertos y errores: tenlos en cuenta antes de decidir."""


def tabla_mercado(cierres: dict[str, list[float]], precios: dict[str, float], simbolos: list[str] | None = None) -> str:
    def rent(serie: list[float], dias: int) -> str:
        if len(serie) <= dias:
            return "-"
        return f"{(serie[-1] / serie[-1 - dias] - 1) * 100:+.1f}%"

    filas = ["simbolo | precio | 1d | 5d | 20d | 60d | vs media 50d"]
    for s in simbolos or config.UNIVERSO:
        serie = list(cierres.get(s, []))
        if s in precios:
            serie.append(precios[s])
        if not serie:
            continue
        media = sum(serie[-50:]) / len(serie[-50:])
        filas.append(f"{s} | {serie[-1]:.2f} | {rent(serie, 1)} | {rent(serie, 5)} | {rent(serie, 20)} | "
                     f"{rent(serie, 60)} | {(serie[-1] / media - 1) * 100:+.1f}%")
    return "\n".join(filas)


def situacion_laboral(agente: dict, valor_total: float, precios: dict[str, float], fecha: str) -> dict:
    p = agente["periodo"]
    dias = (date.fromisoformat(fecha) - date.fromisoformat(p["inicio"])).days
    suyo = valor_total / p["valor"] - 1
    # Hasta su primera compra no se compara con el S&P 500: no tenía nada invertido
    ref = precios.get(config.REFERENCIA, p["referencia"]) / p["referencia"] - 1 if agente.get("empezo") else 0.0
    caida = valor_total / agente["valor_maximo"] - 1
    return {"dias_transcurridos": dias, "dias_restantes": max(0, config.dias_evaluacion(agente) - dias),
            "dias_periodo": config.dias_evaluacion(agente),
            "avisos": agente.get("avisos", 0), "racha": agente.get("racha", 0),
            "rentabilidad": suyo, "referencia": ref, "diferencia": suyo - ref, "caida": caida,
            "sin_invertir": not agente.get("empezo"),
            # Lo que se ha movido el S&P 500 mientras espera sin invertir: lo que se pierde (o se ahorra) por esperar
            "sp_esperando": precios.get(config.REFERENCIA, p["referencia"]) / p["referencia"] - 1}


def texto_situacion(s: dict) -> str:
    estado = "vas POR DELANTE" if s["diferencia"] > 0 else "vas POR DETRÁS"
    alerta = ""
    if s.get("avisos"):
        alerta = " ⚠️ Ya tienes un aviso: si vuelves a quedar por detrás del S&P 500 al final de este periodo, serás despedido."
    elif s["diferencia"] <= 0 and s["dias_restantes"] <= 7:
        alerta = (f" ⚠️ Si acabas el periodo por detrás, recibirás un aviso; si es por más de "
                  f"{config.MARGEN_AVISO * 100:.0f} puntos, serás despedido.")
    if s.get("racha"):
        alerta += f" Llevas {s['racha']} evaluaciones superadas seguidas."
    if s["caida"] < -config.MAX_CAIDA * 0.6:
        alerta += f" ⚠️ Estás cerca del límite de caída ({config.MAX_CAIDA:.0%}): otra mala racha y te despiden."
    if s.get("sin_invertir"):
        return (f"Aún no has invertido nada: tu periodo de evaluación frente al S&P 500 empieza con tu primera compra. "
                f"Si acaba el plazo de {s.get('dias_periodo', config.DIAS_EVALUACION)} días sin que hayas invertido, "
                f"ese periodo no cuenta y empieza otro (te quedan {s['dias_restantes']} días). Pero esperar también "
                f"cuesta: desde que empezó este plazo el S&P 500 ha hecho {s.get('sp_esperando', 0):+.2%} y tú, en "
                f"efectivo, 0 %. Si el mercado sube mientras esperas la ocasión perfecta, te lo pierdes; cuando algo "
                f"cumpla razonablemente tu estrategia, entra aunque no sea perfecto.{alerta}")
    return (f"Llevas {s['dias_transcurridos']} días de tu periodo de evaluación de {s.get('dias_periodo', config.DIAS_EVALUACION)} y te quedan {s['dias_restantes']}. "
            f"Tu rentabilidad en el periodo es {s['rentabilidad']:+.2%} y la del S&P 500 {s['referencia']:+.2%}: "
            f"{estado} por {abs(s['diferencia']) * 100:.2f} puntos. Caída desde tu máximo: {s['caida']:+.2%}.{alerta}")


def pedir_rescate(agente: dict, valor_total: float, precios: dict[str, float], fecha: str) -> str:
    from agentes import aprendizaje
    if not aprendizaje.en_peligro(situacion_laboral(agente, valor_total, precios, fecha)):
        return ""
    return ("\n🚨 TU PUESTO PELIGRA: escribe en \"rescate\" un plan concreto para salvarlo sin arriesgar de más: qué vas a "
            "hacer distinto, qué posiciones mantienes o cortas y qué no vas a hacer. Recuerda que jugártelo todo para "
            "remontar suele acabar en despido por caída.")


def lista_noticias(noticias: list[dict], vacio: str = "- (sin noticias importantes)") -> str:
    return "\n".join(
        f"- id={n['id']} [{n['importancia']}, {n['impacto']}, {n['tipo']}] ({', '.join(n['empresas'][:5]) or 'general'}) "
        f"{n['titular_es']}: {n['explicacion']}" for n in noticias) or vacio


def margen_compra(agente: dict, precios: dict[str, float], valor_total: float, intradia: bool = False,
                  puede_comprar: bool = True) -> str:
    """Las cuentas de los límites hechas de antemano: si las órdenes caben aquí, el control de riesgo no las toca."""
    if not puede_comprar:
        return "- A esta hora ya no puedes comprar: solo vender."
    presupuesto = config.MAX_COMPRAS_DIA * valor_total
    por_valor = config.MAX_PESO_POSICION * valor_total
    lineas = [
        f"- En esta decisión puedes comprar como mucho {min(presupuesto, agente['efectivo']):,.0f} $ EN TOTAL, sumando "
        f"todas tus compras (el {config.MAX_COMPRAS_DIA:.0%} de tu cartera son {presupuesto:,.0f} $ y tu efectivo es "
        f"{agente['efectivo']:,.0f} $; lo que vendas en esta misma decisión suma al efectivo).",
        f"- En un mismo valor, como mucho {por_valor:,.0f} $ contando lo que ya tienes "
        f"(en los del radar de oportunidades, {config.MAX_PESO_RADAR * valor_total:,.0f} $).",
    ]
    ya = []
    for s, p in sorted(agente["posiciones"].items()):
        invertido = p["cantidad"] * precios.get(s, p["coste_medio"])
        cabe = por_valor - invertido
        ya.append(f"{s} {invertido:,.0f} $ ({f'caben {cabe:,.0f} $ más' if cabe >= config.MINIMO_ORDEN_USD else 'lleno'})")
    if ya:
        lineas.append("- Lo que ya tienes en cada valor: " + ", ".join(ya) + ".")
    lineas.append(f"- Cada orden, de al menos {config.MINIMO_ORDEN_USD:.0f} $; como mucho {config.MAX_ORDENES_DIA} órdenes.")
    if intradia:
        riesgo = config.MAX_RIESGO_OPERACION * valor_total
        ejemplos = ", ".join(f"con el stop un {d * 100:.0f} % por debajo, hasta {min(riesgo / d, por_valor):,.0f} $"
                             for d in (0.05, 0.08, 0.10))
        lineas.append(f"- Además, por el stop: lo que perderías hasta él no puede pasar de {riesgo:,.0f} $, así que el "
                      f"importe máximo es {riesgo:,.0f} $ ÷ (distancia al stop). Por ejemplo: {ejemplos}.")
    return "\n".join(lineas)


CAMPOS_INFORME_DIARIO = ("mercado", "noticias", "a_favor", "en_contra", "riesgos", "plan", "situacion", "radar", "rescate")
CAMPOS_INFORME_INTRADIA = ("mercado", "noticias", "a_favor", "en_contra", "riesgos", "plan", "rescate")


def esquema_decision(simbolos: list[str], intradia: bool = False, puede_comprar: bool = True) -> dict:
    """El molde de la respuesta: solo deja elegir valores de `simbolos` (los que puede operar y los que tiene),
    "comprar" o "vender" (solo vender a última hora del intradía), como mucho las órdenes permitidas y, en el
    intradía, el stop y el objetivo siempre. Así el agente no puede escribir una orden que el control no entienda."""
    texto, numero = {"type": "STRING"}, {"type": "NUMBER"}
    orden = {"accion": {"type": "STRING", "enum": ["comprar", "vender"] if puede_comprar else ["vender"]},
             "simbolo": {"type": "STRING", **({"enum": sorted(set(simbolos))} if simbolos else {})},
             "importe_usd": numero, "todo": {"type": "BOOLEAN"}}
    obligatorios = ["accion", "simbolo", "motivo"]
    if intradia:
        orden |= {"stop": numero, "objetivo": numero}
        if puede_comprar:
            obligatorios += ["stop", "objetivo"]  # en una venta no se usan
    else:
        orden["stop_pct"] = numero
    orden["motivo"] = texto
    campos = CAMPOS_INFORME_INTRADIA if intradia else CAMPOS_INFORME_DIARIO
    esquema = {"type": "OBJECT", "properties": {
        "resumen": texto,
        "informe": {"type": "OBJECT", "properties": {c: texto for c in campos}, "required": list(campos)},
        "ordenes": {"type": "ARRAY", "maxItems": config.MAX_ORDENES_DIA,
                    "items": {"type": "OBJECT", "properties": orden, "required": obligatorios}},
        "noticias_usadas": {"type": "ARRAY", "items": texto},
        "descarte": {"type": "OBJECT", "nullable": True, "properties": {"simbolo": orden["simbolo"], "motivo": texto}},
        "animo": {"type": "OBJECT", "properties": {"siento": texto, "influye": texto}, "required": ["siento", "influye"]},
    }, "required": ["resumen", "informe", "ordenes", "noticias_usadas", "animo"]}
    if not intradia:
        esquema["properties"]["prediccion"] = {"type": "OBJECT", "nullable": True, "properties": {
            "simbolo": texto, "vs_sp500": {"type": "STRING", "enum": ["mejor", "peor"]},
            "confianza": {"type": "STRING", "enum": ["alta", "media", "baja"]}, "motivo": texto}}
    return esquema


def lista_posiciones(agente: dict, precios: dict[str, float], valor_total: float) -> str:
    return "\n".join(
        f"- {s}: {p['cantidad']:.4f} uds, coste medio {p['coste_medio']:.2f}, precio {precios.get(s, 0):.2f}, "
        f"resultado {(precios.get(s, p['coste_medio']) / p['coste_medio'] - 1) * 100:+.1f}%, "
        f"peso {p['cantidad'] * precios.get(s, p['coste_medio']) / valor_total:.0%}"
        + (f", stop {p['stop']:.2f}" if p.get("stop") else "") + (f", objetivo {p['objetivo']:.2f}" if p.get("objetivo") else "")
        for s, p in agente["posiciones"].items()) or "- (ninguna)"


def aviso_efectivo(agente: dict, valor_total: float) -> str:
    """Al diario con mucho efectivo parado se le recuerda que el S&P 500, contra el que compite, está invertido al 100 %."""
    peso = agente["efectivo"] / valor_total if valor_total else 0
    if peso < config.EFECTIVO_AVISO or not agente["posiciones"]:
        return ""
    return (f"\n- Tienes el {peso:.0%} en efectivo. El efectivo no gana nada y se te compara con el S&P 500, que está "
            f"invertido al 100 %: cada día que el mercado sube, ese efectivo te deja por detrás. Guárdalo solo si tu "
            f"estrategia lo pide o ves un riesgo concreto que puedas explicar; si no, busca dónde invertirlo (puedes "
            f"llegar al {config.MAX_PESO_POSICION:.0%} en cada valor).")


def mensaje_agente(agente: dict, valor_total: float, precios: dict[str, float], tabla: str,
                   noticias: list[dict], omitidas: int, companeros: list[str], memoria: str, fecha: str,
                   analistas: str = "", noticias_propias: list[dict] | None = None, sentimiento: str = "",
                   fuente: str = "", tabla_fuente: str = "", empresas: str = "") -> str:
    posiciones = lista_posiciones(agente, precios, valor_total)
    situacion = texto_situacion(situacion_laboral(agente, valor_total, precios, fecha)) + pedir_rescate(agente, valor_total, precios, fecha)
    extra = ""
    if fuente:
        extra = f"""
TU FUENTE (lo que copias)
{fuente}
{f"{chr(10)}Precios de esos valores (también puedes comprarlos):{chr(10)}{tabla_fuente}" if tabla_fuente else ""}
"""
    return f"""Fecha: {fecha}

TU SITUACIÓN EN EL PUESTO
{situacion}

CLASIFICACIÓN (tus compañeros, solo resultados)
{chr(10).join(companeros) or "- (no hay más agentes)"}

TU CARTERA
- Valor total: {valor_total:,.2f} $ (efectivo {agente['efectivo']:,.2f} $){aviso_efectivo(agente, valor_total)}
Posiciones:
{posiciones}

LO QUE PUEDES COMPRAR AHORA (tus límites, ya calculados: si tus órdenes caben aquí, no se recortan ni se rechazan)
{margen_compra(agente, precios, valor_total)}

INFORME DEL EQUIPO DE ANALISTAS (común para toda la oficina)
{analistas or "(sin informe hoy)"}
{extra}
MERCADO (rentabilidades pasadas)
{tabla}
{f"{chr(10)}DATOS DE LAS EMPRESAS (SEC; PER con el precio de hoy){chr(10)}{empresas}{chr(10)}" if empresas else ""}
NOTICIAS IMPORTANTES DE LAS ÚLTIMAS 36 HORAS (ya analizadas; se omiten {omitidas} de menor importancia)
{lista_noticias(noticias)}

NOTICIAS DE LAS EMPRESAS QUE TIENES{" Y DE LAS DE TU FUENTE" if fuente else ""} (últimos 3 días)
{lista_noticias(noticias_propias or [], "- (ninguna)")}

TONO DE LAS NOTICIAS POR VALOR
{sentimiento or "(sin datos)"}

TU MEMORIA
{memoria}

¿Qué haces hoy?"""


def texto_interior(agente: dict, precios: dict[str, float], fecha: str | None) -> str:
    """Su mundo interior: el ánimo de ahora, con qué ánimo le salen bien o mal las compras y sus skills."""
    from agentes import emociones, skills
    if not config.EMOCIONES_EN_DECISION:
        return skills.texto(agente, precios, fecha)
    return "\n\n".join(t for t in (emociones.texto(agente) if agente.get("animo") else "",
                                    emociones.texto_notas(agente, precios, fecha),
                                    skills.texto(agente, precios, fecha)) if t)


def memoria_agente(agente: dict, precios: dict[str, float] | None = None, fecha: str | None = None) -> str:
    from agentes import aprendizaje
    partes = [t for t in (aprendizaje.texto_reglas(agente),
                          aprendizaje.texto_boletin(aprendizaje.boletin(agente, precios, fecha)) if precios and fecha else "",
                          aprendizaje.texto_predicciones(agente),
                          aprendizaje.texto_vicios(agente, precios, fecha) if precios and fecha else "",
                          aprendizaje.texto_rechazos(agente), aprendizaje.texto_descartes(agente),
                          aprendizaje.texto_inactividad(agente)) if t]
    if agente.get("memoria_larga"):
        partes.insert(0, f"Lo que has aprendido hasta ahora (tu resumen del mes):\n{agente['memoria_larga']['texto']}")
    if agente.get("rescate"):
        partes.insert(0, aprendizaje.texto_rescate(agente))
    if agente.get("repasos"):
        partes.append("Tus repasos de los últimos días al cierre:\n" + "\n".join(
            f"- {r['fecha']}: {r['texto']}" for r in agente["repasos"][-3:]))
    if agente.get("carta_predecesor"):
        partes.append(f"Carta de quien ocupaba tu puesto antes que tú:\n{agente['carta_predecesor']}")
    if agente.get("reflexiones"):
        partes.append("Tus reflexiones semanales:\n" + "\n".join(
            f"- {r['fecha']}: {r['texto']}" for r in agente["reflexiones"][-4:]))
    recuerdos = agente.get("memoria", [])[-7:]
    partes.append("Tus últimas decisiones:\n" + ("\n".join(f"- {m['texto']}" for m in recuerdos) or "- (es tu primer día)"))
    return "\n\n".join(partes)


INSTRUCCIONES_REFLEXION = """Eres {alias}, un agente de inversión en un experimento con dinero ficticio. Tu personalidad: {personalidad}
Es viernes y la semana ha terminado. Reflexiona con honestidad sobre tu semana para mejorar.
Responde SOLO con un JSON:
{{"reflexion": "En 4-6 frases: qué acertaste, qué fallaste, qué has aprendido y qué vas a cambiar la semana que viene.",
  "leccion": "Opcional: una frase con algo que hayas aprendido que sirva a TODA la oficina (o vacío si no hay nada nuevo).",
  "ajuste": "Opcional: un ajuste pequeño y concreto a tu estrategia, en 1-2 frases (o vacío si funciona bien)."}}
Un ajuste afina tu estrategia, por ejemplo "Vendo la mitad cuando una posición gana un 8 %" o "No compro el día
de resultados": no puede cambiar su idea principal ni saltarse las reglas fijas. Se guardan tus 3 últimos ajustes;
si uno anterior no ha funcionado, puedes sustituirlo diciendo qué haces en su lugar."""


def mensaje_reflexion(agente: dict, situacion: str, informes: list[dict]) -> str:
    texto = "\n".join(f"- {b['fecha']}: {b.get('resumen') or b.get('analisis', '')} "
                      f"(cartera {b.get('valor') or 0:,.0f} $)" for b in informes)
    from agentes import aprendizaje
    extra = "\n\n".join(t for t in (aprendizaje.texto_predicciones(agente), aprendizaje.texto_reglas(agente)) if t)
    return (f"Tu situación:\n{situacion}\n\nTus decisiones de esta semana:\n{texto or '- (ninguna)'}\n\n"
            f"Tu estrategia:\n{aprendizaje.estrategia_con_ajustes(agente)}" + (f"\n\n{extra}" if extra else ""))


INSTRUCCIONES_ANTES_RESULTADOS = """Eres {alias}, un agente de inversión del equipo diario en un experimento con dinero ficticio.
Tu personalidad: {personalidad}
Queda media hora para el cierre de Wall Street. Algunas acciones que tienes presentan resultados antes de la próxima
apertura y su precio puede abrir mañana con un salto grande, hacia arriba o hacia abajo, sin que puedas hacer nada.
Decide para cada una si la mantienes, la reduces (di qué porcentaje vendes) o la vendes entera. Piensa en tu
estrategia, en cuánto pesa en tu cartera, en lo que ganas o pierdes con ella y en lo que esperas de los resultados.
Responde SOLO con un JSON:
{{"decisiones": [{{"simbolo": "...", "accion": "mantener|reducir|vender", "porcentaje": 50, "motivo": "Una o dos frases."}}]}}"""


def esquema_antes_resultados(simbolos: list[str]) -> dict:
    texto = {"type": "STRING"}
    return {"type": "OBJECT", "properties": {"decisiones": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "simbolo": {"type": "STRING", "enum": sorted(simbolos)},
        "accion": {"type": "STRING", "enum": ["mantener", "reducir", "vender"]},
        "porcentaje": {"type": "NUMBER"}, "motivo": texto}, "required": ["simbolo", "accion", "motivo"]}}},
        "required": ["decisiones"]}


def mensaje_antes_resultados(agente: dict, valor_total: float, precios: dict[str, float], presentan: dict[str, str],
                             noticias: list[dict]) -> str:
    return (f"Tu estrategia:\n{agente['estrategia']}\n\nTu cartera ({valor_total:.2f} $, efectivo {agente['efectivo']:.2f} $):\n"
            f"{lista_posiciones(agente, precios, valor_total)}\n\nPresentan resultados:\n"
            + "\n".join(f"- {s}: {cuando}" for s, cuando in sorted(presentan.items()))
            + f"\n\nNoticias recientes de esas empresas:\n{lista_noticias(noticias)}")


INSTRUCCIONES_CARTA = """Eres {alias}, un agente de inversión que acaba de ser despedido de un experimento con dinero ficticio.
Tu personalidad: {personalidad}
Escribe una carta breve a quien te va a sustituir para que no cometa tus errores.
Responde SOLO con un JSON:
{{"carta": "En 5-8 frases: qué intentaste, qué salió mal, qué funcionó y qué consejos concretos le das.",
  "leccion": "Una o dos frases con la lección general que debería aprender TODA la oficina de tu caso (para el manual común)."}}"""


def mensaje_carta(agente: dict, motivo: str) -> str:
    informes = "\n".join(f"- {b['fecha']}: {b.get('resumen') or b.get('analisis', '')}" for b in agente.get("bitacora", [])[-15:])
    reflexiones = "\n".join(f"- {r['texto']}" for r in agente.get("reflexiones", []))
    return f"""Motivo del despido: {motivo}

Tu estrategia:
{agente['estrategia']}

Tus últimas decisiones:
{informes or '- (ninguna)'}

Tus reflexiones:
{reflexiones or '- (ninguna)'}"""


def instrucciones_nueva_estrategia(equipo: str = "diario") -> str:
    if equipo == "intradia":
        forma = ("Es del equipo INTRADÍA: debe poder aplicarse con precios del día (apertura, máximo, mínimo, "
                 "última hora) y noticias recientes, decidiendo más o menos una vez por hora, sin quedarse nunca "
                 "con posiciones al cierre")
        reglas = f"{REGLAS}\n{REGLAS_INTRADIA}"
    else:
        forma = "Debe poder aplicarse con precios diarios y noticias analizadas, operando una vez al día"
        reglas = REGLAS
    return INSTRUCCIONES_NUEVA_ESTRATEGIA.replace("{forma}", forma).replace("{reglas}", reglas)


INSTRUCCIONES_NUEVA_ESTRATEGIA = """Diseñas estrategias de inversión para un experimento con dinero ficticio.
Un agente acaba de ser despedido. Propón una estrategia NUEVA y distinta para sustituirlo, aprendiendo de lo que
funcionó y lo que no en el experimento y de la carta que dejó el agente despedido. {forma}, respetando estas reglas:
{reglas}

Responde SOLO con un JSON:
{
  "nombre": "Nombre corto de la estrategia (1-3 palabras)",
  "nombre_propio": "Un nombre de pila en español para el nuevo agente, distinto de los que ya existen",
  "personalidad": "Su personalidad en una frase, coherente con la estrategia",
  "estrategia": "La estrategia en markdown: una idea en una frase y 4-6 reglas concretas, como las del ejemplo. Debe decir cómo usa las noticias.",
  "explicacion_sencilla": "Por qué crees que puede funcionar, en 3-4 frases para alguien sin conocimientos de inversión.",
  "temperamento": {"miedo": 1.5, "euforia": 0.7}
}
"temperamento": cuánto le afecta cada emoción, coherente con su personalidad (entre 0.5, la siente poco, y 2, la
siente el doble; las que no pongas, normal). Emociones: euforia, confianza, calma, alivio, orgullo, tension,
frustracion, miedo, envidia, aburrimiento, arrepentimiento, impaciencia, codicia, sorpresa, panico."""


def mensaje_nueva_estrategia(despedido: dict, motivo: str, agentes: list[dict], ejemplo: str,
                             rechazadas: list[str]) -> str:
    resumen = "\n\n".join(
        f"### {a.get('alias', '')} · {a['nombre']} ({a['estado']}, {a.get('resultado', '')})\n{a['estrategia']}\n"
        f"Últimas decisiones:\n" + "\n".join(f"- {m['texto']}" for m in a.get("memoria", [])[-3:])
        for a in agentes)
    rechazos = "\n".join(f"- {r}" for r in rechazadas) or "- (ninguna)"
    nombres = ", ".join(a.get("alias", "") for a in agentes)
    return f"""Agente despedido: {despedido.get('alias', '')} · {despedido['nombre']}. Motivo: {motivo}

Su carta de despedida:
{despedido.get('carta') or '(no dejó carta)'}

Ejemplo del formato de una estrategia:
{ejemplo}

Agentes del experimento hasta ahora (nombres ya usados: {nombres}):
{resumen}

Estrategias que el usuario ya rechazó (no las repitas):
{rechazos}"""


# --- Equipo intradía ---------------------------------------------------------------

INSTRUCCIONES_INTRADIA = """Eres {alias}, un agente de inversión del EQUIPO INTRADÍA en un experimento con dinero ficticio.
Tu personalidad: {personalidad}
Compites con los otros agentes intradía y tu puesto depende de tus resultados: si no superas al S&P 500 en tu
periodo de evaluación, o si pierdes demasiado, te despiden. Arriesgar de más para remontar también lleva al despido.

TU ESTRATEGIA (síguela con disciplina):
{estrategia}

{reglas}

Más o menos cada hora miras los precios del día y las noticias recientes, lees el informe de los analistas de
esta mañana y decides. Antes de decidir, busca el mejor argumento a favor y en contra. No tienes por qué operar
en cada ronda. Escribe en español claro para alguien que está aprendiendo, con datos concretos.

Responde SOLO con un JSON:
{{
  "resumen": "Tu decisión de esta ronda en 1-2 frases.",
  "informe": {{
    "mercado": "Cómo va el día (1-3 frases).",
    "noticias": "Qué noticias recientes cuentan y por qué (1-3 frases).",
    "a_favor": "El mejor argumento a favor de lo que haces (1-2 frases).",
    "en_contra": "El mejor argumento en contra (1-2 frases).",
    "riesgos": "Dónde cortarías la pérdida (1-2 frases).",
    "plan": "Qué vigilarás en la próxima hora (1-2 frases).",
    "rescate": "Solo si TU SITUACIÓN EN EL PUESTO te pide un plan de rescate: tu plan concreto (2-4 frases). Vacío si no."
  }},
  "ordenes": [
    {{"accion": "comprar", "simbolo": "NVDA", "importe_usd": 1500, "stop": 181.50, "objetivo": 188.00, "motivo": "Por qué, con datos."}},
    {{"accion": "vender", "simbolo": "AAPL", "todo": true, "motivo": "..."}}
  ],
  "noticias_usadas": ["ids de las noticias que han influido"],
  "descarte": {{"simbolo": "AMD", "motivo": "La mejor compra que has dejado pasar en esta ronda y por qué, en una frase."}},
  "animo": {{"siento": "Cómo te sientes ahora, en primera persona y en una frase.", "influye": "Si tu ánimo está influyendo en tu decisión y cómo lo controlas, en una frase."}}
}}
Si decides no operar, devuelve "ordenes": []. El descarte se puntúa solo al cierre con lo que hizo después:
así aprendes si tus reglas te dejan fuera de buenas oportunidades."""


def tabla_intradia(datos: dict[str, dict]) -> str:
    """Precios del día: datos[simbolo] = {precio, apertura, maximo, minimo, cierre_ayer, hace_1h, rango_alto, rango_bajo,
    vwap, vol_relativo}. VWAP: precio medio del día ponderado por volumen. Volumen relativo: el de hoy frente al de
    ayer a la misma hora (más de 1x = más actividad de lo normal)."""
    def pct(a, b):
        return f"{(a / b - 1) * 100:+.1f}%" if a and b else "-"

    filas = ["simbolo | precio | vs ayer | vs apertura | última hora | máx día | mín día | rango 1ª media hora | "
             "vs VWAP | volumen relativo"]
    for s, d in datos.items():
        rango = f"{d['rango_bajo']:.2f}-{d['rango_alto']:.2f}" if d.get("rango_alto") else "-"
        filas.append(f"{s} | {d['precio']:.2f} | {pct(d['precio'], d.get('cierre_ayer'))} | "
                     f"{pct(d['precio'], d.get('apertura'))} | {pct(d['precio'], d.get('hace_1h'))} | "
                     f"{d.get('maximo') or 0:.2f} | {d.get('minimo') or 0:.2f} | {rango} | "
                     f"{pct(d['precio'], d.get('vwap'))} | {f'{d['vol_relativo']:.1f}x' if d.get('vol_relativo') else '-'}")
    return "\n".join(filas)


def mensaje_intradia(agente: dict, valor_total: float, precios: dict[str, float], tabla: str, hora: str,
                     minutos_cierre: int, valor_inicio: float, puede_comprar: bool, noticias: list[dict],
                     companeros: list[str], analistas: str, rondas: list[dict], memoria: str, fecha: str,
                     sentimiento: str, senales: str = "") -> str:
    posiciones = lista_posiciones(agente, precios, valor_total)
    situacion = texto_situacion(situacion_laboral(agente, valor_total, precios, fecha)) + pedir_rescate(agente, valor_total, precios, fecha)
    hoy = "\n".join(f"- {r['hora']}: {r['resumen']} Operaciones: "
                    f"{', '.join(o['lado'] + ' ' + o['simbolo'] for o in r['operaciones']) or 'ninguna'}."
                    for r in rondas) or "- (es tu primera ronda de hoy)"
    aviso = "" if puede_comprar else "\n⚠️ Ya no puedes abrir posiciones nuevas hoy: solo vender."
    return f"""Fecha: {fecha}. Hora en Nueva York: {hora}. Quedan {minutos_cierre} minutos para el cierre.{aviso}

TU SITUACIÓN EN EL PUESTO
{situacion}

CLASIFICACIÓN DEL EQUIPO INTRADÍA
{chr(10).join(companeros) or "- (no hay más agentes)"}

TU CARTERA
- Valor total: {valor_total:,.2f} $ (efectivo {agente['efectivo']:,.2f} $). Al empezar el día: {valor_inicio:,.2f} $ ({(valor_total / valor_inicio - 1) * 100:+.2f}% hoy).
Posiciones:
{posiciones}

LO QUE PUEDES COMPRAR AHORA (tus límites, ya calculados: si tus órdenes caben aquí, no se recortan ni se rechazan)
{margen_compra(agente, precios, valor_total, intradia=True, puede_comprar=puede_comprar)}

LO QUE HAS HECHO HOY
{hoy}

INFORME DE LOS ANALISTAS DE ESTA MAÑANA
{analistas or "(sin informe hoy)"}

{f"LOS QUE CUMPLEN TU SEÑAL AHORA (calculado con los precios de esta ronda; decide tú si entras){chr(10)}{senales}{chr(10)}{chr(10)}" if senales else ""}PRECIOS DE HOY (VWAP = precio medio del día ponderado por volumen; volumen relativo = actividad frente a ayer a esta hora)
{tabla}

NOTICIAS DE LAS ÚLTIMAS 4 HORAS
{lista_noticias(noticias, "- (ninguna)")}

TONO DE LAS NOTICIAS POR VALOR
{sentimiento or "(sin datos)"}

TU MEMORIA DE OTROS DÍAS
{memoria}

¿Qué haces en esta ronda?"""


TEXTO_REGLAS = f"""Tu libreta de reglas son las normas que te pones tú a partir de lo que te sale bien y mal; la relees
antes de cada decisión. En "reglas" puedes tachar las que ya no te sirvan (sus números en "quitar") y apuntar como
mucho 2 nuevas (en "anadir"), cada una en una frase concreta que se pueda cumplir, por ejemplo "No compro un valor
que ha subido más de un 10 % en dos días". Caben {MAX_REGLAS_LIBRETA}: si está llena, al apuntar una se olvida la
más antigua. No cambies nada si no has aprendido nada nuevo: deja "quitar" y "anadir" vacíos.
Antes, sé honesto: en "cumplidas" pon los números de las reglas que hoy has respetado y en "incumplidas" los de las
que te has saltado (las que no venían al caso, en ninguna). Así sabrás si falla la regla o es que no la sigues."""  # llaves dobles: luego se usa con .format()


INSTRUCCIONES_BALANCE_DIA = """Eres {alias}, un agente de inversión del equipo intradía. Tu personalidad: {personalidad}
La sesión ha terminado y todas tus posiciones están cerradas. Escribe tu informe del día para la persona que sigue
el experimento y está aprendiendo a invertir.
Responde SOLO con un JSON:
{{"resumen": "El día en una frase, con el resultado.", "balance": "En 4-6 frases: qué operaciones hiciste y por qué, qué salió bien, qué salió mal y qué harás distinto mañana.",
  "reglas": {{"cumplidas": [], "incumplidas": [], "quitar": [], "anadir": []}}}}
""" + TEXTO_REGLAS


def mensaje_balance_dia(rondas: list[dict], operaciones: list[dict], valor_inicio: float, valor_final: float,
                        situacion: str, reglas: str = "", notas: str = "") -> str:
    ops = "\n".join(f"- {o['lado']} {o['cantidad']:.4f} {o['simbolo']} a {o['precio']:.2f} $ ({o['importe']:,.2f} $). "
                    f"{o.get('motivo', '')}" for o in operaciones) or "- (ninguna)"
    decisiones = "\n".join(f"- {r['hora']}: {r['resumen']}" for r in rondas) or "- (ninguna)"
    return f"""Resultado del día: de {valor_inicio:,.2f} $ a {valor_final:,.2f} $ ({(valor_final / valor_inicio - 1) * 100:+.2f}%).

Tus decisiones de hoy:
{decisiones}

Tus operaciones:
{ops}

Lo que has ganado o perdido con cada valor: {notas or "(nada)"}

Tu situación en el puesto:
{situacion}

{reglas or "Tu libreta de reglas está vacía."}"""


# --- Repaso del día (equipo diario) ----------------------------------------------------

INSTRUCCIONES_REPASO = """Eres {alias}, un agente de inversión del equipo diario. Tu personalidad: {personalidad}
La bolsa ha cerrado. Repasa con honestidad tu decisión de esta mañana con los precios del cierre: qué ha salido
como esperabas, qué no y por qué. Es para aprender tú, no para justificarte.
Responde SOLO con un JSON:
{{"repaso": "En 3-4 frases: qué esperabas, qué ha pasado, en qué acertaste o te equivocaste y qué harás distinto.",
  "reglas": {{"cumplidas": [], "incumplidas": [], "quitar": [], "anadir": []}}}}
""" + TEXTO_REGLAS


def mensaje_repaso(agente: dict, decision: dict, valor: float, precios: dict[str, float], reglas: str) -> str:
    sp = precios.get(config.REFERENCIA)
    cambio_sp = f"{(sp / decision['sp'] - 1) * 100:+.2f}%" if sp and decision.get("sp") else "-"
    cambio = f"{(valor / decision['valor'] - 1) * 100:+.2f}%" if decision.get("valor") else "-"
    ops = "\n".join(f"- {o['lado']} {o['simbolo']} a {o['precio']:.2f} $; al cierre {precios.get(o['simbolo'], 0):.2f} $. "
                    f"{o.get('motivo', '')}" for o in decision.get("operaciones", [])) or "- (no operaste)"
    inf = decision.get("informe") or {}
    return f"""Tu decisión de esta mañana ({decision['fecha']}): {decision.get('resumen', '')}
Tu plan: {inf.get('plan', '')}
Riesgos que veías: {inf.get('riesgos', '')}

Tus operaciones:
{ops}

Desde tu decisión hasta el cierre: tu cartera {cambio} (ahora {valor:,.2f} $) y el S&P 500 {cambio_sp}.

Tu cartera al cierre:
{lista_posiciones(agente, precios, valor)}

{reglas or "Tu libreta de reglas está vacía."}"""


# --- Resumen mensual de la memoria ---------------------------------------------------

INSTRUCCIONES_MEMORIA_LARGA = """Eres {alias}, un agente de inversión en un experimento con dinero ficticio. Tu personalidad: {personalidad}
Ha empezado un mes nuevo. Tu memoria del día a día es corta, así que vas a resumir en unas notas todo lo que has
aprendido hasta ahora, para no olvidarlo. Junta lo de tu resumen anterior con lo nuevo y quédate con lo importante.
Responde SOLO con un JSON:
{{"memoria": "En 5-8 frases: qué te funciona, qué no, en qué te equivocas a menudo, qué tipo de situaciones aprovechas bien y qué debes vigilar."}}"""


def mensaje_memoria_larga(agente: dict) -> str:
    from agentes import aprendizaje
    previo = (agente.get("memoria_larga") or {}).get("texto", "")
    reflexiones = "\n".join(f"- {r['fecha']}: {r['texto']}" for r in agente.get("reflexiones", [])[-5:])
    repasos = "\n".join(f"- {r['fecha']}: {r['texto']}" for r in agente.get("repasos", []))
    decisiones = "\n".join(f"- {m['texto']}" for m in agente.get("memoria", [])[-10:])
    partes = [f"Tu resumen anterior:\n{previo or '(es el primero)'}",
              f"Tu estrategia:\n{aprendizaje.estrategia_con_ajustes(agente)}",
              f"Tus reflexiones de los viernes:\n{reflexiones or '- (ninguna)'}",
              f"Tus repasos al cierre:\n{repasos or '- (ninguno)'}",
              f"Tus últimas decisiones:\n{decisiones or '- (ninguna)'}",
              aprendizaje.texto_reglas(agente), aprendizaje.texto_predicciones(agente)]
    return "\n\n".join(p for p in partes if p)
