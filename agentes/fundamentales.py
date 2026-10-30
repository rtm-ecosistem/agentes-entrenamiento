"""Datos de empresa para el equipo diario: PER, crecimiento del beneficio por acción y de las ventas,
deuda frente a capital y la próxima fecha de resultados.

Las cifras salen de los "frames" de la SEC (una descarga por dato y año para todas las empresas a la vez) y las
fechas de resultados del calendario de Nasdaq; si Nasdaq no responde, se estima a partir del cierre del trimestre.
Se refresca una vez por semana (en la recogida de noticias) y se guarda en datos/fundamentales.json.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from agentes import config

TICKERS_SEC = "https://www.sec.gov/files/company_tickers.json"
FRAME = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/%s/%s.json"
NASDAQ = "https://api.nasdaq.com/api/calendar/earnings?date=%s"
NAVEGADOR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DIAS_VALIDEZ = 7            # se refresca si tiene una semana o más
DIAS_CALENDARIO = 21        # fechas de resultados que se buscan hacia delante
VENTAS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
HORAS = {"time-pre-market": "antes de abrir", "time-after-hours": "tras el cierre"}


def descargar(url: str) -> bytes:
    import requests
    cabecera = NAVEGADOR if "nasdaq" in url else config.SEC_USER_AGENT
    r = requests.get(url, headers={"User-Agent": cabecera, "Accept": "application/json",
                                   "Accept-Encoding": "gzip, deflate"}, timeout=60)
    r.raise_for_status()
    return r.content


def cargar() -> dict:
    if config.FUNDAMENTALES.exists():
        return json.loads(config.FUNDAMENTALES.read_text(encoding="utf-8"))
    return {}


def guardar(datos: dict) -> None:
    config.DATOS.mkdir(parents=True, exist_ok=True)
    config.FUNDAMENTALES.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def caducado(datos: dict, hoy: date) -> bool:
    revisado = datos.get("revisado")
    return not revisado or (hoy - date.fromisoformat(revisado)).days >= DIAS_VALIDEZ


def actualizar(hoy: date, obtener=None, simbolos=None) -> dict:
    """Refresca los datos si tienen una semana o más. Un fallo deja los anteriores."""
    obtener = obtener or descargar
    datos = cargar()
    if not caducado(datos, hoy):
        return datos
    simbolos = sorted(simbolos or config.UNIVERSO)
    try:
        empresas = cifras(obtener, simbolos, hoy)
    except Exception as e:
        print(f"No se pudieron leer los datos de empresa de la SEC: {e}")
        return datos
    datos = {"revisado": hoy.isoformat(), "empresas": empresas,
             "resultados": calendario(obtener, simbolos, empresas, hoy)}
    guardar(datos)
    return datos


# --- SEC ---------------------------------------------------------------------------

def _ciks(obtener, simbolos: list[str]) -> dict[int, str]:
    tabla = json.loads(obtener(TICKERS_SEC))
    buscados = set(simbolos)
    return {int(f["cik_str"]): t for f in tabla.values()
            if (t := f["ticker"].upper().replace(".", "-")) in buscados}


def _frame(obtener, concepto: str, unidad: str, periodo: str, ciks: dict[int, str]) -> dict[str, dict]:
    """{ticker: {"val", "end"}} de un frame; si la SEC no lo tiene (404), vacío."""
    try:
        datos = json.loads(obtener(FRAME % (concepto, unidad, periodo)))
    except Exception as e:
        print(f"Frame {concepto} {periodo} no disponible: {e}")
        return {}
    return {ciks[f["cik"]]: {"val": f["val"], "end": f["end"]} for f in datos.get("data", []) if f.get("cik") in ciks}


def _crecimiento(actual, anterior) -> float | None:
    if actual is None or anterior is None or anterior == 0:
        return None
    return round((actual - anterior) / abs(anterior) * 100, 1)


def cifras(obtener, simbolos: list[str], hoy: date) -> dict[str, dict]:
    ciks = _ciks(obtener, simbolos)
    anos = [hoy.year - 1, hoy.year - 2, hoy.year - 3]
    bpa = {a: _frame(obtener, "EarningsPerShareDiluted", "USD-per-shares", f"CY{a}", ciks) for a in anos}
    por_concepto = {c: {a: _frame(obtener, c, "USD", f"CY{a}", ciks) for a in anos} for c in VENTAS}
    balance = {}
    for a in anos[:2]:
        deuda = _frame(obtener, "Liabilities", "USD", f"CY{a}Q4I", ciks)
        capital = _frame(obtener, "StockholdersEquity", "USD", f"CY{a}Q4I", ciks)
        for t in deuda.keys() & capital.keys():
            if t not in balance and capital[t]["val"] > 0:
                balance[t] = round(deuda[t]["val"] / capital[t]["val"], 2)
    empresas = {}
    for t in simbolos:
        # El último año que ya tiene cifra y el anterior (en enero aún no han presentado el año que acaba)
        # Las ventas, con el mismo concepto en los dos años (cada empresa usa uno)
        ventas = next((v for v in por_concepto.values() if any(t in v[a] for a in anos[:2])), {a: {} for a in anos})
        ultimo = next((a for a in anos[:2] if t in bpa[a] or t in ventas[a]), None)
        if ultimo is None:
            continue
        e = {}
        if t in bpa[ultimo]:
            e["bpa"] = bpa[ultimo][t]["val"]
            e["cierre_fiscal"] = bpa[ultimo][t]["end"]
            if (c := _crecimiento(e["bpa"], bpa[ultimo - 1].get(t, {}).get("val"))) is not None:
                e["crec_bpa"] = c
        if t in ventas[ultimo]:
            e.setdefault("cierre_fiscal", ventas[ultimo][t]["end"])
            if (c := _crecimiento(ventas[ultimo][t]["val"], ventas[ultimo - 1].get(t, {}).get("val"))) is not None:
                e["crec_ventas"] = c
        if t in balance:
            e["deuda_capital"] = balance[t]
        empresas[t] = e
    return empresas


# --- Fechas de resultados ---------------------------------------------------------------

def _mas_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    a, m = d.year + m // 12, m % 12 + 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(a, m, dia)
        except ValueError:
            continue
    raise ValueError(d)


def estimar_resultados(cierre_fiscal: str, hoy: date) -> str | None:
    """Unos 30 días tras cada cierre de trimestre (40 tras el cierre del año fiscal): la próxima que no ha pasado."""
    try:
        fin = date.fromisoformat(cierre_fiscal)
    except (TypeError, ValueError):
        return None
    for k in range(0, 40):
        trimestre = _mas_meses(fin, 3 * k)
        fecha = trimestre + timedelta(days=40 if k % 4 == 0 else 30)
        if fecha >= hoy:
            while fecha.weekday() >= 5:
                fecha += timedelta(days=1)
            return fecha.isoformat()
    return None


def calendario(obtener, simbolos: list[str], empresas: dict, hoy: date) -> dict[str, dict]:
    buscados, resultados = set(simbolos), {}
    for i in range(DIAS_CALENDARIO):
        dia = hoy + timedelta(days=i)
        if dia.weekday() >= 5:
            continue
        try:
            filas = (json.loads(obtener(NASDAQ % dia.isoformat())).get("data") or {}).get("rows") or []
        except Exception as e:
            print(f"Calendario de Nasdaq no disponible ({dia}): {e}")
            continue
        for f in filas:
            t = str(f.get("symbol", "")).upper().replace(".", "-")
            if t in buscados and t not in resultados:
                resultados[t] = {"fecha": dia.isoformat(), "hora": HORAS.get(f.get("time"), "")}
    for t in buscados - resultados.keys():
        if (fecha := estimar_resultados(empresas.get(t, {}).get("cierre_fiscal"), hoy)):
            resultados[t] = {"fecha": fecha, "estimada": True}
    return resultados


# --- Para los agentes -------------------------------------------------------------------

def _porcentaje(v: float) -> str:
    return f"{v:+.0f} %"


def _fecha_corta(r: dict) -> str:
    d = date.fromisoformat(r["fecha"])
    return f"{d.day}/{d.month}" + ("?" if r.get("estimada") else "")


def texto_empresas(datos: dict, precios: dict[str, float], hoy: str, simbolos=None) -> str:
    """Una línea corta por empresa, para la decisión del equipo diario."""
    empresas, resultados = datos.get("empresas", {}), datos.get("resultados", {})
    if not empresas:
        return ""
    lineas = []
    for t in sorted(simbolos or empresas):
        e = empresas.get(t)
        if not e:
            continue
        partes = []
        if (p := precios.get(t)) and e.get("bpa"):
            partes.append(f"PER {p / e['bpa']:.0f}" if e["bpa"] > 0 else "PER - (pérdidas)")
        if "crec_bpa" in e:
            partes.append(f"BPA {_porcentaje(e['crec_bpa'])}")
        if "crec_ventas" in e:
            partes.append(f"ventas {_porcentaje(e['crec_ventas'])}")
        if "deuda_capital" in e:
            partes.append(f"deuda/capital {e['deuda_capital']:.1f}")
        if (r := resultados.get(t)) and r["fecha"] >= hoy:
            partes.append(f"resultados {_fecha_corta(r)}")
        if partes:
            lineas.append(f"- {t}: {', '.join(partes)}")
    if not lineas:
        return ""
    return ("BPA y ventas: crecimiento del último año fiscal. Una fecha con «?» es estimada. Cerca de los resultados "
            "el precio puede dar saltos grandes.\n" + "\n".join(lineas))


def presentan_esta_noche(datos: dict, hoy: str) -> dict[str, str]:
    """Las acciones con resultados confirmados entre el cierre de hoy y la apertura del siguiente día de bolsa:
    {símbolo: cuándo}. Son las que pueden amanecer con un salto grande."""
    d = date.fromisoformat(hoy)
    manana = (d + timedelta(days=3 if d.weekday() == 4 else 1)).isoformat()
    return {t: "hoy tras el cierre" if r["fecha"] == hoy else "mañana antes de abrir"
            for t, r in datos.get("resultados", {}).items()
            if not r.get("estimada") and (r["fecha"], r.get("hora")) in ((hoy, "tras el cierre"), (manana, "antes de abrir"))}


def texto_resultados_cercanos(datos: dict, hoy: str) -> str:
    """Para el intradía: quién presenta resultados hoy o mañana (el precio puede saltar)."""
    d = date.fromisoformat(hoy)
    manana = d + timedelta(days=3 if d.weekday() == 4 else 1)
    lineas = []
    for t, r in sorted(datos.get("resultados", {}).items()):
        if r.get("estimada") or r["fecha"] not in (hoy, manana.isoformat()):
            continue
        cuando = "hoy" if r["fecha"] == hoy else "el próximo día de bolsa"
        lineas.append(f"{t} ({cuando}{', ' + r['hora'] if r.get('hora') else ''})")
    if not lineas:
        return ""
    return "PRESENTAN RESULTADOS (el precio puede dar saltos grandes): " + ", ".join(lineas)
