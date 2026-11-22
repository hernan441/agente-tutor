"""Trae inscripciones y entregas a la base (sin IA).

Todo llega por mail: `buzon.py` lee la casilla del docente en solo lectura y clasifica cada
mensaje. Este módulo valida y guarda. En modo piloto se leen los .eml de `piloto/buzon/`.
"""

import json
from datetime import datetime

from . import buzon, db, seguridad, unidades


def _fecha(s: str) -> str:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
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
        if not apellido and " " in nombre:
            nombre, apellido = nombre.split(" ", 1)
        existe = db.inscripto(con, mail)
        con.execute(
            """INSERT INTO alumnos (mail, nombre, apellido, curso, inscripto_en) VALUES (?,?,?,?,?)
               ON CONFLICT(mail) DO UPDATE SET nombre=excluded.nombre, apellido=excluded.apellido,
               curso=excluded.curso""",
            (mail, nombre, apellido, f.get("curso", "").upper(), _fecha(f.get("marca_temporal", ""))),
        )
        actualizados += existe
        nuevos += not existe
    con.commit()
    return {"nuevos": nuevos, "actualizados": actualizados, "rechazados": rechazados}


def cargar_entregas(con, filas) -> dict:
    nuevas = sin_inscripcion = 0
    for f in filas:
        mail = seguridad.normalizar_mail(f.get("mail", ""))
        if not mail or not f.get("unidad") or not f.get("actividad"):
            continue
        archivos = f.get("archivos") or []
        flags = [f"adjuntos: {a}" for a in f.get("avisos", [])]
        cur = con.execute(
            """INSERT OR IGNORE INTO entregas (marca_temporal, mail, nombre, unidad, actividad,
               respuesta, uso_ia, autoevaluacion, archivos, message_id, flags, pregunta, cambios)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_fecha(f.get("marca_temporal", "")), mail, seguridad.limpiar_texto(f.get("nombre", ""))[:80],
             f["unidad"].strip()[:40], f["actividad"].strip().split()[0][:10],
             seguridad.limpiar_texto(f.get("respuesta", "")), seguridad.limpiar_texto(f.get("uso_ia", "")),
             seguridad.limpiar_texto(f.get("autoevaluacion", "")), "\n".join(archivos), f.get("message_id") or None,
             json.dumps(flags, ensure_ascii=False) if flags else None,
             seguridad.limpiar_texto(f.get("pregunta", ""))[:1000], seguridad.limpiar_texto(f.get("cambios", ""))[:2000]),
        )
        if cur.rowcount:
            nuevas += 1
            sin_inscripcion += not db.inscripto(con, mail)
    con.commit()
    return {"nuevas": nuevas, "sin_inscripcion": sin_inscripcion}


def sincronizar(cfg, con) -> dict:
    import sys
    mod = sys.modules[__name__]
    ins0 = con.execute("SELECT COUNT(*) FROM alumnos").fetchone()[0]
    rech0 = con.execute("SELECT COUNT(*) FROM rechazos WHERE origen='inscripcion'").fetchone()[0]
    ent0 = con.execute("SELECT COUNT(*) FROM entregas").fetchone()[0]
    sin0 = con.execute("SELECT COUNT(*) FROM entregas WHERE mail NOT IN (SELECT mail FROM alumnos)").fetchone()[0]
    mails = buzon.leer(cfg, con, unidades.todas(cfg.unidades), mod)
    return {
        "mails": mails,
        "inscripcion": {"nuevos": con.execute("SELECT COUNT(*) FROM alumnos").fetchone()[0] - ins0,
                        "rechazados": con.execute("SELECT COUNT(*) FROM rechazos WHERE origen='inscripcion'").fetchone()[0] - rech0},
        "entregas": {"nuevas": con.execute("SELECT COUNT(*) FROM entregas").fetchone()[0] - ent0,
                     "sin_inscripcion": con.execute("SELECT COUNT(*) FROM entregas WHERE mail NOT IN (SELECT mail FROM alumnos)").fetchone()[0] - sin0},
    }
