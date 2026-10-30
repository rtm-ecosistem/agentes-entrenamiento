"""Lo que cada agente aprende de sí mismo día a día: el boletín de notas de sus operaciones, su libreta de reglas,
sus predicciones con nota y el repaso del día al cierre. Nada de esto gasta peticiones del modelo principal."""

from __future__ import annotations

from datetime import date, timedelta

from agentes import cartera, config, prompts

MAX_OPERACIONES = 30       # operaciones que se siguen en el boletín y para los vicios
DIAS_BOLETIN = 30          # y durante cuántos días
MAX_REGLAS = prompts.MAX_REGLAS_LIBRETA
MAX_LARGO_REGLA = 200
DIAS_PREDICCION = 7
CONFIANZAS = ("alta", "media", "baja")        # una semana después se le pone nota
MAX_PREDICCIONES = 20


def _dias(desde: str, hasta: str) -> int:
    return (date.fromisoformat(hasta) - date.fromisoformat(desde)).days


def _rent(ahora: float | None, antes: float | None) -> float | None:
    return ahora / antes - 1 if ahora and antes else None


# --- 1. Boletín de notas ------------------------------------------------------------

def seguir_operaciones(agente: dict, fecha: str, operaciones: list[dict], precios: dict[str, float],
                       subidas_5d: dict[str, float] | None = None) -> None:
    """Apunta cada compra y venta con el precio del S&P 500 de ese momento, para ponerle nota más adelante,
    y cuánto había subido el valor en la última semana, para ver si el agente persigue subidas."""
    sp = precios.get(config.REFERENCIA)
    for op in operaciones:
        agente.setdefault("seguimiento", []).append({
            "fecha": fecha, "lado": op["lado"], "simbolo": op["simbolo"], "precio": op["precio"], "sp": sp,
            **{k: op[k] for k in ("emocion", "noticias") if k in op},
            **({"subida_5d": round(subidas_5d[op["simbolo"]], 4)} if subidas_5d and op["simbolo"] in subidas_5d else {})})
    agente["seguimiento"] = agente.get("seguimiento", [])[-MAX_OPERACIONES:]


def boletin(agente: dict, precios: dict[str, float], fecha: str) -> list[dict]:
    """Cómo han ido las operaciones de las últimas semanas comparadas con el S&P 500 desde ese día.
    Una compra va bien si el valor lo ha hecho mejor que el S&P 500; una venta, si lo ha hecho peor."""
    notas = []
    for s in agente.get("seguimiento", []):
        if s["fecha"] >= fecha or _dias(s["fecha"], fecha) > DIAS_BOLETIN:
            continue
        valor, sp = _rent(precios.get(s["simbolo"]), s["precio"]), _rent(precios.get(config.REFERENCIA), s.get("sp"))
        if valor is None or sp is None:
            continue
        bien = valor > sp if s["lado"] == "comprar" else valor < sp
        notas.append({**s, "rent": valor, "rent_sp": sp, "bien": bien})
    return notas


def texto_boletin(notas: list[dict]) -> str:
    if not notas:
        return ""
    lineas = []
    for n in notas[-8:]:
        verbo = "Compraste" if n["lado"] == "comprar" else "Vendiste"
        if n["lado"] == "comprar":
            juicio = "Bien: lo ha hecho mejor que el S&P 500" if n["bien"] else "Mal: lo ha hecho peor que el S&P 500"
        else:
            juicio = ("Bien: ha ido peor que el S&P 500 desde que vendiste" if n["bien"]
                      else "Quizá vendiste pronto: desde entonces ha ido mejor que el S&P 500")
        lineas.append(f"- {verbo} {n['simbolo']} el {n['fecha']} a {n['precio']:.2f}: desde entonces "
                      f"{n['rent']:+.1%} (S&P 500 {n['rent_sp']:+.1%}). {juicio}.")
    bien = sum(n["bien"] for n in notas)
    return (f"Tu boletín de notas (cómo te han ido tus operaciones de las últimas semanas): {bien} de {len(notas)} "
            f"bien.\n" + "\n".join(lineas))


# --- 2. Libreta de reglas -------------------------------------------------------------

