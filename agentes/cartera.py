"""Libro de cada agente: efectivo, posiciones y reglas de riesgo que ninguno puede saltarse."""

from __future__ import annotations

from agentes import config


def nuevo_agente(ident: str, nombre: str, estrategia: str, fecha: str, precio_referencia: float,
                 origen: str = "inicial", alias: str = "", personalidad: str = "",
                 equipo: str = "diario", plantilla: str = "", fuente: str = "") -> dict:
    return {
        "id": ident,
        "nombre": nombre,                 # nombre de la estrategia
        "alias": alias or nombre,         # nombre propio del agente
        "personalidad": personalidad,
        "equipo": equipo,                 # "diario" o "intradia"
        "plantilla": plantilla,           # archivo de estrategias/ del que salió, si salió de uno
        "fuente": fuente,                 # datos externos que copia: "berkshire", "congreso" o ""
        "estrategia": estrategia,
        "origen": origen,
        "estado": "activo",
        "creado": fecha,
        "efectivo": config.CAPITAL_INICIAL,
        "posiciones": {},                 # simbolo -> {"cantidad": float, "coste_medio": float}
        "valor_maximo": config.CAPITAL_INICIAL,
        "periodo": {"inicio": fecha, "valor": config.CAPITAL_INICIAL, "referencia": precio_referencia},
        "historial": [],                  # [{"fecha", "valor", "referencia"}]
        "periodos_superados": 0,
        "referencia_inicial": precio_referencia,   # S&P 500 el día que empezó (para la graduación)
    }


def valor(agente: dict, precios: dict[str, float]) -> float:
    return agente["efectivo"] + sum(p["cantidad"] * precios.get(s, p["coste_medio"])
                                    for s, p in agente["posiciones"].items())


def _operacion(agente: dict, lado: str, simbolo: str, cantidad: float, precio: float) -> dict:
    return {"agente": agente["id"], "lado": lado, "simbolo": simbolo,
            "cantidad": round(cantidad, 6), "precio": round(precio, 4),
            "importe": round(cantidad * precio, 2)}


def vender(agente: dict, simbolo: str, cantidad: float, precio: float) -> dict:
    pos = agente["posiciones"][simbolo]
    cantidad = min(cantidad, pos["cantidad"])
    precio_neto = precio * (1 - config.DESLIZAMIENTO)
    agente["efectivo"] += cantidad * precio_neto
    pos["cantidad"] -= cantidad
    if pos["cantidad"] < 1e-6:
        del agente["posiciones"][simbolo]
    return _operacion(agente, "vender", simbolo, cantidad, precio_neto)


def comprar(agente: dict, simbolo: str, importe: float, precio: float, stop: float | None = None,
            objetivo: float | None = None) -> dict:
    precio_neto = precio * (1 + config.DESLIZAMIENTO)
    cantidad = importe / precio_neto
    pos = agente["posiciones"].setdefault(simbolo, {"cantidad": 0.0, "coste_medio": 0.0})
    total = pos["cantidad"] + cantidad
    pos["coste_medio"] = (pos["cantidad"] * pos["coste_medio"] + cantidad * precio_neto) / total
    pos["cantidad"] = total
    if stop:
        pos["stop"] = round(stop, 2)
        if objetivo:
            pos["objetivo"] = round(objetivo, 2)
        else:
            pos.pop("objetivo", None)
    agente["efectivo"] -= importe
    return _operacion(agente, "comprar", simbolo, cantidad, precio_neto)


