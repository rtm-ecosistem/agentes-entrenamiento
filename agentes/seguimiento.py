"""Evolución de la sesión en curso para las gráficas del día: un punto por minuto con el valor de cada agente.

Cada vez que se actualizan los precios se piden a Alpaca las barras de un minuto desde el último punto y se
valora con ellas la cartera que cada agente tenía entonces (guardada en el último punto). Así la gráfica sale
minuto a minuto aunque GitHub ejecute cada 15 minutos (o más tarde) y sin gastar IA.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentes import cartera, config
from agentes.alpaca import NUEVA_YORK

MAX_PUNTOS = 450  # una sesión tiene 390 minutos
DIAS_GUARDADOS = 5  # sesiones que se guardan minuto a minuto para la ventana de gráficas (1H, 1D, 1S)
DIAS_CIERRES = 400  # días con el cierre de cada acción en cartera, para las ventanas largas de la gráfica por acción


def _hora(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _carteras(estado: dict) -> dict[str, dict]:
    return {a["id"]: {"efectivo": a["efectivo"], "posiciones": {s: p["cantidad"] for s, p in a["posiciones"].items()}}
            for a in estado["agentes"] if a["estado"] == "activo"}


def _valor(c: dict, precios: dict[str, float]) -> float:
    return c["efectivo"] + sum(q * precios.get(s, 0.0) for s, q in c["posiciones"].items())


def _precios_de(carteras: dict[str, dict], precios: dict[str, float]) -> dict[str, float]:
    """El precio de cada acción que tiene alguien, para la gráfica de cada acción de su ficha."""
    return {s: round(precios[s], 4) for s in sorted({s for c in carteras.values() for s in c["posiciones"]}) if s in precios}


def _rellenar(dia: dict, mercado, precios: dict[str, float], ahora: datetime) -> list[dict]:
    """Puntos de cada minuto entre el último guardado (o la apertura, en la primera ejecución del día) y ahora,
    con las carteras del último punto (en la apertura, las del cierre anterior)."""
    ultima = dia["puntos"][-1]["hora"] if dia["puntos"] else dia.get("desde")
    if not ultima or not dia.get("carteras") or not hasattr(mercado, "barras_minuto"):
        return []
    desde = datetime.strptime(ultima, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    if ahora - desde < timedelta(minutes=2):
        return []
    simbolos = sorted({s for c in dia["carteras"].values() for s in c["posiciones"]} | {config.REFERENCIA})
    try:
        barras = mercado.barras_minuto(simbolos, desde + timedelta(minutes=1), ahora)
    except Exception as e:
        print(f"Aviso: no se pudieron traer las barras de un minuto: {e}")
        return []
    por_minuto: dict[str, dict[str, float]] = {}
    for s, lista in barras.items():
        for t, cierre in lista:
            por_minuto.setdefault(t[:16] + "Z", {})[s] = cierre
    ultimo = dict(dia.get("ultimos_precios") or precios)
    puntos = []
    for hora in sorted(por_minuto):
        if hora >= _hora(ahora):
            break
        ultimo.update(por_minuto[hora])
        puntos.append({"hora": hora, "ref": ultimo.get(config.REFERENCIA),
                       "valores": {i: round(_valor(c, ultimo), 2) for i, c in dia["carteras"].items()},
                       "precios": _precios_de(dia["carteras"], ultimo)})
    return puntos


def registrar(estado: dict, mercado, precios: dict[str, float], ahora: datetime | None = None) -> None:
    """Añade los minutos que faltaban y un punto ahora con las carteras actuales. Nunca rompe la ejecución."""
    try:
        ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
        fecha = ahora.astimezone(NUEVA_YORK).date().isoformat()
        dia = estado.get("dia_en_curso") or {}
        if dia.get("fecha") != fecha:
            archivar(dia)
            # El día nuevo se rellena desde la apertura con lo que tenía cada uno al cierre anterior
            apertura = ahora.astimezone(NUEVA_YORK).replace(hour=9, minute=29, second=0, microsecond=0)
            dia = {"fecha": fecha, "puntos": [], "desde": _hora(apertura), "carteras": dia.get("carteras"),
                   "ultimos_precios": dia.get("ultimos_precios")}
        nuevos = _rellenar(dia, mercado, precios, ahora)
        ahora_punto = {"hora": _hora(ahora), "ref": precios.get(config.REFERENCIA),
                       "valores": {a["id"]: round(cartera.valor(a, precios), 2)
                                   for a in estado["agentes"] if a["estado"] == "activo"},
                       "precios": _precios_de({**(dia.get("carteras") or {}), **_carteras(estado)}, precios)}
        horas = {p["hora"] for p in nuevos} | {ahora_punto["hora"]}
        dia["puntos"] = ([p for p in dia["puntos"] if p["hora"] not in horas] + nuevos + [ahora_punto])[-MAX_PUNTOS:]
        dia["carteras"] = _carteras(estado)
        dia["ultimos_precios"] = {s: precios[s] for s in {s for c in dia["carteras"].values()
                                                          for s in c["posiciones"]} | {config.REFERENCIA} if s in precios}
        estado["dia_en_curso"] = dia
        # El cierre de hoy de cada acción en cartera: se sobrescribe en cada ejecución y la última es el cierre
        cierres = estado.setdefault("cierres", {})
        cierres[fecha] = {**cierres.get(fecha, {}), **_precios_de(dia["carteras"], precios)}
        estado["cierres"] = {f: cierres[f] for f in sorted(cierres)[-DIAS_CIERRES:]}
        _rellenar_hueco_inicial(dia, mercado)
        completar_precios(estado, mercado)
    except Exception as e:
        print(f"Aviso: no se pudo guardar la evolución del día: {e}")


def _rellenar_hueco_inicial(dia: dict, mercado) -> None:
    """Una sola vez, para la sesión que empezó antes de que se rellenase desde la apertura: el hueco entre las 9:30 y
    su primer punto, con la cartera de ahora de quien no ha operado desde la apertura (el resto se queda sin esos minutos)."""
    import csv
    if "desde" in dia or not dia.get("puntos") or not dia.get("carteras") or not hasattr(mercado, "barras_minuto"):
        return
    apertura = datetime.fromisoformat(dia["fecha"]).replace(hour=9, minute=30, tzinfo=NUEVA_YORK)
    dia["desde"] = _hora(apertura - timedelta(minutes=1))
    primero = dia["puntos"][0]["hora"]
    operaron = set()
    if config.OPERACIONES.exists():
        with config.OPERACIONES.open(encoding="utf-8") as f:
            operaron = {o["agente"] for o in csv.DictReader(f) if o["fecha"] == dia["fecha"]}
    carteras = {i: c for i, c in dia["carteras"].items() if i not in operaron}
    if not carteras:
        return
    simbolos = sorted({s for c in carteras.values() for s in c["posiciones"]} | {config.REFERENCIA})
    try:
        barras = mercado.barras_minuto(simbolos, apertura, datetime.strptime(primero, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc))
    except Exception as e:
        print(f"Aviso: no se pudo rellenar el principio de la sesión: {e}")
        return
    por_minuto: dict[str, dict[str, float]] = {}
    for s, lista in barras.items():
        for t, cierre in lista:
            por_minuto.setdefault(t[:16] + "Z", {})[s] = cierre
    ultimo = dict(dia.get("ultimos_precios") or {})
    nuevos = []
    for hora in sorted(por_minuto):
        if hora >= primero:
            break
        ultimo.update(por_minuto[hora])
        nuevos.append({"hora": hora, "ref": ultimo.get(config.REFERENCIA),
                       "valores": {i: round(_valor(c, ultimo), 2) for i, c in carteras.items()}})
    dia["puntos"] = (nuevos + dia["puntos"])[-MAX_PUNTOS:]


def _compacto(dia: dict) -> dict:
    """Una sesión en columnas, que ocupa mucho menos: horas, S&P 500 y el valor de cada agente en cada minuto."""
    puntos = dia.get("puntos", [])
    ids = sorted({i for p in puntos for i in p.get("valores", {})})
    simbolos = sorted({s for p in puntos for s in p.get("precios", {})})
    return {"t": [p["hora"] for p in puntos], "ref": [p.get("ref") for p in puntos],
            "v": {i: [p.get("valores", {}).get(i) for p in puntos] for i in ids},
            **({"p": {s: [p.get("precios", {}).get(s) for p in puntos] for s in simbolos}} if simbolos else {})}


def cargar_archivo() -> dict:
    import json
    try:
        return json.loads(config.EVOLUCION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def archivar(dia: dict) -> None:
    """Guarda la sesión que termina, minuto a minuto, junto a las anteriores (solo las últimas DIAS_GUARDADOS)."""
    import json
    if not dia.get("fecha") or len(dia.get("puntos", [])) < 2:
        return
    archivo = cargar_archivo()
    archivo[dia["fecha"]] = _compacto(dia)
    archivo = {f: archivo[f] for f in sorted(archivo)[-DIAS_GUARDADOS:]}
    config.EVOLUCION.parent.mkdir(parents=True, exist_ok=True)
    config.EVOLUCION.write_text(json.dumps(archivo, separators=(",", ":")), encoding="utf-8")


def para_web(estado: dict) -> dict:
    """Las últimas sesiones minuto a minuto, incluida la de hoy aunque aún no se haya archivado."""
    archivo = cargar_archivo()
    dia = estado.get("dia_en_curso") or {}
    if dia.get("fecha") and len(dia.get("puntos", [])) >= 2:
        archivo[dia["fecha"]] = _compacto(dia)
    return {f: archivo[f] for f in sorted(archivo)[-DIAS_GUARDADOS:]}


def completar_precios(estado: dict, mercado) -> None:
    """Una sola vez: los precios de las acciones en cartera de los días anteriores a que se guardasen. Los cierres
    diarios desde la primera operación y, en las sesiones guardadas minuto a minuto, el precio de cada minuto."""
    import csv
    import json
    if estado.get("precios_completados") or not hasattr(mercado, "cierres_por_fecha") or not config.OPERACIONES.exists():
        return
    with config.OPERACIONES.open(encoding="utf-8") as f:
        ops = list(csv.DictReader(f))
    simbolos = sorted({s for a in estado["agentes"] if a["estado"] == "activo" for s in a["posiciones"]})
    if not ops or not simbolos:
        return
    try:
        desde = min(o["fecha"] for o in ops)
        cierres = estado.setdefault("cierres", {})
        for s, dias in mercado.cierres_por_fecha(simbolos, desde).items():
            for d, c in dias.items():
                cierres.setdefault(d, {}).setdefault(s, round(c, 4))
        archivo = cargar_archivo()
        for d, x in archivo.items():
            if x.get("p") or len(x.get("t", [])) < 2:
                continue
            ini = datetime.strptime(x["t"][0], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            fin = datetime.strptime(x["t"][-1], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc) + timedelta(minutes=1)
            ultimo, p = {}, {s: [] for s in simbolos}
            por_minuto: dict[str, dict[str, float]] = {}
            for s, lista in mercado.barras_minuto(simbolos, ini - timedelta(minutes=30), fin).items():
                for t, cierre in lista:
                    por_minuto.setdefault(t[:16] + "Z", {})[s] = cierre
            minutos = sorted(por_minuto)
            for t in x["t"]:
                while minutos and minutos[0] <= t:
                    ultimo.update(por_minuto[minutos.pop(0)])
                for s in simbolos:
                    p[s].append(round(ultimo[s], 4) if s in ultimo else None)
            x["p"] = {s: v for s, v in p.items() if any(c is not None for c in v)}
        config.EVOLUCION.write_text(json.dumps(archivo, separators=(",", ":")), encoding="utf-8")
        estado["precios_completados"] = True
    except Exception as e:
        print(f"Aviso: no se pudieron completar los precios de días anteriores: {e}")
