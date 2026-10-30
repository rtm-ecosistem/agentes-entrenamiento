"""Ciclo diario: revisar propuestas, estudiar noticias, decidir, operar, evaluar, despedir y actualizar la web.

Se ejecuta desde GitHub Actions: `python -m agentes.ciclo`.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date, datetime, timezone

from agentes import analistas, apariencias, aprendizaje, cartera, config, emociones, fuentes, fundamentales, noticias, oficina, panel, prompts, seguimiento, web
from agentes.alpaca import NUEVA_YORK

VENTANA_MINUTOS = (50, 150)  # deciden entre ~1 h y 2,5 h después de la apertura
CAMPOS_OPERACION = ["fecha", "agente", "lado", "simbolo", "cantidad", "precio", "importe", "motivo", "hora"]


# --- Estado en disco ------------------------------------------------------------

def cargar_estado() -> dict:
    if config.ESTADO.exists():
        estado = json.loads(config.ESTADO.read_text(encoding="utf-8"))
        puesta_al_dia(estado)
        migrar_empezo(estado)
        suavizar_reglas(estado)
        return estado
    return {"agentes": [], "propuestas": [], "contador": 0, "ultima_ejecucion": None,
            "ultimo_resumen": None, "referencia": []}


def puesta_al_dia(estado: dict) -> None:
    """Una sola vez, al llegar las emociones: aprovecha lo que ya pasó en el último día (sin gastar IA) para que
    no empiecen de cero. La hora de sus operaciones en el registro, su primer ánimo (stops y objetivos que
    saltaron, rondas sin operar, su puesto) y las notas del intradía de ese día para sus skills."""
    if "emociones" in estado.get("migraciones", []) or not estado.get("agentes"):
        return
    estado.setdefault("migraciones", []).append("emociones")
    precios = estado.get("precios") or {}
    try:
        ahora = datetime.fromisoformat(estado.get("precios_hora") or "")
    except ValueError:
        ahora = datetime.now(timezone.utc)
    horas: dict[tuple, str] = {}  # (fecha, agente, lado, símbolo, importe) -> hora UTC
    for a in estado["agentes"]:
        for b in a.get("bitacora", []):
            if b.get("tipo") != "decision":
                continue
            for r in b.get("rondas", []):
                try:
                    local = datetime.fromisoformat(f"{b['fecha']}T{r['hora']}").replace(tzinfo=NUEVA_YORK)
                except (KeyError, ValueError):
                    continue
                for o in r.get("operaciones", []):
                    horas[(b["fecha"], a["id"], o["lado"], o["simbolo"], f"{float(o['importe']):.2f}")] = \
                        local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            if not b.get("rondas") and (estado.get("ultimo_ciclo_hora") or "").startswith(b["fecha"]):
                for o in b.get("operaciones", []):
                    horas[(b["fecha"], a["id"], o["lado"], o["simbolo"], f"{float(o['importe']):.2f}")] = \
                        estado["ultimo_ciclo_hora"][:16] + "Z"
    if horas and config.OPERACIONES.exists():
        with config.OPERACIONES.open(encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
        for fila in filas:
            if not fila.get("hora"):
                try:
                    fila["hora"] = horas.get((fila["fecha"], fila["agente"], fila["lado"], fila["simbolo"],
                                              f"{float(fila['importe']):.2f}"), "")
                except (KeyError, ValueError):
                    pass
        with config.OPERACIONES.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS_OPERACION, extrasaction="ignore", restval="")
            w.writeheader()
            w.writerows(filas)
    if config.REFERENCIA not in precios:
        return
    for a in estado["agentes"]:
        if a["estado"] != "activo" or a.get("animo"):
            continue
        ultimo = next((b for b in reversed(a.get("bitacora", [])) if b.get("tipo") in ("decision", "stop")), None)
        if ultimo:
            emociones.por_ventas_automaticas(a, ultimo.get("operaciones", []), ahora)
            if ultimo.get("rondas") and a.get("dia", {}).get("cerrado") and not a.get("notas_emocion"):
                emociones.apuntar_dia_intradia(a, ultimo["fecha"], ultimo["operaciones"])
        situacion = prompts.situacion_laboral(a, cartera.valor(a, precios), precios, ahora.date().isoformat())
        emociones.actualizar(a, precios, ahora.date().isoformat(), situacion, ahora,
                             rondas_hoy=(ultimo or {}).get("rondas"))


def guardar_estado(estado: dict) -> None:
    apariencias.asignar(estado)  # la cara de cada agente nuevo queda registrada por su ID antes de guardar
    config.DATOS.mkdir(parents=True, exist_ok=True)
    config.ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def nombre_completo(agente: dict) -> str:
    return f"{agente.get('alias', agente['nombre'])} · {agente['nombre']}"


def anotar_diario(agente: dict, fecha: str, texto: str) -> None:
    """Diario en markdown, legible directamente en GitHub."""
    config.DIARIOS.mkdir(parents=True, exist_ok=True)
    ruta = config.DIARIOS / f"{agente['id']}.md"
    if not ruta.exists():
        ruta.write_text(f"# Diario de {nombre_completo(agente)}\n\n*{agente.get('personalidad', '')}*\n\n"
                        f"## Estrategia\n\n{agente['estrategia']}\n", encoding="utf-8")
    with ruta.open("a", encoding="utf-8") as f:
        f.write(f"\n## {fecha}\n\n{texto}\n")


def anotar_bitacora(agente: dict, fecha: str, resumen: str, tipo: str = "decision", **extra) -> None:
    """Versión estructurada del diario, para la web."""
    agente.setdefault("bitacora", []).append({"fecha": fecha, "tipo": tipo, "resumen": resumen,
                                              "operaciones": [], "rechazos": [], "valor": None, **extra})
    agente["bitacora"] = agente["bitacora"][-60:]


def empezar_a_invertir(agente: dict, fecha: str, operaciones: list[dict], valor_antes: float, sp: float | None) -> None:
    """Su periodo de evaluación cuenta de verdad desde su primera compra: antes no tenía nada invertido, así que no se
    compara con el S&P 500. `sp` es el S&P desde el que se compara: en el diario, el de su hora de compra; en el
    intradía, el de la apertura de ese día (si tarda en entrar, es cosa suya). Las gráficas empiezan en el mismo sitio."""
    compras = [o for o in operaciones if o["lado"] == "comprar"]
    if agente.get("empezo") or not compras:
        return
    agente["empezo"] = compras[0].get("hora") or fecha
    if sp:
        agente["periodo"] = {"inicio": fecha, "valor": round(valor_antes, 2), "referencia": sp}


def migrar_empezo(estado: dict) -> None:
    """Una sola vez: los que ya habían invertido antes de que el periodo empezase en la primera compra. La hora sale del
    registro de operaciones y el S&P de ese momento, de las sesiones guardadas minuto a minuto (si no está, se deja)."""
    if "empezo" in estado.get("migraciones", []) or not estado.get("agentes"):
        return
    estado.setdefault("migraciones", []).append("empezo")
    primeras: dict[str, str] = {}
    if config.OPERACIONES.exists():
        with config.OPERACIONES.open(encoding="utf-8") as f:
            for o in csv.DictReader(f):
                if o.get("lado") == "comprar":
                    primeras.setdefault(o["agente"], o.get("hora") or o["fecha"])
    sesiones = seguimiento.para_web(estado)
    for a in estado["agentes"]:
        empezo = primeras.get(a["id"])
        if not empezo or a.get("empezo"):
            continue
        a["empezo"] = empezo
        sesion = sesiones.get(empezo[:10]) or {}
        if a.get("periodos_superados") or a.get("avisos") or not sesion.get("t"):
            continue
        k = 0
        if a.get("equipo", "diario") == "diario" and len(empezo) > 10:
            k = max([j for j, t in enumerate(sesion["t"]) if t <= empezo] or [0])
        if sesion["ref"][k]:
            a["periodo"] = {"inicio": empezo[:10], "valor": a["periodo"]["valor"], "referencia": sesion["ref"][k]}


# Reglas de la libreta tan estrictas que no dejaban operar ningún día (revisadas el 28/10): (agente, lo que se cambia, por)
REGLAS_SUAVIZADAS = [
    ("10-pablo", "volumen relativo mínimo de 4x", "volumen relativo mínimo de 2x"),
    ("03-lucía", "supera el medio punto porcentual", "supera el 2 %"),
]


def suavizar_reglas(estado: dict) -> None:
    """Una sola vez: afloja las reglas de REGLAS_SUAVIZADAS, que bloqueaban a su autor todos los días."""
    if "reglas_suavizadas" in estado.get("migraciones", []) or not estado.get("agentes"):
        return
    estado.setdefault("migraciones", []).append("reglas_suavizadas")
    for a in estado["agentes"]:
        for quien, antes, despues in REGLAS_SUAVIZADAS:
            for r in a.get("reglas", []) if a["id"] == quien else []:
                if antes in r["texto"]:
                    r["texto"] = r["texto"].replace(antes, despues)


def anotar_operaciones(fecha: str, operaciones: list[dict]) -> None:
    """`hora`: cuándo se hizo, en UTC (la web la enseña en hora de España)."""
    nuevo = not config.OPERACIONES.exists()
    config.DATOS.mkdir(parents=True, exist_ok=True)
    if not nuevo:
        with config.OPERACIONES.open(encoding="utf-8") as f:
            cabecera = f.readline().strip().split(",")
        if cabecera != CAMPOS_OPERACION:  # un archivo de antes, sin la columna de la hora: se le añade
            with config.OPERACIONES.open(encoding="utf-8") as f:
                filas = list(csv.DictReader(f))
            with config.OPERACIONES.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CAMPOS_OPERACION, extrasaction="ignore", restval="")
                w.writeheader()
                w.writerows(filas)
    hora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    with config.OPERACIONES.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_OPERACION, extrasaction="ignore", restval="")
        if nuevo:
            w.writeheader()
        for op in operaciones:
            op.setdefault("hora", hora)  # también queda en el informe del día (son los mismos diccionarios)
            w.writerow({"fecha": fecha, **op})


# --- Altas, despidos y propuestas --------------------------------------------------

def plantilla_inicial() -> list[dict]:
    """Los agentes de estrategias/personas.json, cada uno con su archivo de estrategia."""
    personas = json.loads((config.ESTRATEGIAS / "personas.json").read_text(encoding="utf-8"))
    lista = []
    for stem, persona in personas.items():
        texto = (config.ESTRATEGIAS / f"{stem}.md").read_text(encoding="utf-8")
        lista.append({**persona, "plantilla": stem, "estrategia": texto,
                      "titulo": texto.splitlines()[0].lstrip("# ").strip()})
    return lista


def proximo_fichaje(estado: dict, equipo: str) -> dict | None:
    """El fichaje ya pensado (estrategias/fichajes.json) que ocupará el próximo puesto libre de su equipo, si
    aún no se ha propuesto nunca. Si el usuario lo rechaza, la IA propone otro como siempre."""
    ruta = config.ESTRATEGIAS / "fichajes.json"
    if not ruta.exists():
        return None
    usadas = {a.get("plantilla") for a in estado["agentes"]} | {p.get("plantilla") for p in estado["propuestas"]}
    for stem, persona in json.loads(ruta.read_text(encoding="utf-8")).items():
        if persona.get("equipo", "diario") == equipo and stem not in usadas:
            texto = (config.ESTRATEGIAS / f"{stem}.md").read_text(encoding="utf-8")
            return {**persona, "plantilla": stem, "estrategia": texto, "titulo": texto.splitlines()[0].lstrip("# ").strip()}
    return None


def incorporar_plantilla(estado: dict, fecha: str, precios: dict[str, float], mercado) -> None:
    """Da de alta a los agentes de la plantilla que aún no han trabajado nunca en la oficina.

    Sirve para empezar el experimento y para sumar agentes nuevos sin tocar a los que ya están. Un agente
    de la plantilla que fue despedido no vuelve: su puesto lo ocupa quien apruebe el usuario."""
    if not estado["agentes"] and config.ESPEJO_ALPACA and mercado is not None:
        try:  # el experimento empieza de cero: la cuenta de práctica también
            mercado.limpiar_cuenta()
        except Exception as e:
            print(f"Aviso: no se pudo limpiar la cuenta de práctica: {e}")
    plantilla = plantilla_inicial()
    plazos = {p["plantilla"]: p["dias_evaluacion"] for p in plantilla if p.get("dias_evaluacion")}
    temperamentos = {p["plantilla"]: p["temperamento"] for p in plantilla if p.get("temperamento")}
    ruta = config.ESTRATEGIAS / "fichajes.json"
    if ruta.exists():
        temperamentos |= {k: v["temperamento"] for k, v in json.loads(ruta.read_text(encoding="utf-8")).items()
                          if v.get("temperamento")}
    for a in estado["agentes"]:  # los primeros agentes no guardaban de qué archivo salieron
        a.setdefault("equipo", "diario")
        if a.get("plantilla") in temperamentos and "temperamento" not in a:  # los que ya trabajaban, también
            a["temperamento"] = emociones.temperamento_valido(temperamentos[a["plantilla"]])
        if a.get("plantilla") in plazos:  # plazo propio de la plantilla (también para los que ya trabajan)
            a["dias_evaluacion"] = plazos[a["plantilla"]]
    revisar_estrategias(estado, plantilla)
    usadas = {a.get("plantilla") for a in estado["agentes"]} | \
             {a["nombre"] for a in estado["agentes"] if not a.get("plantilla") and a.get("origen") == "inicial"}
    for p in plantilla:
        if p["plantilla"] in usadas or p["titulo"] in usadas:
            continue
        alta_agente(estado, p["titulo"], p["estrategia"], fecha, precios, "inicial", p.get("nombre", ""),
                    p.get("personalidad", ""), equipo=p.get("equipo", "diario"), plantilla=p["plantilla"],
                    fuente=p.get("fuente", ""), dias_evaluacion=p.get("dias_evaluacion"),
                    temperamento=p.get("temperamento"))


def revisar_estrategias(estado: dict, plantilla: list[dict] | None = None) -> None:
    """Los que ya trabajan con una estrategia de la plantilla revisada (config.ESTRATEGIAS_REVISADAS) pasan una vez
    a la versión nueva de su archivo."""
    textos = {p["plantilla"]: p["estrategia"] for p in plantilla or plantilla_inicial()}
    for a in estado["agentes"]:
        revision = config.ESTRATEGIAS_REVISADAS.get(a.get("plantilla"))
        if revision and a["estado"] == "activo" and a.get("estrategia_revisada") != revision:
            a["estrategia"], a["estrategia_revisada"] = textos[a["plantilla"]], revision


def alta_agente(estado: dict, nombre: str, estrategia: str, fecha: str, precios: dict[str, float],
                origen: str, alias: str = "", personalidad: str = "", carta: str = "", equipo: str = "diario",
                plantilla: str = "", fuente: str = "", dias_evaluacion: int | None = None,
                temperamento: dict | None = None) -> dict:
    estado["contador"] += 1
    ident = f"{estado['contador']:02d}-{(alias or nombre).lower().replace(' ', '-')}"
    agente = cartera.nuevo_agente(ident, nombre, estrategia, fecha, precios[config.REFERENCIA], origen,
                                  alias, personalidad, equipo, plantilla, fuente)
    if carta:
        agente["carta_predecesor"] = carta
    if dias_evaluacion:
        agente["dias_evaluacion"] = dias_evaluacion
    if temperamento:
        agente["temperamento"] = emociones.temperamento_valido(temperamento)
    heredadas = aprendizaje.heredar_reglas(estado, agente, precios, fecha)
    estado["agentes"].append(agente)
    texto = f"Me incorporo con {config.CAPITAL_INICIAL:,.0f} $ ficticios."
    if heredadas:
        texto += " Empiezo con estas reglas de la oficina que están funcionando: " + " / ".join(heredadas)
    anotar_diario(agente, fecha, texto + (f"\n\n**Carta de mi predecesor:** {carta}" if carta else ""))
    anotar_bitacora(agente, fecha, texto, tipo="alta", valor=config.CAPITAL_INICIAL)
    return agente


def revisar_propuestas(estado: dict, fecha: str, precios: dict[str, float], ia, avisos) -> None:
    """Una propuesta se aprueba cerrando su issue como 'completada' y se rechaza cerrándola como 'no planificada'."""
    for p in estado["propuestas"]:
        if p["estado"] != "pendiente" or not p.get("issue"):
            continue
        situacion, motivo = avisos.estado_issue(p["issue"])
        if situacion != "closed":
            continue
        if motivo == "not_planned":
            p["estado"] = "rechazada"
            proponer_estrategia(estado, p["sustituye"], "El usuario rechazó la propuesta anterior.", ia, avisos)
        else:
            p["estado"] = "aprobada"
            despedido = next((a for a in estado["agentes"] if a["id"] == p["sustituye"]), {})
            alta_agente(estado, p["nombre"], p["estrategia"], fecha, precios, f"propuesta {p['issue']}",
                        p.get("alias", ""), p.get("personalidad", ""), despedido.get("carta", ""),
                        equipo=despedido.get("equipo", "diario"), plantilla=p.get("plantilla", ""),
                        temperamento=p.get("temperamento"))


def proponer_estrategia(estado: dict, id_despedido: str, motivo: str, ia, avisos) -> None:
    despedido = next(a for a in estado["agentes"] if a["id"] == id_despedido)
    rechazadas = [p["nombre"] for p in estado["propuestas"] if p["estado"] == "rechazada"]
    equipo = despedido.get("equipo", "diario")
    ejemplo = (config.ESTRATEGIAS / ("impulso.md" if equipo == "intradia" else "tendencia.md")).read_text(encoding="utf-8")
    fichaje = proximo_fichaje(estado, equipo)
    if fichaje:  # el puesto lo ocupa un fichaje ya pensado: no hace falta pedir una estrategia a la IA
        nombre, estrategia, alias = fichaje["titulo"], fichaje["estrategia"], fichaje["nombre"]
        personalidad, explicacion = fichaje.get("personalidad", ""), fichaje.get("explicacion", "")
        temperamento = fichaje.get("temperamento")
    else:
        try:
            r = ia.pedir_json(prompts.instrucciones_nueva_estrategia(equipo),
                              prompts.mensaje_nueva_estrategia(despedido, motivo, estado["agentes"], ejemplo, rechazadas)
                              + ("\n\n" + oficina.texto_manual(estado, 20) if estado.get("manual") else ""))
            nombre, estrategia = r["nombre"].strip(), r["estrategia"].strip()
            alias, personalidad = str(r.get("nombre_propio", "")).strip(), str(r.get("personalidad", "")).strip()
            explicacion = r.get("explicacion_sencilla", "")
            temperamento = emociones.temperamento_valido(r.get("temperamento"))
        except Exception as e:  # se reintentará en la próxima ejecución
            print(f"No se pudo generar la estrategia: {e}")
            estado["propuestas"].append({"estado": "por_generar", "sustituye": id_despedido, "motivo": motivo})
            return
    if not estrategia.startswith("#"):
        estrategia = f"# {nombre}\n\n{estrategia}"
    cuerpo = f"""**{nombre_completo(despedido)}** ha sido despedido. Motivo: {motivo}