def ejecutar_ordenes(agente: dict, ordenes: list[dict], precios: dict[str, float],
                     permitidos: set[str] | None = None, puede_comprar: bool = True,
                     exigir_stop: bool = False, limite_peso: dict[str, float] | None = None,
                     stop_diario: bool = False) -> tuple[list[dict], list[str]]:
    """El control de riesgo revisa cada orden y ejecuta lo permitido. Devuelve (operaciones, rechazos).

    `permitidos` amplía la lista de valores (el Copión y la del Congreso pueden comprar lo que copian);
    `puede_comprar=False` solo deja vender (el intradía en la última hora);
    `limite_peso` baja el peso máximo de algunos valores (los del radar de oportunidades, un 5 %);
    `stop_diario` pone a cada compra del equipo diario su stop ("stop_pct", entre un 4 % y un 15 %; un 8 % si no lo dice)."""
    limite_peso = limite_peso or {}
    hechas, rechazos = [], []
    permitidos = set(config.UNIVERSO) | (permitidos or set())
    if len(ordenes) > config.MAX_ORDENES_DIA:
        rechazos.append(f"Demasiadas órdenes ({len(ordenes)}): solo se atienden las {config.MAX_ORDENES_DIA} primeras.")
        ordenes = ordenes[: config.MAX_ORDENES_DIA]
    ventas = [o for o in ordenes if str(o.get("accion", "")).lower() == "vender"]
    compras = [o for o in ordenes if str(o.get("accion", "")).lower() == "comprar"]
    otras = [o for o in ordenes if o not in ventas and o not in compras]
    rechazos += [f"Acción desconocida: {o}" for o in otras]

    for o in ventas:  # primero ventas, para liberar efectivo
        simbolo = str(o.get("simbolo", "")).upper()
        if simbolo not in agente["posiciones"]:
            rechazos.append(f"Ajuste automático: venta de {simbolo} ignorada (no lo tiene y no se permite apostar a la baja).")
            continue
        if simbolo not in precios:
            rechazos.append(f"Sin precio para {simbolo}.")
            continue
        pos = agente["posiciones"][simbolo]
        if o.get("todo") or o.get("importe_usd") is None:
            cantidad = pos["cantidad"]
        else:
            cantidad = float(o["importe_usd"]) / precios[simbolo]
        hechas.append({**vender(agente, simbolo, cantidad, precios[simbolo]), "motivo": str(o.get("motivo", ""))})

    if compras and not puede_comprar:
        rechazos += [f"Compra de {str(o.get('simbolo', '')).upper()} rechazada: a esta hora ya no se abren posiciones."
                     for o in compras]
        compras = []
    presupuesto = config.MAX_COMPRAS_DIA * valor(agente, precios)
    if exigir_stop:
        compras = [_completar_stop(o, precios, rechazos) for o in compras]
    compras = _ajustar_compras(agente, compras, precios, permitidos, limite_peso, presupuesto, exigir_stop, rechazos)
    for o in compras:
        simbolo = str(o.get("simbolo", "")).upper()
        importe = o["importe_usd"]
        total = valor(agente, precios)
        ya_invertido = agente["posiciones"].get(simbolo, {}).get("cantidad", 0) * precios[simbolo]
        peso = limite_peso.get(simbolo, config.MAX_PESO_POSICION)
        limite = peso * total - ya_invertido
        permitido = min(importe, limite, agente["efectivo"], presupuesto)
        # Qué límite manda, para que el agente (y quien lo lea en la web) sepa por qué
        if permitido == presupuesto:
            motivo = (f"ya ha gastado el máximo de compras por decisión ({config.MAX_COMPRAS_DIA:.0%} de la cartera, "
                      f"le quedaban {max(presupuesto, 0):.0f} $)")
        elif permitido == agente["efectivo"]:
            motivo = f"solo le quedan {agente['efectivo']:.0f} $ de efectivo"
        else:
            motivo = f"no puede tener más del {peso:.0%} de la cartera en un mismo valor"
        if permitido < config.MINIMO_ORDEN_USD:
            rechazos.append(f"Compra de {simbolo} por {importe:.0f} $ rechazada: {motivo}.")
            continue
        if permitido < importe - 1:
            rechazos.append(f"Compra de {simbolo} recortada de {importe:.0f} $ a {permitido:.0f} $: {motivo}.")
        stop = objetivo = None
        if stop_diario:
            stop = precios[simbolo] * (1 - porcentaje_stop(o.get("stop_pct")))
        if exigir_stop:  # como el gestor de riesgo de day-trader-skill: sin stop no hay compra
            precio = precios[simbolo]
            try:
                stop, objetivo = float(o.get("stop") or 0), float(o.get("objetivo") or 0)
            except (TypeError, ValueError):
                stop = objetivo = 0
            if not 0 < stop < precio * 0.9995:
                rechazos.append(f"Compra de {simbolo} rechazada: necesita un stop (precio de salida si se equivoca) "
                                f"por debajo del precio actual ({precio:.2f} $).")
                continue
            if objetivo - precio < config.MIN_BENEFICIO_RIESGO * (precio - stop):
                rechazos.append(f"Compra de {simbolo} rechazada: el objetivo ({objetivo:.2f} $) debe estar al menos "
                                f"tan lejos del precio como el stop ({stop:.2f} $).")
                continue
            maximo = config.MAX_RIESGO_OPERACION * total / (precio - stop) * precio
            if permitido > maximo:
                if maximo < config.MINIMO_ORDEN_USD:
                    rechazos.append(f"Compra de {simbolo} rechazada: con ese stop arriesgaría más del "
                                    f"{config.MAX_RIESGO_OPERACION:.0%} de la cartera.")
                    continue
                rechazos.append(f"Compra de {simbolo} recortada a {maximo:.0f} $ para no arriesgar más del "
                                f"{config.MAX_RIESGO_OPERACION:.0%} de la cartera hasta el stop.")
                permitido = maximo
        presupuesto -= permitido
        hechas.append({**comprar(agente, simbolo, permitido, precios[simbolo], stop, objetivo),
                       "motivo": str(o.get("motivo", ""))})
    return hechas, rechazos


