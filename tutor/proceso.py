"""¿La versión nueva es un trabajo real del alumno? Comparación entre versiones (sin IA).

Con las iteraciones, el riesgo es que el alumno le pida a una IA "mejorame esto" y reenvíe.
Esto no lo prueba nada; marca para que el docente mire:
- reescritura total: casi nada del texto anterior sobrevive Y además cambió la "voz"
  (largo de palabras y de oraciones, vocabulario);
- salto de largo: la versión nueva es varias veces más larga;
- sin explicar: es una versión 2+ y no dice "Qué cambié:".
"""

import difflib
import re
import unicodedata

MIN_PALABRAS = 25          # con menos texto no se compara (p. ej. todo en un adjunto)
UMBRAL_SIMILITUD = 0.25    # menos de esto = casi todo nuevo
UMBRAL_ESTILO = 0.35       # distancia de estilo relativa a partir de la cual "cambió la voz"
FACTOR_LARGO = 3.0
MIN_ANTERIOR_LARGO = 10    # para el salto de largo, la versión anterior tiene que tener al menos esto


def _palabras(s: str) -> list:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", s)


def estilo(texto: str) -> dict:
    p = _palabras(texto)
    oraciones = [o for o in re.split(r"[.!?\n]+", texto or "") if o.strip()]
    if not p:
        return {"largo_palabra": 0.0, "palabras_oracion": 0.0, "variedad": 0.0}
    return {"largo_palabra": sum(map(len, p)) / len(p),
            "palabras_oracion": len(p) / max(len(oraciones), 1),
            "variedad": len(set(p)) / len(p)}


def distancia_estilo(a: dict, b: dict) -> float:
    """Promedio de diferencias relativas entre los tres rasgos (0 = misma voz)."""
    difs = [abs(a[k] - b[k]) / max(a[k], b[k], 1e-9) for k in a]
    return sum(difs) / len(difs)


def comparar(anterior: str, nueva: str, cambios: str = "", iteracion: int = 2) -> dict:
    pa, pn = _palabras(anterior), _palabras(nueva)
    out = {"similitud": None, "distancia_estilo": None, "alertas": []}
    if iteracion > 1 and not (cambios or "").strip():
        out["alertas"].append("versión nueva sin explicar qué cambió")
    # Salto de largo: alcanza con que la anterior tenga algo de texto (un "ver PDF adjunto" no cuenta)
    if len(pa) >= MIN_ANTERIOR_LARGO and len(pn) >= MIN_PALABRAS and len(pn) > FACTOR_LARGO * len(pa):
        out["alertas"].append(f"la versión nueva es {len(pn) / len(pa):.1f} veces más larga")
    if len(pa) < MIN_PALABRAS or len(pn) < MIN_PALABRAS:
        return out
    sim = difflib.SequenceMatcher(None, pa, pn, autojunk=False).ratio()
    dist = distancia_estilo(estilo(anterior), estilo(nueva))
    out["similitud"], out["distancia_estilo"] = round(sim, 2), round(dist, 2)
    if sim < UMBRAL_SIMILITUD and dist > UMBRAL_ESTILO:
        out["alertas"].append(f"reescritura total con otra voz (similitud {sim:.0%}, estilo {dist:.2f})")
    return out


def revisar_version(con, fila) -> list:
    """Alertas de proceso para una entrega, comparándola con la versión anterior de la misma actividad."""
    prev = con.execute(
        """SELECT respuesta FROM entregas WHERE mail=? AND unidad=? AND actividad=? AND id<? AND estado='ok'
           ORDER BY marca_temporal DESC, id DESC LIMIT 1""",
        (fila["mail"], fila["unidad"], fila["actividad"], fila["id"])).fetchone()
    if not prev:
        return []
    return comparar(prev["respuesta"] or "", fila["respuesta"] or "", fila["cambios"] or "")["alertas"]
