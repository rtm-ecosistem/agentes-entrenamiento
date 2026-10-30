"""Datos públicos que copian dos agentes: la cartera de Berkshire Hathaway (13F de la SEC) para el Copión
y las operaciones declaradas por los congresistas de EE. UU. (PTR de la Cámara) para la del Congreso.

Se consultan como mucho una vez al día y se guardan en datos/fuentes.json. La lectura de los 13F y de los PTR
está adaptada de trader-skill (https://github.com/Jakeschincariol/trader-skill, licencia MIT,
© 2026 Jake Schincariol).
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta

from agentes import config

BERKSHIRE_CIK = "0001067983"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK%s.json"
ARCHIVO_SEC = "https://www.sec.gov/Archives/edgar/data/%d/%s/"
INDICE_CAMARA = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/%dFD.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/%d/%s.pdf"
DIAS_CONGRESO = 45          # declaraciones presentadas en los últimos 45 días
MAX_PTR_POR_DIA = 20        # PDFs nuevos que se leen cada día como mucho

# CUSIP (identificador de cada valor en el 13F) -> ticker, para las posiciones habituales de Berkshire
CUSIP_TICKER = {
    "037833100": "AAPL", "025816109": "AXP", "191216100": "KO", "02079K305": "GOOGL", "02079K107": "GOOG",
    "060505104": "BAC", "166764100": "CVX", "674599105": "OXY", "H1467J104": "CB", "615369105": "MCO",
    "500754106": "KHC", "23918K108": "DVA", "247361702": "DAL", "829933100": "SIRI", "92343E102": "VRSN",
    "501044101": "KR", "02005N100": "ALLY", "526057104": "LEN", "526057302": "LEN.B", "530909308": "LLYVK",
    "530909100": "LLYVA", "650111107": "NYT", "14040H105": "COF", "546347105": "LPX", "670346105": "NUE",
    "55616P104": "M", "62944T105": "NVR", "47233W109": "JEF", "23331A109": "DHI", "21036P108": "STZ",
    "92826C839": "V", "57636Q104": "MA", "91324P102": "UNH", "22160K105": "COST", "25754A201": "DPZ",
    "73278L105": "POOL", "422806109": "HEI", "G0403H108": "AON", "16119P108": "CHTR", "92556H206": "PARA",
    "023135106": "AMZN", "742718109": "PG", "478160104": "JNJ", "580135101": "MCD", "531229854": "FWONK",
}
TICKER_VALIDO = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def descargar(url: str) -> bytes:
    import requests
    r = requests.get(url, headers={"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
                     timeout=60)
    r.raise_for_status()
    return r.content


def cargar() -> dict:
    if config.FUENTES.exists():
        return json.loads(config.FUENTES.read_text(encoding="utf-8"))
    return {"berkshire": {}, "congreso": {"leidas": {}}}


def guardar(datos: dict) -> None:
    config.DATOS.mkdir(parents=True, exist_ok=True)
    config.FUENTES.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def actualizar(hoy: date, obtener=None, pdf_a_texto=None) -> dict:
    """Refresca las dos fuentes si no se han revisado hoy. Un fallo deja los datos anteriores."""
    obtener, pdf_a_texto = obtener or descargar, pdf_a_texto or pdf_texto
    datos = cargar()
    if datos.get("berkshire", {}).get("revisado") != hoy.isoformat():
        try:
            datos["berkshire"] = berkshire(obtener, datos.get("berkshire", {})) | {"revisado": hoy.isoformat()}
        except Exception as e:
            print(f"No se pudo leer el 13F de Berkshire: {e}")
    if datos.get("congreso", {}).get("revisado") != hoy.isoformat():
        try:
            datos["congreso"] = congreso(obtener, datos.get("congreso", {}), hoy, pdf_a_texto) | {
                "revisado": hoy.isoformat()}
        except Exception as e:
            print(f"No se pudieron leer las declaraciones del Congreso: {e}")
    guardar(datos)
    return datos


# --- Berkshire Hathaway (13F) -------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def leer_tabla_13f(xml: bytes) -> dict[str, dict]:
    """Posiciones de un 13F agrupadas por CUSIP: nombre, clase, valor en $ y número de acciones."""
    posiciones: dict[str, dict] = {}
    for fila in ET.fromstring(xml).iter():
        if _local(fila.tag) != "infoTable":
            continue
        campos = {_local(c.tag): c for c in fila.iter()}
        if campos.get("putCall") is not None:
            continue
        cusip = (campos["cusip"].text or "").strip().upper()
        p = posiciones.setdefault(cusip, {"nombre": (campos["nameOfIssuer"].text or "").strip(),
                                          "clase": (campos["titleOfClass"].text or "").strip(),
                                          "valor": 0.0, "acciones": 0.0})
        p["valor"] += float(campos["value"].text or 0)
        p["acciones"] += float(campos["sshPrnamt"].text or 0)
    return posiciones


def _tabla_de(obtener, acceso: str) -> dict[str, dict]:
    carpeta = ARCHIVO_SEC % (int(BERKSHIRE_CIK), acceso.replace("-", ""))
    indice = json.loads(obtener(carpeta + "index.json"))
    xmls = [i["name"] for i in indice["directory"]["item"]
            if i["name"].lower().endswith(".xml") and i["name"].lower() != "primary_doc.xml"]
    if not xmls:
        raise ValueError(f"El 13F {acceso} no tiene tabla de posiciones.")
    return leer_tabla_13f(obtener(carpeta + xmls[0]))


def berkshire(obtener, anterior: dict) -> dict:
    envios = json.loads(obtener(SUBMISSIONS % BERKSHIRE_CIK))["filings"]["recent"]
    informes = [(envios["accessionNumber"][i], envios["reportDate"][i], envios["filingDate"][i])
                for i in range(len(envios["form"])) if envios["form"][i] == "13F-HR"]
    if not informes:
        raise ValueError("Berkshire no tiene 13F.")
    acceso, trimestre, presentado = informes[0]
    if anterior.get("acceso") == acceso:
        return anterior
    actual = _tabla_de(obtener, acceso)
    previa = _tabla_de(obtener, informes[1][0]) if len(informes) > 1 else {}
    total = sum(p["valor"] for p in actual.values()) or 1
    posiciones = []
    for cusip, p in sorted(actual.items(), key=lambda kv: -kv[1]["valor"]):
        antes = previa.get(cusip)
        if not antes:
            cambio = "nueva"
        elif antes["acciones"] and abs(p["acciones"] / antes["acciones"] - 1) >= 0.005:
            cambio = f"{(p['acciones'] / antes['acciones'] - 1) * 100:+.0f} % de acciones"
        else:
            cambio = "sin cambios"
        posiciones.append({"nombre": p["nombre"], "clase": p["clase"], "ticker": CUSIP_TICKER.get(cusip),
                           "peso": round(p["valor"] / total, 4), "valor": round(p["valor"]), "cambio": cambio})
    vendidas = [p["nombre"] for c, p in previa.items() if c not in actual]
    return {"acceso": acceso, "trimestre": trimestre, "presentado": presentado, "total": round(total),
            "posiciones": posiciones, "vendidas": vendidas}


def texto_berkshire(b: dict, maximo: int = 20) -> str:
    if not b.get("posiciones"):
        return "Aún no se ha podido leer la cartera de Berkshire Hathaway. Sin datos nuevos hoy."
    lineas = [f"- {p['ticker'] or '(sin ticker)'} · {p['nombre']} {p['clase']}: {p['peso']:.1%} de su cartera "
              f"({p['cambio']} respecto al trimestre anterior)" for p in b["posiciones"][:maximo]]
    vendidas = ", ".join(b.get("vendidas", [])) or "ninguna"
    return (f"Último 13F de Berkshire Hathaway: cartera a {b['trimestre']}, presentado el {b['presentado']} "
            f"({b['total'] / 1e9:,.0f} mil millones de $ en acciones de EE. UU.).\n" + "\n".join(lineas) +
            f"\nPosiciones que vendió del todo ese trimestre: {vendidas}.")


def tickers_berkshire(b: dict) -> set[str]:
    return {p["ticker"] for p in b.get("posiciones", []) if p.get("ticker") and TICKER_VALIDO.match(p["ticker"])}


# --- Congreso de EE. UU. (PTR de la Cámara de Representantes) -------------------------

def iso(mdy: str) -> str | None:
    """'8/21/2026' -> '2026-08-21'."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$", mdy or "")
    if not m:
        return None
    mes, dia, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return _dt.date(anio + 2000 if anio < 100 else anio, mes, dia).isoformat()
    except ValueError:
        return None


