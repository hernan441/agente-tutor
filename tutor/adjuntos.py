"""Extrae el texto de lo que suben los alumnos, sin IA.

PDF y docx -> texto directo (OCR si el PDF es escaneado). Fotos -> se enderezan, se
achican y pasan por OCR. Audio/video -> faster-whisper local. Todo se cachea por hash.
Solo si una foto no tiene texto legible (dibujo, letra manuscrita) se marca
`requiere_vision` y esa imagen reducida es lo único que mira la IA.
"""

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PDF = {".pdf"}
DOCX = {".docx"}
TEXTO = {".txt", ".md", ".csv"}
IMAGEN = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
AUDIO = {".ogg", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".mp4", ".webm", ".mov", ".3gp", ".amr"}

UMBRAL_CONFIANZA = 0.80
MIN_CARACTERES = 25
_ocr = None


def tipo_de(ruta: Path) -> str:
    e = ruta.suffix.lower()
    for nombre, exts in (("pdf", PDF), ("docx", DOCX), ("texto", TEXTO), ("imagen", IMAGEN), ("audio", AUDIO)):
        if e in exts:
            return nombre
    return "otro"


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _motor_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def ocr_imagen(img) -> tuple[str, float]:
    """img: PIL.Image. Devuelve (texto, confianza media 0-1)."""
    import numpy as np
    res, _ = _motor_ocr()(np.array(img.convert("RGB")))
    if not res:
        return "", 0.0
    # Ordenar por renglón (y) y luego x
    res.sort(key=lambda r: (round(r[0][0][1] / 20), r[0][0][0]))
    texto = " ".join(r[1] for r in res)
    conf = sum(float(r[2]) for r in res) / len(res)
    return texto.strip(), conf


def normalizar_imagen(ruta: Path, destino: Path, lado_max: int):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")
    img.thumbnail((lado_max, lado_max))
    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "JPEG", quality=85)
    return img


def leer_pdf(ruta: Path) -> tuple[str, str, float]:
    import pymupdf
    doc = pymupdf.open(ruta)
    texto = "\n".join(p.get_text() for p in doc).strip()
    if len(texto) >= MIN_CARACTERES * max(1, len(doc)) // 2:
        return texto, "pymupdf", 1.0
    # Escaneado: renderizar cada página y pasar OCR
    from PIL import Image
    partes, confs = [], []
    for p in doc:
        pix = p.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        t, c = ocr_imagen(img)
        partes.append(t)
        confs.append(c)
    return "\n".join(partes).strip(), "pdf+ocr", (sum(confs) / len(confs) if confs else 0.0)


def leer_docx(ruta: Path) -> str:
    xml = zipfile.ZipFile(ruta).read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    texto = re.sub(r"<[^>]+>", "", xml)
    import html
    return html.unescape("\n".join(l for l in texto.splitlines() if l.strip()))


def transcribir(cfg, rutas: list[Path], vocabulario: str = "") -> dict:
    if not rutas:
        return {}
    py = cfg.audio_python or sys.executable
    script = Path(__file__).with_name("whisper_cli.py")
    r = subprocess.run([py, str(script), cfg.whisper_modelo, vocabulario, *map(str, rutas)],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode:
        return {str(p): {"error": r.stderr.strip()[-300:]} for p in rutas}
    return {d["archivo"]: d for d in json.loads(r.stdout)}


def _guardar(con, h, ruta, tipo, texto, metodo, conf, vision=0, img_vision=None):
    con.execute(
        """INSERT OR REPLACE INTO adjuntos (sha256, ruta, tipo, texto, metodo, confianza,
           requiere_vision, imagen_vision, procesado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
        (h, str(ruta), tipo, texto, metodo, conf, vision, img_vision, datetime.now().isoformat(timespec="seconds")),
    )


def procesar_entregas(cfg, con, entrega_ids: list[int], vocabulario: str = "") -> dict:
    """Extrae el texto de los adjuntos de esas entregas. Devuelve estadísticas."""
    stats = {"archivos": 0, "cache": 0, "pdf": 0, "docx": 0, "texto": 0, "imagen": 0, "audio": 0,
             "otro": 0, "requiere_vision": 0}
    cache_dir = cfg.datos / "cache"
    audios = []
    for eid in entrega_ids:
        fila = con.execute("SELECT archivos FROM entregas WHERE id=?", (eid,)).fetchone()
        for r in filter(None, (fila["archivos"] or "").split("\n")):
            ruta = Path(r)
            if not ruta.exists():
                continue
            stats["archivos"] += 1
            h = sha256(ruta)
            con.execute("INSERT OR IGNORE INTO entrega_adjuntos VALUES (?,?)", (eid, h))
            if con.execute("SELECT 1 FROM adjuntos WHERE sha256=?", (h,)).fetchone():
                stats["cache"] += 1
                continue
            tipo = tipo_de(ruta)
            stats[tipo] += 1
            try:
                if tipo == "pdf":
                    t, metodo, c = leer_pdf(ruta)
                    _guardar(con, h, ruta, tipo, t, metodo, c)
                elif tipo == "docx":
                    _guardar(con, h, ruta, tipo, leer_docx(ruta), "docx-xml", 1.0)
                elif tipo == "texto":
                    _guardar(con, h, ruta, tipo, ruta.read_text(encoding="utf-8", errors="replace"), "lectura", 1.0)
                elif tipo == "imagen":
                    img = normalizar_imagen(ruta, cache_dir / f"{h[:16]}_ocr.jpg", 2000)
                    t, c = ocr_imagen(img)
                    vision = int(len(t) < MIN_CARACTERES or c < UMBRAL_CONFIANZA)
                    img_v = None
                    if vision:
                        img_v = cache_dir / f"{h[:16]}_vision.jpg"
                        normalizar_imagen(ruta, img_v, 1024)
                        stats["requiere_vision"] += 1
                    _guardar(con, h, ruta, tipo, t, "ocr", c, vision, str(img_v) if img_v else None)
                elif tipo == "audio":
                    audios.append((h, ruta))
                else:
                    _guardar(con, h, ruta, tipo, "", "no soportado", 0.0)
            except Exception as exc:
                _guardar(con, h, ruta, tipo, "", f"error: {exc}"[:200], 0.0)
    if audios:
        res = transcribir(cfg, [r for _, r in audios], vocabulario)
        for h, ruta in audios:
            d = res.get(str(ruta), {"error": "sin resultado"})
            _guardar(con, h, ruta, "audio", d.get("texto", ""), "whisper" if "texto" in d else f"error: {d.get('error')}"[:200],
                     1.0 if "texto" in d else 0.0)
    con.commit()
    return stats


def de_entrega(con, eid: int) -> list:
    return [dict(r) for r in con.execute(
        """SELECT a.* FROM adjuntos a JOIN entrega_adjuntos ea ON ea.sha256=a.sha256
           WHERE ea.entrega_id=? ORDER BY a.ruta""", (eid,))]
