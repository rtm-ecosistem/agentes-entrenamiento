"""El ánimo de cada agente: una mezcla de emociones que sale de hechos (cómo va frente al S&P 500, cómo le salen
las operaciones, lo que hace el mercado, lo que dejó pasar...) sin gastar ninguna llamada a la IA.

Cada agente tiene su temperamento (a quién el miedo le pesa el doble, a quién se le sube la euforia) y las
emociones se van apagando con el tiempo: una mala racha le dura unos días. El agente lee su ánimo antes de
decidir y dice si le está influyendo; sus reglas siguen mandando. Cada operación guarda la emoción con la que
se hizo, para que aprenda con qué ánimo opera peor.
"""

from __future__ import annotations

from datetime import datetime

from agentes import aprendizaje, config

EMOCIONES = {
    "euforia": ("😄", "Euforia"), "confianza": ("😊", "Confianza"), "calma": ("😌", "Calma"),
    "alivio": ("😮‍💨", "Alivio"), "orgullo": ("😎", "Orgullo"), "tension": ("😬", "Tensión"),
    "frustracion": ("😤", "Frustración"), "miedo": ("😨", "Miedo"), "envidia": ("😒", "Envidia"),
    "aburrimiento": ("🥱", "Aburrimiento"), "arrepentimiento": ("😣", "Arrepentimiento"),
    "impaciencia": ("😑", "Impaciencia"), "codicia": ("🤑", "Codicia"), "sorpresa": ("😲", "Sorpresa"),
    "panico": ("😱", "Pánico"),
}
# Las que empujan a decidir mal si mandan ellas: con estas se mide la sangre fría
INTENSAS = {"euforia", "miedo", "panico", "codicia", "frustracion", "envidia", "impaciencia"}
VIDA_HORAS = 24.0        # cada día una emoción se queda en la mitad si nada la alimenta
MINIMO = 0.03            # por debajo se olvida
CALMA_BASE = 0.35        # sin nada que le mueva, el agente está en calma


def _pct(x: float, signo: bool = False) -> str:
    return (f"{x * 100:+.1f} %" if signo else f"{x * 100:.1f} %").replace(".", ",")


def _puntos(x: float) -> str:
    return f"{x * 100:.2f}".replace(".", ",")


def temperamento_valido(t) -> dict[str, float]:
    """Solo emociones conocidas y multiplicadores entre 0,5 y 2 (lo que inventa la IA para un fichaje también)."""
    if not isinstance(t, dict):
        return {}
    limpio = {}
    for e, v in t.items():
        try:
            if e in EMOCIONES:
                limpio[e] = round(min(2.0, max(0.5, float(v))), 2)
        except (TypeError, ValueError):
            continue
    return limpio


def _apagar(animo: dict, ahora: datetime) -> None:
    try:
        horas = (ahora - datetime.fromisoformat(animo["hora"])).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        horas = 0
    if horas > 0:
        factor = 0.5 ** (horas / VIDA_HORAS)
        animo["mezcla"] = {e: v * factor for e, v in animo["mezcla"].items()}
    animo["hora"] = ahora.isoformat()


def sentir(agente: dict, hechos: list[tuple[str, float, str]], ahora: datetime, sumar: bool = False) -> None:
    """`hechos` = [(emoción, fuerza 0-1, motivo)]. Las situaciones (ir por detrás, un mal día de bolsa) suben la
    emoción hasta su fuerza; los sucesos (salta un stop) se suman a lo que ya sentía. El temperamento lo multiplica."""
    animo = agente.setdefault("animo", {"mezcla": {}, "motivos": {}, "hora": ahora.isoformat()})
    _apagar(animo, ahora)
    temperamento = agente.get("temperamento", {})
    for emocion, fuerza, motivo in hechos:
        if emocion not in EMOCIONES:
            continue
        f = fuerza * temperamento.get(emocion, 1.0)
        actual = animo["mezcla"].get(emocion, 0.0)
        animo["mezcla"][emocion] = min(1.0, actual + f if sumar else max(actual, f))
        animo["motivos"][emocion] = motivo
    for e in [e for e, v in animo["mezcla"].items() if v < MINIMO]:
        animo["mezcla"].pop(e)
        animo["motivos"].pop(e, None)
    animo["mezcla"] = {e: round(v, 3) for e, v in animo["mezcla"].items()}


