"""Equipo de analistas común: escribe un informe en cada recogida de noticias (8:00, 16:00 y 23:00, hora de
España) y los agentes leen, al decidir, los que se han escrito desde su última decisión.

Cada informe es una sola llamada a la IA en la que cuatro analistas (técnico, noticias, sentimiento y un debate
entre uno alcista y uno bajista) ponen en común lo que ven. Idea tomada de TradingAgents, simplificada para
caber en la cuota gratuita.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from agentes import config, noticias, prompts

from zoneinfo import ZoneInfo

VISTAS = ("alcista", "bajista", "neutral")
MADRID = ZoneInfo("Europe/Madrid")
MAX_HISTORIAL = 90  # unos 30 días con tres informes al día
TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def informe(ia, fecha: str, tabla: str, noticias_dia: list[dict], sentimiento: str, anterior: dict | None,
            hora: str | None = None, estado: dict | None = None) -> dict | None:
    try:
        r = ia.pedir_json(prompts.INSTRUCCIONES_ANALISTAS,
                          prompts.mensaje_analistas(fecha, tabla, noticias_dia, sentimiento, anterior,
                                                    textocalendario(fecha, estado)))
    except Exception as e:
        print(f"Los analistas no pudieron escribir su informe: {e}")
        return None
    debate = r.get("debate") if isinstance(r.get("debate"), dict) else {}
    valores = []
    for v in r.get("valores_destacados") or []:
        if isinstance(v, dict) and v.get("simbolo"):
            valores.append({"simbolo": str(v["simbolo"]).upper()[:8],
                            "vista": v.get("vista") if v.get("vista") in VISTAS else "neutral",
                            "motivo": str(v.get("motivo", "")).strip()})
    return {
        "fecha": fecha,
        "hora": hora or datetime.now(timezone.utc).isoformat(timespec="minutes"),
        # Cuándo terminaron de escribirlo: la web lo publica un minuto después
        "listo": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "tecnico": str(r.get("tecnico", "")).strip(),
        "noticias": str(r.get("noticias", "")).strip(),
        "sentimiento": str(r.get("sentimiento", "")).strip(),
        "alcista": str(debate.get("alcista", "")).strip(),
        "bajista": str(debate.get("bajista", "")).strip(),
        "conclusion": str(r.get("conclusion", "")).strip(),
        "tono": r.get("tono") if r.get("tono") in VISTAS else "neutral",
        "valores": valores[:10],
        "radar": _leer_radar(r.get("radar")),
        "eventos": _leer_eventos(r.get("eventos"), fecha),
    }


def _siguiente_dia_de_bolsa(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def eventos_resultados(fundamentales: dict | None) -> list[tuple[str, str]]:
    """Los resultados confirmados de config.GIGANTES como eventos: antes de abrir, ese día a las 9:30; tras el
    cierre, el siguiente día de bolsa a las 9:30 (es cuando el mercado reacciona). Los del mismo día, juntos."""
    por_dia: dict[str, list[str]] = {}
    for t, r in ((fundamentales or {}).get("resultados") or {}).items():
        if t not in config.GIGANTES or r.get("estimada") or r.get("hora") not in ("antes de abrir", "tras el cierre"):
            continue
        d = date.fromisoformat(r["fecha"])
        if r["hora"] == "tras el cierre":
            por_dia.setdefault(_siguiente_dia_de_bolsa(d).isoformat(), []).append(f"{config.GIGANTES[t]} (tras el cierre anterior)")
        else:
            por_dia.setdefault(d.isoformat(), []).append(config.GIGANTES[t])
    return [(f"{d}T09:30", "resultados de " + " y ".join(sorted(nombres))) for d, nombres in por_dia.items()]


def todos_los_eventos(estado: dict | None = None, fundamentales: dict | None = None) -> list[tuple[str, str]]:
    """El calendario completo: el oficial, los resultados de los gigantes y los que han marcado los analistas."""
    if fundamentales is None:
        from agentes import fundamentales as f
        fundamentales = f.cargar()
    return (list(config.EVENTOS) + eventos_resultados(fundamentales)
            + [(e["cuando"], e["texto"]) for e in (estado or {}).get("eventos", [])])


def calendario(fecha: str, estado: dict | None, dias: int = 7, fundamentales: dict | None = None) -> list[tuple[str, str]]:
    hasta = (date.fromisoformat(fecha) + timedelta(days=dias)).isoformat()
    return sorted({(c, t) for c, t in todos_los_eventos(estado, fundamentales) if fecha <= c[:10] <= hasta})


def textocalendario(fecha: str, estado: dict | None = None) -> str:
    return "\n".join(f"- {c[:10]} a las {c[11:16]}: {t}" for c, t in calendario(fecha, estado))


def _leer_eventos(lista, fecha: str) -> list[dict]:
    """Los eventos que proponen los analistas: de hoy a 5 días, en día laborable y con hora entre las 8:00 y las 14:30
    de Nueva York (para que dé tiempo a operar antes de que se corten las compras). Uno tras el cierre (desde las
    16:00) pasa a la apertura del siguiente día de bolsa, que es cuando el mercado reacciona."""
    eventos, hoy = [], date.fromisoformat(fecha)
    for e in lista if isinstance(lista, list) else []:
        if not isinstance(e, dict):
            continue
        dia, hora, texto = str(e.get("fecha", "")).strip(), str(e.get("hora", "")).strip()[:5], str(e.get("texto", "")).strip()
        try:
            d, h = date.fromisoformat(dia), datetime.strptime(hora, "%H:%M")
        except ValueError:
            continue
        if f"{h:%H:%M}" >= "16:00":
            d, h, texto = _siguiente_dia_de_bolsa(d), h.replace(hour=9, minute=30), texto + " (el día anterior tras el cierre)"
        if texto and 0 <= (d - hoy).days <= 5 and d.weekday() < 5 and "08:00" <= f"{h:%H:%M}" <= "14:30":
            eventos.append({"cuando": f"{d.isoformat()}T{h:%H:%M}", "texto": texto[:160]})
    return eventos[:config.MAX_EVENTOS_ANALISTAS]


def _leer_radar(lista) -> list[dict]:
    """Las propuestas del radar que tienen un ticker válido y no están ya en la lista de valores."""
    radar, vistos = [], set(config.UNIVERSO) | {config.REFERENCIA}
    for v in lista if isinstance(lista, list) else []:
        simbolo = str(v.get("simbolo", "")).upper().strip() if isinstance(v, dict) else ""
        if TICKER.match(simbolo) and simbolo not in vistos:
            vistos.add(simbolo)
            radar.append({"simbolo": simbolo, "nombre": str(v.get("nombre", "")).strip()[:60],
                          "motivo": str(v.get("motivo", "")).strip()})
    return radar[:config.MAX_RADAR]


def completar_radar(inf: dict | None, mercado) -> None:
    """Deja en el radar solo las empresas que cotizan a más de 5 $ y con negociación suficiente (para poder
    comprar y vender sin problemas), con su precio. Si no se puede comprobar, el radar queda vacío."""
    if not inf or not inf.get("radar"):
        return
    try:
        datos = mercado.liquidez([v["simbolo"] for v in inf["radar"]])
    except Exception as e:
        print(f"No se pudo comprobar el radar de oportunidades: {e}")
        datos = {}
    inf["radar"] = [{**v, "precio": round(datos[v["simbolo"]][0], 2)} for v in inf["radar"]
                    if v["simbolo"] in datos and datos[v["simbolo"]][0] >= config.MIN_PRECIO_RADAR
                    and datos[v["simbolo"]][1] >= config.MIN_VOLUMEN_RADAR_USD]


def radar_de(informes: list[dict]) -> list[dict]:
    """El radar de los informes que leen los agentes, sin repetir (de cada empresa, lo más reciente)."""
    radar = {}
    for inf in informes:
        for v in inf.get("radar") or []:
            if v.get("precio"):
                radar.pop(v["simbolo"], None)
                radar[v["simbolo"]] = v
    return list(radar.values())[-2 * config.MAX_RADAR:]


def texto_radar(radar: list[dict], precios: dict[str, float] | None = None) -> str:
    if not radar:
        return ""
    precios = precios or {}
    filas = "\n".join(f"- {v['simbolo']} ({v['nombre'] or 'sin nombre'}), {precios.get(v['simbolo'], v['precio']):.2f} $: "
                      f"{v['motivo']}" for v in radar)
    return (f"RADAR DE OPORTUNIDADES (empresas fuera de la tabla; puedes comprarlas, como mucho un "
            f"{config.MAX_PESO_RADAR:.0%} de tu cartera en cada una; di qué opinas en el campo \"radar\" de tu informe)\n{filas}")


def texto(inf: dict | None) -> str:
    if not inf:
        return "(Hoy el equipo de analistas no ha podido escribir su informe.)"
    valores = "\n".join(f"- {v['simbolo']} ({v['vista']}): {v['motivo']}" for v in inf["valores"]) or "- (ninguno)"
    return f"""Informe del {inf['fecha']}. Tono general: {inf['tono']}.
