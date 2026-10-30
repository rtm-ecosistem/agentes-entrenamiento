"""Noticias del mercado: se recogen varias veces al día, la IA las traduce y clasifica, y se guardan 30 días.

Se puede ejecutar sola (`python -m agentes.noticias`) además de dentro del ciclo diario.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from agentes import config, prompts

TAMANO_LOTE = 25
IMPORTANCIAS = {"alta": 0, "media": 1, "baja": 2}


def cargar() -> dict:
    if config.NOTICIAS.exists():
        return json.loads(config.NOTICIAS.read_text(encoding="utf-8"))
    return {"ultima_consulta": None, "noticias": [], "resumenes": []}


def guardar(datos: dict) -> None:
    config.DATOS.mkdir(parents=True, exist_ok=True)
    config.NOTICIAS.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _ahora(ahora: datetime | None) -> datetime:
    return (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)


def actualizar(mercado, ia, ahora: datetime | None = None) -> dict:
    """Trae las noticias nuevas, clasifica las pendientes, borra las antiguas y escribe un resumen del momento."""
    datos = cargar()
    ahora = _ahora(ahora)
    desde = ahora - timedelta(hours=24)
    if datos.get("ultima_consulta"):
        desde = max(desde, datetime.fromisoformat(datos["ultima_consulta"]) - timedelta(minutes=30))
    try:
        nuevas = mercado.noticias(desde, config.MAX_NOTICIAS_CONSULTA)
    except Exception as e:
        print(f"No se pudieron descargar noticias: {e}")
        nuevas = []
    conocidas = {n["id"] for n in datos["noticias"]}
    for n in nuevas:
        if n["id"] not in conocidas:
            datos["noticias"].append({**n, "pendiente": True, "intentos": 0, "usada_por": []})
            conocidas.add(n["id"])
    datos["ultima_consulta"] = ahora.isoformat()

    limite = (ahora - timedelta(days=config.DIAS_NOTICIAS)).isoformat()
    datos["noticias"] = [n for n in datos["noticias"] if n["fecha"] >= limite[:19]]
    datos["noticias"].sort(key=lambda n: n["fecha"], reverse=True)

    clasificadas = clasificar_pendientes(datos, ia)
    if clasificadas or not datos["resumenes"]:
        escribir_resumen(datos, ia, ahora)
    guardar(datos)
    return datos


def clasificar_pendientes(datos: dict, ia) -> int:
    pendientes = [n for n in datos["noticias"] if n.get("pendiente")]
    hechas = 0
    for i in range(0, len(pendientes), TAMANO_LOTE):
        lote = pendientes[i:i + TAMANO_LOTE]
        try:
            r = ia.pedir_json(prompts.INSTRUCCIONES_CLASIFICAR, prompts.mensaje_clasificar(lote))
            por_id = {str(x.get("id")): x for x in r.get("noticias", []) if isinstance(x, dict)}
        except Exception as e:
            print(f"No se pudo clasificar un lote de noticias: {e}")
            por_id = {}
        for n in lote:
            x = por_id.get(n["id"])
            if x:
                n.update({
                    "titular_es": str(x.get("titular_es") or n["titular"]),
                    "explicacion": str(x.get("explicacion") or ""),
                    "importancia": x.get("importancia") if x.get("importancia") in IMPORTANCIAS else "baja",
                    "impacto": x.get("impacto") if x.get("impacto") in ("positivo", "negativo", "neutral", "mixto") else "neutral",
                    "tipo": x.get("tipo") if x.get("tipo") in prompts.TIPOS_NOTICIA else "otros",
                    "empresas": [str(e).upper() for e in (x.get("empresas") or n["simbolos"])][:8],
                })
                n.pop("pendiente", None)
                hechas += 1
            else:
                n["intentos"] = n.get("intentos", 0) + 1
                if n["intentos"] >= 3:  # tras 3 intentos se guarda sin analizar
                    n.update({"titular_es": n["titular"], "explicacion": "", "importancia": "baja",
                              "impacto": "neutral", "tipo": "otros", "empresas": n["simbolos"]})
                    n.pop("pendiente", None)
        if i + TAMANO_LOTE < len(pendientes):
            time.sleep(4)  # respeta el límite de peticiones por minuto de la capa gratuita
    return hechas


def escribir_resumen(datos: dict, ia, ahora: datetime) -> None:
    hace_un_dia = (ahora - timedelta(hours=24)).isoformat()[:19]
    recientes = [n for n in datos["noticias"] if not n.get("pendiente") and n["fecha"] >= hace_un_dia]
    ordenar_por_importancia(recientes)
    if not recientes:
        return
    try:
        r = ia.pedir_json(prompts.INSTRUCCIONES_RESUMEN, prompts.mensaje_resumen(recientes[:20]))
    except Exception as e:
        print(f"No se pudo escribir el resumen del mercado: {e}")
        return
    datos["resumenes"].append({"momento": ahora.isoformat()[:16], "resumen_mercado": r.get("resumen_mercado", ""),
                               "claves": [str(c) for c in r.get("claves", [])][:5],
                               "concepto_del_dia": r.get("concepto_del_dia")})
    datos["resumenes"] = datos["resumenes"][-90:]


def para_agentes(datos: dict, ahora: datetime | None = None, horas: int = 36, maximo: int = 30) -> tuple[list[dict], int]:
    """Las noticias importantes recientes que se pasan a los agentes, y cuántas de menor importancia se omiten."""
    desde = (_ahora(ahora) - timedelta(hours=horas)).isoformat()[:19]
    recientes = [n for n in datos["noticias"] if not n.get("pendiente") and n["fecha"] >= desde]
    utiles = [n for n in recientes if n["importancia"] in ("alta", "media")]
    ordenar_por_importancia(utiles)
    return utiles[:maximo], len(recientes) - len(utiles[:maximo])


def de_valores(datos: dict, simbolos: set[str], excluir: set[str], ahora: datetime | None = None,
               horas: int = 72, maximo: int = 12) -> list[dict]:
    """Noticias de las empresas concretas que interesan a un agente (las que tiene), aunque sean de menor importancia."""
    desde = (_ahora(ahora) - timedelta(hours=horas)).isoformat()[:19]
    propias = [n for n in datos["noticias"] if not n.get("pendiente") and n["fecha"] >= desde
               and n["id"] not in excluir and simbolos & set(n.get("empresas") or n.get("simbolos") or [])]
    ordenar_por_importancia(propias)
    return propias[:maximo]


PESOS = {"alta": 3, "media": 2, "baja": 1}


def sentimiento_por_valor(datos: dict, ahora: datetime | None = None, horas: int = 36) -> dict[str, dict]:
    """Tono de las noticias de cada valor, calculado sin IA: suma +peso si es positiva y -peso si es negativa."""
    desde = (_ahora(ahora) - timedelta(hours=horas)).isoformat()[:19]
    tono: dict[str, dict] = {}
    for n in datos["noticias"]:
        if n.get("pendiente") or n["fecha"] < desde:
            continue
        signo = {"positivo": 1, "negativo": -1}.get(n.get("impacto"), 0)
        for s in (n.get("empresas") or [])[:4]:
            t = tono.setdefault(s, {"noticias": 0, "positivas": 0, "negativas": 0, "puntos": 0})
            t["noticias"] += 1
            t["positivas"] += signo > 0
            t["negativas"] += signo < 0
            t["puntos"] += signo * PESOS.get(n.get("importancia"), 1)
    return tono


def texto_sentimiento(tono: dict[str, dict], maximo: int = 8) -> str:
    orden = sorted(tono.items(), key=lambda kv: -kv[1]["puntos"])
    def linea(s, t):
        return f"{s} ({t['puntos']:+d}: {t['positivas']} buenas, {t['negativas']} malas de {t['noticias']})"
    buenos = [linea(s, t) for s, t in orden if t["puntos"] > 0][:maximo]
    malos = [linea(s, t) for s, t in orden[::-1] if t["puntos"] < 0][:maximo]
    return (f"Con mejores noticias: {', '.join(buenos) or 'ninguno'}.\n"
            f"Con peores noticias: {', '.join(malos) or 'ninguno'}.")


def ordenar_por_importancia(noticias: list[dict]) -> None:
    """Primero las más importantes y, dentro de cada importancia, las más recientes."""
    noticias.sort(key=lambda n: n["fecha"], reverse=True)
    noticias.sort(key=lambda n: IMPORTANCIAS.get(n.get("importancia"), 2))


def marcar_uso(datos: dict, ids: list[str], agente_id: str) -> None:
    ids = {str(i) for i in ids}
    for n in datos["noticias"]:
        if n["id"] in ids and agente_id not in n.setdefault("usada_por", []):
            n["usada_por"].append(agente_id)


def ya_hecha(estado: dict, ahora: datetime | None = None, horas: float = 2) -> bool:
    """Si los analistas ya escribieron un informe hace poco: la ejecución de reserva de GitHub no hace falta."""
    hora = (estado.get("analistas") or {}).get("hora")
    return bool(hora) and _ahora(ahora) - datetime.fromisoformat(hora) < timedelta(hours=horas)


def main() -> None:
    """Por fases, para que la web publique las noticias sin esperar al informe de los analistas:
    `--fase noticias` las recoge y clasifica, `--fase analistas` escribe el informe y `--comprobar` dice si la
    ejecución de reserva puede saltarse. Sin argumentos hace las dos fases seguidas."""
    import os
    import sys

    from agentes import analistas, aprendizaje, web
    from agentes.alpaca import Alpaca
    from agentes.ciclo import anotar_bitacora, anotar_diario, cargar_estado, guardar_estado
    from agentes.ia import crear_ia

    estado = cargar_estado()
    if "--comprobar" in sys.argv:
        hecha = os.environ.get("GITHUB_EVENT_NAME") == "schedule" and ya_hecha(estado)
        print("El despertador ya hizo esta recogida: la reserva no hace nada." if hecha else "Toca recoger noticias.")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"hecha={'true' if hecha else 'false'}\n")
        return
    fase = sys.argv[sys.argv.index("--fase") + 1] if "--fase" in sys.argv else None

    mercado = Alpaca()
    if fase in (None, "noticias"):
        datos = actualizar(mercado, crear_ia(ligera=True))
        print(f"Noticias guardadas: {len(datos['noticias'])}")
        # Los datos de empresa, una vez por semana (sin IA)
        try:
            from datetime import date
            from agentes import fundamentales
            if fundamentales.caducado(fundamentales.cargar(), date.today()):
                f = fundamentales.actualizar(date.today())
                print(f"Datos de empresa: {len(f.get('empresas', {}))} empresas, "
                      f"{len(f.get('resultados', {}))} fechas de resultados")
        except Exception as e:
            print(f"No se pudieron actualizar los datos de empresa: {e}")
        web.generar(estado, datos)
    if fase in (None, "analistas"):
        datos = cargar()
        # Con cada recogida, los analistas escriben su informe; los agentes lo leerán en su próxima decisión
        try:
            inf = analistas.analizar(estado, mercado, crear_ia(prisa=True), datos)
            print("Informe de los analistas:", inf["conclusion"] if inf else "no se pudo escribir")
            if inf:
                guardar_estado(estado)
        except Exception as e:
            print(f"Los analistas no pudieron trabajar: {e}")
        # Con la bolsa cerrada, el equipo diario repasa su decisión del día con el modelo ligero
        try:
            hechos = aprendizaje.repasar_dia(estado, mercado, crear_ia(ligera=True), anotar_diario, anotar_bitacora)
            if hechos:
                print(f"Repasos del día: {hechos}")
                guardar_estado(estado)
        except Exception as e:
            print(f"No se pudo hacer el repaso del día: {e}")
        web.generar(estado, datos)


if __name__ == "__main__":
    main()