def actualizar_reglas(agente: dict, cambios, fecha: str, valor: float | None = None, sp: float | None = None) -> list[str]:
    """`cambios` = {"quitar": [números de regla], "anadir": ["texto", ...]}. Devuelve lo que ha cambiado.
    Cada regla nueva guarda el valor de la cartera y el del S&P 500 de ese día, para saber después si funciona."""
    if not isinstance(cambios, dict):
        return []
    reglas = agente.setdefault("reglas", [])
    hecho = []
    quitar = set()
    for n in cambios.get("quitar") or []:
        try:
            i = int(n) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(reglas):
            quitar.add(i)
    for i in sorted(quitar, reverse=True):
        hecho.append(f"Tacha: {reglas.pop(i)['texto']}")
    for texto in (cambios.get("anadir") or [])[:2]:
        texto = str(texto).strip()[:MAX_LARGO_REGLA]
        if texto and all(texto.lower() != r["texto"].lower() for r in reglas):
            reglas.append({"fecha": fecha, "texto": texto, **({"valor": round(valor, 2), "sp": sp} if valor and sp else {})})
            hecho.append(f"Apunta: {texto}")
    # Si la libreta se llena, se olvida la regla más antigua
    agente["reglas"] = reglas[-MAX_REGLAS:]
    return hecho


DIAS_PARA_JUZGAR_REGLA = 5
MAX_REGLAS_OFICINA = 6


def rendimiento_regla(agente: dict, regla: dict, precios: dict[str, float], fecha: str) -> float | None:
    """Cuántos puntos le saca su autor al S&P 500 desde que la apuntó (None si aún es pronto para juzgarla)."""
    if not regla.get("valor") or not regla.get("sp") or _dias(regla["fecha"], fecha) < DIAS_PARA_JUZGAR_REGLA:
        return None
    sp = _rent(precios.get(config.REFERENCIA), regla["sp"])
    return None if sp is None else cartera.valor(agente, precios) / regla["valor"] - 1 - sp


def reglas_que_funcionan(estado: dict, precios: dict[str, float], fecha: str) -> list[tuple[float, str, dict]]:
    """Las reglas cuyo autor va por delante del S&P 500 desde que las apuntó: primero las de quien va en cabeza
    y después de más a menos ventaja, sin repetir. Cada una es (ventaja, "Alias" o "Alias (va primero)", regla)."""
    activos = [a for a in estado["agentes"] if a["estado"] == "activo"]
    if not activos:
        return []
    # Quien va primero frente al S&P 500 en su periodo: sus reglas que funcionan van delante
    primero = max(activos, key=lambda a: prompts.situacion_laboral(a, cartera.valor(a, precios), precios, fecha)["diferencia"])
    buenas = []
    for a in activos:
        for r in a.get("reglas", []):
            ventaja = rendimiento_regla(a, r, precios, fecha)
            if ventaja is not None and ventaja > 0:
                buenas.append((a is primero, ventaja, a.get("alias", a["nombre"]), r))
    buenas.sort(key=lambda b: (not b[0], -b[1]))
    vistas, lista = set(), []
    for lider, v, quien, r in buenas:
        if r["texto"].lower() not in vistas:
            vistas.add(r["texto"].lower())
            lista.append((v, f"{quien}{' (va primero)' if lider else ''}", r))
    return lista


def texto_reglas_oficina(estado: dict, precios: dict[str, float], fecha: str) -> str:
    """Las reglas de los compañeros que están funcionando: su autor va por delante del S&P 500 desde que la apuntó."""
    buenas = reglas_que_funcionan(estado, precios, fecha)
    if not buenas:
        return ""
    return ("Reglas que están funcionando en la oficina (sus autores van por delante del S&P 500 desde que las "
            "apuntaron; primero las de quien va en cabeza; tómalas solo si encajan con tu estrategia):\n"
            + "\n".join(f"- {quien}: {r['texto']} (+{v * 100:.1f} puntos desde el {r['fecha']})"
                        for v, quien, r in buenas[:MAX_REGLAS_OFICINA]))


def _notas_regla(r: dict) -> str:
    notas = []
    veces = r.get("cumplida", 0) + r.get("incumplida", 0)
    if veces:
        notas.append(f"la has cumplido {r.get('cumplida', 0)} de {veces} veces")
    if r.get("aviso"):
        notas.append(f"⚠️ NO TE ESTÁ FUNCIONANDO ({r['ventaja_aviso'] * 100:+.1f} puntos frente al S&P 500 desde que la "
                     "apuntaste): corrígela o táchala, o se tachará sola")
    return f" ({'; '.join(notas)})" if notas else ""


def texto_reglas(agente: dict) -> str:
    reglas = agente.get("reglas", [])
    if not reglas:
        return ""
    return ("Tu libreta de reglas (las has escrito tú a partir de tus aciertos y errores; síguelas, pero si una "
            "regla te deja sin operar día tras día, es demasiado estricta: corrígela):\n"
            + "\n".join(f"{i}. {r['texto']}{_notas_regla(r)}" for i, r in enumerate(reglas, 1)))


