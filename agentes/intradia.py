"""Equipo intradía: cuatro agentes que deciden más o menos cada hora y lo cierran todo antes del cierre.

Se ejecuta desde GitHub Actions cada media hora mientras la bolsa está abierta: `python -m agentes.intradia`.
Cada agente decide como mucho una vez cada ~50 minutos; en la última hora y cuarto solo puede vender y a
falta de 45 minutos se le cierra todo. Al cierre escribe su informe del día y se le evalúa.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentes import analistas, aprendizaje, cartera, ciclo, config, emociones, fundamentales, noticias, oficina, prompts, seguimiento, web
from agentes.alpaca import NUEVA_YORK


def _intradia(estado: dict) -> list[dict]:
    return [a for a in estado["agentes"] if a["estado"] == "activo" and a.get("equipo") == "intradia"]


def _entrada_del_dia(agente: dict, fecha: str) -> dict:
    """La entrada de la bitácora de hoy: un único informe diario que se completa ronda a ronda."""
    bitacora = agente.setdefault("bitacora", [])
    if bitacora and bitacora[-1].get("fecha") == fecha and "rondas" in bitacora[-1]:
        return bitacora[-1]
    ciclo.anotar_bitacora(agente, fecha, "", rondas=[])
    return agente["bitacora"][-1]


def cerrar_dia(estado: dict, agente: dict, fecha: str, precios: dict[str, float], motivo: str, mercado, ia,
               avisos) -> None:
    """Cierra las posiciones, escribe el informe del día, anota el valor y evalúa al agente."""
    dia = agente["dia"]
    operaciones = cartera.cerrar_todo(agente, precios)
    for o in operaciones:
        o["motivo"] = motivo
    ciclo.espejo(mercado, operaciones, fecha)
    ciclo.anotar_operaciones(fecha, operaciones)
    entrada = _entrada_del_dia(agente, fecha)
    entrada["operaciones"] += operaciones
    dia["cerrado"] = True
    v = cartera.valor(agente, precios)
    situacion = prompts.texto_situacion(prompts.situacion_laboral(agente, v, precios, fecha))
    resumen = f"{motivo} Termina el día con {v:,.2f} $ ({(v / dia['valor_inicio'] - 1) * 100:+.2f} %)."
    balance, cambios = "", []
    # El boletín del día: lo que ha ganado o perdido con cada valor (al cierre ya está todo vendido)
    por_valor: dict[str, float] = {}
    for o in entrada["operaciones"]:
        por_valor[o["simbolo"]] = por_valor.get(o["simbolo"], 0) + (o["importe"] if o["lado"] == "vender" else -o["importe"])
    notas = ", ".join(f"{s} {g:+,.2f} $" for s, g in por_valor.items())
    if entrada["rondas"] or entrada["operaciones"]:
        try:
            r = ia.pedir_json(prompts.INSTRUCCIONES_BALANCE_DIA.format(alias=agente.get("alias", ""),
                                                                       personalidad=agente.get("personalidad", "")),
                              prompts.mensaje_balance_dia(entrada["rondas"], entrada["operaciones"],
                                                          dia["valor_inicio"], v, situacion,
                                                          aprendizaje.texto_reglas(agente), notas))
            resumen = str(r.get("resumen") or resumen).strip()
            balance = str(r.get("balance", "")).strip()
            aprendizaje.registrar_cumplimiento(agente, r.get("reglas"))
            cambios = aprendizaje.actualizar_reglas(agente, r.get("reglas"), fecha, v, precios.get(config.REFERENCIA))
        except Exception as e:
            print(f"No se pudo escribir el informe del día de {agente['id']}: {e}")
    entrada.update({"resumen": resumen, "balance": balance, "valor": round(v, 2), "reglas": cambios})
    aprendizaje.calificar_descartes(agente, fecha, precios, al_cierre=True)
    emociones.apuntar_dia_intradia(agente, fecha, entrada["operaciones"])
    # Su día pesa más que su historial: lo cierra todo cada tarde
    sp, sp_inicio = precios.get(config.REFERENCIA), dia.get("sp_inicio")
    cambio_sp = sp / sp_inicio - 1 if sp and sp_inicio else None
    dif_dia = v / dia["valor_inicio"] - 1 - (cambio_sp or 0) if dia.get("valor_inicio") else None
    emociones.al_cierre(agente, dif_dia, precios, fecha, prompts.situacion_laboral(agente, v, precios, fecha),
                        datetime.now(timezone.utc), cambio_sp)
    aprendizaje.actualizar_inactividad(agente, fecha, any(o["lado"] == "comprar" for o in entrada["operaciones"]))
    ciclo.anotar_diario(agente, fecha, f"**Cierre del día.** {resumen}\n\n{balance}\n\n"
                        + "".join(f"- {c}\n" for c in cambios) + f"\nValor de la cartera: {v:,.2f} $")
    hechas = ", ".join(f"{o['lado']} {o['simbolo']}" for o in entrada["operaciones"]) or "ninguna"
    agente.setdefault("memoria", []).append({"fecha": fecha, "texto": f"{fecha}: {resumen[:250]} {balance[:250]} "
                                             f"Operaciones: {hechas[:200]}." + (f" Resultado por valor: {notas[:200]}." if notas else "")})
    agente["memoria"] = agente["memoria"][-10:]
    if not agente["historial"] or agente["historial"][-1]["fecha"] != fecha:
        agente["historial"].append({"fecha": fecha, "valor": round(v, 2)})
    despido = ciclo.evaluar(estado, agente, fecha, precios)
    if despido:
        ciclo.despedir(estado, agente, despido, fecha, precios, mercado, ia, avisos)


def _pct(a, b) -> float | None:
    return (a / b - 1) * 100 if a and b else None


def candidatos(plantilla: str, datos: dict[str, dict], tono: dict[str, dict], maximo: int = 5) -> str:
    """Los valores que ya cumplen la señal de cada estrategia intradía, calculados con los precios de la ronda: así el
    agente decide sobre casos concretos en vez de buscarlos en la tabla (y no se queda siempre esperando)."""
    filas = []
    for s, d in datos.items():
        p, vs_ap, hora = d["precio"], _pct(d["precio"], d.get("apertura")), _pct(d["precio"], d.get("hace_1h"))
        vs_vwap, t = _pct(p, d.get("vwap")), tono.get(s, {})
        noticia = (f"noticias {t['puntos']:+d} ({t['positivas']} buenas, {t['negativas']} malas)"
                   if t.get("noticias") else "sin noticias")
        if vs_ap is None:
            continue
        if plantilla == "rebote" and vs_ap <= -2 and hora is not None and hora >= -0.3:
            filas.append((vs_ap, f"{s}: cae un {-vs_ap:.1f} % desde la apertura y en la última hora {hora:+.1f} % "
                                 f"(ya no cae con fuerza); {noticia}."))
        elif plantilla == "noticias_rapidas" and t.get("puntos", 0) > 0:
            vs_ayer = _pct(p, d.get("cierre_ayer")) or vs_ap
            if vs_ayer < 3:
                filas.append((-t["puntos"], f"{s}: {noticia}; sube solo un {vs_ayer:+.1f} % desde ayer (menos del 3 %)."))
            elif vs_vwap is not None and abs(vs_vwap) <= 0.5:
                filas.append((-t["puntos"], f"{s}: {noticia}; ya sube un {vs_ayer:+.1f} %, pero ha vuelto a su VWAP "
                                            f"({vs_vwap:+.1f} %)."))
        elif plantilla == "apertura" and d.get("rango_alto") and p > d["rango_alto"] * 1.002:
            vol = f"volumen {d['vol_relativo']:.1f}x" if d.get("vol_relativo") else "volumen sin datos"
            filas.append((-_pct(p, d["rango_alto"]), f"{s}: rompe el máximo de la primera media hora "
                                                     f"({d['rango_alto']:.2f}) en un {_pct(p, d['rango_alto']):+.1f} %; {vol}; {noticia}."))
        elif plantilla == "impulso" and vs_ap >= 1 and hora is not None and hora > 0 and d.get("maximo") \
                and p >= d["maximo"] * 0.99:
            filas.append((-vs_ap, f"{s}: sube un {vs_ap:+.1f} % desde la apertura, {hora:+.1f} % en la última hora y "
                                  f"está cerca de su máximo; {noticia}."))
    if not filas and plantilla in ("rebote", "noticias_rapidas", "apertura", "impulso"):
        return "- Ninguno ahora mismo."
    return "\n".join(f"- {texto}" for _, texto in sorted(filas)[:maximo])


def ronda(agente: dict, fecha: str, hora: str, minutos_cierre: int, precios: dict[str, float], tabla: str,
          noticias_hora: list[dict], ranking: list[dict], texto_analistas: str, sentimiento: str, mercado, ia,
          datos_noticias: dict, ahora: datetime, senales: str = "") -> None:
    dia = agente["dia"]
    entrada = _entrada_del_dia(agente, fecha)
    v = cartera.valor(agente, precios)
    puede_comprar = minutos_cierre > config.MINUTOS_SIN_COMPRAS
    emocion = emociones.actualizar(agente, precios, fecha, prompts.situacion_laboral(agente, v, precios, fecha),
                                   ahora, rondas_hoy=entrada["rondas"])
    instrucciones = prompts.INSTRUCCIONES_INTRADIA.format(
        alias=agente.get("alias", agente["nombre"]), personalidad=agente.get("personalidad", ""),
        estrategia=aprendizaje.estrategia_con_ajustes(agente), reglas=f"{prompts.REGLAS}\n{prompts.REGLAS_INTRADIA}")
    mensaje = prompts.mensaje_intradia(agente, v, precios, tabla, hora, minutos_cierre, dia["valor_inicio"],
                                       puede_comprar, noticias_hora, ciclo.lineas_clasificacion(ranking, agente),
                                       texto_analistas, entrada["rondas"],
                                       "\n\n".join(t for t in (prompts.memoria_agente(agente),
                                                                 prompts.texto_interior(agente, precios, fecha)) if t), fecha,
                                       sentimiento, senales)
    try:
        simbolos = sorted((set(config.UNIVERSO) & set(precios)) | set(agente["posiciones"]))
        decision = ciclo.leer_decision(ia.pedir_json(
            instrucciones, mensaje, esquema=prompts.esquema_decision(simbolos, intradia=True, puede_comprar=puede_comprar)))
    except Exception as e:
        # Sin apuntar la hora: lo vuelve a intentar en la siguiente ejecución (media hora después)
        print(f"{agente['id']} no pudo decidir a las {hora}: {e}")
        entrada["rondas"].append({"hora": hora, "resumen": "No pude decidir: la IA no respondió bien. Lo intento en la próxima ronda.",
                                  "error": True, "operaciones": [], "rechazos": [], "valor": round(v, 2)})
        entrada["resumen"] = entrada.get("resumen") or entrada["rondas"][-1]["resumen"]
        return
    dia["ultima"] = ahora.isoformat()
    aprendizaje.actualizar_rescate(agente, aprendizaje.en_peligro(prompts.situacion_laboral(agente, v, precios, fecha)),
                                   decision["informe"]["rescate"], fecha)
    operaciones, rechazos = cartera.ejecutar_ordenes(agente, decision["ordenes"], precios,
                                                     puede_comprar=puede_comprar, exigir_stop=True)
    emociones.etiquetar(operaciones, emocion["emocion"], bool(decision["noticias_usadas"]))
    aprendizaje.apuntar_rechazos(agente, f"{fecha} {hora}", rechazos)
    aprendizaje.apuntar_descarte(agente, decision["descarte"], fecha, precios,
                                 {o["simbolo"] for o in operaciones if o["lado"] == "comprar"}, hora)
    ciclo.espejo(mercado, operaciones, fecha)
    ciclo.anotar_operaciones(fecha, operaciones)
    ciclo.empezar_a_invertir(agente, fecha, operaciones, v, dia.get("sp_inicio") or precios.get(config.REFERENCIA))
    noticias.marcar_uso(datos_noticias, decision["noticias_usadas"], agente["id"])
    v = cartera.valor(agente, precios)
    entrada["rondas"].append({"hora": hora, "resumen": decision["resumen"], "informe": decision["informe"],
                              "animo": decision["animo"], "emocion": emocion["emocion"],
                              "operaciones": operaciones, "rechazos": rechazos, "valor": round(v, 2)})
    entrada["operaciones"] += operaciones
    entrada["rechazos"] += rechazos
    entrada["noticias_usadas"] = sorted(set(entrada.get("noticias_usadas", [])) | set(decision["noticias_usadas"]))
    entrada.update({"resumen": decision["resumen"], "informe": decision["informe"], "valor": round(v, 2)})
    ciclo.anotar_diario(agente, f"{fecha} {hora}",
                        ciclo.informe_markdown(decision, operaciones, rechazos, v))


def eventos_del_dia(fecha: str, estado: dict | None = None) -> list[tuple[datetime, datetime, str]]:
    """Los eventos de ese día: (hora del evento, desde cuándo se decide cada ronda, qué es). Si es antes de abrir,
    las rondas seguidas empiezan con la primera ronda del día (15 minutos después de la apertura)."""
    lista = analistas.todos_los_eventos(estado)
    vistos, dia = set(), []
    for cuando, texto in sorted(lista):
        if cuando[:10] != fecha or cuando in vistos:
            continue
        vistos.add(cuando)
        t = datetime.fromisoformat(cuando).replace(tzinfo=NUEVA_YORK)
        dia.append((t, max(t, t.replace(hour=9, minute=45)), texto))
    return dia


def evento_en_curso(ahora: datetime, estado: dict | None = None) -> tuple[datetime, str] | None:
    """El evento cuya hora de rondas seguidas (MINUTOS_EVENTO) está en curso, si lo hay."""
    for t, desde, texto in eventos_del_dia(ahora.astimezone(NUEVA_YORK).date().isoformat(), estado):
        if desde <= ahora < desde + timedelta(minutes=config.MINUTOS_EVENTO):
            return t, texto
    return None


def evento_para_el_diario(ahora: datetime, estado: dict) -> tuple[datetime, str] | None:
    """Un evento de hoy con la bolsa ya abierta (no antes de abrir) del que ha pasado una hora: el equipo diario
    decide una vez más para poder reaccionar. Hasta las 15:15 de Nueva York (que quede tiempo) y una vez por evento."""
    local = ahora.astimezone(NUEVA_YORK)
    for t, _, texto in eventos_del_dia(local.date().isoformat(), estado):
        if (t > t.replace(hour=9, minute=30) and t + timedelta(minutes=config.MINUTOS_EVENTO) <= ahora
                and local < local.replace(hour=15, minute=15, second=0, microsecond=0)
                and estado.get("evento_diario") != t.isoformat()):
            return t, texto
    return None


def texto_eventos(ahora: datetime, estado: dict | None = None) -> str:
    """El aviso para el intradía: el evento en curso o los que quedan hoy."""
    en_curso = evento_en_curso(ahora, estado)
    if en_curso:
        return (f"⚡ EVENTO: {en_curso[1]} a las {en_curso[0]:%H:%M} de Nueva York. Durante una hora decides cada "
                f"15 minutos en vez de cada hora: es cuando más se mueve el mercado dentro del día.")
    luego = [(t, texto) for t, desde, texto in eventos_del_dia(ahora.astimezone(NUEVA_YORK).date().isoformat(), estado)
             if desde > ahora]
    return "\n".join(f"📅 Hoy, {texto} a las {t:%H:%M} de Nueva York: desde entonces, durante una hora, decidirás "
                     f"cada 15 minutos. Piensa si te conviene esperar a conocerlo o colocarte antes." for t, texto in luego)


def ejecutar(mercado, ia, avisos, ahora: datetime | None = None, ia_noticias=None) -> str:
    estado = ciclo.cargar_estado()
    ciclo.revisar_estrategias(estado)  # una estrategia revisada vale desde la primera ronda, no desde el ciclo diario
    equipo = _intradia(estado)
    if not equipo:
        return "No hay agentes intradía."
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local = ahora.astimezone(NUEVA_YORK)
    fecha, hora = local.date().isoformat(), local.strftime("%H:%M")
    sesion = mercado.sesion(ahora)
    pendientes = [a for a in equipo if a["posiciones"] and (sesion is None or a.get("dia", {}).get("fecha") != fecha)]

    if sesion is None and not pendientes:
        return "La bolsa está cerrada: no se hace nada."
    # También lo que tiene el equipo diario: aquí se vigilan sus stops
    simbolos = sorted(set(config.UNIVERSO) | {config.REFERENCIA}
                      | {s for a in estado["agentes"] if a["estado"] == "activo" for s in a["posiciones"]})
    if sesion is None:
        # Seguridad: si una ejecución no llegó a tiempo, se cierra lo que quedara abierto al último precio
        precios = mercado.ultimos_precios(simbolos)
        for a in pendientes:
            a.setdefault("dia", {"fecha": fecha, "valor_inicio": cartera.valor(a, precios), "cerrado": False})
            cerrar_dia(estado, a, a["dia"]["fecha"], precios, "Cierre forzoso: la sesión terminó con posiciones abiertas.",
                       mercado, ia, avisos)
        return _guardar(estado, "Se cerraron posiciones que quedaron abiertas.", precios)

    desde, hasta = sesion
    datos = mercado.precios_del_dia(simbolos, ahora)
    precios = {s: d["precio"] for s, d in datos.items()}
    stops_diario = revisar_stops_diario(estado, precios, fecha, hora, mercado, ahora)
    resultados_diario = antes_de_resultados(estado, precios, fecha, hora, hasta, mercado, ia, ahora)
    hay_cambios = bool(pendientes or stops_diario or resultados_diario)  # si cambió algo, se guarda aunque no haya ronda
    for a in pendientes:  # posiciones de un día anterior que no se cerraron
        cerrar_dia(estado, a, a["dia"]["fecha"], precios, "Cierre forzoso de posiciones de un día anterior.",
                   mercado, ia, avisos)
    for a in equipo:
        if a.get("dia", {}).get("fecha") != fecha:
            a["dia"] = {"fecha": fecha, "valor_inicio": round(cartera.valor(a, precios), 2), "cerrado": False,
                        "ultima": None, "sp_inicio": precios.get(config.REFERENCIA)}

    abiertos = [a for a in _intradia(estado) if not a["dia"]["cerrado"]]
    if not abiertos:
        return _guardar(estado, "El equipo intradía ya cerró hoy.", precios) if hay_cambios else "El equipo intradía ya cerró hoy."
    if hasta <= config.MINUTOS_CIERRE_INTRADIA:
        for a in abiertos:
            cerrar_dia(estado, a, fecha, precios, "Cierre del día: el intradía nunca duerme con posiciones.",
                       mercado, ia, avisos)
        return _guardar(estado, "Cierre del equipo intradía.", precios)
    if desde < 15:
        return _guardar(estado, "Aún es pronto.", precios) if hay_cambios else "Aún es pronto: se espera a la primera ronda."

    cerrados_antes = len([a for a in estado["agentes"] if a.get("dia", {}).get("cerrado")])
    saltos = 0
    for a in abiertos:  # stops y objetivos: se revisan en cada ejecución, sin gastar IA
        hechas = cartera.revisar_stops(a, precios)
        if hechas:
            emociones.por_ventas_automaticas(a, hechas, ahora)
            saltos += len(hechas)
            ciclo.espejo(mercado, hechas, fecha)
            ciclo.anotar_operaciones(fecha, hechas)
            entrada = _entrada_del_dia(a, fecha)
            entrada["operaciones"] += hechas
            entrada["valor"] = round(cartera.valor(a, precios), 2)
            ciclo.anotar_diario(a, f"{fecha} {hora}", "\n".join(f"- {o['lado'].capitalize()} {o['simbolo']}: {o['motivo']}"
                                                              for o in hechas))
    for a in abiertos:
        if cartera.valor(a, precios) < a["dia"]["valor_inicio"] * (1 - config.MAX_PERDIDA_DIA_INTRADIA):
            cerrar_dia(estado, a, fecha, precios, f"Límite de pérdida diaria ({config.MAX_PERDIDA_DIA_INTRADIA:.0%}) "
                       "alcanzado: lo cierra todo y no vuelve a operar hasta mañana.", mercado, ia, avisos)
    evento = evento_en_curso(ahora, estado)
    espera = config.MINUTOS_ENTRE_DECISIONES_EVENTO if evento else config.MINUTOS_ENTRE_DECISIONES
    toca = [a for a in _intradia(estado) if not a["dia"]["cerrado"] and (
        not a["dia"].get("ultima") or ahora - datetime.fromisoformat(a["dia"]["ultima"]) >= timedelta(minutes=espera))]
    if not toca:
        if saltos or hay_cambios or len([a for a in estado["agentes"] if a.get("dia", {}).get("cerrado")]) > cerrados_antes:
            return _guardar(estado, "Sin ronda, pero saltaron stops, objetivos o el límite de pérdida diaria.", precios)
        return "Sin ronda: aún no ha pasado una hora desde la anterior."

    datos_noticias = noticias.actualizar(mercado, ia_noticias or ia, ahora)
    desde_hora = (ahora - timedelta(hours=4)).isoformat()[:19]
    recientes = [n for n in datos_noticias["noticias"] if not n.get("pendiente") and n["fecha"] >= desde_hora]
    noticias.ordenar_por_importancia(recientes)
    tono = noticias.sentimiento_por_valor(datos_noticias, ahora, horas=12)
    sentimiento = noticias.texto_sentimiento(tono)
    inf = estado.get("analistas")  # el de esta mañana o, antes de que lo escriban, el del día anterior
    tabla = prompts.tabla_intradia({s: datos[s] for s in config.UNIVERSO if s in datos})
    ranking = ciclo.clasificacion(estado, precios, fecha, "intradia")
    oficina_reglas = aprendizaje.texto_reglas_oficina(estado, {**estado.get("precios", {}), **precios}, fecha)
    resultados = fundamentales.texto_resultados_cercanos(fundamentales.cargar(), fecha)
    aviso = texto_eventos(ahora, estado)
    comun = "\n\n".join(t for t in (aviso, analistas.texto(inf), oficina.texto_manual(estado), oficina_reglas, resultados) if t)
    for a in toca:
        ronda(a, fecha, hora, hasta, precios, tabla, recientes[:25], ranking, comun, sentimiento,
              mercado, ia, datos_noticias, ahora, senales=candidatos(a.get("plantilla", ""), datos, tono))
    noticias.guardar(datos_noticias)
    return _guardar(estado, f"Ronda intradía de las {hora} ({len(toca)} agentes).", precios)


def revisar_stops_diario(estado: dict, precios: dict[str, float], fecha: str, hora: str, mercado,
                         ahora: datetime | None = None) -> int:
    """Los stops del equipo diario, en cada ronda del intradía (sin gastar IA ni ejecuciones nuevas)."""
    saltos = 0
    for a in estado["agentes"]:
        if a["estado"] != "activo" or a.get("equipo", "diario") != "diario":
            continue
        cartera.poner_stops_por_defecto(a)
        hechas = cartera.revisar_stops(a, precios)
        if not hechas:
            continue
        saltos += len(hechas)
        emociones.por_ventas_automaticas(a, hechas, ahora or datetime.now(timezone.utc))
        ciclo.espejo(mercado, hechas, fecha)
        ciclo.anotar_operaciones(fecha, hechas)
        aprendizaje.seguir_operaciones(a, fecha, hechas, precios)
        texto = " ".join(f"Vendo {o['simbolo']}: {o['motivo']}" for o in hechas)
        ciclo.anotar_diario(a, f"{fecha} {hora}", f"**Stop automático.** {texto}")
        ciclo.anotar_bitacora(a, fecha, texto, tipo="stop", operaciones=hechas,
                              valor=round(cartera.valor(a, precios), 2))
    return saltos


def antes_de_resultados(estado: dict, precios: dict[str, float], fecha: str, hora: str, minutos_cierre: int,
                        mercado, ia, ahora: datetime | None = None) -> bool:
    """Media hora antes del cierre, cada agente diario con acciones que presentan resultados esta noche decide si
    las mantiene, las reduce o las vende (una llamada a la IA por agente, solo esos días). Una vez al día."""
    if not config.MINUTOS_ULTIMA_DECISION_RESULTADOS < minutos_cierre <= config.MINUTOS_ANTES_RESULTADOS \
            or estado.get("antes_resultados") == fecha:
        return False
    estado["antes_resultados"] = fecha
    presentan = fundamentales.presentan_esta_noche(fundamentales.cargar(), fecha)
    afectados = [(a, {s: presentan[s] for s in a["posiciones"] if s in presentan}) for a in estado["agentes"]
                 if a["estado"] == "activo" and a.get("equipo", "diario") == "diario"]
    afectados = [(a, suyas) for a, suyas in afectados if suyas]
    if not afectados:
        return True
    desde = ((ahora or datetime.now(timezone.utc)) - timedelta(hours=36)).isoformat()[:19]
    todas = [n for n in noticias.cargar().get("noticias", []) if n["fecha"] >= desde]
    for agente, suyas in afectados:
        v = cartera.valor(agente, precios)
        suyas_noticias = [n for n in todas if set(suyas) & set(n.get("empresas") or n.get("simbolos") or [])][-10:]
        try:
            r = ia.pedir_json(prompts.INSTRUCCIONES_ANTES_RESULTADOS.format(alias=agente.get("alias", ""),
                                                                             personalidad=agente.get("personalidad", "")),
                              prompts.mensaje_antes_resultados(agente, v, precios, suyas, suyas_noticias),
                              esquema=prompts.esquema_antes_resultados(list(suyas)))
        except Exception as e:
            print(f"Aviso: {agente['id']} no pudo decidir antes de los resultados: {e}")
            continue
        hechas, notas = [], []
        for d in r.get("decisiones") or []:
            s, accion, motivo = d.get("simbolo"), d.get("accion"), str(d.get("motivo", "")).strip()
            if s not in suyas or s not in agente["posiciones"] or not precios.get(s):
                continue
            notas.append(f"{s} ({suyas[s]}): {accion}. {motivo}")
            parte = {"vender": 1.0, "reducir": min(max(float(d.get("porcentaje") or 50), 0), 100) / 100}.get(accion, 0)
            if parte > 0:
                op = cartera.vender(agente, s, agente["posiciones"][s]["cantidad"] * parte, precios[s])
                hechas.append({**op, "motivo": f"Antes de sus resultados ({suyas[s]}): {motivo}"})
        if hechas:
            ciclo.espejo(mercado, hechas, fecha)
            ciclo.anotar_operaciones(fecha, hechas)
            aprendizaje.seguir_operaciones(agente, fecha, hechas, precios)
        if notas:
            ciclo.anotar_diario(agente, f"{fecha} {hora}", "**Antes de los resultados.** " + " ".join(notas))
            ciclo.anotar_bitacora(agente, fecha, "Antes de los resultados: " + " ".join(notas), tipo="resultados",
                                  operaciones=hechas, valor=round(cartera.valor(agente, precios), 2))
    return True


def _guardar(estado: dict, mensaje: str, precios: dict[str, float] | None = None) -> str:
    estado["precios"] = {**estado.get("precios", {}), **{s: round(p, 4) for s, p in (precios or {}).items()}}
    if precios:
        estado["precios_hora"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    ciclo.guardar_estado(estado)
    web.generar(estado)
    return mensaje


def actualizar_precios(mercado, ahora: datetime | None = None) -> str:
    """Pone al día el valor de todos los agentes con los últimos precios, sin usar la IA.
    Funciona con la bolsa abierta y hasta media hora después del cierre, para recoger el precio de cierre."""
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if mercado.sesion(ahora) is None:
        cierre = mercado.cierre_de_hoy(ahora)
        if not cierre or not timedelta(0) <= ahora - cierre <= timedelta(minutes=30):
            return "Precios: la bolsa está cerrada."
    estado = ciclo.cargar_estado()
    activos = [a for a in estado["agentes"] if a["estado"] == "activo"]
    simbolos = sorted(set(config.UNIVERSO) | {config.REFERENCIA} | {s for a in activos for s in a["posiciones"]})
    precios = mercado.ultimos_precios(simbolos)
    if not precios:
        return "Precios: Alpaca no devolvió nada."
    seguimiento.registrar(estado, mercado, {**estado.get("precios", {}), **precios}, ahora)
    return _guardar(estado, f"Precios actualizados ({len(precios)} valores).", precios)


def main() -> None:
    from agentes.alpaca import Alpaca
    from agentes.avisos import GitHub
    from agentes.ia import crear_ia

    # El intradía usa el modelo ligero: decide unas 6 veces al día y la cuota gratuita del normal es pequeña
    ligera = crear_ia(ligera=True)
    mercado = Alpaca()
    print(ejecutar(mercado, ligera, GitHub(), ia_noticias=ligera))
    # Cada ejecución, haya ronda o no, deja al día el valor de todos los agentes (también los diarios)
    print(actualizar_precios(mercado))
    # Una hora después de un evento con la bolsa abierta, el equipo diario decide una vez más
    estado = ciclo.cargar_estado()
    ahora = datetime.now(timezone.utc)
    if (evento := evento_para_el_diario(ahora, estado)):
        estado["evento_diario"] = evento[0].isoformat()  # una sola vez, aunque falle
        ciclo.guardar_estado(estado)
        diarios = {a["id"] for a in estado["agentes"] if a["estado"] == "activo" and a.get("equipo", "diario") == "diario"}
        print(ciclo.ejecutar(mercado, crear_ia(prisa=True), GitHub(), ahora=ahora, ia_noticias=ligera, solo=diarios,
                             evento=f"{evento[1]} a las {evento[0]:%H:%M} de Nueva York"))


if __name__ == "__main__":
    main()
