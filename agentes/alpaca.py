"""Cliente mínimo de Alpaca: reloj del mercado, precios, noticias y órdenes de práctica."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from agentes import config

NUEVA_YORK = ZoneInfo("America/New_York")
TRADING_URL = "https://paper-api.alpaca.markets"  # siempre la cuenta de práctica
DATOS_URL = "https://data.alpaca.markets"


class Alpaca:
    def __init__(self, clave: str | None = None, secreto: str | None = None):
        self.http = requests.Session()
        self.http.headers.update({
            "APCA-API-KEY-ID": clave or config.secreto("ALPACA_API_KEY"),
            "APCA-API-SECRET-KEY": secreto or config.secreto("ALPACA_SECRET_KEY"),
        })

    def _get(self, url: str, params: dict | None = None) -> dict | list:
        r = self.http.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # --- Mercado -----------------------------------------------------------
    def minutos_desde_apertura(self, ahora: datetime | None = None) -> int | None:
        """Minutos desde la apertura de hoy, o None si hoy no abre o ya cerró."""
        reloj = self._get(f"{TRADING_URL}/v2/clock")
        if not reloj["is_open"]:
            return None
        ahora = ahora or datetime.now(NUEVA_YORK)
        hoy = ahora.astimezone(NUEVA_YORK).date().isoformat()
        dias = self._get(f"{TRADING_URL}/v2/calendar", {"start": hoy, "end": hoy})
        if not dias:
            return None
        h, m = (int(x) for x in dias[0]["open"].split(":"))
        apertura = datetime.fromisoformat(hoy).replace(hour=h, minute=m, tzinfo=NUEVA_YORK)
        return int((ahora - apertura).total_seconds() // 60)

    def cierre_de_hoy(self, ahora: datetime | None = None) -> datetime | None:
        """Hora de cierre de hoy (contando los días de cierre anticipado), o None si hoy no abre."""
        hoy = (ahora or datetime.now(NUEVA_YORK)).astimezone(NUEVA_YORK).date().isoformat()
        dias = self._get(f"{TRADING_URL}/v2/calendar", {"start": hoy, "end": hoy})
        if not dias:
            return None
        h, m = (int(x) for x in dias[0]["close"].split(":"))
        return datetime.fromisoformat(hoy).replace(hour=h, minute=m, tzinfo=NUEVA_YORK)

    def sesion(self, ahora: datetime | None = None) -> tuple[int, int] | None:
        """(minutos desde la apertura, minutos hasta el cierre) si la bolsa está abierta; si no, None."""
        reloj = self._get(f"{TRADING_URL}/v2/clock")
        if not reloj["is_open"]:
            return None
        ahora = ahora or datetime.now(NUEVA_YORK)
        desde = self.minutos_desde_apertura(ahora)
        hasta = int((datetime.fromisoformat(reloj["next_close"]) - ahora).total_seconds() // 60)
        return (desde, hasta) if desde is not None else None

    def precios_del_dia(self, simbolos: list[str], ahora: datetime | None = None) -> dict[str, dict]:
        """Para el equipo intradía: precio, apertura, máximo, mínimo, cierre de ayer, precio de hace una hora
        y el rango de la primera media hora."""
        fotos = self._get(f"{DATOS_URL}/v2/stocks/snapshots", {"symbols": ",".join(simbolos), "feed": "iex"})
        fotos = fotos.get("snapshots", fotos)
        ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
        apertura = ahora.astimezone(NUEVA_YORK).replace(hour=9, minute=30, second=0, microsecond=0)
        params = {"symbols": ",".join(simbolos), "timeframe": "15Min", "start": apertura.astimezone(timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%SZ"), "feed": "iex", "limit": 10000}
        barras: dict[str, list[dict]] = {}
        while True:
            datos = self._get(f"{DATOS_URL}/v2/stocks/bars", params)
            for s, lista in (datos.get("bars") or {}).items():
                barras.setdefault(s, []).extend(lista)
            if not datos.get("next_page_token"):
                break
            params["page_token"] = datos["next_page_token"]
        hace_1h = (ahora - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resultado = {}
        for s in simbolos:
            f = fotos.get(s) or {}
            precio = (f.get("latestTrade") or {}).get("p")
            if not precio:
                continue
            dia, ayer = f.get("dailyBar") or {}, f.get("prevDailyBar") or {}
            b = barras.get(s, [])
            antes = [x for x in b if x["t"] <= hace_1h]
            minutos = max(1.0, (ahora - apertura.astimezone(timezone.utc)).total_seconds() / 60)
            vol_rel = (dia.get("v") / (ayer["v"] * min(1.0, minutos / 390))) if dia.get("v") and ayer.get("v") else None
            resultado[s] = {"precio": float(precio), "apertura": dia.get("o"), "maximo": dia.get("h"),
                            "vwap": dia.get("vw"), "vol_relativo": vol_rel,
                            "minimo": dia.get("l"), "cierre_ayer": ayer.get("c"),
                            "hace_1h": antes[-1]["c"] if antes else (b[0]["o"] if b else None),
                            "rango_alto": max(x["h"] for x in b[:2]) if len(b) >= 2 else None,
                            "rango_bajo": min(x["l"] for x in b[:2]) if len(b) >= 2 else None}
        return resultado

    def ultimos_precios(self, simbolos: list[str]) -> dict[str, float]:
        datos = self._get(f"{DATOS_URL}/v2/stocks/trades/latest",
                          {"symbols": ",".join(simbolos), "feed": "iex"})
        return {s: float(t["p"]) for s, t in datos.get("trades", {}).items()}

    def barras_minuto(self, simbolos: list[str], desde: datetime, hasta: datetime) -> dict[str, list[tuple[str, float]]]:
        """Precio de cierre de cada minuto entre `desde` y `hasta`: {símbolo: [(hora UTC ISO, cierre), ...]}."""
        params = {"symbols": ",".join(simbolos), "timeframe": "1Min", "feed": "iex", "limit": 10000,
                  "start": desde.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end": hasta.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        barras: dict[str, list[tuple[str, float]]] = {}
        while True:
            datos = self._get(f"{DATOS_URL}/v2/stocks/bars", params)
            for s, lista in (datos.get("bars") or {}).items():
                barras.setdefault(s, []).extend((b["t"], float(b["c"])) for b in lista)
            if not datos.get("next_page_token"):
                return barras
            params["page_token"] = datos["next_page_token"]

    def cierres_diarios(self, simbolos: list[str], dias: int = 100) -> dict[str, list[float]]:
        """Precios de cierre diarios (del más antiguo al más reciente) de cada símbolo."""
        inicio = (datetime.now(timezone.utc) - timedelta(days=dias)).date().isoformat()
        params = {"symbols": ",".join(simbolos), "timeframe": "1Day", "start": inicio,
                  "adjustment": "all", "feed": "iex", "limit": 10000}
        cierres: dict[str, list[float]] = {s: [] for s in simbolos}
        while True:
            datos = self._get(f"{DATOS_URL}/v2/stocks/bars", params)
            for s, barras in (datos.get("bars") or {}).items():
                cierres.setdefault(s, []).extend(float(b["c"]) for b in barras)
            if not datos.get("next_page_token"):
                return cierres
            params["page_token"] = datos["next_page_token"]

    def cierres_por_fecha(self, simbolos: list[str], desde: str) -> dict[str, dict[str, float]]:
        """Cierre de cada día desde `desde` (AAAA-MM-DD): {símbolo: {fecha: cierre}}."""
        params = {"symbols": ",".join(simbolos), "timeframe": "1Day", "start": desde, "feed": "iex", "limit": 10000}
        cierres: dict[str, dict[str, float]] = {}
        while True:
            datos = self._get(f"{DATOS_URL}/v2/stocks/bars", params)
            for s, barras in (datos.get("bars") or {}).items():
                cierres.setdefault(s, {}).update({datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NUEVA_YORK).date().isoformat(): float(b["c"]) for b in barras})
            if not datos.get("next_page_token"):
                return cierres
            params["page_token"] = datos["next_page_token"]

    def liquidez(self, simbolos: list[str], dias: int = 30) -> dict[str, tuple[float, float]]:
        """Último cierre y negociado medio diario en dólares (en IEX) de cada símbolo: {símbolo: (precio, usd)}."""
        inicio = (datetime.now(timezone.utc) - timedelta(days=dias)).date().isoformat()
        params = {"symbols": ",".join(simbolos), "timeframe": "1Day", "start": inicio, "feed": "iex", "limit": 10000}
        barras: dict[str, list[dict]] = {}
        while True:
            datos = self._get(f"{DATOS_URL}/v2/stocks/bars", params)
            for s, lista in (datos.get("bars") or {}).items():
                barras.setdefault(s, []).extend(lista)
            if not datos.get("next_page_token"):
                break
            params["page_token"] = datos["next_page_token"]
        return {s: (float(b[-1]["c"]), sum(x["c"] * x["v"] for x in b) / len(b)) for s, b in barras.items() if b}

    def noticias(self, desde: datetime, maximo: int = 200) -> list[dict]:
        """Todas las noticias del mercado de EE. UU. desde `desde` (de la más reciente a la más antigua)."""
        params = {"start": desde.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "limit": 50, "sort": "desc", "include_content": "false"}
        resultado: list[dict] = []
        while len(resultado) < maximo:
            datos = self._get(f"{DATOS_URL}/v1beta1/news", params)
            for n in datos.get("news", []):
                resultado.append({
                    "id": str(n.get("id")), "fecha": n.get("created_at", ""), "fuente": n.get("source", ""),
                    "url": n.get("url", ""), "titular": n.get("headline", ""),
                    "resumen": (n.get("summary") or "")[:400], "simbolos": n.get("symbols", [])[:8],
                })
            if not datos.get("next_page_token"):
                break
            params["page_token"] = datos["next_page_token"]
        return resultado[:maximo]

    # --- Órdenes en la cuenta de práctica (espejo de los libros de cada agente) ----
    def orden(self, simbolo: str, lado: str, cantidad: float, etiqueta: str) -> None:
        r = self.http.post(f"{TRADING_URL}/v2/orders", json={
            "symbol": simbolo, "qty": f"{cantidad:.6f}", "side": lado, "type": "market",
            "time_in_force": "day", "client_order_id": etiqueta[:48],
        }, timeout=30)
        r.raise_for_status()

    def limpiar_cuenta(self) -> None:
        """Cancela órdenes pendientes y cierra posiciones de la cuenta de práctica (al reiniciar el experimento)."""
        self.http.delete(f"{TRADING_URL}/v2/orders", timeout=30).raise_for_status()
        self.http.delete(f"{TRADING_URL}/v2/positions", params={"cancel_orders": "true"}, timeout=30).raise_for_status()


def fecha_nueva_york() -> date:
    return datetime.now(NUEVA_YORK).date()
