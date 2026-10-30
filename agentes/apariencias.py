"""La cara y la ropa de cada agente en la oficina: un registro persistente por ID y las variantes de los sustitutos.

Los diez personajes de las láminas aprobadas (agentes/recursos/personajes.png y cargando.png) son las bases. Un
sustituto recibe una variante propia: una base con otra paleta de pelo y ropa, recoloreada píxel a píxel con las
máscaras de materiales de agentes/identidades (se conservan sombras, luces, contornos y texturas, y las mismas
poses, tamaño y física). El peinado y el corte de la ropa son los de la base: cambiarlos necesita dibujar piezas
nuevas para todas las poses.

El registro vive en estado["apariencias"] con el ID inmutable del agente como clave. No se borra al despedir a
nadie (sus fichas históricas conservan la cara) y no depende del alias, así que recargar, cambiar el alias o
rehacer la web no cambia a nadie de aspecto.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import unicodedata
from pathlib import Path

from agentes import config

VERSION = 1
RECURSOS = config.RAIZ / "agentes" / "recursos"
IDENTIDADES = config.RAIZ / "agentes" / "identidades"
# Filas de las láminas, en orden
BASES = ["tomas", "marta", "iker", "lucia", "elena", "carmen", "bernardo", "pablo", "diego", "sara"]
MATERIALES = {1: "pelo", 2: "superior", 3: "inferior"}
PALETAS = {
    "pelo": {"negro": "#2a2226", "castaño oscuro": "#4a2f22", "castaño": "#7a4b2c", "rubio": "#d2ab62",
             "pelirrojo": "#c4562a", "caoba": "#7e2b22", "canoso": "#a8a5a0"},
    "superior": {"mostaza": "#d9a531", "burdeos": "#8e2b3a", "azul marino": "#2b4a7e", "lila": "#8c6bc0",
                 "naranja": "#e07a2e", "gris": "#7d8288", "turquesa": "#2fa3a0", "oliva": "#6f7a35",
                 "crema": "#e6dcc0", "coral": "#e8735f", "verde": "#3f8f4f", "rojo": "#c9342f",
                 "celeste": "#5fa8d8", "rosa": "#e07fa8", "morado": "#6a4691", "amarillo": "#f0c330"},
    "inferior": {"vaquero": "#2c4e72", "negro": "#26262a", "gris": "#55595f", "beige": "#b9a37e",
                 "marrón": "#5a4030", "verde oscuro": "#2f4a3a", "granate": "#5e2430"},
}
# Distancia mínima (RGB) para que dos colores de la misma prenda se vean distintos de un vistazo
DISTINTO = 90
PAD = 20  # margen transparente de cada figura en la hoja de una variante (el retrato recorta más ancho que la figura)
DISENO = "h3"  # versión del plano de las hojas de variantes: al cambiarlo se rehacen todas
# Ojos de cada base en su pose sentada (x0, y0, x1, y1 dentro de la figura): de noche se pintan cerrados
OJOS = {0: [[25, 30, 27, 34], [34, 31, 39, 35]], 1: [[22, 28, 26, 32], [34, 30, 39, 35]], 2: [[26, 36, 28, 40], [37, 38, 41, 42]], 3: [[26, 38, 28, 43], [36, 40, 41, 45]], 4: [[25, 37, 28, 41], [36, 39, 41, 44]], 5: [[26, 32, 30, 35], [38, 34, 43, 38]], 6: [[25, 33, 28, 37], [37, 35, 42, 39]], 7: [[27, 38, 29, 41], [38, 38, 42, 42]], 8: [[25, 31, 28, 34], [37, 33, 41, 37]], 9: [[25, 42, 28, 45], [37, 43, 40, 47]]}


def _sin_acentos(texto: str) -> str:
    return unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode().lower().strip()


def _hash(texto: str) -> int:
    return int(hashlib.sha1(texto.encode("utf-8")).hexdigest(), 16)


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[k:k + 2], 16) for k in (1, 3, 5))


def _distancia(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** .5


def medidas() -> dict:
    return json.loads((IDENTIDADES / "medidas.json").read_text(encoding="utf-8"))


_ORIGINALES: dict | None = None


def originales() -> dict[str, dict[str, str]]:
    """El color medio de cada material de cada base, sacado de las láminas con sus máscaras (en caché)."""
    global _ORIGINALES
    if _ORIGINALES is None:
        from PIL import Image
        img = Image.open(RECURSOS / "personajes.png").convert("RGBA")
        mascara = Image.open(IDENTIDADES / "mascara-personajes.png")
        _ORIGINALES = {}
        for fila, base in enumerate(BASES):
            sumas: dict[int, list[int]] = {}
            for x, y, w, h in medidas()["personajes"][fila]:
                pix, mat = img.crop((x, y, x + w, y + h)).get_flattened_data(), mascara.crop((x, y, x + w, y + h)).get_flattened_data()
                for p, m in zip(pix, mat):
                    if m:
                        s = sumas.setdefault(m, [0, 0, 0, 0])
                        s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; s[3] += 1
            _ORIGINALES[base] = {MATERIALES[m]: "#%02x%02x%02x" % tuple(round(v / s[3]) for v in s[:3])
                                 for m, s in sorted(sumas.items())}
    return _ORIGINALES


def _colores_de(entrada: dict) -> dict[str, str]:
    return entrada.get("colores") or originales()[entrada["base"]]


def _elegir(agente: dict, ocupadas: list[dict], todas: list[dict]) -> dict | None:
    """Una base y una paleta que no repiten el aspecto de ningún personaje activo. None si no queda ninguna.

    Se prefieren las bases menos usadas contando también a los despedidos: así el sustituto no suele tener la
    cara de quien acaba de irse."""
    uso = {b: sum(1 for o in todas if o.get("base") == b) for b in BASES}
    for base in sorted(BASES, key=lambda b: (uso[b], _hash(agente["id"] + b))):
        propios = originales()[base]
        iguales = [_colores_de(o) for o in ocupadas if o["base"] == base]
        opciones = {m: sorted(PALETAS[m].values(), key=lambda c: _hash(agente["id"] + m + c)) for m in propios}
        for k in range(len(opciones["superior"])):
            colores = {m: lista[(k * 3 + i) % len(lista)] for i, (m, lista) in enumerate(opciones.items())}
            # La prenda de arriba es lo que más se ve: tiene que cambiar respecto a la base y a los demás con su base.
            # El pelo también cambia, para que no parezca la misma persona con otra ropa
            if _distancia(colores["superior"], propios["superior"]) < DISTINTO or \
                    ("pelo" in colores and _distancia(colores["pelo"], propios["pelo"]) < DISTINTO * .7):
                continue
            if any(_distancia(colores["superior"], o["superior"]) < DISTINTO and
                   all(_distancia(colores[m], o.get(m, colores[m])) < DISTINTO for m in colores if m != "superior")
                   for o in iguales):
                continue
            variante = hashlib.sha1(json.dumps([base, colores], sort_keys=True).encode()).hexdigest()[:10]
            return {"version": VERSION, "base": base, "variante": variante, "colores": colores,
                    "hoja": f"variantes/{variante}.png"}
    return None


def asignar(estado: dict) -> dict:
    """Da apariencia a los agentes que aún no tienen. Las ya guardadas no se tocan nunca.

    Los agentes se recorren por orden de alta, así que repetir la asignación da siempre el mismo resultado."""
    registro = estado.setdefault("apariencias", {})
    for agente in estado["agentes"]:
        if agente["id"] in registro and not registro[agente["id"]].get("pendiente"):
            continue
        ocupadas = [registro[a["id"]] for a in estado["agentes"]
                    if a["estado"] == "activo" and a["id"] != agente["id"] and a["id"] in registro
                    and not registro[a["id"]].get("pendiente")]
        base = _sin_acentos(agente.get("alias", ""))
        original_libre = base in BASES and not any(
            e.get("base") == base and e.get("variante") == "original" for e in registro.values())
        if original_libre:
            registro[agente["id"]] = {"version": VERSION, "base": base, "variante": "original"}
        else:  # sin variante libre no se clona a nadie: la incorporación visual queda pendiente
            registro[agente["id"]] = _elegir(agente, ocupadas, list(registro.values())) or {"version": VERSION, "pendiente": True}
    return registro


def _recolorear(pixeles, materiales, medios: dict[int, tuple[float, float]], destino: dict[int, tuple]) -> list:
    salida = []
    for p, m in zip(pixeles, materiales):
        if p[3] < 128:
            salida.append((0, 0, 0, 0))
        elif m in destino:
            h, l, s = colorsys.rgb_to_hls(p[0] / 255, p[1] / 255, p[2] / 255)
            lm, sm = medios[m]
            ht, lt, st = destino[m]
            l2 = min(.97, max(.02, lt + (l - lm)))
            s2 = min(1, st * min(1.4, s / sm)) if sm > .08 else st
            r, g, b = colorsys.hls_to_rgb(ht, l2, s2)
            salida.append((round(r * 255), round(g * 255), round(b * 255), p[3]))
        else:
            salida.append(p)
    return salida


def cerrar_ojos(figura, fila: int) -> None:
    """Pinta los ojos cerrados sobre una figura sentada: tapa el ojo y su contorno con el tono de la piel de
    alrededor y dibuja el párpado (una curva oscura en los ojos anchos, una línea en los estrechos)."""
    px = figura.load()
    for x0, y0, x1, y1 in OJOS[fila]:
        anillo = [px[x, y] for x in range(x0 - 2, x1 + 3) for y in range(y0 - 2, y1 + 3)
                  if not (x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1) and px[x, y][3] > 128 and max(px[x, y][:3]) >= 110]
        if not anillo:
            continue
        piel = tuple(sorted(c[k] for c in anillo)[len(anillo) // 2] for k in range(3))
        d = lambda c: sum((c[k] - piel[k]) ** 2 for k in range(3)) ** .5
        es_piel = lambda c: c[3] > 128 and max(c[:3]) >= 110 and d(c) < 70
        # caja algo más grande para llevarse el contorno del ojo; no toca cejas ni el borde de la cara
        X0, Y0, X1, Y1 = x0 - 1, y0 - 1, x1 + 1, y1 + 1
        # un solo tono para todo el ojo: la piel de alrededor sin las sombras del perfil ni de las cejas
        cerca = sorted((c for c in anillo if es_piel(c)), key=lambda c: sum(c[:3]))
        claros = cerca[len(cerca) // 3:] or [piel]
        relleno = tuple(sorted(c[k] for c in claros)[len(claros) // 2] for k in range(3))
        tapados = []
        for y in range(Y0, Y1 + 1):
            for x in range(X0, X1 + 1):
                borde = y == Y0 or x in (X0, X1)
                # en el borde de la caja solo se tapan los restos claros del contorno, nunca cejas ni el perfil de la cara
                if px[x, y][3] > 128 and d(px[x, y]) > 18 and (not borde or max(px[x, y][:3]) >= 100):
                    px[x, y] = relleno + (255,)
                    tapados.append((x, y))
        if not tapados:
            continue
        a, b = x0, x1
        fila_p = round((y0 + y1) / 2) + 1
        oscuro = tuple(int(v * .38) for v in piel) + (255,)
        medio = tuple(int(v * .7) for v in piel) + (255,)
        n = b - a
        for x in range(a, b + 1):
            curva = 1 if n >= 3 and 0 < x - a < n else 0   # ◡ en los ojos anchos; recto en los estrechos
            px[x, fila_p + curva] = oscuro
        if n >= 3:
            px[a - 1, fila_p - 1] = medio  # pestaña exterior suave
            px[b + 1, fila_p] = medio
        else:
            px[a - 1, fila_p] = medio


def hoja_durmiendo(ruta: Path) -> list[list[int]]:
    """La lámina de los diez sentados con los ojos cerrados (agentes/recursos/durmiendo.png). Devuelve sus medidas."""
    from PIL import Image
    img = Image.open(RECURSOS / "personajes.png").convert("RGBA")
    celdas, x = [], 0
    for fila in range(len(BASES)):
        _, _, w, h = medidas()["personajes"][fila][3]
        celdas.append([x + PAD, PAD, w, h])
        x += w + 2 * PAD
    hoja = Image.new("RGBA", (x, max(c[3] for c in celdas) + 2 * PAD), (0, 0, 0, 0))
    for fila, (dx, dy, w, h) in enumerate(celdas):
        sx, sy, _, _ = medidas()["personajes"][fila][3]
        figura = img.crop((sx, sy, sx + w, sy + h))
        cerrar_ojos(figura, fila)
        hoja.paste(figura, (dx, dy))
    hoja.save(ruta, optimize=True)
    return celdas


def plano(base: str) -> dict:
    """Dónde va cada figura en la hoja de una variante: personajes (4 poses) arriba, cargando (4) debajo y la
    sentada con los ojos cerrados al final."""
    fila = BASES.index(base)
    plano_, y = {}, 0
    for clave in ("personajes", "cargando"):
        x, alto, celdas = 0, 0, []
        for _, _, w, h in medidas()[clave][fila]:
            celdas.append([x + PAD, y + PAD, w, h])
            x += w + 2 * PAD
            alto = max(alto, h + 2 * PAD)
        plano_[clave] = celdas
        y += alto
    _, _, w, h = medidas()["personajes"][fila][3]
    plano_["durmiendo"] = [[PAD, y + PAD, w, h]]
    return plano_


def generar_hoja(entrada: dict, ruta: Path) -> None:
    """Dibuja la hoja de una variante y comprueba que es válida antes de guardarla."""
    from PIL import Image
    base = entrada["base"]
    fila, destino_plano = BASES.index(base), plano(base)
    ancho = max(c[0] + c[2] + PAD for celdas in destino_plano.values() for c in celdas)
    alto = max(c[1] + c[3] + PAD for celdas in destino_plano.values() for c in celdas)
    hoja = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    objetivo = {m: colorsys.rgb_to_hls(*(v / 255 for v in _rgb(entrada["colores"][nombre])))
                for m, nombre in MATERIALES.items() if nombre in entrada["colores"]}
    # Luminosidad y saturación medias de cada material en la base: el recoloreado mantiene las diferencias
    medios: dict[int, tuple[float, float]] = {}
    for m, nombre in MATERIALES.items():
        if nombre in originales()[base]:
            h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in _rgb(originales()[base][nombre])))
            medios[m] = (l, s)
    for clave in ("personajes", "cargando"):
        img = Image.open(RECURSOS / f"{clave}.png").convert("RGBA")
        mascara = Image.open(IDENTIDADES / f"mascara-{clave}.png")
        for (x, y, w, h), (dx, dy, _, _) in zip(medidas()[clave][fila], destino_plano[clave]):
            caja = (x, y, x + w, y + h)
            figura = Image.new("RGBA", (w, h))
            figura.putdata(_recolorear(img.crop(caja).get_flattened_data(), mascara.crop(caja).get_flattened_data(), medios, objetivo))
            cambiados = sum(1 for a, b in zip(img.crop(caja).get_flattened_data(), figura.get_flattened_data()) if a[3] >= 128 and a != b)
            if cambiados < 200 or not figura.getbbox():
                raise ValueError(f"La variante {entrada['variante']} no cambia la figura {clave} de {base}")
            hoja.paste(figura, (dx, dy))
            if clave == "personajes" and (dx, dy) == tuple(destino_plano["personajes"][3][:2]):
                dormida = figura.copy()
                cerrar_ojos(dormida, fila)
                hoja.paste(dormida, tuple(destino_plano["durmiendo"][0][:2]))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(ruta, optimize=True)


def para_web(estado: dict, destino: Path | None = None) -> dict[str, dict]:
    """La apariencia de cada agente para la página. Con `destino` (la carpeta de la web) prepara las hojas que falten."""
    registro = asignar(estado)
    web = {}
    for ident, entrada in registro.items():
        if entrada.get("pendiente"):
            web[ident] = {"pendiente": True}
            continue
        datos = {"base": entrada["base"], "variante": entrada["variante"]}
        if entrada.get("hoja"):
            hoja = f"variantes/{entrada['variante']}-{DISENO}.png"
            if destino is not None:
                ruta = destino / "recursos" / hoja
                if not ruta.exists():
                    generar_hoja(entrada, ruta)
            datos |= {"colores": entrada["colores"], "hoja": "recursos/" + hoja, "medidas": plano(entrada["base"])}
        web[ident] = datos
    return web