**Su carta de despedida:** {despedido.get('carta') or '(no dejó carta)'}

{"Como sustituto toca el fichaje que ya teníamos pensado" if fichaje else "La IA propone como sustituto a"} **{alias} · {nombre}**. *{personalidad}*

{estrategia}

**Por qué puede funcionar:** {explicacion}

---

**¿Qué tienes que hacer?**
- Para **aprobarla**: pulsa *Close issue* (cerrar como completada).
- Para **rechazarla** y pedir otra: despliega la flecha junto a *Close issue* y elige *Close as not planned*.

El nuevo agente empezará en la siguiente sesión de bolsa con {config.CAPITAL_INICIAL:,.0f} $ ficticios y recibirá la carta de su predecesor."""
    titulo = f"Nueva estrategia para revisar ({config.EQUIPOS.get(equipo, equipo).lower()}): {alias} · {nombre}"
    numero = avisos.crear_issue(titulo, cuerpo, ["nueva-estrategia"])
    estado["propuestas"].append({"estado": "pendiente", "issue": numero, "nombre": nombre, "alias": alias,
                                 "personalidad": personalidad, "estrategia": estrategia,
                                 "sustituye": id_despedido, "motivo": motivo,
                                 "plantilla": fichaje["plantilla"] if fichaje else "", "temperamento": temperamento})


def despedir(estado: dict, agente: dict, motivo: str, fecha: str, precios: dict[str, float],
             mercado, ia, avisos) -> None:
    operaciones = cartera.cerrar_todo(agente, precios)
    espejo(mercado, operaciones, fecha)
    anotar_operaciones(fecha, operaciones)
    agente["estado"] = "despedido"
    agente["despedido"] = fecha
    agente["motivo"] = motivo
    agente["resultado"] = f"{(cartera.valor(agente, precios) / config.CAPITAL_INICIAL - 1) * 100:+.2f}% total"
    try:
        r = ia.pedir_json(prompts.INSTRUCCIONES_CARTA.format(alias=agente.get("alias", ""),
                                                             personalidad=agente.get("personalidad", "")),
                          prompts.mensaje_carta(agente, motivo))
        agente["carta"] = str(r.get("carta", "")).strip()
        oficina.anotar_leccion(estado, agente, fecha, "despido", f"{motivo} {str(r.get('leccion', '')).strip()}")
    except Exception as e:
        print(f"No se pudo escribir la carta de despedida: {e}")
    anotar_diario(agente, fecha, f"**Despedido.** {motivo}\n\n**Carta a mi sustituto:** {agente.get('carta', '')}")
    anotar_bitacora(agente, fecha, f"Despedido. {motivo}", tipo="despido", operaciones=operaciones,
                    valor=round(cartera.valor(agente, precios), 2), carta=agente.get("carta", ""))
    proponer_estrategia(estado, agente["id"], motivo, ia, avisos)


def espejo(mercado, operaciones: list[dict], fecha: str) -> None:
    """Replica las operaciones en la cuenta de práctica de Alpaca para poder verlas allí."""
    if not config.ESPEJO_ALPACA or mercado is None:
        return
    for i, op in enumerate(operaciones):
        lado = "buy" if op["lado"] == "comprar" else "sell"
        try:
            mercado.orden(op["simbolo"], lado, op["cantidad"],
                          f"{op['agente']}-{fecha}-{i}-{datetime.now().strftime('%H%M%S')}")
        except Exception as e:
            print(f"Aviso: no se pudo replicar en Alpaca {op}: {e}")


# --- Decisión diaria -------------------------------------------------------------

def clasificacion(estado: dict, precios: dict[str, float], fecha: str, equipo: str | None = None) -> list[dict]:
    filas = []
    for a in estado["agentes"]:
        if a["estado"] != "activo" or (equipo and a.get("equipo", "diario") != equipo):
            continue
        v = cartera.valor(a, precios)
        s = prompts.situacion_laboral(a, v, precios, fecha)
        filas.append({"id": a["id"], "nombre": nombre_completo(a), "valor": v, **s})
    return sorted(filas, key=orden_clasificacion)


def orden_clasificacion(f: dict) -> tuple:
    return not encima_del_testigo(f), -f["diferencia"]


def encima_del_testigo(f: dict) -> bool:
    """Solo quien ya ha invertido puede ir por encima del testigo: sin operar nunca no le ha ganado a nada."""
    return not f.get("sin_invertir") and f["diferencia"] >= 0


TESTIGO = "— TESTIGO: el S&P 500 comprado y mantenido, sin IA (por encima, le ganan; por debajo, pierden) —"


def lineas_clasificacion(ranking: list[dict], agente: dict) -> list[str]:
    lineas = [f"{i}. {f['nombre']}{' (TÚ)' if f['id'] == agente['id'] else ''}: periodo {f['rentabilidad']:+.2%} "
              f"frente a S&P {f['referencia']:+.2%}, le quedan {f['dias_restantes']} días"
              for i, f in enumerate(ranking, 1)]
    if lineas:  # el testigo, como una línea más donde le tocaría
        lineas.insert(sum(map(encima_del_testigo, ranking)), TESTIGO)
    return lineas


CAMPOS_INFORME = ("mercado", "noticias", "a_favor", "en_contra", "riesgos", "plan", "situacion", "radar", "rescate")


def leer_decision(r: dict) -> dict:
    informe = r.get("informe") if isinstance(r.get("informe"), dict) else {}
    animo = r.get("animo") if isinstance(r.get("animo"), dict) else {}
    return {
        "resumen": str(r.get("resumen") or r.get("analisis") or "").strip(),
        "informe": {k: str(informe.get(k, "")).strip() for k in CAMPOS_INFORME},
        "ordenes": [o for o in (r.get("ordenes") or []) if isinstance(o, dict)],
        "noticias_usadas": [str(i) for i in (r.get("noticias_usadas") or [])][:20],
        "prediccion": r.get("prediccion"),
        "descarte": r.get("descarte") if isinstance(r.get("descarte"), dict) else None,
        "animo": {k: str(animo.get(k, "")).strip()[:300] for k in ("siento", "influye")},
    }


def decidir(agente: dict, fecha: str, precios: dict[str, float], tabla: str, noticias_dia: list[dict],
            omitidas: int, ranking: list[dict], ia, analistas: str = "", noticias_propias: list[dict] | None = None,
            sentimiento: str = "", fuente: str = "", tabla_fuente: str = "", parecidos: str = "",
            empresas: str = "", simbolos: list[str] | None = None) -> dict:
    valor_total = cartera.valor(agente, precios)
    reglas = prompts.REGLAS
    if fuente:
        reglas += "\n- Además de la lista anterior, puedes comprar los valores de tu fuente que tengan precio."
    instrucciones = prompts.INSTRUCCIONES_AGENTE.format(
        alias=agente.get("alias", agente["nombre"]), personalidad=agente.get("personalidad", ""),
        estrategia=aprendizaje.estrategia_con_ajustes(agente), reglas=reglas)
    mensaje = prompts.mensaje_agente(agente, valor_total, precios, tabla, noticias_dia, omitidas,
                                     lineas_clasificacion(ranking, agente), "\n\n".join(t for t in (prompts.memoria_agente(agente, precios, fecha), prompts.texto_interior(agente, precios, fecha), parecidos) if t), fecha,
                                     analistas, noticias_propias, sentimiento, fuente, tabla_fuente, empresas)
    simbolos = simbolos or sorted((set(config.UNIVERSO) & set(precios)) | set(agente["posiciones"]))
    return leer_decision(ia.pedir_json(instrucciones, mensaje, esquema=prompts.esquema_decision(simbolos)))


def informe_markdown(decision: dict, operaciones: list[dict], rechazos: list[str], valor: float) -> str:
    inf = decision["informe"]
    ops = "\n".join(f"- **{op['lado'].capitalize()}** {op['cantidad']:.4f} {op['simbolo']} a {op['precio']:.2f} $ "
                    f"({op['importe']:,.2f} $). {op.get('motivo', '')}" for op in operaciones) or "- Sin operaciones."
    texto = f"""**{decision['resumen']}**

