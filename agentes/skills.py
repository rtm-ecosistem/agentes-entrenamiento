"""Las skills de cada agente salen de sus resultados reales y él las lee como sus puntos fuertes y débiles.

Valentía y Actividad describen cómo opera; el resto dice si le sale bien: Olfato (días que sube, sin contar en
contra los días en que cae todo el mercado), Disciplina (cumple sus reglas), Timing (acierta con lo que compra y
con lo que descarta), Lectura de noticias (ganan las operaciones que hace por noticias) y Sangre fría (le salen
bien las operaciones que hace con miedo, euforia u otra emoción fuerte).
"""

from __future__ import annotations

from agentes import cartera, config, emociones

MINIMO_DATOS = 2
DE_RESULTADOS = ("olfato", "disciplina", "timing", "noticias", "sangre_fria")


def _ratio(bien: int, total: int, minimo: int = MINIMO_DATOS) -> float | None:
    return bien / total if total >= minimo else None


def olfato(agente: dict, referencia: list[dict] | None) -> float | None:
    if not referencia:
        return None
    sp = {r["fecha"]: r["precio"] for r in referencia}
    h = agente.get("historial", [])
    subidas = cuentan = 0
    for antes, p in zip(h, h[1:]):
        sube = p["valor"] > antes["valor"]
        if sp.get(p["fecha"]) and sp.get(antes["fecha"]) and sp[p["fecha"]] < sp[antes["fecha"]] and not sube:
            continue  # cae todo el mercado y él también: no le cuenta en contra
        cuentan += 1
        subidas += sube
    return subidas / cuentan if cuentan else None


def calcular(agente: dict, precios: dict[str, float], fecha: str | None, referencia: list[dict] | None = None) -> list[dict]:
    notas = emociones.notas_operaciones(agente, precios, fecha)
    valor = cartera.valor(agente, precios) if precios else None
    decisiones = [b for b in agente.get("bitacora", []) if b.get("tipo") == "decision"]
    reglas = agente.get("reglas", [])
    cumplidas = sum(r.get("cumplida", 0) for r in reglas)
    veces = cumplidas + sum(r.get("incumplida", 0) for r in reglas)
    descartes = [d for d in agente.get("descartes", []) if "rent" in d]
    con_noticias = [n for n in notas if n.get("noticias")]
    intensas = [n for n in notas if n.get("emocion") in emociones.INTENSAS]
    if intensas:
        sangre = _ratio(sum(n["bien"] for n in intensas), len(intensas))
        ayuda_sangre = "Cómo le salen las operaciones que hace con miedo, euforia u otra emoción fuerte"
    else:
        caida = (valor / agente["valor_maximo"] - 1) if valor and agente.get("valor_maximo") else None
        sangre = min(1.0, max(0.0, 1 + caida / config.MAX_CAIDA)) if caida is not None and agente["estado"] == "activo" else None
        ayuda_sangre = "Aún no ha operado con emociones fuertes: de momento, lo lejos que está del límite de caída"
    return [
        {"clave": "olfato", "nombre": "Olfato", "valor": olfato(agente, referencia),
         "ayuda": "Días en que su cartera sube. Los días en que cae todo el mercado no le cuentan en contra; si ese día sube igualmente, le suma"},
        {"clave": "disciplina", "nombre": "Disciplina", "valor": _ratio(cumplidas, veces),
         "ayuda": "Cuántas veces cumple las reglas de su libreta"},
        {"clave": "timing", "nombre": "Timing", "valor": _ratio(sum(n["bien"] for n in notas) + sum(d["rent"] <= d["rent_sp"] for d in descartes),
                                                             len(notas) + len(descartes), 3),
         "ayuda": "Si acierta con lo que compra y con lo que deja pasar"},
        {"clave": "noticias", "nombre": "Lectura de noticias", "valor": _ratio(sum(n["bien"] for n in con_noticias), len(con_noticias)),
         "ayuda": "Si le salen bien las operaciones que hace por noticias"},
        {"clave": "sangre_fria" if intensas else "sangre_fria_provisional", "nombre": "Sangre fría", "valor": sangre,
         "ayuda": ayuda_sangre},
        {"clave": "valentia", "nombre": "Valentía", "valor": 1 - agente["efectivo"] / valor if valor else None,
         "ayuda": "Cuánto de su dinero tiene invertido"},
        {"clave": "actividad", "nombre": "Actividad",
         "valor": min(1.0, sum(len(b.get("operaciones", [])) for b in decisiones) / len(decisiones) / 4) if decisiones else None,
         "ayuda": "Operaciones que hace cada día"},
    ]


def texto(agente: dict, precios: dict[str, float], fecha: str | None, referencia: list[dict] | None = None) -> str:
    utiles = [s for s in calcular(agente, precios, fecha, referencia) if s["clave"] in DE_RESULTADOS and s["valor"] is not None]
    if not utiles:
        return ""
    fuerte, debil = max(utiles, key=lambda s: s["valor"]), min(utiles, key=lambda s: s["valor"])
    lineas = " · ".join(f"{s['nombre']} {s['valor'] * 100:.0f}" for s in utiles)
    texto = f"TUS SKILLS (salen de tus resultados, de 0 a 100): {lineas}."
    if len(utiles) > 1:
        texto += (f" Tu punto fuerte es {fuerte['nombre'].lower()} ({fuerte['ayuda'].lower()}); el débil, "
                  f"{debil['nombre'].lower()} ({debil['ayuda'].lower()}). Apóyate en lo fuerte y vigila lo débil.")
    return texto