def mezcla(agente: dict) -> list[dict]:
    """Las emociones de ahora en porcentaje, de la que más pesa a la que menos."""
    animo = agente.get("animo") or {"mezcla": {}, "motivos": {}}
    valores = dict(animo["mezcla"])
    resto = sum(v for e, v in valores.items() if e != "calma")
    calma = max(valores.get("calma", 0.0), CALMA_BASE - resto)
    if calma > 0:
        valores["calma"] = calma
    total = sum(valores.values()) or 1
    filas = [{"emocion": e, "cara": EMOCIONES[e][0], "nombre": EMOCIONES[e][1], "pct": v / total,
              "motivo": animo["motivos"].get(e) or ("No hay nada que le preocupe ni le emocione." if e == "calma" else "")}
             for e, v in valores.items()]
    return sorted(filas, key=lambda f: -f["pct"])


def dominante(agente: dict) -> dict:
    return mezcla(agente)[0]


def texto(agente: dict) -> str:
    filas = [f for f in mezcla(agente) if f["pct"] >= 0.1]
    lineas = "\n".join(f"- {f['nombre']} {f['pct'] * 100:.0f} %: {f['motivo']}" for f in filas)
    return ("TU ÁNIMO AHORA: lo que te ha pasado te empuja hacia estas emociones. No es una orden: es un espejo para "
            "que reconozcas las trampas en las que suelen caer los inversores (la euforia y la codicia hacen comprar de "
            "más; el miedo y la frustración, vender en el peor momento; la envidia, perseguir lo que ya subió).\n" + lineas +
            "\nTus reglas y tu estrategia van primero: decide como si no sintieras nada y di en \"animo\" cómo te sientes "
            "y cómo evitas que te influya.")


# --- Lo que le mueve: hechos que ya están en sus datos -------------------------------------------------