DIAS_PARA_AVISAR_REGLA = 14
DIAS_PARA_TACHAR_REGLA = 7


def revisar_reglas(agente: dict, precios: dict[str, float], fecha: str) -> list[str]:
    """Una regla que lleva dos semanas sin funcionar (su autor va peor que el S&P 500 desde que la apuntó) recibe
    un aviso; si una semana después sigue sin funcionar, se tacha sola. Devuelve las tachadas."""
    tachadas, quedan = [], []
    for r in agente.get("reglas", []):
        ventaja = rendimiento_regla(agente, r, precios, fecha)
        if ventaja is None or _dias(r["fecha"], fecha) < DIAS_PARA_AVISAR_REGLA or ventaja >= 0:
            r.pop("aviso", None)
            r.pop("ventaja_aviso", None)
        elif not r.get("aviso"):
            r.update({"aviso": fecha, "ventaja_aviso": round(ventaja, 4)})
        elif _dias(r["aviso"], fecha) >= DIAS_PARA_TACHAR_REGLA:
            tachadas.append(r["texto"])
            continue
        else:
            r["ventaja_aviso"] = round(ventaja, 4)
        quedan.append(r)
    if "reglas" in agente:
        agente["reglas"] = quedan
    return tachadas


def registrar_cumplimiento(agente: dict, cambios) -> None:
    """`cambios` = {"cumplidas": [números], "incumplidas": [números]} con la numeración de antes de tachar."""
    if not isinstance(cambios, dict):
        return
    reglas = agente.get("reglas", [])
    for clave, campo in (("cumplidas", "cumplida"), ("incumplidas", "incumplida")):
        for n in set(cambios.get(clave) or []):
            try:
                i = int(n) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(reglas):
                reglas[i][campo] = reglas[i].get(campo, 0) + 1


# --- 3. Predicciones con nota -----------------------------------------------------------

def apuntar_prediccion(agente: dict, prediccion, fecha: str, precios: dict[str, float]) -> None:
    if not isinstance(prediccion, dict):
        return
    simbolo = str(prediccion.get("simbolo", "")).strip().upper()
    vs = str(prediccion.get("vs_sp500", "")).strip().lower()
    confianza = str(prediccion.get("confianza", "")).strip().lower()
    if vs not in ("mejor", "peor") or not precios.get(simbolo) or not precios.get(config.REFERENCIA):
        return
    agente.setdefault("predicciones", []).append({
        "fecha": fecha, "simbolo": simbolo, "vs_sp500": vs,
        "confianza": confianza if confianza in CONFIANZAS else "media", "motivo": str(prediccion.get("motivo", "")).strip()[:250],
        "precio": precios[simbolo], "sp": precios[config.REFERENCIA]})
    agente["predicciones"] = agente["predicciones"][-MAX_PREDICCIONES:]


def corregir_predicciones(agente: dict, fecha: str, precios: dict[str, float]) -> list[dict]:
    """Pone nota a las predicciones que ya tienen una semana. Devuelve las corregidas hoy."""
    corregidas = []
    for p in agente.get("predicciones", []):
        if "acierto" in p or _dias(p["fecha"], fecha) < DIAS_PREDICCION:
            continue
        valor, sp = _rent(precios.get(p["simbolo"]), p["precio"]), _rent(precios.get(config.REFERENCIA), p["sp"])
        if valor is None or sp is None:
            continue
        p.update({"corregida": fecha, "rent": round(valor, 4), "rent_sp": round(sp, 4),
                  "acierto": valor > sp if p["vs_sp500"] == "mejor" else valor < sp})
        corregidas.append(p)
    return corregidas


def aciertos(agente: dict) -> tuple[int, int]:
    hechas = [p for p in agente.get("predicciones", []) if "acierto" in p]
    return sum(p["acierto"] for p in hechas), len(hechas)


def aciertos_por_confianza(agente: dict) -> dict[str, tuple[int, int]]:
    hechas = [p for p in agente.get("predicciones", []) if "acierto" in p]
    return {c: (sum(p["acierto"] for p in grupo), len(grupo))
            for c in CONFIANZAS for grupo in [[p for p in hechas if p.get("confianza", "media") == c]] if grupo}