**Mercado.** {inf['mercado']}

**Noticias.** {inf['noticias']}

**A favor.** {inf.get('a_favor', '')}

**En contra.** {inf.get('en_contra', '')}

**Operaciones**
{ops}

**Riesgos.** {inf['riesgos']}

**Plan.** {inf['plan']}

**Mi puesto.** {inf['situacion']}"""
    if inf.get("radar"):
        texto += f"\n\n**Radar de oportunidades.** {inf['radar']}"
    if inf.get("rescate"):
        texto += f"\n\n**Plan de rescate.** {inf['rescate']}"
    ajustes = [r for r in rechazos if r.startswith("Ajuste automático")]
    if ajustes:
        texto += "\n\nAjustado por el control de riesgo:\n" + "\n".join(f"- {r}" for r in ajustes)
    if bloqueos := [r for r in rechazos if r not in ajustes]:
        texto += "\n\nBloqueado por las reglas de riesgo:\n" + "\n".join(f"- {r}" for r in bloqueos)
    return texto + f"\n\nValor de la cartera: {valor:,.2f} $"


def evaluar(estado: dict, agente: dict, fecha: str, precios: dict[str, float]) -> str | None:
    """Devuelve el motivo de despido o None si sigue."""
    v = cartera.valor(agente, precios)
    agente["valor_maximo"] = max(agente["valor_maximo"], v)
    caida = v / agente["valor_maximo"] - 1
    if caida < -config.MAX_CAIDA:
        return f"Cayó un {-caida:.1%} desde su máximo (límite {config.MAX_CAIDA:.0%})."
    p = agente["periodo"]
    dias = (date.fromisoformat(fecha) - date.fromisoformat(p["inicio"])).days
    if dias >= config.dias_evaluacion(agente) and not agente.get("empezo"):
        texto = f"En {dias} días no invertí nada, así que este periodo no cuenta frente al S&P 500. Empieza otro."
        anotar_diario(agente, fecha, f"**{texto}**")
        anotar_bitacora(agente, fecha, texto, tipo="evaluacion", valor=round(v, 2))
        agente["periodo"] = {"inicio": fecha, "valor": v, "referencia": precios[config.REFERENCIA]}
    elif dias >= config.dias_evaluacion(agente):
        suyo = v / p["valor"] - 1
        ref = precios[config.REFERENCIA] / p["referencia"] - 1
        resultado = f"{suyo:+.2%} frente al {ref:+.2%} del S&P 500"
        if suyo - ref < -config.MARGEN_AVISO:
            return f"En {dias} días ganó {resultado}: más de {config.MARGEN_AVISO * 100:.0f} puntos por detrás."
        if suyo <= ref:
            agente["avisos"] = agente.get("avisos", 0) + 1
            agente["racha"] = 0
            if agente["avisos"] >= config.AVISOS_PARA_DESPIDO:
                return f"En {dias} días ganó {resultado}: segundo aviso seguido por quedar por detrás."
            texto = (f"Aviso: en {dias} días ganó {resultado}. Ha quedado por detrás, pero por menos de "
                     f"{config.MARGEN_AVISO * 100:.0f} puntos. Si el próximo periodo vuelve a quedar por detrás, será despedido.")
            tipo = "aviso"
        else:
            agente["periodos_superados"] += 1
            agente["avisos"] = 0
            agente["racha"] = agente.get("racha", 0) + 1
            texto = (f"Evaluación superada ({resultado}). Empieza un nuevo periodo"
                     + (f", con {min(config.MAX_DIAS_EXTRA, config.DIAS_EXTRA_POR_RACHA * agente['racha'])} días más de "
                        "plazo como premio." if agente["racha"] > 0 else "."))
            tipo = "evaluacion"
            plan = (agente.get("memoria") or [{}])[-1].get("texto", "")
            oficina.anotar_leccion(estado, agente, fecha, "evaluacion",
                                   f"Superó al S&P 500 ({resultado}) con la estrategia «{agente['nombre']}». {plan[:250]}")
        anotar_diario(agente, fecha, f"**{texto}**")
        anotar_bitacora(agente, fecha, texto, tipo=tipo, valor=round(v, 2))
        agente["periodo"] = {"inicio": fecha, "valor": v, "referencia": precios[config.REFERENCIA]}
    return None


def reflexionar(agente: dict, fecha: str, precios: dict[str, float], ia, estado: dict | None = None) -> None:
    v = cartera.valor(agente, precios)
    situacion = prompts.texto_situacion(prompts.situacion_laboral(agente, v, precios, fecha))
    semana = [b for b in agente.get("bitacora", []) if b["tipo"] == "decision"][-5:]
    try:
        r = ia.pedir_json(prompts.INSTRUCCIONES_REFLEXION.format(alias=agente.get("alias", ""),
                                                                 personalidad=agente.get("personalidad", "")),
                          prompts.mensaje_reflexion(agente, situacion, semana))
    except Exception as e:
        print(f"No se pudo escribir la reflexión de {agente['id']}: {e}")
        return
    texto = str(r.get("reflexion", "")).strip()
    ajuste = aprendizaje.aplicar_ajuste(agente, r.get("ajuste"), fecha)
    if ajuste:
        texto += f"\n\nAjuste a mi estrategia: {ajuste}"
    if estado is not None and str(r.get("leccion") or "").strip():
        oficina.anotar_leccion(estado, agente, fecha, "reflexion", str(r["leccion"]))
    agente.setdefault("reflexiones", []).append({"fecha": fecha, "texto": texto})
    agente["reflexiones"] = agente["reflexiones"][-12:]
    anotar_diario(agente, fecha, f"**Reflexión de la semana.** {texto}")
    anotar_bitacora(agente, fecha, texto, tipo="reflexion", valor=round(v, 2))


def avisar_calendario(estado: dict, fecha: str, avisos, dias: int = 30) -> None:
    """Si al calendario oficial de datos (IPC, empleo...) le queda menos de un mes, un aviso para meter el del año
    siguiente (una sola vez por cada fecha final). Mientras tanto, los analistas los marcan desde las noticias."""
    datos = [c[:10] for c, t in config.EVENTOS if t != config._FED]
    ultimo = max(datos, default=fecha)
    if (date.fromisoformat(ultimo) - date.fromisoformat(fecha)).days >= dias or estado.get("aviso_calendario") == ultimo:
        return
    estado["aviso_calendario"] = ultimo
    avisos.crear_issue("📅 Actualizar el calendario de eventos",
                       f"El calendario oficial de datos (IPC, empleo, PPI, ventas minoristas, PCE) de `config.EVENTOS` "
                       f"acaba el {ultimo}. Hay que añadir las fechas siguientes (bls.gov, census.gov, bea.gov y "
                       f"federalreserve.gov). Mientras tanto, los analistas los marcan a partir de las noticias.",
                       ["calendario"])


def resumen_semanal(estado: dict, fecha: str, precios: dict[str, float], avisos) -> None:
    activos = [a for a in estado["agentes"] if a["estado"] == "activo"]
    lineas = [f"- **{nombre_completo(a)}**: {cartera.valor(a, precios):,.0f} $ "
              f"({(cartera.valor(a, precios) / config.CAPITAL_INICIAL - 1) * 100:+.2f} % desde que empezó)"
              for a in sorted(activos, key=lambda a: -cartera.valor(a, precios))]
    hace_semana = [r for r in estado["referencia"] if r["fecha"] <= fecha][-6:-5]
    ref = ""
    if hace_semana:
        ref = f"\n\nEl S&P 500 se movió un {(precios[config.REFERENCIA] / hace_semana[0]['precio'] - 1) * 100:+.2f} % esta semana."
    avisos.crear_issue(f"Resumen semanal {fecha}",
                       "Así van los agentes (dinero ficticio):\n\n" + "\n".join(lineas) + ref +
                       "\n\nCada agente ha escrito su reflexión de la semana; la tienes en la web, en su ficha. "
                       f"También en [PANEL.md](https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/blob/main/PANEL.md). "
                       "Puedes cerrar esta issue cuando la leas.",
                       ["resumen-semanal"])
    estado["ultimo_resumen"] = fecha


def simbolos_necesarios(estado: dict, datos_fuentes: dict) -> list[str]:
    """La lista de valores, la referencia, lo que tiene cada agente y lo que copian el Copión y la del Congreso."""
    simbolos = set(config.UNIVERSO) | {config.REFERENCIA}
    for a in estado["agentes"]:
        simbolos |= set(a["posiciones"])
    simbolos |= fuentes.tickers_berkshire(datos_fuentes.get("berkshire", {}))
    simbolos |= fuentes.tickers_congreso(datos_fuentes.get("congreso", {}))
    return sorted(simbolos)


def ejecutar(mercado, ia, avisos, ahora: datetime | None = None, forzar: bool = False, ia_noticias=None,
             solo: set[str] | None = None, evento: str = "") -> str:
    """`solo`: repetir hoy, a mano, la decisión de esos agentes del diario (por ejemplo si la IA falló). No evalúa ni
    despide a nadie ni toca al resto: solo deciden ellos y se corrige su valor de hoy. `evento`: es la decisión extra
    de una hora después de un evento grande (lo ven al decidir)."""
    estado = cargar_estado()
    if solo:
        forzar = True
    if not forzar:
        minutos = mercado.minutos_desde_apertura(ahora)
        if minutos is None or not (VENTANA_MINUTOS[0] <= minutos <= VENTANA_MINUTOS[1]):
            return "Fuera del horario de decisión: no se hace nada."
    fecha = ahora.date().isoformat() if ahora else _hoy()
    if estado.get("ultima_ejecucion") == fecha and not solo:
        return "Ya se ejecutó hoy."

    datos_fuentes = fuentes.actualizar(date.fromisoformat(fecha))
    base = sorted(set(config.UNIVERSO) | {config.REFERENCIA})
    precios = mercado.ultimos_precios(base)
    cierres = mercado.cierres_diarios(base)
    extra = sorted(set(simbolos_necesarios(estado, datos_fuentes)) - set(base))
    if extra:  # aparte: un ticker raro de las fuentes no debe tumbar el día
        try:
            precios.update(mercado.ultimos_precios(extra))
            cierres.update(mercado.cierres_diarios(extra))
        except Exception as e:
            print(f"Aviso: no se pudieron traer los precios de {len(extra)} valores de las fuentes: {e}")
    tabla = prompts.tabla_mercado(cierres, precios)
    datos_noticias = noticias.actualizar(mercado, ia_noticias or ia, ahora)
    noticias_dia, omitidas = noticias.para_agentes(datos_noticias, ahora)
    sentimiento = noticias.texto_sentimiento(noticias.sentimiento_por_valor(datos_noticias, ahora))
    # Los informes que los analistas escribieron desde la última decisión (en cada recogida de noticias);
    # si no hay ninguno, escriben uno ahora
    momento = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    informes = analistas.desde(estado, estado.get("ultimo_ciclo_hora"), momento)
    if not informes:
        inf = analistas.informe(ia, fecha, tabla, noticias_dia, sentimiento, estado.get("analistas"),
                                hora=momento.isoformat(timespec="minutes"), estado=estado)
        if inf:
            analistas.completar_radar(inf, mercado)
            analistas.guardar(estado, inf)
            informes = [inf]
    # El radar de oportunidades: empresas fuera de la lista que se pueden comprar, como mucho un 5 % en cada una
    radar = analistas.radar_de(informes)
    nuevos = sorted({v["simbolo"] for v in radar} - set(precios))
    if nuevos:
        try:
            precios.update(mercado.ultimos_precios(nuevos))
        except Exception as e:
            print(f"Aviso: no se pudieron traer los precios del radar: {e}")
    radar = [v for v in radar if v["simbolo"] in precios]
    simbolos_radar = {v["simbolo"] for v in radar}
    texto_analistas = "\n\n".join(t for t in (analistas.texto_varios(informes), analistas.texto_radar(radar, precios),
                                               oficina.texto_manual(estado),
                                               aprendizaje.texto_reglas_oficina(estado, precios, fecha)) if t)
    if evento:
        texto_analistas = (f"⚡ DECISIÓN EXTRA POR UN EVENTO: hoy ha habido {evento}. Ha pasado una hora y el mercado "
                           f"ya ha reaccionado: esta decisión es para que puedas aprovecharlo o protegerte. Si no ves "
                           f"nada claro, no hace falta operar.\n\n" + texto_analistas)

    # Los intradía se evalúan al cierre de su sesión, cuando ya no tienen posiciones
    diarios = lambda: [a for a in estado["agentes"] if a["estado"] == "activo" and a.get("equipo", "diario") == "diario"
                       and (not solo or a["id"] in solo)]
    if solo:
        return _decidir_diarios(estado, mercado, ia, avisos, fecha, ahora, precios, cierres, tabla, datos_fuentes,
                                datos_noticias, noticias_dia, omitidas, sentimiento, momento, texto_analistas,
                                simbolos_radar, diarios, ia_noticias, solo=True)
    incorporar_plantilla(estado, fecha, precios, mercado)
    for p in estado["propuestas"]:
        if p["estado"] == "por_generar":
            p["estado"] = "reintentada"
            proponer_estrategia(estado, p["sustituye"], p["motivo"], ia, avisos)
    revisar_propuestas(estado, fecha, precios, ia, avisos)

    for agente in diarios():
        motivo = evaluar(estado, agente, fecha, precios)
        if motivo:
            despedir(estado, agente, motivo, fecha, precios, mercado, ia, avisos)
    # Las reglas que llevan semanas sin funcionar reciben un aviso y, si siguen igual, se tachan solas
    for agente in estado["agentes"]:
        if agente["estado"] == "activo":
            for regla in aprendizaje.revisar_reglas(agente, precios, fecha):
                anotar_diario(agente, fecha, f"**Regla tachada porque no me funcionaba:** {regla}")
    return _decidir_diarios(estado, mercado, ia, avisos, fecha, ahora, precios, cierres, tabla, datos_fuentes,
                            datos_noticias, noticias_dia, omitidas, sentimiento, momento, texto_analistas,
                            simbolos_radar, diarios, ia_noticias)


def _decidir_diarios(estado, mercado, ia, avisos, fecha, ahora, precios, cierres, tabla, datos_fuentes, datos_noticias,
                     noticias_dia, omitidas, sentimiento, momento, texto_analistas, simbolos_radar, diarios,
                     ia_noticias, solo: bool = False) -> str:
    # La gráfica del día: hasta ahora con las carteras de antes de decidir y, después, con las nuevas
    seguimiento.registrar(estado, mercado, precios, momento)
    ranking = clasificacion(estado, precios, fecha, "diario")
    vistas = {n["id"] for n in noticias_dia}
    # Cómo va el S&P 500 frente al cierre de ayer, para recordar a cada agente qué hizo en días parecidos
    try:
        cierre_ayer = mercado.precios_del_dia([config.REFERENCIA])[config.REFERENCIA]["cierre_ayer"]
        cambio_sp = precios[config.REFERENCIA] / cierre_ayer - 1 if cierre_ayer else None
    except Exception as e:
        print(f"Aviso: no se pudo saber cómo va hoy el S&P 500: {e}")
        cambio_sp = None
    tipo_dia = aprendizaje.tipo_de_dia(cambio_sp)
    texto_empresas = fundamentales.texto_empresas(fundamentales.cargar(), precios, fecha)
    # Lo que ha subido cada valor en 5 sesiones (lo mismo que ven en la tabla), para ver si persiguen subidas
    subidas_5d = {s: (serie + [precios[s]])[-1] / (serie + [precios[s]])[-6] - 1
                  for s, serie in cierres.items() if s in precios and len(serie) >= 5 and serie[-5]}
    for agente in diarios():
        aprendizaje.calificar_descartes(agente, fecha, precios)
        for p in aprendizaje.corregir_predicciones(agente, fecha, precios):
            anotar_diario(agente, fecha, f"**Nota de mi predicción del {p['fecha']}.** Dije que {p['simbolo']} lo haría "
                          f"{p['vs_sp500']} que el S&P 500: hizo {p['rent']:+.1%} frente a {p['rent_sp']:+.1%}. "
                          f"{'Acierto' if p['acierto'] else 'Fallo'}.")
        texto_fuente, extra = fuentes.para_agente(agente, datos_fuentes)
        extra = {s for s in extra if s in precios}
        tabla_fuente = prompts.tabla_mercado(cierres, precios, sorted(extra - set(config.UNIVERSO))) if extra else ""
        propias = noticias.de_valores(datos_noticias, set(agente["posiciones"]), vistas, ahora)
        if extra:  # el Copión y la del Congreso: también las noticias de lo que compra su fuente y aún no tienen
            propias += noticias.de_valores(datos_noticias, extra - set(agente["posiciones"]),
                                           vistas | {n["id"] for n in propias}, ahora, maximo=8)
        situacion = prompts.situacion_laboral(agente, cartera.valor(agente, precios), precios, fecha)
        peligro = aprendizaje.en_peligro(situacion)
        emocion = emociones.actualizar(agente, precios, fecha, situacion, momento, cambio_sp)
        argumentos = (agente, fecha, precios, tabla, noticias_dia, omitidas, ranking, ia, texto_analistas,
                      propias, sentimiento, texto_fuente, tabla_fuente,
                      aprendizaje.texto_dias_parecidos(agente, tipo_dia, estado["referencia"], fecha), texto_empresas,
                      sorted(((set(config.UNIVERSO) | extra | simbolos_radar) & set(precios)) | set(agente["posiciones"])))
        try:
            try:
                decision = decidir(*argumentos)
            except Exception as e:  # una respuesta mal formada o un corte suele salir bien a la segunda
                print(f"{nombre_completo(agente)}: la IA falló al decidir ({e}); se pide otra vez")
                decision = decidir(*argumentos)
        except Exception as e:
            anotar_diario(agente, fecha, f"No pude decidir hoy (error de la IA: {e}). No opero.")
            anotar_bitacora(agente, fecha, "Hoy no pude decidir porque la IA no respondió. No opero.",
                            tipo="error", valor=round(cartera.valor(agente, precios), 2))
            continue
        valor_antes = cartera.valor(agente, precios)
        operaciones, rechazos = cartera.ejecutar_ordenes(
            agente, decision["ordenes"], precios, extra | simbolos_radar,
            limite_peso={s: config.MAX_PESO_RADAR for s in simbolos_radar - extra}, stop_diario=True)
        cartera.poner_stops_por_defecto(agente)  # lo que tenía de antes sin stop también queda cubierto
        emociones.etiquetar(operaciones, emocion["emocion"], bool(decision["noticias_usadas"]))
        aprendizaje.apuntar_rechazos(agente, fecha, rechazos)
        aprendizaje.apuntar_descarte(agente, decision["descarte"], fecha, precios,
                                     {o["simbolo"] for o in operaciones if o["lado"] == "comprar"})
        desde = agente.get("sin_operar", {}).get("desde", fecha)
        aprendizaje.actualizar_inactividad(
            agente, fecha, bool(operaciones),
            {s for s in fuentes.compras_desde(agente, datos_fuentes, desde) if s in precios} if agente.get("fuente") else None)
        espejo(mercado, operaciones, fecha)
        anotar_operaciones(fecha, operaciones)
        empezar_a_invertir(agente, fecha, operaciones, valor_antes, precios.get(config.REFERENCIA))
        noticias.marcar_uso(datos_noticias, decision["noticias_usadas"], agente["id"])
        aprendizaje.seguir_operaciones(agente, fecha, operaciones, precios, subidas_5d)
        aprendizaje.apuntar_prediccion(agente, decision["prediccion"], fecha, precios)
        aprendizaje.actualizar_rescate(agente, peligro, decision["informe"]["rescate"], fecha)
        v = cartera.valor(agente, precios)
        anotar_diario(agente, fecha, informe_markdown(decision, operaciones, rechazos, v))
        anotar_bitacora(agente, fecha, decision["resumen"], informe=decision["informe"], operaciones=operaciones,
                        rechazos=rechazos, valor=round(v, 2), noticias_usadas=decision["noticias_usadas"],
                        sp=precios.get(config.REFERENCIA), animo=decision["animo"], emocion=emocion["emocion"],
                        dia={"tipo": tipo_dia, "cambio_sp": round(cambio_sp, 4)} if tipo_dia else None)
        hechas = ", ".join(f"{o['lado']} {o['simbolo']}" for o in operaciones) or "ninguna"
        agente.setdefault("memoria", []).append({"fecha": fecha, "texto": f"{fecha}: {decision['resumen'][:300]} "
                                                 f"Plan: {decision['informe']['plan'][:200]} Operaciones: {hechas}."})
        agente["memoria"] = agente["memoria"][-10:]

    seguimiento.registrar(estado, mercado, precios, momento)
    if solo:  # su valor de hoy ya estaba apuntado: se corrige con el de después de decidir
        for agente in diarios():
            if agente["historial"] and agente["historial"][-1]["fecha"] == fecha:
                agente["historial"][-1]["valor"] = round(cartera.valor(agente, precios), 2)
        estado["precios"] = {**estado.get("precios", {}), **{s: round(p, 4) for s, p in precios.items()}}
        guardar_estado(estado)
        noticias.guardar(datos_noticias)
        web.generar(estado, datos_noticias)
        config.PANEL_MD.write_text(panel.panel_md(estado, precios, fecha), encoding="utf-8")
        return "Decisión repetida: " + ", ".join(nombre_completo(a) for a in diarios())
    es_viernes = date.fromisoformat(fecha).weekday() == 4
    for agente in estado["agentes"]:
        if agente["estado"] == "activo":
            if agente.get("equipo", "diario") == "diario":  # el intradía anota su valor al cierre
                agente["historial"].append({"fecha": fecha, "valor": round(cartera.valor(agente, precios), 2)})
            if es_viernes:  # con el modelo ligero: la cuota del principal (20 al día) se queda para las decisiones
                reflexionar(agente, fecha, precios, ia_noticias or ia, estado)
    # Una vez al mes, cada agente resume lo aprendido (con el modelo ligero, para no gastar cuota del principal)
    for agente in estado["agentes"]:
        if aprendizaje.toca_resumen(agente, fecha):
            aprendizaje.resumir_memoria(agente, fecha, ia_noticias or ia, anotar_diario)
    estado["referencia"].append({"fecha": fecha, "precio": precios[config.REFERENCIA]})
    oficina.revisar_graduaciones(estado, fecha, avisos, anotar_bitacora)

    if es_viernes and estado.get("ultimo_resumen") != fecha:
        resumen_semanal(estado, fecha, precios, avisos)
    avisar_calendario(estado, fecha, avisos)

    estado["ultima_ejecucion"] = fecha
    estado["ultimo_ciclo_hora"] = momento.isoformat(timespec="minutes")
    estado["precios"] = {s: round(p, 4) for s, p in precios.items()}
    guardar_estado(estado)
    noticias.guardar(datos_noticias)
    web.generar(estado, datos_noticias)
    config.PANEL_DIR.mkdir(exist_ok=True)
    (config.PANEL_DIR / "evolucion.svg").write_text(
        panel.grafico_svg(estado["agentes"], [(r["fecha"], r["precio"]) for r in estado["referencia"]]),
        encoding="utf-8")
    config.PANEL_MD.write_text(panel.panel_md(estado, precios, fecha), encoding="utf-8")
    return "Ciclo completado."


def _hoy() -> str:
    from agentes.alpaca import fecha_nueva_york
    return fecha_nueva_york().isoformat()


def incorporar(mercado) -> str:
    """Sienta en la oficina a los agentes de la plantilla que aún no han empezado, sin esperar al ciclo diario.
    Empiezan con su capital y deciden por primera vez en su próxima sesión."""
    estado = cargar_estado()
    antes = len(estado["agentes"])
    precios = mercado.ultimos_precios(sorted(set(config.UNIVERSO) | {config.REFERENCIA}))
    incorporar_plantilla(estado, _hoy(), precios, mercado)
    nuevos = estado["agentes"][antes:]
    if not nuevos:
        return "No hay agentes nuevos que incorporar."
    estado["precios"] = {**estado.get("precios", {}), **{s: round(p, 4) for s, p in precios.items()}}
    guardar_estado(estado)
    web.generar(estado)
    return "Incorporados: " + ", ".join(nombre_completo(a) for a in nuevos)


def main() -> None:
    from agentes.alpaca import Alpaca
    from agentes.avisos import GitHub
    from agentes.ia import crear_ia

    if "--incorporar" in sys.argv:
        print(incorporar(Alpaca()))
        return
    # Con prisa: si el modelo principal está saturado, esperar minutos por cada agente bloqueaba al resto de
    # tareas (el 27/10 el ciclo tardó 24 minutos y se perdió la recogida de noticias de las 16:00)
    solo = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--solo=")), "")
    solo = {i.strip() for i in solo.split(",") if i.strip()} or None
    print(ejecutar(Alpaca(), crear_ia(prisa=True), GitHub(), forzar="--forzar" in sys.argv,
                   ia_noticias=crear_ia(ligera=True), solo=solo))


if __name__ == "__main__":
    main()
