"""Genera PANEL.md (se ve en GitHub, también en la app del móvil) y un gráfico SVG."""

from __future__ import annotations

from agentes import config

COLORES = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed", "#0891b2", "#65a30d", "#dc2626"]


def grafico_svg(agentes: list[dict], referencia: list[tuple[str, float]]) -> str:
    """Valor de cada agente frente a 10.000 $ invertidos en el S&P 500 al comienzo del experimento."""
    ancho, alto, m = 800, 360, 50
    series = [(a.get("alias", a["nombre"]), [(h["fecha"], h["valor"]) for h in a["historial"]]) for a in agentes]
    series = [(n, s) for n, s in series if s]
    if referencia:
        base = referencia[0][1]
        series.append(("S&P 500", [(f, config.CAPITAL_INICIAL * p / base) for f, p in referencia]))
    fechas = sorted({f for _, s in series for f, _ in s})
    if len(fechas) < 2:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{ancho}" height="80">'
                '<text x="10" y="40" font-family="sans-serif">El gráfico aparecerá tras dos días de datos.</text></svg>')
    valores = [v for _, s in series for _, v in s]
    vmin, vmax = min(valores) * 0.995, max(valores) * 1.005
    x = {f: m + i * (ancho - 2 * m) / (len(fechas) - 1) for i, f in enumerate(fechas)}

    def y(v: float) -> float:
        return alto - m - (v - vmin) / (vmax - vmin) * (alto - 2 * m)

    partes = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{ancho}" height="{alto + 30 * ((len(series) + 3) // 4)}" '
              'font-family="sans-serif" font-size="12">',
              f'<rect width="100%" height="100%" fill="white"/>']
    for i in range(5):
        v = vmin + i * (vmax - vmin) / 4
        partes.append(f'<line x1="{m}" x2="{ancho - m}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e5e7eb"/>'
                      f'<text x="4" y="{y(v) + 4:.1f}" fill="#6b7280">{v:,.0f}</text>')
    partes.append(f'<text x="{m}" y="{alto - m + 18}" fill="#6b7280">{fechas[0]}</text>'
                  f'<text x="{ancho - m}" y="{alto - m + 18}" fill="#6b7280" text-anchor="end">{fechas[-1]}</text>')
    for i, (nombre, s) in enumerate(series):
        color = "#111827" if nombre == "S&P 500" else COLORES[i % len(COLORES)]
        trazo = ' stroke-dasharray="6 4"' if nombre == "S&P 500" else ""
        puntos = " ".join(f"{x[f]:.1f},{y(v):.1f}" for f, v in s)
        partes.append(f'<polyline fill="none" stroke="{color}" stroke-width="2"{trazo} points="{puntos}"/>')
        lx, ly = m + (i % 4) * 180, alto + 10 + 30 * (i // 4)
        partes.append(f'<line x1="{lx}" x2="{lx + 20}" y1="{ly}" y2="{ly}" stroke="{color}" stroke-width="3"{trazo}/>'
                      f'<text x="{lx + 26}" y="{ly + 4}">{nombre}</text>')
    partes.append("</svg>")
    return "\n".join(partes)


def _pct(v: float) -> str:
    return f"{v * 100:+.2f} %"


def panel_md(estado: dict, precios: dict[str, float], fecha: str) -> str:
    from agentes.cartera import valor

    ref_actual = precios.get(config.REFERENCIA)
    filas = []
    activos = [a for a in estado["agentes"] if a["estado"] == "activo"]
    for a in sorted(activos, key=lambda a: -valor(a, precios)):
        v = valor(a, precios)
        p = a["periodo"]
        ref = (ref_actual / p["referencia"] - 1) if ref_actual and a.get("empezo") else 0  # sin invertir no se compara
        filas.append(f"| {a.get('alias', a['nombre'])} · {a['nombre']} | {v:,.0f} $ | {_pct(v / config.CAPITAL_INICIAL - 1)} | "
                     f"{_pct(v / p['valor'] - 1)} | {_pct(ref)} | {_pct(v / a['valor_maximo'] - 1)} | "
                     f"{p['inicio']} | [diario](datos/diarios/{a['id']}.md) |")
    despedidos = [f"| {a.get('alias', a['nombre'])} · {a['nombre']} | {a.get('despedido', '')} | {a.get('motivo', '')} | [diario](datos/diarios/{a['id']}.md) |"
                  for a in estado["agentes"] if a["estado"] == "despedido"]
    pendientes = [f"- **{p['nombre']}**: pendiente de tu revisión en la [issue #{p['issue']}](../../issues/{p['issue']})"
                  for p in estado.get("propuestas", []) if p["estado"] == "pendiente" and p.get("issue")]

    texto = f"""# Panel de agentes

Actualizado: **{fecha}**. Todo es dinero ficticio. Los agentes deciden una vez al día, cuando la bolsa de EE. UU. lleva una hora abierta.

![Evolución](panel/evolucion.svg)

## Agentes en activo

| Agente | Valor | Desde que empezó | Periodo actual | S&P 500 en el periodo | Caída desde máximo | Periodo desde | Razonamientos |
|---|---|---|---|---|---|---|---|
{chr(10).join(filas) or "| (ninguno) |"}

Para seguir en el puesto, cada agente debe superar al S&P 500 en su periodo de {config.DIAS_EVALUACION} días (el Copión, 91, y el del Congreso, 56) y no caer más de un {config.MAX_CAIDA:.0%} desde su máximo.
"""
    if pendientes:
        texto += "\n## Estrategias pendientes de tu revisión\n\n" + "\n".join(pendientes) + "\n"
    if despedidos:
        texto += ("\n## Despedidos\n\n| Agente | Fecha | Motivo | Razonamientos |\n|---|---|---|---|\n"
                  + "\n".join(despedidos) + "\n")
    texto += "\nTodas las operaciones están en [datos/operaciones.csv](datos/operaciones.csv).\n"
    return texto