def texto_predicciones(agente: dict) -> str:
    preds = agente.get("predicciones", [])
    if not preds:
        return ""
    bien, total = aciertos(agente)
    lineas = []
    for p in [p for p in preds if "acierto" in p][-4:]:
        lineas.append(f"- {p['fecha']}: dijiste con confianza {p.get('confianza', 'media')} que {p['simbolo']} lo haría {p['vs_sp500']} que el S&P 500. "
                      f"Hizo {p['rent']:+.1%} frente a {p['rent_sp']:+.1%}: {'ACIERTO' if p['acierto'] else 'FALLO'}.")
    for p in [p for p in preds if "acierto" not in p][-3:]:
        lineas.append(f"- {p['fecha']}: dijiste que {p['simbolo']} lo haría {p['vs_sp500']} que el S&P 500 "
                      f"(se corregirá a la semana).")
    cabecera = (f"Tus predicciones: has acertado {bien} de {total}." if total
                else "Tus predicciones (aún ninguna corregida):")
    por_confianza = aciertos_por_confianza(agente)
    if por_confianza:
        cabecera += " Según tu confianza: " + ", ".join(f"{c} {b} de {t}" for c, (b, t) in por_confianza.items()) + \
            ". Si aciertas poco cuando estás muy seguro, te estás fiando demasiado de tus ideas."
    return cabecera + "\n" + "\n".join(lineas)


# --- 4. Repaso del día al cierre --------------------------------------------------------

def _decision_sin_repaso(agente: dict) -> dict | None:
    for b in reversed(agente.get("bitacora", [])):
        if b["tipo"] == "repaso":
            return None
        if b["tipo"] == "decision":
            return b if b.get("sp") else None
    return None


def repasar_dia(estado: dict, mercado, ia, anotar_diario, anotar_bitacora) -> int:
    """Con la bolsa cerrada, cada agente del equipo diario repasa su última decisión con los precios del cierre (y su
    boletín de operaciones anteriores), actualiza su libreta de reglas y su ánimo, igual que el intradía al cerrar
    su día. Usa el modelo ligero. Devuelve cuántos agentes han repasado."""
    from datetime import datetime, timezone
    from agentes import emociones  # emociones ya importa este módulo
    if mercado.minutos_desde_apertura() is not None:
        return 0  # con la bolsa abierta el día aún no ha terminado
    pendientes = [(a, b) for a in estado["agentes"]
                  if a["estado"] == "activo" and a.get("equipo", "diario") == "diario"
                  for b in [_decision_sin_repaso(a)] if b]
    if not pendientes:
        return 0
    simbolos = {config.REFERENCIA}
    for a, b in pendientes:
        simbolos |= set(a["posiciones"]) | {o["simbolo"] for o in b["operaciones"]}
    precios = mercado.ultimos_precios(sorted(simbolos))
    hechos = 0
    for a, b in pendientes:
        todos = {**estado.get("precios", {}), **precios}
        v = cartera.valor(a, todos)
        mensaje = prompts.mensaje_repaso(a, b, v, precios, texto_reglas(a)) + (
            "\n\n" + texto_boletin(boletin(a, todos, b["fecha"])) if boletin(a, todos, b["fecha"]) else "")
        try:
            r = ia.pedir_json(prompts.INSTRUCCIONES_REPASO.format(alias=a.get("alias", ""),
                                                                  personalidad=a.get("personalidad", "")), mensaje)
        except Exception as e:
            print(f"No se pudo escribir el repaso de {a['id']}: {e}")
            continue
        texto = str(r.get("repaso", "")).strip()
        if not texto:
            continue
        registrar_cumplimiento(a, r.get("reglas"))
        cambios = actualizar_reglas(a, r.get("reglas"), b["fecha"], v, precios.get(config.REFERENCIA))
        anotar_diario(a, b["fecha"], f"**Repaso del día.** {texto}"
                      + ("\n\n" + "\n".join(f"- {c}" for c in cambios) if cambios else ""))
        anotar_bitacora(a, b["fecha"], texto, tipo="repaso", valor=round(v, 2), reglas=cambios)
        a.setdefault("repasos", []).append({"fecha": b["fecha"], "texto": texto})
        a["repasos"] = a["repasos"][-5:]
        sentir_cierre(a, b, v, todos, emociones, datetime.now(timezone.utc))
        hechos += 1
    return hechos