def _completar_stop(o: dict, precios: dict[str, float], rechazos: list[str]) -> dict:
    """Intradía: si la compra llega sin un stop válido se le pone el de por defecto, y si el objetivo queda más
    cerca que el stop se aleja lo justo. Así no se pierde una compra que le interesa por un dato mal puesto."""
    simbolo = str(o.get("simbolo", "")).upper()
    if not (precio := precios.get(simbolo)):
        return o
    try:
        stop, objetivo = float(o.get("stop") or 0), float(o.get("objetivo") or 0)
    except (TypeError, ValueError):
        stop = objetivo = 0.0
    cambios = []
    if not 0 < stop < precio * 0.9995:
        stop = round(precio * (1 - config.STOP_INTRADIA_DEFECTO), 2)
        cambios.append(f"stop en {stop:.2f} $, un {config.STOP_INTRADIA_DEFECTO:.1%} por debajo, porque el suyo no era válido")
    minimo = precio + config.MIN_BENEFICIO_RIESGO * (precio - stop)
    if objetivo < minimo - 0.005:
        objetivo = round(minimo + 0.005, 2)
        cambios.append(f"objetivo en {objetivo:.2f} $, para que esté al menos tan lejos como el stop")
    if not cambios:
        return o
    rechazos.append(f"Ajuste automático: compra de {simbolo} con {' y '.join(cambios)}.")
    return {**o, "stop": stop, "objetivo": objetivo}