Análisis técnico: {inf['tecnico']}
Noticias y economía: {inf['noticias']}
Sentimiento: {inf['sentimiento']}
Argumento alcista: {inf['alcista']}
Argumento bajista: {inf['bajista']}
Conclusión: {inf['conclusion']}
Valores destacados:
{valores}"""


def guardar(estado: dict, inf: dict) -> None:
    """Además, apunta en el calendario los eventos que han marcado (sin repetir día y hora ni pisar el oficial)."""
    ocupados = {c for c, _ in todos_los_eventos()}
    hoy = inf["fecha"]
    eventos = [e for e in estado.get("eventos", []) if e["cuando"][:10] >= hoy]
    for e in inf.get("eventos") or []:
        if e["cuando"] not in ocupados | {x["cuando"] for x in eventos}:
            eventos.append({**e, "origen": "analistas", "apuntado": inf.get("hora")})
    if eventos or "eventos" in estado:
        estado["eventos"] = eventos
    estado["analistas"] = inf
    estado["analistas_historial"] = (estado.get("analistas_historial", []) + [inf])[-MAX_HISTORIAL:]


def analizar(estado: dict, mercado, ia, datos_noticias: dict, ahora: datetime | None = None) -> dict | None:
    """El informe de una recogida de noticias: mira el mercado y las noticias del momento y lo guarda en el estado."""
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = sorted(set(config.UNIVERSO) | {config.REFERENCIA})
    tabla = prompts.tabla_mercado(mercado.cierres_diarios(base), mercado.ultimos_precios(base))
    noticias_dia, _ = noticias.para_agentes(datos_noticias, ahora)
    sentimiento = noticias.texto_sentimiento(noticias.sentimiento_por_valor(datos_noticias, ahora))
    inf = informe(ia, ahora.date().isoformat(), tabla, noticias_dia, sentimiento, estado.get("analistas"),
                  hora=ahora.isoformat(timespec="minutes"), estado=estado)
    if inf:
        completar_radar(inf, mercado)
        guardar(estado, inf)
    return inf


def _hora(inf: dict) -> str:
    return inf.get("hora") or inf["fecha"] + "T12:00+00:00"  # los primeros informes solo tenían fecha


def desde(estado: dict, hora: str | None, ahora: datetime, maximo: int = 3) -> list[dict]:
    """Los informes escritos después de `hora` (la última decisión), o en las últimas 30 horas si no hay."""
    limite = datetime.fromisoformat(hora) if hora else ahora - timedelta(hours=30)
    return [i for i in estado.get("analistas_historial", []) if datetime.fromisoformat(_hora(i)) > limite][-maximo:]


def texto_varios(informes: list[dict]) -> str:
    """El último informe entero y, antes, las conclusiones de los anteriores para ver cómo ha cambiado la visión."""
    if not informes:
        return texto(None)
    previos = "\n".join(f"- {_momento(i)} (tono {i['tono']}): {i['conclusion']}" for i in informes[:-1])
    ultimo = f"ÚLTIMO INFORME ({_momento(informes[-1])})\n{texto(informes[-1])}"
    return (f"INFORMES ANTERIORES DESDE TU ÚLTIMA DECISIÓN\n{previos}\n\n" if previos else "") + ultimo


def _momento(inf: dict) -> str:
    hora = datetime.fromisoformat(_hora(inf)).astimezone(MADRID)
    return f"{inf['fecha']} {hora:%H:%M} (hora de España)"