def sentir_cierre(agente: dict, decision: dict, valor: float, precios: dict[str, float], emociones,
                  ahora) -> None:
    """Al cierre, el ánimo del equipo diario se pone al día con cómo le ha ido su decisión de hoy frente al S&P 500
    (pesa poco: es una decisión) y con su historial de operaciones (pesa más). Sin llamadas a la IA."""
    sp, sp_decision = precios.get(config.REFERENCIA), decision.get("sp")
    cambio_sp = sp / sp_decision - 1 if sp and sp_decision else None
    dif = valor / decision["valor"] - 1 - cambio_sp if decision.get("operaciones") and decision.get("valor") \
        and cambio_sp is not None else None
    situacion = prompts.situacion_laboral(agente, valor, precios, decision["fecha"])
    emociones.al_cierre(agente, dif, precios, decision["fecha"], situacion, ahora, cambio_sp)


# --- 5. Plan de rescate -------------------------------------------------------------------

def en_peligro(s: dict) -> bool:
    """Con un aviso, por detrás del S&P 500 en la última semana del periodo o cerca del límite de caída."""
    return bool(s.get("avisos")) or (s["diferencia"] <= 0 and s["dias_restantes"] <= 7) \
        or s["caida"] < -config.MAX_CAIDA * 0.6


def actualizar_rescate(agente: dict, peligro: bool, texto: str, fecha: str) -> None:
    """Guarda el último plan de rescate mientras el puesto peligra; cuando ya no peligra, se olvida."""
    if not peligro:
        agente.pop("rescate", None)
    elif texto.strip():
        agente["rescate"] = {"fecha": fecha, "texto": texto.strip()[:600]}


def texto_rescate(agente: dict) -> str:
    r = agente.get("rescate")
    return f"Tu plan de rescate (lo escribiste el {r['fecha']}; síguelo o corrígelo):\n{r['texto']}" if r else ""


# --- 6. Ajustes de estrategia de los viernes ------------------------------------------------

MAX_AJUSTES = 3


def aplicar_ajuste(agente: dict, texto, fecha: str) -> str:
    texto = str(texto or "").strip()[:300]
    if not texto or any(texto.lower() == a["texto"].lower() for a in agente.get("ajustes", [])):
        return ""
    agente.setdefault("ajustes", []).append({"fecha": fecha, "texto": texto})
    # Solo se quedan los últimos: la estrategia original no cambia nunca
    agente["ajustes"] = agente["ajustes"][-MAX_AJUSTES:]
    return texto


def estrategia_con_ajustes(agente: dict) -> str:
    ajustes = agente.get("ajustes", [])
    if not ajustes:
        return agente["estrategia"]
    return (agente["estrategia"] + "\n\nTus ajustes a la estrategia (los decidiste tú en tus reflexiones de los viernes; "
            "afinan la estrategia, no cambian su idea principal ni las reglas fijas):\n"
            + "\n".join(f"- {a['texto']} ({a['fecha']})" for a in ajustes))


# --- 7. Resumen mensual de la memoria --------------------------------------------------------

DIAS_PARA_RESUMEN = 14


def toca_resumen(agente: dict, fecha: str) -> bool:
    hecho = (agente.get("memoria_larga") or {}).get("fecha", "")
    return (agente["estado"] == "activo" and hecho[:7] != fecha[:7]
            and _dias(agente["creado"], fecha) >= DIAS_PARA_RESUMEN)


def resumir_memoria(agente: dict, fecha: str, ia, anotar_diario) -> bool:
    """Una vez al mes condensa todo lo aprendido para que no se pierda lo antiguo (con el modelo ligero)."""
    try:
        r = ia.pedir_json(prompts.INSTRUCCIONES_MEMORIA_LARGA.format(alias=agente.get("alias", ""),
                                                                     personalidad=agente.get("personalidad", "")),
                          prompts.mensaje_memoria_larga(agente))
    except Exception as e:
        print(f"No se pudo escribir el resumen del mes de {agente['id']}: {e}")
        return False
    texto = str(r.get("memoria", "")).strip()
    if not texto:
        return False
    agente["memoria_larga"] = {"fecha": fecha, "texto": texto[:1500]}
    anotar_diario(agente, fecha, f"**Lo que he aprendido hasta ahora (resumen del mes).** {texto}")
    return True


# --- 8. Días parecidos ------------------------------------------------------------------

def tipo_de_dia(cambio_sp: float | None) -> str | None:
    """Cómo va el S&P 500 frente al cierre de ayer cuando decide el equipo diario."""
    if cambio_sp is None:
        return None
    if cambio_sp <= -0.015:
        return "caída fuerte"
    if cambio_sp <= -0.005:
        return "caída"
    if cambio_sp >= 0.015:
        return "subida fuerte"
    if cambio_sp >= 0.005:
        return "subida"
    return "tranquilo"