def senales(agente: dict, precios: dict[str, float], fecha: str, situacion: dict,
            cambio_sp: float | None = None, rondas_hoy: list[dict] | None = None) -> list[tuple[str, float, str]]:
    hechos = []
    dif, caida, dias = situacion["diferencia"], situacion["caida"], situacion["dias_restantes"]
    if caida < -config.MAX_CAIDA * 0.6:
        hechos.append(("panico", 0.8, f"Ha caído un {_pct(-caida)} desde su máximo y el límite de despido es un {config.MAX_CAIDA:.0%}."))
    if dif < 0 and dias <= 7:
        hechos.append(("miedo", 0.5, f"Quedan {dias} días para su evaluación y va {_puntos(-dif)} puntos por detrás del S&P 500."))
    if dif > 0.02:
        hechos.append(("euforia", 0.4, f"Le saca {_puntos(dif)} puntos al S&P 500."))
    elif dif > 0:
        hechos.append(("confianza", 0.4, f"Va {_puntos(dif)} puntos por delante del S&P 500."))
    elif dif > -0.02:
        hechos.append(("tension", 0.35, f"Va justo por detrás del S&P 500 ({_puntos(-dif)} puntos)."))
    else:
        hechos.append(("tension", 0.5, f"Va {_puntos(-dif)} puntos por detrás del S&P 500 y tiene que remontar."))
    if cambio_sp is not None:
        if cambio_sp <= -0.015:
            hechos.append(("miedo", 0.45, f"El S&P 500 cae un {_pct(-cambio_sp)} hoy."))
        elif cambio_sp >= 0.015:
            hechos.append(("euforia", 0.3, f"El S&P 500 sube un {_pct(cambio_sp)} hoy."))
        if abs(cambio_sp) >= 0.025:
            hechos.append(("sorpresa", 0.5, f"Movimiento enorme del mercado: el S&P 500 {_pct(cambio_sp, True)} hoy."))
    for s, pos in agente["posiciones"].items():
        if (p := precios.get(s)) and pos["coste_medio"] and abs(p / pos["coste_medio"] - 1) >= 0.06:
            hechos.append(("sorpresa", 0.3, f"{s} se ha movido un {_pct(p / pos['coste_medio'] - 1, True)} desde que lo compró."))
            break
    notas = notas_operaciones(agente, precios, fecha)[-3:]
    if len(notas) == 3 and all(n["bien"] for n in notas):
        hechos += [("euforia", 0.3, "Sus 3 últimas operaciones le han salido bien."),
                   ("codicia", 0.35, "Con la racha ganadora le tienta meter más dinero.")]
    elif len(notas) >= 2 and sum(not n["bien"] for n in notas) >= 2:
        hechos.append(("frustracion", 0.4, f"{sum(not n['bien'] for n in notas)} de sus 3 últimas operaciones le han salido mal."))
    ventas_mal = [n for n in (aprendizaje.boletin(agente, precios, fecha)[-5:] if precios and fecha else [])
                  if n["lado"] == "vender" and not n["bien"]]
    if ventas_mal:
        n = ventas_mal[-1]
        hechos.append(("arrepentimiento", 0.35, f"Vendió {n['simbolo']} y desde entonces ha ido mejor que el S&P 500 ({_pct(n['rent'], True)})."))
    descartes = [d for d in agente.get("descartes", []) if "rent" in d][-3:]
    if (malos := [d for d in descartes if d["rent"] > d["rent_sp"]]):
        d = malos[-1]
        hechos.append(("envidia", 0.35, f"Dejó pasar {d['simbolo']} y después hizo {_pct(d['rent'], True)}."))
    if aprendizaje.inactivo(agente):
        intradia = agente.get("equipo", "diario") == "intradia"
        hechos.append(("impaciencia" if intradia else "aburrimiento", 0.45, "Lleva demasiado tiempo sin operar."))
    if rondas_hoy and len(rondas_hoy) >= 3 and not any(r.get("operaciones") for r in rondas_hoy[-3:]):
        hechos.append(("impaciencia", 0.4, f"Lleva {len(rondas_hoy)} rondas hoy sin ver nada claro."))
    # Cambios de puesto: alivio y orgullo al ponerse por delante, frustración al quedarse atrás
    antes = (agente.get("animo") or {}).get("dif")
    if antes is not None and antes <= 0 < dif:
        hechos += [("alivio", 0.5, "Ha vuelto a ponerse por delante del S&P 500."),
                   ("orgullo", 0.3, "Le está ganando al testigo.")]
    elif antes is not None and antes > 0 >= dif:
        hechos.append(("frustracion", 0.3, "Se ha quedado por detrás del S&P 500."))
    return hechos


def actualizar(agente: dict, precios: dict[str, float], fecha: str, situacion: dict, ahora: datetime,
               cambio_sp: float | None = None, rondas_hoy: list[dict] | None = None) -> dict:
    """Recalcula el ánimo antes de decidir. Devuelve la emoción que manda (la que llevarán sus operaciones)."""
    sentir(agente, senales(agente, precios, fecha, situacion, cambio_sp, rondas_hoy), ahora)
    agente["animo"]["dif"] = round(situacion["diferencia"], 5)
    return dominante(agente)


# Al cerrar la bolsa, cuánto pesa su día y cuánto su historial de operaciones: el intradía lo cierra todo cada tarde
# y su día dice mucho; al diario, con una decisión al día, le dice más cómo le van sus últimas operaciones
PESOS_CIERRE = {"intradia": {"dia": 0.45, "historial": 0.2}, "diario": {"dia": 0.15, "historial": 0.4}}


def al_cierre(agente: dict, dif_dia: float | None, precios: dict[str, float], fecha: str, situacion: dict,
              ahora: datetime, cambio_sp: float | None = None) -> None:
    """El ánimo con el que se va a casa: las señales de siempre, su día frente al S&P 500 (`dif_dia`, en tanto por
    uno) y el balance de sus últimas operaciones con nota, cada cosa con el peso de su equipo."""
    pesos = PESOS_CIERRE["intradia" if agente.get("equipo", "diario") == "intradia" else "diario"]
    actualizar(agente, precios, fecha, situacion, ahora, cambio_sp)
    hechos = []
    if dif_dia is not None and abs(dif_dia) >= 0.002:
        hechos.append(("orgullo", pesos["dia"], f"Hoy le ha ganado al S&P 500 por {_puntos(dif_dia)} puntos.") if dif_dia > 0
                      else ("frustracion", pesos["dia"], f"Hoy ha ido {_puntos(-dif_dia)} puntos peor que el S&P 500."))
    notas = notas_operaciones(agente, precios, fecha)[-10:]
    if len(notas) >= 4:
        bien = sum(n["bien"] for n in notas)
        if bien / len(notas) >= 0.6:
            hechos.append(("confianza", pesos["historial"], f"{bien} de sus últimas {len(notas)} operaciones le han salido bien."))
        elif bien / len(notas) <= 0.4:
            hechos.append(("tension", pesos["historial"], f"Solo {bien} de sus últimas {len(notas)} operaciones le han salido bien."))
    sentir(agente, hechos, ahora)


