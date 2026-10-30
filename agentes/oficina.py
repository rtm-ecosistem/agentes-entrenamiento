"""Lo que la oficina aprende y premia: el manual común con las lecciones de todos y la graduación al dinero real."""

from __future__ import annotations

from datetime import date

from agentes import config

MAX_LECCIONES = 30
DIAS_GRADUACION = 182            # unos 6 meses superando al S&P 500...
BACHE_SP = 0.05                  # ...y si el S&P cae más de un 5 % en ese tiempo, el agente debe caer menos


# --- Manual de la oficina ------------------------------------------------------------

def anotar_leccion(estado: dict, agente: dict, fecha: str, tipo: str, texto: str) -> None:
    texto = (texto or "").strip()
    if not texto:
        return
    estado.setdefault("manual", []).append({"fecha": fecha, "agente": agente["id"],
                                            "quien": f"{agente.get('alias', '')} · {agente['nombre']}",
                                            "tipo": tipo, "texto": texto[:500]})
    estado["manual"] = estado["manual"][-MAX_LECCIONES:]


def texto_manual(estado: dict, maximo: int = 12, cartas: int = 3) -> str:
    """El manual común y las últimas cartas de despedida: las lee toda la oficina, no solo el sustituto."""
    lecciones = estado.get("manual", [])[-maximo:]
    etiqueta = {"despido": "despedido", "evaluacion": "superó su evaluación", "reflexion": "reflexión"}
    partes = []
    if lecciones:
        partes.append("Manual de la oficina (lo que han aprendido los agentes que estuvieron o están aquí; aprende de sus "
                      "errores y aciertos):\n" + "\n".join(
                          f"- {l['fecha']} · {l['quien']} ({etiqueta.get(l['tipo'], l['tipo'])}): {l['texto']}" for l in lecciones))
    despedidos = sorted((a for a in estado.get("agentes", []) if a.get("estado") == "despedido" and a.get("carta")),
                        key=lambda a: a.get("despedido") or "")[-cartas:]
    if despedidos:
        partes.append("Últimas cartas de despedida de la oficina (escritas por agentes despedidos para que nadie repita "
                      "sus errores):\n" + "\n".join(
                          f"- {a.get('despedido') or ''} · {a.get('alias', '')} · {a['nombre']}: {a['carta']}" for a in despedidos))
    return "\n\n".join(partes)


# --- Graduación al dinero real -------------------------------------------------------

def _caida_maxima(valores: list[float]) -> tuple[float, int, int]:
    """La mayor caída desde un máximo: (caída negativa, índice del máximo, índice del mínimo)."""
    pico, i_pico, peor = valores[0], 0, (0.0, 0, 0)
    for i, v in enumerate(valores):
        if v > pico:
            pico, i_pico = v, i
        if v / pico - 1 < peor[0]:
            peor = (v / pico - 1, i_pico, i)
    return peor


def progreso_graduacion(agente: dict, referencia: list[dict]) -> dict | None:
    """Cómo va el agente en su camino al dinero real (None si aún no tiene datos)."""
    ref = {r["fecha"]: r["precio"] for r in referencia if r.get("precio")}
    h = [p for p in agente.get("historial", []) if p["fecha"] in ref]
    if not h:
        return None
    inicio = agente.get("creado") or h[0]["fecha"]
    dias = (date.fromisoformat(h[-1]["fecha"]) - date.fromisoformat(inicio)).days
    valor_inicial = config.CAPITAL_INICIAL if agente.get("creado") else h[0]["valor"]
    periodo = agente.get("periodo") or {}
    ref_inicial = agente.get("referencia_inicial") or (
        periodo.get("referencia") if periodo.get("inicio") == agente.get("creado") else None) or ref[h[0]["fecha"]]
    suyo = h[-1]["valor"] / valor_inicial - 1
    sp = ref[h[-1]["fecha"]] / ref_inicial - 1
    valores, precios = [p["valor"] for p in h], [ref[p["fecha"]] for p in h]
    caida = _caida_maxima(valores)[0]
    bache_sp, i0, i1 = _caida_maxima(precios)
    bache_ok = True
    if bache_sp < -BACHE_SP:  # en el tramo en que cayó el S&P, el agente debe caer menos
        tramo = valores[i0:i1 + 1]
        bache_ok = min(tramo) / max(tramo[0], 1e-9) - 1 > bache_sp
    listo = dias >= DIAS_GRADUACION and suyo > sp and caida >= -config.MAX_CAIDA and bache_ok
    return {"dias": dias, "objetivo": DIAS_GRADUACION, "rentabilidad": suyo, "referencia": sp,
            "diferencia": suyo - sp, "caida_maxima": caida, "bache_sp": bache_sp, "bache_ok": bache_ok,
            "listo": listo}


def revisar_graduaciones(estado: dict, fecha: str, avisos, anotar_bitacora) -> None:
    """Avisa (una vez) por GitHub cuando un agente cumple los requisitos para pasar a dinero real."""
    for a in estado["agentes"]:
        if a["estado"] != "activo" or a.get("graduado"):
            continue
        p = progreso_graduacion(a, estado.get("referencia", []))
        if not p or not p["listo"]:
            continue
        a["graduado"] = fecha
        texto = (f"🎓 Graduado: en {p['dias']} días ha ganado un {p['rentabilidad']:+.2%} frente al "
                 f"{p['referencia']:+.2%} del S&P 500, sin caer más de un {-p['caida_maxima']:.1%}.")
        anotar_bitacora(a, fecha, texto, tipo="graduacion")
        anotar_leccion(estado, a, fecha, "evaluacion", f"Se graduó para dinero real con la estrategia «{a['nombre']}».")
        try:
            avisos.crear_issue(f"🎓 {a.get('alias', '')} · {a['nombre']} está listo para dinero real", f"""{texto}

Cumple los requisitos acordados: al menos {DIAS_GRADUACION} días superando al S&P 500 contando costes, sin caer más de un {config.MAX_CAIDA:.0%} desde su máximo{' y cayendo menos que el S&P en su peor bache' if p['bache_sp'] < -BACHE_SP else ''}.

**Nada se mueve solo:** si quieres darle dinero real (por ejemplo, 200 $), dímelo y lo preparamos juntos. Si prefieres esperar, cierra este aviso.""", ["graduacion"])
        except Exception as e:
            print(f"Aviso: no se pudo crear el aviso de graduación: {e}")