def texto_dias_parecidos(agente: dict, tipo: str | None, referencia: list[dict], fecha: str, maximo: int = 2) -> str:
    """Qué hizo el agente la última vez que el mercado se movió igual y cómo le fue la semana siguiente."""
    if not tipo or tipo == "tranquilo":
        return ""
    ref = {r["fecha"]: r["precio"] for r in referencia}
    lineas = []
    for b in reversed(agente.get("bitacora", [])):
        dia = b.get("dia") or {}
        if b["tipo"] != "decision" or dia.get("tipo") != tipo or not b.get("valor") or not b.get("sp") \
                or _dias(b["fecha"], fecha) < DIAS_PREDICCION:
            continue
        despues = next((h for h in agente.get("historial", [])
                        if _dias(b["fecha"], h["fecha"]) >= DIAS_PREDICCION and h["fecha"] in ref), None)
        if not despues:
            continue
        suyo, sp = despues["valor"] / b["valor"] - 1, ref[despues["fecha"]] / b["sp"] - 1
        lineas.append(f"- El {b['fecha']} el S&P 500 iba {dia['cambio_sp']:+.1%} ({tipo}). Decidiste: {b['resumen'][:200]} "
                      f"Una semana después tu cartera había hecho {suyo:+.1%} y el S&P 500 {sp:+.1%}: "
                      f"{'te salió bien' if suyo > sp else 'te salió mal'}.")
        if len(lineas) >= maximo:
            break
    if not lineas:
        return ""
    return (f"Días parecidos a hoy (el S&P 500 va en {tipo}) y cómo te fue:\n" + "\n".join(lineas))


# --- 9. Evolución del aprendizaje (para la web) --------------------------------------------

def evolucion_semanal(agente: dict, referencia: list[dict]) -> list[dict]:
    """Semana a semana: cuántos puntos le saca al S&P 500 y cuántas predicciones acierta.
    Sirve para ver si el aprendizaje le está haciendo mejorar."""
    ref = {r["fecha"]: r["precio"] for r in referencia}
    puntos = [h for h in agente.get("historial", []) if h["fecha"] in ref]
    semanas: dict[str, dict] = {}
    anterior = None
    for h in puntos:
        lunes = (date.fromisoformat(h["fecha"]) - timedelta(days=date.fromisoformat(h["fecha"]).weekday())).isoformat()
        s = semanas.setdefault(lunes, {"semana": lunes, "base": anterior or h})
        s["fin"] = h
        anterior = h
    filas = []
    for lunes, s in sorted(semanas.items()):
        b, f = s["base"], s["fin"]
        fila = {"semana": lunes, "ventaja": (f["valor"] / b["valor"] - 1) - (ref[f["fecha"]] / ref[b["fecha"]] - 1)}
        hechas = [p for p in agente.get("predicciones", []) if "acierto" in p
                  and lunes <= p.get("corregida", "") < (date.fromisoformat(lunes) + timedelta(days=7)).isoformat()]
        if hechas:
            fila["aciertos"] = [sum(p["acierto"] for p in hechas), len(hechas)]
        filas.append(fila)
    return filas


def veredicto(filas: list[dict]) -> str | None:
    """Con 4 semanas o más: si las 2 últimas van mejor que las 2 primeras frente al S&P 500."""
    if len(filas) < 4:
        return None
    antes = sum(f["ventaja"] for f in filas[:2]) / 2
    ahora = sum(f["ventaja"] for f in filas[-2:]) / 2
    if ahora - antes > 0.002:
        return "mejora"
    if antes - ahora > 0.002:
        return "empeora"
    return "igual"


# --- 10. Reglas para los fichajes nuevos ----------------------------------------------------

MAX_HEREDADAS = 3


def heredar_reglas(estado: dict, agente: dict, precios: dict[str, float], fecha: str) -> list[str]:
    """Un fichaje nuevo no empieza de cero: se lleva las reglas de la oficina que mejor están funcionando."""
    sp = precios.get(config.REFERENCIA)
    hechas = []
    for _, quien, r in reglas_que_funcionan(estado, precios, fecha)[:MAX_HEREDADAS]:
        agente.setdefault("reglas", []).append({"fecha": fecha, "texto": r["texto"], "heredada": quien.removesuffix(" (va primero)"),
                                                **({"valor": config.CAPITAL_INICIAL, "sp": sp} if sp else {})})
        hechas.append(r["texto"])
    return hechas


