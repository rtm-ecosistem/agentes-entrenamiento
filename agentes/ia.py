"""Acceso al modelo de IA. Cambiar de proveedor es cambiar la variable PROVEEDOR_IA."""

from __future__ import annotations

import json
import re
import time

import requests

from agentes import config


class IA:
    """Interfaz común: recibe instrucciones y un mensaje y devuelve un JSON ya interpretado."""

    def pedir_json(self, instrucciones: str, mensaje: str, esquema: dict | None = None) -> dict:
        """`esquema` es la forma fija que debe tener la respuesta (los valores que puede elegir, los campos
        obligatorios...): con Gemini el modelo no puede salirse de ella."""
        raise NotImplementedError


# Segundos mínimos entre peticiones a un mismo modelo, para no pasar del límite por minuto de la capa gratuita
# (Flash: 5 por minuto; Flash Lite: 15 por minuto)
ESPACIO_GEMINI = {config.MODELO_GEMINI: 13.0, config.MODELO_GEMINI_LIGERO: 4.5}
_ultima_peticion: dict[str, float] = {}
# Modelos que ya fallaron en esta ejecución: con prisa no se vuelven a intentar hasta la siguiente
_caidos: set[str] = set()


def _esperar_turno(modelo: str) -> None:
    espacio = ESPACIO_GEMINI.get(modelo, 13.0)
    falta = _ultima_peticion.get(modelo, -espacio) + espacio - time.monotonic()
    if falta > 0:
        time.sleep(falta)
    _ultima_peticion[modelo] = time.monotonic()


class Gemini(IA):
    def __init__(self, modelo: str = config.MODELO_GEMINI, clave: str | None = None,
                 respaldo: str | None = config.MODELO_GEMINI_LIGERO, prisa: bool = False):
        self.modelo = modelo
        self.clave = clave or config.secreto("GEMINI_API_KEY")
        # Si el modelo principal está saturado o sin cuota, se pide lo mismo al ligero antes de rendirse
        self.respaldo = respaldo if respaldo != modelo else None
        # Con prisa (analistas y ciclo diario), si el principal está saturado se pasa enseguida al ligero en vez
        # de esperar varios minutos a que se libere, y ya no se vuelve a probar en el resto de la ejecución
        self.prisa = prisa

    def pedir_json(self, instrucciones: str, mensaje: str, esquema: dict | None = None) -> dict:
        if self.prisa and self.respaldo and self.modelo in _caidos:
            return self._pedir(self.respaldo, instrucciones, mensaje, esquema)
        try:
            try:
                return self._pedir(self.modelo, instrucciones, mensaje, esquema)
            except json.JSONDecodeError as e:
                # Un JSON mal formado suele salir bien a la segunda
                print(f"{self.modelo} devolvió un JSON mal formado ({e}); se pide otra vez")
                return self._pedir(self.modelo, instrucciones, mensaje, esquema)
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError, RuntimeError) as e:
            if not self.respaldo:
                raise
            print(f"{self.modelo} no responde ({e}); se usa {self.respaldo}")
            if self.prisa:
                _caidos.add(self.modelo)
            return self._pedir(self.respaldo, instrucciones, mensaje, esquema)

    def _pedir(self, modelo: str, instrucciones: str, mensaje: str, esquema: dict | None = None) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        cuerpo = {
            "systemInstruction": {"parts": [{"text": instrucciones}]},
            "contents": [{"role": "user", "parts": [{"text": mensaje}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
        }
        if esquema:
            cuerpo["generationConfig"]["responseSchema"] = esquema
        _esperar_turno(modelo)
        rapido = self.prisa and self.respaldo and modelo == self.modelo
        inicio = time.monotonic()
        try:
            datos = _post_con_reintentos(url, cuerpo, {"x-goog-api-key": self.clave},
                                         **({"intentos": 2, "espera": 5, "limite": 120} if rapido else {}))
        except requests.HTTPError as e:
            # Si Gemini no acepta el molde (400), se pide sin él: el control de riesgo sigue revisando las órdenes
            if not esquema or e.response is None or e.response.status_code != 400:
                raise
            print(f"{modelo} no aceptó el molde de respuesta ({e}); se pide sin él")
            return self._pedir(modelo, instrucciones, mensaje)
        print(f"{modelo} respondió en {time.monotonic() - inicio:.0f} s")
        texto = "".join(p.get("text", "") for p in datos["candidates"][0]["content"]["parts"])
        return extraer_json(texto)


class Claude(IA):
    def __init__(self, modelo: str = config.MODELO_CLAUDE, clave: str | None = None):
        self.modelo = modelo
        self.clave = clave or config.secreto("ANTHROPIC_API_KEY")

    def pedir_json(self, instrucciones: str, mensaje: str, esquema: dict | None = None) -> dict:
        cuerpo = {
            "model": self.modelo, "max_tokens": 4000, "system": instrucciones,
            "messages": [{"role": "user", "content": mensaje + "\n\nResponde solo con el JSON."}],
        }
        datos = _post_con_reintentos("https://api.anthropic.com/v1/messages", cuerpo,
                                     {"x-api-key": self.clave, "anthropic-version": "2023-06-01"})
        texto = "".join(b.get("text", "") for b in datos["content"] if b.get("type") == "text")
        return extraer_json(texto)


def crear_ia(ligera: bool = False, prisa: bool = False) -> IA:
    """`ligera` usa un modelo más rápido y con más cuota gratuita, para tareas sencillas como resumir noticias.
    `prisa` no espera al modelo principal si está saturado: pasa enseguida al ligero."""
    if config.PROVEEDOR_IA == "claude":
        return Claude()
    return Gemini(config.MODELO_GEMINI_LIGERO if ligera else config.MODELO_GEMINI, prisa=prisa)


def _post_con_reintentos(url: str, cuerpo: dict, cabeceras: dict, intentos: int = 4, espera: float = 30,
                        limite: float = 300) -> dict:
    """Los modelos que "piensan" pueden tardar varios minutos: se espera hasta `limite` segundos y se reintenta.
    Si el servicio está saturado (503) se espera cada vez más: 30 s, 60 s y 90 s (con `espera` = 30)."""
    for intento in range(intentos):
        ultimo = intento == intentos - 1
        try:
            r = requests.post(url, json=cuerpo, headers=cabeceras, timeout=(15, limite))
        except (requests.Timeout, requests.ConnectionError):
            if ultimo:
                raise
            time.sleep(espera * (intento + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 529) and not ultimo:
            time.sleep(espera * (intento + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("La IA no respondió")


def extraer_json(texto: str) -> dict:
    """Interpreta la respuesta aunque venga envuelta en ```json ... ```."""
    texto = texto.strip()
    bloque = re.search(r"```(?:json)?\s*(.*?)```", texto, re.S)
    if bloque:
        texto = bloque.group(1)
    inicio, fin = texto.find("{"), texto.rfind("}")
    texto = texto[inicio:fin + 1]
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        # El fallo más habitual de los modelos pequeños: una coma de más antes de } o ]
        return json.loads(re.sub(r",\s*([}\]])", r"\1", texto))
