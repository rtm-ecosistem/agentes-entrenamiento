"""Genera la web: web/index.html (agentes, clasificación, operaciones) y web/noticias.json (el periódico)."""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agentes import analistas, apariencias, aprendizaje, cartera, config, emociones, oficina, prompts, real, seguimiento, skills

NUEVA_YORK = ZoneInfo("America/New_York")

PLANTILLA = config.RAIZ / "agentes" / "plantilla_web.html"
# Láminas de los personajes y demás imágenes: se copian junto a la página
RECURSOS = config.RAIZ / "agentes" / "recursos"
# Uno distinto para cada mesa de la oficina, así en las gráficas con todos no se confunden
COLORES = ["#6c5ce7", "#00b894", "#e17055", "#0984e3", "#e84393", "#00cec9", "#d63031", "#f9ca24", "#badc58", "#ff9ff3",
           "#a0522d", "#dfe6e9"]


def _operaciones_recientes(n: int = 200) -> list[dict]:
    if not config.OPERACIONES.exists():
        return []
    with config.OPERACIONES.open(encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    return filas[-n:][::-1]


def _movimientos(estado: dict) -> dict[str, dict[str, list]]:
    """Las compras y ventas de lo que tiene ahora cada agente: {id: {símbolo: [[fecha, hora UTC, cantidad ±], ...]}}.
    Con ellas la web sabe cuántas acciones tenía en cada momento, para la gráfica de cada acción de su ficha."""
    if not config.OPERACIONES.exists():
        return {}
    tiene = {(a["id"], s) for a in estado["agentes"] if a["estado"] == "activo" for s in a.get("posiciones", {})}
    movs: dict[str, dict[str, list]] = {}
    with config.OPERACIONES.open(encoding="utf-8") as f:
        for o in csv.DictReader(f):
            if (o["agente"], o["simbolo"]) in tiene:
                q = float(o["cantidad"]) * (1 if o["lado"] == "comprar" else -1)
                movs.setdefault(o["agente"], {}).setdefault(o["simbolo"], []).append([o["fecha"], o.get("hora") or "", q])
    return movs


def _con_hoy(serie: list[dict], hoy: str | None, punto: dict) -> list[dict]:
    """La serie diaria con el punto de hoy al valor de los últimos precios (sustituye al de hoy si ya existe)."""
    if not hoy or not serie:
        return serie
    return [p for p in serie if p["fecha"] != hoy] + [{"fecha": hoy, **punto}]


def _dia_de_precios(estado: dict) -> str | None:
    """Fecha de la sesión (hora de Nueva York) de los últimos precios guardados."""
    hora = estado.get("precios_hora")
    return datetime.fromisoformat(hora).astimezone(NUEVA_YORK).date().isoformat() if hora else None


def _puntos_del_dia(estado: dict, hoy: str | None) -> list[dict]:
    """Los puntos guardados cada 15 minutos durante la sesión de hoy (vacío si son de otro día)."""
    dia = estado.get("dia_en_curso") or {}
    return dia.get("puntos", []) if hoy and dia.get("fecha") == hoy else []


def _agente_web(a: dict, i: int, precios: dict[str, float], fecha: str | None, hoy: str | None = None,
                puntos_dia: list[dict] | None = None, referencia: list[dict] | None = None) -> dict:
    v = cartera.valor(a, precios)
    posiciones = []
    for s, pos in a["posiciones"].items():
        precio = precios.get(s, pos["coste_medio"])
        posiciones.append({"simbolo": s, "valor": pos["cantidad"] * precio, "peso": pos["cantidad"] * precio / v if v else 0,
                           "coste": pos["coste_medio"], "precio": precio, "rent": precio / pos["coste_medio"] - 1})
    situacion = prompts.situacion_laboral(a, v, precios, fecha or a["periodo"]["inicio"])
    evolucion = aprendizaje.evolucion_semanal(a, referencia or [])
    return {
        "id": a["id"], "alias": a.get("alias", a["nombre"]), "nombre": a["nombre"],
        "personalidad": a.get("personalidad", ""), "color": COLORES[i % len(COLORES)],
        "equipo": a.get("equipo", "diario"), "fuente": a.get("fuente", ""),
        "estado": a["estado"], "estrategia": a["estrategia"], "creado": a["creado"],
        "despedido": a.get("despedido"), "motivo": a.get("motivo"), "carta": a.get("carta"),
        "carta_predecesor": a.get("carta_predecesor"), "valor": v, "efectivo": a["efectivo"],
        "rent_total": v / config.CAPITAL_INICIAL - 1, "situacion": situacion,
        # Inicio de su periodo de evaluación: la gráfica pequeña empieza aquí, igual que la diferencia con el S&P
        "periodo": {k: a["periodo"][k] for k in ("inicio", "valor", "referencia")},
        "periodos_superados": a.get("periodos_superados", 0),
        "animo": {"mezcla": emociones.mezcla(a),
                  "notas": [{"emocion": e, "cara": emociones.EMOCIONES[e][0], "nombre": emociones.EMOCIONES[e][1], "bien": b, "total": t}
                            for e, (b, t) in emociones.por_emocion(a, precios, hoy or fecha).items()]} if a.get("animo") else None,
        "skills": skills.calcular(a, precios, hoy or fecha, referencia),
        "posiciones": sorted(posiciones, key=lambda x: -x["valor"]),
        # Los activos llevan el punto de hoy al día: así las gráficas se mueven con cada actualización de precios
        "historial": _con_hoy(a["historial"], hoy if a["estado"] == "activo" else None, {"valor": round(v, 2)}),
        # Evolución de hoy cada 15 minutos: [hora UTC, valor]
        "hoy": [[p["hora"], p["valores"][a["id"]]] for p in (puntos_dia or []) if a["id"] in p.get("valores", {})],
        "bitacora": a.get("bitacora", [])[::-1],
        "reflexiones": a.get("reflexiones", [])[::-1],
        "aprendizaje": {
            "reglas": [{**r, "ventaja": aprendizaje.rendimiento_regla(a, r, precios, hoy or fecha)}
                       for r in a.get("reglas", [])] if (hoy or fecha) else a.get("reglas", []),
            "aciertos": list(aprendizaje.aciertos(a)),
            "por_confianza": {c: list(v) for c, v in aprendizaje.aciertos_por_confianza(a).items()},
            "ajustes": a.get("ajustes", []),
            "memoria_larga": a.get("memoria_larga"),
            "rescate": a.get("rescate"),
            "evolucion": evolucion,
            "vicios": aprendizaje.vicios(a, precios, hoy or fecha) if (hoy or fecha) else {},
            "veredicto": aprendizaje.veredicto(evolucion),
            "predicciones": a.get("predicciones", [])[::-1],
            "descartes": a.get("descartes", [])[::-1][:8],
            "inactivo": aprendizaje.inactivo(a),
            "boletin": aprendizaje.boletin(a, precios, hoy or fecha)[::-1] if (hoy or fecha) else [],
        },
    }


def empleado_del_mes(agentes: list[dict], referencia: list[dict], hoy: str | None, dias: int = 30) -> str | None:
    """El agente activo que más le saca al S&P 500 en los últimos 30 días (con al menos 5 días de datos)."""
    if not hoy or not referencia:
        return None
    desde = (datetime.fromisoformat(hoy) - timedelta(days=dias)).date().isoformat()
    ref = {r["fecha"]: r["precio"] for r in referencia if r.get("precio")}
    mejor, mejor_dif = None, 0.0
    for a in agentes:
        h = [p for p in a["historial"] if p["fecha"] >= desde and p["fecha"] in ref]
        if a["estado"] != "activo" or len(h) < 5:
            continue
        dif = (h[-1]["valor"] / h[0]["valor"] - 1) - (ref[h[-1]["fecha"]] / ref[h[0]["fecha"]] - 1)
        if dif > mejor_dif:
            mejor, mejor_dif = a["id"], dif
    return mejor


def datos_web(estado: dict, datos_noticias: dict, destino=None) -> dict:
    """`destino`: la carpeta de la web, donde se preparan las hojas de los personajes nuevos que falten."""
    precios = estado.get("precios", {})
    fecha = estado.get("ultima_ejecucion")
    hoy = _dia_de_precios(estado)
    puntos_dia = _puntos_del_dia(estado, hoy)
    agentes = [_agente_web(a, i, precios, fecha, hoy, puntos_dia, estado.get("referencia", []))
               for i, a in enumerate(estado["agentes"])]
    for web_a, a in zip(agentes, estado["agentes"]):
        # Desde aquí se compara con el S&P 500, en sus gráficas y en su periodo: antes no había invertido nada
        web_a["empezo"] = a.get("empezo")
        web_a["graduacion"] = oficina.progreso_graduacion(a, estado.get("referencia", []))
        web_a["graduado"] = a.get("graduado")
    # La oficina: la cara de cada uno (registro por ID) y su mesa, que es la del puesto que ocupa (el sustituto
    # se sienta donde se sentaba a quien sustituye)
    caras = apariencias.para_web(estado, destino)
    puesto, sustituye = {}, {}
    for equipo in ("diario", "intradia"):
        cadenas = [c for c in real.puestos(estado) if c[0].get("equipo", "diario") == equipo]
        for n, cadena in enumerate(cadenas):
            for k, a in enumerate(cadena):
                puesto[a["id"]] = n
                if k:
                    sustituye[a["id"]] = cadena[k - 1]["id"]
    movs = _movimientos(estado)
    for web_a in agentes:
        web_a["movs"] = movs.get(web_a["id"], {})
        web_a["apariencia"] = caras.get(web_a["id"])
        web_a["puesto"] = puesto.get(web_a["id"])
        web_a["sustituye"] = sustituye.get(web_a["id"])
    alias = {a["id"]: f"{a['alias']} · {a['nombre']}" for a in agentes}
    en_cartera = {s for m in movs.values() for s in m}
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pendientes = [{"nombre": f"{p.get('alias', '')} · {p['nombre']}",
                   "url": f"https://github.com/{repo}/issues/{p['issue']}" if repo and p.get("issue") else None}
                  for p in estado.get("propuestas", []) if p["estado"] == "pendiente"]
    referencia = _con_hoy(estado.get("referencia", []), hoy if config.REFERENCIA in precios else None,
                          {"precio": precios.get(config.REFERENCIA)})
    return {
        "actualizado": fecha,
        # Cuándo se rehízo la web: la página lo compara con version.json para saber si hay datos nuevos
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "precios_hora": estado.get("precios_hora"),
        "noticias_actualizadas": (datos_noticias.get("ultima_consulta") or "")[:16],
        "reglas": {"capital": config.CAPITAL_INICIAL, "max_peso": config.MAX_PESO_POSICION,
                   "max_caida": config.MAX_CAIDA, "dias": config.DIAS_EVALUACION,
                   "valores": len(config.UNIVERSO), "max_peso_radar": config.MAX_PESO_RADAR},
        "referencia": referencia,
        # El cierre de cada día de las acciones que tiene ahora alguien, para la gráfica por acción de su ficha
        "cierres": {f: c for f, x in estado.get("cierres", {}).items()
                    if (c := {s: v for s, v in x.items() if s in en_cartera})},
        "empleado_mes": empleado_del_mes(agentes, referencia, hoy),
        "referencia_hoy": [[p["hora"], p["ref"]] for p in puntos_dia if p.get("ref")],
        "agentes": agentes,
        "pendientes": pendientes,
        "manual": estado.get("manual", [])[::-1],
        "resumen": (datos_noticias.get("resumenes") or [None])[-1],
        "analistas": estado.get("analistas"),
        "eventos": [{"cuando": c, "texto": t} for c, t in (analistas.calendario(hoy or fecha, estado, dias=14) if hoy or fecha else [])],
        "analistas_historial": estado.get("analistas_historial", [])[-60:][::-1],
        "operaciones": [{**o, "nombre": alias.get(o["agente"], o["agente"])} for o in _operaciones_recientes()],
        "real": real.calcular(estado, {a["id"]: a["historial"] for a in agentes}, referencia),
    }


def noticias_web(datos_noticias: dict) -> list[dict]:
    """Versión compacta de las noticias para que la web cargue rápido."""
    return [{"id": n["id"], "f": n["fecha"][:16], "fu": n.get("fuente", ""), "u": n.get("url", ""),
             "t": n.get("titular_es") or n["titular"], "o": n["titular"], "e": n.get("explicacion", ""),
             "i": n.get("importancia", "baja"), "m": n.get("impacto", "neutral"), "ti": n.get("tipo", "otros"),
             "em": n.get("empresas") or n.get("simbolos", []), "us": n.get("usada_por", []),
             **({"p": 1} if n.get("pendiente") else {})}
            for n in datos_noticias.get("noticias", [])]


def noticias_recientes(lista: list[dict], ahora: datetime | None = None, horas: int = 48) -> list[dict]:
    """Las noticias de hoy y ayer, que es lo que se lee a diario: la web las carga primero y el resto al pedirlas."""
    desde = ((ahora or datetime.now(timezone.utc)) - timedelta(hours=horas)).isoformat()[:16]
    return [n for n in lista if n["f"] >= desde]


def generar(estado: dict, datos_noticias: dict | None = None) -> None:
    if datos_noticias is None:
        from agentes import noticias
        datos_noticias = noticias.cargar()
    config.WEB_DIR.mkdir(exist_ok=True)
    datos_pagina = datos_web(estado, datos_noticias, config.WEB_DIR)
    datos = json.dumps(datos_pagina, ensure_ascii=False).replace("</", "<\\/")
    html = PLANTILLA.read_text(encoding="utf-8").replace("__DATOS__", datos)
    (config.WEB_DIR / "index.html").write_text(html, encoding="utf-8")
    shutil.copytree(RECURSOS, config.WEB_DIR / "recursos", dirs_exist_ok=True)
    compactas = noticias_web(datos_noticias)
    for nombre, lista in (("noticias.json", compactas), ("noticias-recientes.json", noticias_recientes(compactas))):
        (config.WEB_DIR / nombre).write_text(json.dumps(lista, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # Las gráficas minuto a minuto van aparte: la web solo las carga al abrir la ventana de gráficas
    (config.WEB_DIR / "evolucion.json").write_text(json.dumps(seguimiento.para_web(estado), separators=(",", ":")), encoding="utf-8")
    (config.WEB_DIR / "version.json").write_text(json.dumps({"generado": datos_pagina["generado"]}), encoding="utf-8")