def por_ventas_automaticas(agente: dict, hechas: list[dict], ahora: datetime) -> None:
    """Un stop que salta frustra; un objetivo alcanzado da orgullo."""
    hechos = []
    for o in hechas:
        if str(o.get("motivo", "")).startswith("Stop"):
            hechos.append(("frustracion", 0.45, f"Le ha saltado el stop de {o['simbolo']}."))
        elif str(o.get("motivo", "")).startswith("Objetivo"):
            hechos += [("orgullo", 0.4, f"{o['simbolo']} ha llegado a su objetivo."), ("euforia", 0.2, f"{o['simbolo']} ha llegado a su objetivo.")]
    if hechos:
        sentir(agente, hechos, ahora, sumar=True)


def etiquetar(operaciones: list[dict], emocion: str, con_noticias: bool) -> None:
    for o in operaciones:
        o["emocion"] = emocion
        o["noticias"] = con_noticias


# --- Con qué ánimo opera mejor o peor --------------------------------------------------------------

MAX_NOTAS = 60


def apuntar_dia_intradia(agente: dict, fecha: str, operaciones: list[dict]) -> None:
    """Intradía: al cierre, cada compra del día lleva su nota (si ese valor le dio dinero o se lo quitó)."""
    resultado: dict[str, float] = {}
    for o in operaciones:
        resultado[o["simbolo"]] = resultado.get(o["simbolo"], 0) + (o["importe"] if o["lado"] == "vender" else -o["importe"])
    for o in operaciones:
        if o["lado"] == "comprar" and o["simbolo"] in resultado:
            agente.setdefault("notas_emocion", []).append({
                "fecha": fecha, "simbolo": o["simbolo"], "emocion": o.get("emocion", ""), "noticias": bool(o.get("noticias")),
                "bien": resultado[o["simbolo"]] > 0})
    agente["notas_emocion"] = agente.get("notas_emocion", [])[-MAX_NOTAS:]


def notas_operaciones(agente: dict, precios: dict[str, float], fecha: str | None) -> list[dict]:
    """Las compras con nota, en orden: las del intradía al cierre y las del diario frente al S&P 500 (boletín)."""
    diario = [{"fecha": n["fecha"], "simbolo": n["simbolo"], "emocion": n.get("emocion", ""),
               "noticias": bool(n.get("noticias")), "bien": n["bien"]}
              for n in (aprendizaje.boletin(agente, precios, fecha) if precios and fecha else []) if n["lado"] == "comprar"]
    return sorted(agente.get("notas_emocion", []) + diario, key=lambda n: n["fecha"])


def por_emocion(agente: dict, precios: dict[str, float], fecha: str | None) -> dict[str, tuple[int, int]]:
    cuentas: dict[str, list[int]] = {}
    for n in notas_operaciones(agente, precios, fecha):
        if n.get("emocion") in EMOCIONES:
            c = cuentas.setdefault(n["emocion"], [0, 0])
            c[0] += n["bien"]
            c[1] += 1
    return {e: (b, t) for e, (b, t) in cuentas.items()}


def texto_notas(agente: dict, precios: dict[str, float], fecha: str | None) -> str:
    cuentas = por_emocion(agente, precios, fecha)
    if not cuentas:
        return ""
    partes = [f"con {EMOCIONES[e][1].lower()} {b} de {t} bien" for e, (b, t) in sorted(cuentas.items(), key=lambda kv: -kv[1][1])]
    return "Cómo te salen las compras según tu ánimo al hacerlas: " + "; ".join(partes) + "."
