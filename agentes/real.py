"""«Si fuera real»: cómo iría la oficina si se hubiera metido dinero de verdad desde el primer día.

El capital se reparte a partes iguales entre los puestos (un puesto es un agente y quienes le sustituyen al
despedirle: el dinero pasa al sustituto tal y como lo dejó). Cada parte sube o baja lo mismo que la cartera de
su agente. La comparación con el S&P 500 es justa con cada uno: la parte de cada puesto entra en el índice el
mismo día que empezó su primer agente (hasta entonces está en efectivo) y sigue ahí aunque cambie el agente. La versión con comisiones descuenta lo que cobraría un bróker barato por cada operación. Se ignora el
cambio euro-dólar. No gasta IA: solo usa el historial y el registro de operaciones.
"""

from __future__ import annotations

import csv

from agentes import config


def puestos(estado: dict) -> list[list[dict]]:
    """Cada puesto: la lista de agentes que lo han ocupado, en orden."""
    por_id = {a["id"]: a for a in estado["agentes"]}
    sucesor = {}
    for p in estado.get("propuestas", []):
        if p.get("estado") == "aprobada" and p.get("issue"):
            nuevo = next((a["id"] for a in estado["agentes"] if a.get("origen") == f"propuesta {p['issue']}"), None)
            if nuevo and p.get("sustituye") in por_id:
                sucesor[p["sustituye"]] = nuevo
    sucedidos = set(sucesor.values())
    cadenas = []
    for a in estado["agentes"]:
        if a["id"] in sucedidos:
            continue
        cadena = [a["id"]]
        while cadena[-1] in sucesor and sucesor[cadena[-1]] not in cadena:
            cadena.append(sucesor[cadena[-1]])
        cadenas.append([por_id[i] for i in cadena])
    return cadenas


def operaciones_por_dia() -> dict[tuple[str, str], int]:
    if not config.OPERACIONES.exists():
        return {}
    cuenta: dict[tuple[str, str], int] = {}
    with config.OPERACIONES.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            clave = (fila["agente"], fila["fecha"])
            cuenta[clave] = cuenta.get(clave, 0) + 1
    return cuenta


def calcular(estado: dict, historiales: dict[str, list[dict]], referencia: list[dict],
             capital: float = config.CAPITAL_REAL, comision: float = config.COMISION_REAL) -> dict | None:
    """`historiales`: el historial diario de cada agente (con el punto de hoy). Devuelve los días y los puestos."""
    cadenas = puestos(estado)
    fechas = sorted({p["fecha"] for h in historiales.values() for p in h})
    if not cadenas or not fechas:
        return None
    ops = operaciones_por_dia()
    parte = capital / len(cadenas)
    sp = {r["fecha"]: r["precio"] for r in referencia if r.get("precio")}
    totales = {f: [0.0, 0.0, 0.0] for f in fechas}
    filas = []
    for cadena in cadenas:
        dinero, base, comisiones, n_ops = parte, None, 0.0, 0
        actual, ultimo_agente = parte, None
        # La alternativa: el mismo dinero metido en el S&P 500 el día que empezó el primer agente del puesto
        sp_base, sp_ultimo, en_sp = None, None, parte
        serie = {}  # por día: quién ocupaba el puesto y cuánto tenía (la web lo usa para la curva minuto a minuto)
        valores = {a["id"]: {p["fecha"]: p["valor"] for p in historiales.get(a["id"], [])} for a in cadena}
        for f in fechas:
            # Quién ocupa el puesto ese día: el último de la cadena que ya había empezado
            agente = next((a for a in reversed(cadena) if a["creado"] <= f), None)
            if agente is not None and agente is not ultimo_agente:
                dinero, base, ultimo_agente = actual, valores[agente["id"]].get(f) or config.CAPITAL_INICIAL, agente
            if agente is not None and f in valores[agente["id"]]:
                actual = dinero * valores[agente["id"]][f] / base
                n = ops.get((agente["id"], f), 0)
                n_ops += n
                comisiones += n * comision
                if sp_base is None and f in sp:
                    sp_base = sp[f]
                serie[f] = [agente["id"], round(actual, 2)]
            if sp_base is not None:
                sp_ultimo = sp.get(f, sp_ultimo or sp_base)
                en_sp = parte * sp_ultimo / sp_base
            totales[f][0] += actual
            totales[f][1] += actual - comisiones
            totales[f][2] += en_sp
        filas.append({"agentes": [a.get("alias") or a["nombre"] for a in cadena], "id": cadena[-1]["id"],
                      "inicio": round(parte, 2), "sin": round(actual, 2), "con": round(actual - comisiones, 2),
                      "operaciones": n_ops, "comisiones": round(comisiones, 2),
                      "sp": round(en_sp, 2), "serie": serie})
    dias = [{"f": f, "sin": round(s, 2), "con": round(c, 2), "sp": round(e, 2) if sp else None}
            for f, (s, c, e) in totales.items()]
    return {"capital": capital, "comision": comision, "dias": dias, "puestos": filas}