def _riesgo_maximo(o: dict, precio: float, total: float) -> float | None:
    """Importe con el que lo que se pierde hasta el stop es el máximo permitido (solo intradía)."""
    try:
        stop = float(o.get("stop") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 < stop < precio:
        return None
    return config.MAX_RIESGO_OPERACION * total / (precio - stop) * precio


def _ajustar_compras(agente: dict, compras: list[dict], precios: dict[str, float], permitidos: set[str],
                     limite_peso: dict[str, float], presupuesto: float, exigir_stop: bool,
                     rechazos: list[str]) -> list[dict]:
    """Encaja las compras en los límites antes de ejecutarlas, sin dejar ninguna fuera: cada una baja hasta lo que
    cabe en su valor (y por su stop) y, si entre todas pasan del máximo por decisión o del efectivo, se reparten el
    dinero en proporción a lo que pedía cada una. Así lo que le interesa al agente se compra, aunque sea menos."""
    total = valor(agente, precios)
    validas = []
    for o in compras:
        simbolo = str(o.get("simbolo", "")).upper()
        if simbolo not in permitidos:
            rechazos.append(f"{simbolo} no está en la lista de valores permitidos.")
            continue
        if simbolo not in precios:
            rechazos.append(f"Sin precio para {simbolo}.")
            continue
        try:
            pedido = float(o.get("importe_usd", 0))
        except (TypeError, ValueError):
            rechazos.append(f"Importe no válido en {o}.")
            continue
        ya = agente["posiciones"].get(simbolo, {}).get("cantidad", 0) * precios[simbolo]
        peso = limite_peso.get(simbolo, config.MAX_PESO_POSICION)
        cabe, motivo = peso * total - ya, f"en un mismo valor caben como mucho {peso * total:.0f} $ ({peso:.0%})"
        if exigir_stop and (riesgo := _riesgo_maximo(o, precios[simbolo], total)) is not None and riesgo < cabe:
            cabe, motivo = riesgo, f"con ese stop, lo que perdería no puede pasar del {config.MAX_RIESGO_OPERACION:.0%} de la cartera"
        importe = max(0.0, min(pedido, cabe))
        validas.append([o, simbolo, pedido, importe, motivo if importe < pedido - 1 else ""])
    disponible = min(presupuesto, agente["efectivo"])
    suma = sum(v[3] for v in validas)
    if suma > disponible > 0:
        factor = disponible / suma
        for v in validas:
            v[3] *= factor
            v[4] = v[4] or (f"entre todas las compras pasaban de {disponible:.0f} $ (el máximo por decisión o el "
                            f"efectivo), así que se reparte en proporción")
    elif disponible <= 0:
        for v in validas:
            v[3], v[4] = 0.0, "no queda dinero para comprar en esta decisión"
    finales = []
    for o, simbolo, pedido, importe, motivo in validas:
        if importe < config.MINIMO_ORDEN_USD:
            rechazos.append(f"Compra de {simbolo} rechazada: {motivo or 'el importe es demasiado pequeño'}.")
            continue
        if motivo:
            rechazos.append(f"Ajuste automático: compra de {simbolo} de {pedido:.0f} $ a {importe:.0f} $ ({motivo}).")
        finales.append({**o, "importe_usd": importe})
    return finales


def porcentaje_stop(valor) -> float:
    """El "stop_pct" que pide el agente (8 = un 8 %), dentro de los límites; si no dice nada, el de por defecto."""
    try:
        pct = float(valor) / 100
    except (TypeError, ValueError):
        return config.STOP_DIARIO_DEFECTO
    return min(config.STOP_DIARIO_MAX, max(config.STOP_DIARIO_MIN, pct)) if pct > 0 else config.STOP_DIARIO_DEFECTO


def poner_stops_por_defecto(agente: dict) -> None:
    """Las posiciones sin stop reciben el de por defecto, contado desde su precio de compra."""
    for p in agente["posiciones"].values():
        if not p.get("stop"):
            p["stop"] = round(p["coste_medio"] * (1 - config.STOP_DIARIO_DEFECTO), 2)


def revisar_stops(agente: dict, precios: dict[str, float]) -> list[dict]:
    """Vende las posiciones que han tocado su stop o su objetivo."""
    hechas = []
    for s, p in list(agente["posiciones"].items()):
        precio = precios.get(s)
        if not precio or not p.get("stop"):
            continue
        if precio <= p["stop"]:
            motivo = f"Stop alcanzado ({precio:.2f} $ ≤ {p['stop']:.2f} $): se corta la pérdida."
        elif p.get("objetivo") and precio >= p["objetivo"]:
            motivo = f"Objetivo alcanzado ({precio:.2f} $ ≥ {p['objetivo']:.2f} $): se recoge el beneficio."
        else:
            continue
        hechas.append({**vender(agente, s, p["cantidad"], precio), "motivo": motivo})
    return hechas


def cerrar_todo(agente: dict, precios: dict[str, float]) -> list[dict]:
    return [vender(agente, s, p["cantidad"], precios.get(s, p["coste_medio"]))
            for s, p in list(agente["posiciones"].items())]