# --- 11. Vicios en números -------------------------------------------------------------------

MIN_OPERACIONES_VICIOS = 4


def vicios(agente: dict, precios: dict[str, float], fecha: str) -> dict:
    """Costumbres que se calculan solas: si persigue subidas, si vende pronto, si se concentra y cuánto opera."""
    seg = [s for s in agente.get("seguimiento", []) if _dias(s["fecha"], fecha) <= DIAS_BOLETIN]
    if len(seg) < MIN_OPERACIONES_VICIOS:
        return {}
    res = {}
    compras = [s for s in seg if "subida_5d" in s and s["lado"] == "comprar"]
    if compras:
        res["persigue"] = [sum(s["subida_5d"] > 0.05 for s in compras), len(compras)]
    ventas = [n for n in boletin(agente, precios, fecha) if n["lado"] == "vender"]
    if ventas:
        res["vende_pronto"] = [sum(not n["bien"] for n in ventas), len(ventas)]
    total = cartera.valor(agente, precios)
    if agente.get("posiciones") and total:
        mayor = max(p["cantidad"] * precios.get(s, p["coste_medio"]) for s, p in agente["posiciones"].items())
        res["concentracion"] = round(mayor / total, 3)
        res["posiciones"] = len(agente["posiciones"])
    semanas = max(1, (_dias(min(s["fecha"] for s in seg), fecha) + 1) / 7)
    res["por_semana"] = round(len(seg) / semanas, 1)
    return res


def texto_vicios(agente: dict, precios: dict[str, float], fecha: str) -> str:
    v = vicios(agente, precios, fecha)
    if not v:
        return ""
    lineas = []
    if "persigue" in v:
        b, t = v["persigue"]
        lineas.append(f"- {b} de tus {t} compras fueron de valores que ya habían subido más de un 5 % en la semana"
                      + (": tiendes a perseguir subidas." if b / t > 0.5 else "."))
    if "vende_pronto" in v:
        b, t = v["vende_pronto"]
        lineas.append(f"- Después de {b} de tus {t} ventas el valor siguió mejor que el S&P 500"
                      + (": tiendes a vender demasiado pronto." if b / t > 0.5 else "."))
    if "concentracion" in v:
        lineas.append(f"- Tu mayor posición pesa un {v['concentracion']:.0%} de tu cartera y tienes {v['posiciones']} valores"
                      + (": estás muy concentrado." if v["concentracion"] > 0.18 or v["posiciones"] <= 2 else "."))
    lineas.append(f"- Haces unas {v['por_semana']:.1f} operaciones por semana.")
    return "Tus costumbres en números (de tus operaciones del último mes):\n" + "\n".join(lineas)


# --- Rechazos: lo que el control no pudo arreglar, para no repetirlo -------------------------------------

MAX_RECHAZOS_RECORDADOS = 5


def apuntar_rechazos(agente: dict, fecha: str, rechazos: list[str]) -> None:
    """Guarda las órdenes que el control no pudo arreglar (los ajustes automáticos no cuentan): las lee en su
    siguiente decisión para no repetir el error, sin gastar ninguna llamada de más."""
    nuevos = [{"fecha": fecha, "texto": r} for r in rechazos if not r.startswith("Ajuste automático")]
    if nuevos:
        agente["rechazos_recientes"] = (agente.get("rechazos_recientes", []) + nuevos)[-MAX_RECHAZOS_RECORDADOS:]


def texto_rechazos(agente: dict) -> str:
    if not agente.get("rechazos_recientes"):
        return ""
    return ("Órdenes tuyas que el control de riesgo NO pudo ejecutar hace poco (revisa las reglas y LO QUE PUEDES "
            "COMPRAR AHORA para que no vuelva a pasar):\n"
            + "\n".join(f"- {r['fecha']}: {r['texto']}" for r in agente["rechazos_recientes"]))


# --- Descartes con nota: la mejor compra que dejó pasar y qué hizo después -------------------------------

MAX_DESCARTES = 20


def apuntar_descarte(agente: dict, descarte, fecha: str, precios: dict[str, float], compradas: set[str],
                     hora: str = "") -> None:
    """Viene en la misma respuesta de la decisión: no gasta ninguna llamada. Si al final lo compró, no es un descarte."""
    if not isinstance(descarte, dict):
        return
    simbolo = str(descarte.get("simbolo", "")).strip().upper()
    if not precios.get(simbolo) or not precios.get(config.REFERENCIA) or simbolo in compradas:
        return
    agente.setdefault("descartes", []).append({
        "fecha": fecha, **({"hora": hora} if hora else {}), "simbolo": simbolo,
        "motivo": str(descarte.get("motivo", "")).strip()[:250], "precio": precios[simbolo],
        "sp": precios[config.REFERENCIA]})
    agente["descartes"] = agente["descartes"][-MAX_DESCARTES:]


