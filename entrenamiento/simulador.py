"""Entrenamiento: el equipo diario vive otra vez días pasados, con los mismos programas que la oficina de verdad.

Cada día de bolsa se congela el reloj a las 11:00 de Nueva York (la hora de la decisión diaria) y el mercado solo
enseña lo que se sabía a esa hora: precios hasta ese minuto, cierres hasta ayer y noticias hasta ese momento. Luego
se repasan los stops a lo largo de la tarde. No se manda ninguna orden a Alpaca y no se despide a nadie: el objetivo
es que aprendan (reglas, memoria, reflexiones), no la clasificación.

El progreso se guarda al acabar cada día. Si la IA se queda sin cuota del día, el día a medias se deshace y la
siguiente ejecución sigue por donde iba.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from freezegun import freeze_time

from agentes import alpaca, config, ia as ia_modulo

NUEVA_YORK = ZoneInfo("America/New_York")
INICIO = "2026-08-03"
FIN = "2026-10-29"
HORA_DECISION = (11, 0)
HORAS_STOPS = [(12, 0), (13, 0), (14, 0), (15, 0), (15, 50)]
PROGRESO = config.DATOS / "progreso.json"
COPIA = config.RAIZ / ".dia_en_curso"


class SinCuota(BaseException):
    """La IA no tiene más cuota hoy. Hereda de BaseException para que no la trague ningún `except Exception`."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class MercadoHistorico(alpaca.Alpaca):
    """Alpaca, pero mirando al pasado: nada de después del reloj congelado, y sin órdenes."""

    def _get(self, url: str, params: dict | None = None) -> dict | list:
        params = dict(params or {})
        if "/v2/stocks/bars" in url and "end" not in params:
            if params.get("timeframe") == "1Day":  # la barra de hoy entera sería mirar el futuro
                params["end"] = (_ahora().astimezone(NUEVA_YORK).date() - timedelta(days=1)).isoformat()
            else:
                params["end"] = _ahora().strftime("%Y-%m-%dT%H:%M:%SZ")
        if "/v1beta1/news" in url and "end" not in params:
            params["end"] = _ahora().strftime("%Y-%m-%dT%H:%M:%SZ")
        return super()._get(url, params)

    def minutos_desde_apertura(self, ahora=None):
        a = (ahora or _ahora()).astimezone(NUEVA_YORK)
        return int((a - a.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() // 60)

    def cierre_de_hoy(self, ahora=None):
        return (ahora or _ahora()).astimezone(NUEVA_YORK).replace(hour=16, minute=0, second=0, microsecond=0)

    def sesion(self, ahora=None):
        desde = self.minutos_desde_apertura(ahora)
        return desde, 390 - desde

    def _barras_de_hoy(self, simbolos: list[str]) -> dict[str, list[dict]]:
        apertura = _ahora().astimezone(NUEVA_YORK).replace(hour=9, minute=30, second=0, microsecond=0)
        params = {"symbols": ",".join(simbolos), "timeframe": "5Min", "feed": "iex", "limit": 10000,
                  "start": apertura.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        barras: dict[str, list[dict]] = {}
        while True:
            datos = self._get(f"{alpaca.DATOS_URL}/v2/stocks/bars", params)
            for s, lista in (datos.get("bars") or {}).items():
                barras.setdefault(s, []).extend(lista)
            if not datos.get("next_page_token"):
                return barras
            params["page_token"] = datos["next_page_token"]

    def ultimos_precios(self, simbolos: list[str]) -> dict[str, float]:
        hoy = self._barras_de_hoy(simbolos)
        precios = {s: float(b[-1]["c"]) for s, b in hoy.items() if b}
        falta = [s for s in simbolos if s not in precios]
        if falta:  # sin operaciones aún hoy en IEX: el último cierre
            precios.update({s: c[-1] for s, c in self.cierres_diarios(falta, 10).items() if c})
        return precios

    def precios_del_dia(self, simbolos: list[str], ahora=None) -> dict[str, dict]:
        hoy = self._barras_de_hoy(simbolos)
        ayer = self.cierres_diarios(simbolos, 10)
        hace_1h = (_ahora() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resultado = {}
        for s in simbolos:
            b = hoy.get(s) or []
            if not b:
                continue
            antes = [x for x in b if x["t"] <= hace_1h]
            vol = sum(x["v"] for x in b) or 0
            resultado[s] = {"precio": float(b[-1]["c"]), "apertura": b[0]["o"], "maximo": max(x["h"] for x in b),
                            "minimo": min(x["l"] for x in b), "cierre_ayer": (ayer.get(s) or [None])[-1],
                            "vwap": sum(x.get("vw", x["c"]) * x["v"] for x in b) / vol if vol else None,
                            "vol_relativo": None, "hace_1h": antes[-1]["c"] if antes else b[0]["o"],
                            "rango_alto": max(x["h"] for x in b[:6]) if len(b) >= 6 else None,
                            "rango_bajo": min(x["l"] for x in b[:6]) if len(b) >= 6 else None}
        return resultado

    def orden(self, *args, **kwargs) -> None:
        pass

    def limpiar_cuenta(self) -> None:
        pass


class AvisosMudos:
    """En el entrenamiento nadie recibe correos: los avisos solo se escriben en el registro."""

    def crear_issue(self, titulo: str, cuerpo: str, etiquetas: list[str]) -> None:
        print(f"[aviso] {titulo}")
        return None

    def estado_issue(self, numero: int) -> tuple[str, str | None]:
        return "open", None


def _cuota_agotada(post):
    """Tras todos los reintentos, un 429 es la cuota del día: se para la ejecución (no se pasa al modelo ligero)."""
    def envoltura(url, cuerpo, cabeceras, *args, **kwargs):
        try:
            return post(url, cuerpo, cabeceras, *args, **kwargs)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                raise SinCuota(str(e)) from e
            raise
    return envoltura


def preparar() -> None:
    """Ajustes del entrenamiento sobre los programas de la oficina (sin tocar sus archivos)."""
    from agentes import ciclo, fuentes

    config.ESPEJO_ALPACA = False
    config.MAX_NOTICIAS_CONSULTA = 100  # menos lotes que clasificar: cuota de la IA
    ia_modulo._post_con_reintentos = _cuota_agotada(ia_modulo._post_con_reintentos)
    ciclo.evaluar = lambda *a, **k: None  # nadie es despedido: es un entrenamiento
    plantilla = ciclo.plantilla_inicial
    ciclo.plantilla_inicial = lambda: [p for p in plantilla() if p.get("equipo", "diario") == "diario"]

    # Las fuentes de Bernardo (13F de Berkshire) y Carmen (Congreso): solo lo presentado hasta ese día
    descargar, leer_indice = fuentes.descargar, fuentes.leer_indice

    def descargar_hasta_hoy(url: str) -> bytes:
        datos = descargar(url)
        if "submissions" in url:
            j = json.loads(datos)
            r = j["filings"]["recent"]
            hoy = date.today().isoformat()
            vale = [i for i, f in enumerate(r["filingDate"]) if f <= hoy]
            j["filings"]["recent"] = {k: [v[i] for i in vale] for k, v in r.items() if isinstance(v, list)}
            datos = json.dumps(j).encode()
        return datos

    fuentes.descargar = descargar_hasta_hoy
    fuentes.leer_indice = lambda datos: [r for r in leer_indice(datos)
                                         if (r["presentado"] or "") <= date.today().isoformat()]


def dias_de_bolsa(mercado: MercadoHistorico, inicio: str, fin: str) -> list[str]:
    dias = mercado._get(f"{alpaca.TRADING_URL}/v2/calendar", {"start": inicio, "end": fin})
    return [d["date"] for d in dias]


def _momento(dia: str, hora: tuple[int, int]) -> datetime:
    return datetime.fromisoformat(dia).replace(hour=hora[0], minute=hora[1], tzinfo=NUEVA_YORK).astimezone(timezone.utc)


def simular_dia(dia: str, mercado: MercadoHistorico, ia, ia_ligera, avisos) -> str:
    from agentes import ciclo, fundamentales, intradia

    with freeze_time(_momento(dia, HORA_DECISION), tick=True):
        try:
            if fundamentales.caducado(fundamentales.cargar(), date.today()):
                fundamentales.actualizar(date.today())
        except Exception as e:
            print(f"Sin datos de empresa: {e}")
        texto = ciclo.ejecutar(mercado, ia, avisos, ahora=_ahora(), ia_noticias=ia_ligera)
    for hora in HORAS_STOPS:  # la tarde: saltan los stops del equipo diario, sin IA
        with freeze_time(_momento(dia, hora), tick=True):
            estado = ciclo.cargar_estado()
            precios = mercado.ultimos_precios(sorted({s for a in estado["agentes"] for s in a["posiciones"]}
                                                     | {config.REFERENCIA}))
            if intradia.revisar_stops_diario(estado, precios, dia, f"{hora[0]:02d}:{hora[1]:02d}", mercado, _ahora()):
                ciclo.guardar_estado(estado)
    return texto


def _guardar_copia() -> None:
    shutil.rmtree(COPIA, ignore_errors=True)
    if config.DATOS.exists():
        shutil.copytree(config.DATOS, COPIA)


def _deshacer_dia() -> None:
    shutil.rmtree(config.DATOS, ignore_errors=True)
    if COPIA.exists():
        shutil.copytree(COPIA, config.DATOS)


def main() -> None:
    from agentes.ia import crear_ia
    from entrenamiento import resultados

    maximo = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--dias=")), "0")) or None
    limite = time.monotonic() + float(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--minutos=")),
                                           "320")) * 60
    preparar()
    mercado, avisos = MercadoHistorico(), AvisosMudos()
    ia, ia_ligera = crear_ia(), crear_ia(ligera=True)
    progreso = json.loads(PROGRESO.read_text()) if PROGRESO.exists() else {"hechos": []}
    pendientes = [d for d in dias_de_bolsa(mercado, INICIO, FIN) if d not in progreso["hechos"]]
    print(f"Días por simular: {len(pendientes)}")
    hechos = 0
    for dia in pendientes:
        if (maximo and hechos >= maximo) or time.monotonic() > limite:
            break
        _guardar_copia()
        inicio = time.monotonic()
        try:
            texto = simular_dia(dia, mercado, ia, ia_ligera, avisos)
        except SinCuota as e:
            print(f"{dia}: sin cuota de la IA ({e}). Se deshace el día y se sigue en la próxima ejecución.")
            _deshacer_dia()
            break
        progreso["hechos"].append(dia)
        config.DATOS.mkdir(parents=True, exist_ok=True)
        PROGRESO.write_text(json.dumps(progreso, indent=1))
        hechos += 1
        print(f"{dia}: {texto} ({time.monotonic() - inicio:.0f} s)")
    shutil.rmtree(COPIA, ignore_errors=True)
    resultados.escribir()
    print(f"Simulados {hechos} días; llevamos {len(progreso['hechos'])}.")


if __name__ == "__main__":
    main()