def leer_indice(datos: bytes) -> list[dict]:
    """Declaraciones de operaciones (FilingType P) del índice anual de la Cámara, de la más reciente a la más antigua."""
    if datos[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            datos = z.read(next(n for n in z.namelist() if n.lower().endswith(".xml")))
    filas = []
    for m in ET.fromstring(datos).iter("Member"):
        f = {c.tag: (c.text or "").strip() for c in m}
        if f.get("FilingType") != "P":
            continue
        nombre = " ".join(x for x in (f.get("First"), f.get("Last")) if x)
        filas.append({"doc": f.get("DocID", ""), "nombre": nombre, "distrito": f.get("StateDst", ""),
                      "anio": int(f.get("Year") or 0), "presentado": iso(f.get("FilingDate", ""))})
    filas.sort(key=lambda r: (r["presentado"] or "", r["doc"]), reverse=True)
    return filas


TXN = re.compile(
    r"^\s*(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s+(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<notified>\d{2}/\d{2}/\d{4})\s+(?P<amount>\S.*?)\s*$")
FIN = re.compile(r"^\s*(\* For the complete|I CERTIFY|Digitally Signed|Filing ID #)", re.I)
ETIQUETA = re.compile(r"^\s*(F(ILING)?\s+S(TATUS)?|D(ESCRIPTION)?|S(UBHOLDING)?\s+O(F)?|L(OCATION)?)\s*:", re.I)
CABECERA = re.compile(r"^\s*(ID\s+Owner\s+Asset|Type\s+Date\b|Type$|Date$|Gains >$|\$200\?$)", re.I)


def leer_ptr(texto: str) -> list[dict]:
    """Operaciones de un PTR (texto de `pdftotext -layout`): activo, ticker, tipo, fecha e importe."""
    filas, actual, modo, importe_abierto = [], None, None, False
    for crudo in texto.splitlines():
        linea, s = crudo.rstrip(), crudo.strip()
        t = TXN.match(linea)
        if t and iso(t.group("date")):
            if actual:
                filas.append(actual)
            actual = {"activo": t.group("asset"), "tipo": t.group("type"), "fecha": iso(t.group("date")),
                      "importe": t.group("amount")}
            importe_abierto, modo = actual["importe"].endswith("-"), "activo"
            continue
        if actual is None or not s or CABECERA.match(s):
            if actual is not None and not s and "[" in actual["activo"]:
                modo = "otros"
            continue
        if FIN.match(s) or ETIQUETA.match(linea):
            modo = "otros"
            continue
        if modo == "activo":
            partes = re.split(r"\s{2,}", s)
            if importe_abierto and re.fullmatch(r"\$[\d,]+", partes[-1]):
                actual["importe"] += " " + partes.pop()
                importe_abierto = False
            if partes and partes[0]:
                actual["activo"] += " " + " ".join(partes)
    if actual:
        filas.append(actual)
    resultado = []
    for f in filas:
        activo = " ".join(f["activo"].split())
        ticker = re.findall(r"\(([A-Z][A-Z0-9.\-/]{0,9})\)", activo)
        clase = re.findall(r"\[([A-Z]{2})\]", activo)
        nombre = re.sub(r"\s*\[[A-Z]{2}\]", "", activo)
        if ticker:
            nombre = re.sub(r"\s*\(%s\)" % re.escape(ticker[-1]), "", nombre)
        resultado.append({"activo": nombre.strip(" -"), "ticker": ticker[-1] if ticker else None,
                          "clase": clase[-1] if clase else None,
                          "lado": "compra" if f["tipo"] == "P" else "venta" if f["tipo"].startswith("S") else "canje",
                          "fecha": f["fecha"], "importe": " ".join(f["importe"].split())})
    return resultado


def pdf_texto(pdf: bytes) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        r = subprocess.run([exe, "-layout", f.name, "-"], capture_output=True, timeout=60)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def congreso(obtener, anterior: dict, hoy: date, pdf_a_texto) -> dict:
    desde = (hoy - timedelta(days=DIAS_CONGRESO)).isoformat()
    indice = leer_indice(obtener(INDICE_CAMARA % hoy.year))
    if hoy.month == 1:
        try:
            indice += leer_indice(obtener(INDICE_CAMARA % (hoy.year - 1)))
        except Exception:
            pass
    recientes = [r for r in indice if (r["presentado"] or "") >= desde]
    leidas = {d: v for d, v in anterior.get("leidas", {}).items() if v["presentado"] >= desde}
    nuevas = [r for r in recientes if r["doc"] not in leidas][:MAX_PTR_POR_DIA]
    for r in nuevas:
        try:
            texto = pdf_a_texto(obtener(PTR_URL % (r["anio"] or hoy.year, r["doc"])))
        except Exception as e:
            print(f"No se pudo descargar el PTR {r['doc']}: {e}")
            continue
        if texto is None:  # sin pdftotext no se puede leer: se intentará otro día
            break
        operaciones = [o for o in leer_ptr(texto)
                       if o["clase"] == "ST" and o["ticker"] and o["lado"] != "canje" and TICKER_VALIDO.match(o["ticker"])]
        leidas[r["doc"]] = {"nombre": r["nombre"], "distrito": r["distrito"], "presentado": r["presentado"],
                            "operaciones": operaciones}
    return {"leidas": leidas, "pendientes": len([r for r in recientes if r["doc"] not in leidas])}


def resumen_congreso(c: dict) -> list[dict]:
    """Por cada acción: cuántos congresistas distintos la compraron o vendieron y cuándo."""
    valores: dict[str, dict] = {}
    for declaracion in c.get("leidas", {}).values():
        for o in declaracion["operaciones"]:
            v = valores.setdefault(o["ticker"], {"ticker": o["ticker"], "activo": o["activo"], "compradores": set(),
                                                 "vendedores": set(), "operaciones": 0, "ultima": ""})
            (v["compradores"] if o["lado"] == "compra" else v["vendedores"]).add(declaracion["nombre"])
            v["operaciones"] += 1
            v["ultima"] = max(v["ultima"], o["fecha"] or "")
    filas = [{**v, "compradores": sorted(v["compradores"]), "vendedores": sorted(v["vendedores"])}
             for v in valores.values()]
    return sorted(filas, key=lambda v: (-(len(v["compradores"]) - len(v["vendedores"])), -v["operaciones"]))


def texto_congreso(c: dict, maximo: int = 25) -> str:
    filas = resumen_congreso(c)
    if not filas:
        return "No hay declaraciones recientes de congresistas que se hayan podido leer. Sin datos nuevos hoy."
    lineas = [f"- {v['ticker']} · {v['activo'][:50]}: compran {len(v['compradores'])} "
              f"({', '.join(v['compradores'][:3]) or '-'}), venden {len(v['vendedores'])} "
              f"({', '.join(v['vendedores'][:3]) or '-'}); última operación {v['ultima']}" for v in filas[:maximo]]
    return (f"Operaciones en acciones declaradas por miembros de la Cámara de Representantes en los últimos "
            f"{DIAS_CONGRESO} días ({len(c.get('leidas', {}))} declaraciones leídas; las operaciones son de días o "
            f"semanas antes de la fecha de declaración y los importes son rangos):\n" + "\n".join(lineas))


def tickers_congreso(c: dict) -> set[str]:
    return {v["ticker"] for v in resumen_congreso(c) if v["compradores"]}


def para_agente(agente: dict, datos: dict) -> tuple[str, set[str]]:
    """Texto de la fuente que copia el agente y los valores extra que puede comprar por ella."""
    if agente.get("fuente") == "berkshire":
        b = datos.get("berkshire", {})
        return texto_berkshire(b) + _sin_copiar(agente, b), tickers_berkshire(b)
    if agente.get("fuente") == "congreso":
        c = datos.get("congreso", {})
        return texto_congreso(c), tickers_congreso(c)
    return "", set()


def _sin_copiar(agente: dict, b: dict, mayores: int = 10) -> str:
    """Si aún no tiene copiada la cartera, que no espere al próximo 13F: el último ya vale para empezar."""
    top = [p["ticker"] for p in b.get("posiciones", []) if p.get("ticker") and TICKER_VALIDO.match(p["ticker"])][:mayores]
    tiene = sum(1 for t in top if t in agente.get("posiciones", {}))
    if not top or tiene >= len(top) / 2:
        return ""
    return (f"\n⚠️ Aún no tienes copiada esta cartera: tienes {tiene} de sus {len(top)} mayores posiciones. No esperes al "
            f"próximo 13F: este es el último informe y ya vale para empezar. Cópiala ahora con pesos parecidos, dentro "
            f"de lo que te dejen comprar hoy (si no cabe todo, empieza por las mayores y sigue en los próximos días). "
            f"El próximo 13F solo sirve para ajustarla.")


def compras_desde(agente: dict, datos: dict, desde: str) -> set[str]:
    """Lo que ha comprado la fuente del agente con fecha de presentación desde `desde` (para el aviso de inactividad)."""
    if agente.get("fuente") == "berkshire":
        b = datos.get("berkshire", {})
        if (b.get("presentado") or "") < desde:
            return set()
        return {p["ticker"] for p in b.get("posiciones", []) if p.get("ticker") and TICKER_VALIDO.match(p["ticker"])
                and (p["cambio"] == "nueva" or p["cambio"].startswith("+"))}
    if agente.get("fuente") == "congreso":
        return {o["ticker"] for d in datos.get("congreso", {}).get("leidas", {}).values() if d["presentado"] >= desde
                for o in d["operaciones"] if o["lado"] == "compra"}
    return set()
