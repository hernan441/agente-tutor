"""Trae inscripciones y entregas a la base (sin IA).

Modo real: rclone (solo lectura) exporta las Hojas de respuestas de Google Forms a CSV
y baja la carpeta de archivos subidos. Modo piloto: lee piloto/*.csv.
"""

import csv
import json
import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

from . import db, seguridad


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Encabezado normalizado (empieza con…) -> campo interno.
ALIAS = [
    ("marca temporal", "marca_temporal"), ("timestamp", "marca_temporal"), ("marca_temporal", "marca_temporal"),
    ("direccion de correo", "mail"), ("correo", "mail"), ("email", "mail"), ("mail", "mail"),
    ("nombre y apellido", "nombre"), ("apellido", "apellido"), ("nombre", "nombre"),
    ("curso", "curso"), ("unidad", "unidad"), ("actividad", "actividad"),
    ("tu respuesta", "respuesta"), ("respuesta", "respuesta"),
    ("usaste ia", "uso_ia"), ("uso ia", "uso_ia"),
    ("que entendiste", "autoevaluacion"), ("autoevaluacion", "autoevaluacion"),
    ("opcional subi", "archivos"), ("subi un archivo", "archivos"), ("archivos", "archivos"),
]


def _mapear(encabezados):
    out = {}
    for h in encabezados:
        n = _norm(h)
        for pref, campo in ALIAS:
            if n.startswith(_norm(pref)) and campo not in out.values():
                out[h] = campo
                break
    return out


def leer_csv(ruta: Path):
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        mapa = _mapear(r.fieldnames or [])
        for fila in r:
            yield {mapa[k]: (v or "").strip() for k, v in fila.items() if k in mapa}


def _fecha(s: str) -> str:
    """Forms exporta '17/11/2026 18:20:05'; el piloto usa ISO. Normaliza a ISO."""
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return s


def cargar_inscripciones(con, filas, cfg=None) -> dict:
    """Solo inscribe cuentas del colegio (dominio y nómina si la hay). El resto va a `rechazos`."""
    nuevos = actualizados = rechazados = 0
    nomina = seguridad.cargar_nomina(cfg) if cfg else None
    for f in filas:
        mail = seguridad.normalizar_mail(f.get("mail", ""))
        if not mail:
            continue
        if cfg:
            motivo = seguridad.validar_mail(cfg, mail, nomina)
            if motivo:
                seguridad.rechazar(con, mail, motivo, "inscripcion")
                rechazados += 1
                continue
        nombre = seguridad.limpiar_texto(f.get("nombre", ""))[:60]
        apellido = seguridad.limpiar_texto(f.get("apellido", ""))[:60]
        if not apellido and " " in nombre:  # "Nombre y apellido" en un solo campo
            nombre, apellido = nombre.split(" ", 1)
        existe = db.inscripto(con, mail)
        con.execute(
            """INSERT INTO alumnos (mail, nombre, apellido, curso, inscripto_en) VALUES (?,?,?,?,?)
               ON CONFLICT(mail) DO UPDATE SET nombre=excluded.nombre, apellido=excluded.apellido,
               curso=excluded.curso""",
            (mail, nombre, apellido, f.get("curso", ""), _fecha(f.get("marca_temporal", ""))),
        )
        actualizados += existe
        nuevos += not existe
    con.commit()
    return {"nuevos": nuevos, "actualizados": actualizados, "rechazados": rechazados}


def _resolver_archivos(valor: str, base: Path, por_id: dict) -> list:
    rutas = []
    for parte in re.split(r"[,\n]\s*", valor or ""):
        parte = parte.strip()
        if not parte:
            continue
        m = re.search(r"[?&]id=([\w-]+)|/d/([\w-]+)", parte)
        if m:
            fid = m.group(1) or m.group(2)
            if fid in por_id:
                rutas.append(str(por_id[fid]))
        else:
            p = (base / parte).resolve()
            if p.exists():
                rutas.append(str(p))
    return rutas


def cargar_entregas(con, filas, base: Path, por_id: dict | None = None) -> dict:
    nuevas = sin_inscripcion = 0
    for f in filas:
        mail = f.get("mail", "").lower()
        if not mail or not f.get("unidad") or not f.get("actividad"):
            continue
        archivos = _resolver_archivos(f.get("archivos", ""), base, por_id or {})
        cur = con.execute(
            """INSERT OR IGNORE INTO entregas (marca_temporal, mail, nombre, unidad, actividad,
               respuesta, uso_ia, autoevaluacion, archivos) VALUES (?,?,?,?,?,?,?,?,?)""",
            (_fecha(f.get("marca_temporal", "")), mail, seguridad.limpiar_texto(f.get("nombre", ""))[:80],
             f["unidad"].strip()[:40], f["actividad"].strip().split()[0][:10],
             seguridad.limpiar_texto(f.get("respuesta", "")), seguridad.limpiar_texto(f.get("uso_ia", "")),
             seguridad.limpiar_texto(f.get("autoevaluacion", "")), "\n".join(archivos)),
        )
        if cur.rowcount:
            nuevas += 1
            sin_inscripcion += not db.inscripto(con, mail)
    con.commit()
    return {"nuevas": nuevas, "sin_inscripcion": sin_inscripcion}


# ---------- modo real: rclone ----------

def _rclone(cfg, *args) -> str:
    r = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise SystemExit(f"rclone falló: {r.stderr.strip()[:500]}")
    return r.stdout


def bajar_de_google(cfg) -> tuple[Path, Path, dict]:
    destino = cfg.datos / "google"
    destino.mkdir(parents=True, exist_ok=True)
    rem = cfg.rclone_remoto + ":"
    for hoja in (cfg.hoja_inscripcion, cfg.hoja_entregas):
        _rclone(cfg, "copy", rem, str(destino), "--include", f"/{hoja}", "--drive-export-formats", "csv")
    archivos = cfg.datos / "archivos"
    _rclone(cfg, "copy", rem + cfg.carpeta_archivos, str(archivos))
    listado = json.loads(_rclone(cfg, "lsjson", "-R", "--files-only", rem + cfg.carpeta_archivos))
    por_id = {e["ID"]: archivos / e["Path"] for e in listado if "ID" in e}
    return destino / f"{cfg.hoja_inscripcion}.csv", destino / f"{cfg.hoja_entregas}.csv", por_id


def sincronizar(cfg, con) -> dict:
    if cfg.piloto:
        ins, ent, por_id = cfg.raiz / "piloto" / "inscripcion.csv", cfg.raiz / "piloto" / "entregas.csv", {}
    else:
        ins, ent, por_id = bajar_de_google(cfg)
    res = {}
    if ins.exists():
        res["inscripcion"] = cargar_inscripciones(con, leer_csv(ins), cfg)
    if ent.exists():
        res["entregas"] = cargar_entregas(con, leer_csv(ent), ent.parent, por_id)
    return res
