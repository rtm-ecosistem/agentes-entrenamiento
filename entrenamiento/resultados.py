"""RESULTADOS.md: cómo le habría ido a cada estrategia en los días simulados y qué ha aprendido cada agente."""

from __future__ import annotations

import csv
import json

from agentes import config

ARCHIVO = config.RAIZ / "RESULTADOS.md"


def escribir() -> None:
    if not config.ESTADO.exists():
        return
    estado = json.loads(config.ESTADO.read_text(encoding="utf-8"))
    ref = estado.get("referencia", [])
    if not ref:
        return
    operaciones: dict[str, int] = {}
    if config.OPERACIONES.exists():
        with config.OPERACIONES.open(encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                operaciones[fila.get("agente", "")] = operaciones.get(fila.get("agente", ""), 0) + 1
    sp = ref[-1]["precio"] / ref[0]["precio"] - 1
    lineas = [f"# Resultados del entrenamiento", "",
              f"Del {ref[0]['fecha']} al {ref[-1]['fecha']} ({len(ref)} días de bolsa). S&P 500: {sp:+.1%}.", "",
              "| Agente | Estrategia | Valor | Rentabilidad | Frente al S&P 500 | Peor caída | Operaciones |",
              "|---|---|---|---|---|---|---|"]
    diarios = [a for a in estado["agentes"] if a.get("equipo", "diario") == "diario"]
    for a in sorted(diarios, key=lambda a: -(a["historial"][-1]["valor"] if a["historial"] else 0)):
        h = [x["valor"] for x in a["historial"]] or [config.CAPITAL_INICIAL]
        maximo, caida = h[0], 0.0
        for v in h:
            maximo = max(maximo, v)
            caida = min(caida, v / maximo - 1)
        r = h[-1] / config.CAPITAL_INICIAL - 1
        titulo = a["estrategia"].splitlines()[0].lstrip("# ").strip()
        lineas.append(f"| {a.get('alias', a['nombre'])} | {titulo} | {h[-1]:,.0f} $ | {r:+.1%} | {(r - sp) * 100:+.1f} puntos | "
                      f"{caida:.1%} | {operaciones.get(a['id'], 0)} |")
    lineas += ["", "## Lo que ha aprendido cada uno", ""]
    for a in diarios:
        lineas += [f"### {a.get('alias', a['nombre'])} ({a['nombre']})", ""]
        reglas = [r for r in a.get("reglas", []) if not r.get("tachada")]
        lineas += [f"- {r['texto']}" for r in reglas] or ["- (sin reglas propias)"]
        if a.get("reflexiones"):
            lineas += ["", f"Última reflexión ({a['reflexiones'][-1]['fecha']}): {a['reflexiones'][-1]['texto']}"]
        lineas.append("")
    ARCHIVO.write_text("\n".join(lineas), encoding="utf-8")
