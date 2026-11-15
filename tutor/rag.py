"""RAG liviano: busca en el material de la clase los fragmentos que vienen al caso (sin IA).

Parte clase.md (y otros .md/.txt/.pdf de la carpeta de la unidad) en fragmentos con su
título de sección y los rankea con BM25 en Python puro. Sin modelos ni GPU. La interfaz
`buscar(unidad, consulta, k)` permite cambiar el motor por uno semántico más adelante.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

PALABRAS_POR_FRAGMENTO = 120
K1, B = 1.5, 0.75
VACIAS = set("""a al algo ante antes como con contra cual cuando de del desde donde dos el ella ellos en entre era es
esa ese eso esta este esto estos fue ha hay la las le les lo los mas me mi muy no nos o otra otro para pero por que
qué quien se sea ser si sin sobre son su sus tambien tiene todo tu un una uno unos y ya yo""".split())


def _tokens(texto: str) -> list:
    t = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return [w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2 and w not in VACIAS]


def _leer_material(carpeta: Path) -> list:
    """[(fuente, texto)] con todo el material de la unidad, menos unidad.md (que tiene la respuesta esperada)."""
    out = []
    for p in sorted(carpeta.iterdir()):
        if p.name == "unidad.md" or p.name.startswith("."):
            continue
        if p.suffix.lower() in (".md", ".txt"):
            out.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
        elif p.suffix.lower() == ".pdf":
            import pymupdf
            out.append((p.name, "\n".join(pg.get_text() for pg in pymupdf.open(p))))
    return out


def fragmentar(fuente: str, texto: str) -> list:
    frags, seccion, buffer = [], fuente, []

    def volcar():
        if buffer:
            frags.append({"fuente": fuente, "seccion": seccion, "texto": " ".join(buffer).strip()})
            buffer.clear()

    for linea in texto.splitlines():
        m = re.match(r"^\s*#{1,4}\s+(.+)$", linea)
        if m:
            volcar()
            seccion = m.group(1).strip()
            continue
        if linea.strip().startswith(">"):
            continue  # notas para el docente
        for palabra in linea.split():
            buffer.append(palabra)
            if len(buffer) >= PALABRAS_POR_FRAGMENTO:
                volcar()
    volcar()
    return [f for f in frags if len(f["texto"].split()) >= 8]


class Indice:
    def __init__(self, fragmentos: list):
        self.frags = fragmentos
        self.docs = [Counter(_tokens(f["seccion"] + " " + f["texto"])) for f in fragmentos]
        self.largos = [sum(d.values()) for d in self.docs]
        self.prom = (sum(self.largos) / len(self.largos)) if self.largos else 1
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def buscar(self, consulta: str, k: int = 3) -> list:
        q = _tokens(consulta)
        puntajes = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in set(q):
                if t in d:
                    tf = d[t]
                    s += self.idf[t] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * self.largos[i] / self.prom))
            if s > 0:
                puntajes.append((s, i))
        puntajes.sort(reverse=True)
        return [{**self.frags[i], "puntaje": round(s, 2)} for s, i in puntajes[:k]]


_CACHE: dict = {}


def indice(unidad, cache_dir: Path | None = None) -> Indice:
    material = _leer_material(unidad.carpeta)
    h = hashlib.sha1(json.dumps(material, ensure_ascii=False).encode()).hexdigest()
    if h in _CACHE:
        return _CACHE[h]
    frags = None
    if cache_dir:
        archivo = cache_dir / f"rag_{unidad.codigo}_{h[:12]}.json"
        if archivo.exists():
            frags = json.loads(archivo.read_text(encoding="utf-8"))
    if frags is None:
        frags = [f for fuente, texto in material for f in fragmentar(fuente, texto)]
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            archivo.write_text(json.dumps(frags, ensure_ascii=False), encoding="utf-8")
    _CACHE[h] = Indice(frags)
    return _CACHE[h]


def buscar(unidad, consulta: str, k: int = 3, cache_dir: Path | None = None) -> list:
    return indice(unidad, cache_dir).buscar(consulta, k)