def calificar_descartes(agente: dict, fecha: str, precios: dict[str, float], al_cierre: bool = False) -> list[dict]:
    """Nota de cada descarte con los precios que ya hay: al cierre del día en el intradía, a la semana en el diario."""
    calificados = []
    for d in agente.get("descartes", []):
        if "rent" in d or (d["fecha"] != fecha if al_cierre else _dias(d["fecha"], fecha) < DIAS_PREDICCION):
            continue
        valor, sp = _rent(precios.get(d["simbolo"]), d["precio"]), _rent(precios.get(config.REFERENCIA), d["sp"])
        if valor is None or sp is None:
            continue
        d.update({"calificado": fecha, "rent": round(valor, 4), "rent_sp": round(sp, 4)})
        calificados.append(d)
    return calificados


def texto_descartes(agente: dict) -> str:
    hechos = [d for d in agente.get("descartes", []) if "rent" in d]
    if not hechos:
        return ""
    mejores = sum(d["rent"] > d["rent_sp"] for d in hechos)
    lineas = [f"- {d['fecha']}{' ' + d['hora'] if d.get('hora') else ''}: descartaste {d['simbolo']} ({d['motivo'] or 'sin motivo'}). "
              f"Después hizo {d['rent']:+.1%} frente a {d['rent_sp']:+.1%} del S&P 500: "
              + ("habría ido MEJOR que el índice." if d["rent"] > d["rent_sp"] else "acertaste al dejarlo pasar.")
              for d in hechos[-5:]]
    return (f"Tus descartes con nota (lo que dejaste pasar y qué hizo después): de los últimos {len(hechos)}, "
            f"{mejores} habrían ganado al S&P 500.\n" + "\n".join(lineas))


# --- Aviso de inactividad -------------------------------------------------------------------------------

def actualizar_inactividad(agente: dict, fecha: str, opero: bool, compras_fuente: set[str] | None = None) -> None:
    """Cuenta los días seguidos sin operar. Al copión y a la del Congreso (`compras_fuente` no es None) solo
    les cuenta si su fuente ha comprado en esos días: si la fuente está quieta, estar quieto es copiarla bien."""
    if opero:
        agente.pop("sin_operar", None)
        return
    s = agente.setdefault("sin_operar", {"desde": fecha, "dias": 0})
    if s.get("ultimo") != fecha:
        s["dias"] += 1
        s["ultimo"] = fecha
    if compras_fuente is not None:
        s["fuente"] = sorted(compras_fuente)


def inactivo(agente: dict) -> dict | None:
    """La racha sin operar, solo si ya merece aviso (para el copión y la del Congreso, si su fuente ha comprado)."""
    s = agente.get("sin_operar")
    intradia = agente.get("equipo", "diario") == "intradia"
    if not s or s["dias"] < (config.DIAS_INACTIVIDAD_INTRADIA if intradia else config.DIAS_INACTIVIDAD_DIARIO):
        return None
    if "fuente" in s and not s["fuente"]:
        return None
    return s


def texto_inactividad(agente: dict) -> str:
    if not (s := inactivo(agente)):
        return ""
    intradia = agente.get("equipo", "diario") == "intradia"
    if "fuente" in s:
        return (f"AVISO: llevas {s['dias']} días sin operar y desde el {s['desde']} tu fuente ha comprado "
                f"{', '.join(s['fuente'][:8])}, pero no has copiado nada. Si no las copias, explica por qué; si tu "
                "regla te frena siempre, piensa en ajustarla el viernes. No estás obligado a operar.")
    if intradia:
        cuando = "un día de bolsa entero" if s["dias"] == 1 else f"{s['dias']} días de bolsa enteros"
        cuando += " sin comprar nada"
    else:
        cuando = f"{s['dias']} decisiones seguidas sin operar"
    return (f"AVISO: llevas {cuando}. Sin operaciones no puedes saber si tu estrategia "
            "funciona. Mira tus descartes con nota: si lo que dejas pasar suele ganar al índice, alguna regla te "
            "está dejando fuera y puedes suavizarla en tu ajuste del viernes. No estás obligado a operar.")
