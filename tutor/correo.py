"""Arma .eml y los sube como BORRADORES por IMAP. Este módulo no puede enviar: no usa SMTP."""

import imaplib
import os
import re
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


def mensaje(remitente: str, para: str, asunto: str, cuerpo: str, responde_a: str = "", cco: list | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"] = remitente
    if para:
        m["To"] = para
    if cco:
        m["Bcc"] = ", ".join(cco)
    m["Subject"] = asunto
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid()
    if responde_a:  # queda en el mismo hilo que el mail del alumno
        m["In-Reply-To"] = responde_a
        m["References"] = responde_a
    m.set_content(cuerpo)
    return m


def _remitente(cfg) -> str:
    return f"{cfg.firma_remitente} <{cfg.cuenta}>" if cfg.cuenta else cfg.firma_remitente


def escribir_eml(cfg, con, dev_id: int) -> Path:
    d = con.execute("SELECT * FROM devoluciones WHERE id=?", (dev_id,)).fetchone()
    mid = ""
    if d["entrega_id"]:
        e = con.execute("SELECT message_id FROM entregas WHERE id=?", (d["entrega_id"],)).fetchone()
        mid = (e["message_id"] or "") if e else ""
    m = mensaje(_remitente(cfg), d["mail"], d["asunto"], d["cuerpo"], mid)
    ruta = cfg.datos / "borradores" / d["unidad"] / f"{d['mail']}_{d['actividad']}_{d['tipo']}.eml"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(bytes(m))
    con.execute("UPDATE devoluciones SET ruta_eml=? WHERE id=?", (str(ruta), dev_id))
    con.commit()
    return ruta


def _carpeta_borradores(imap) -> str:
    _, carpetas = imap.list()
    for c in carpetas:
        linea = c.decode()
        if "\\Drafts" in linea:
            return re.search(r'"([^"]+)"$|(\S+)$', linea).group(1) or linea.split()[-1]
    return "[Gmail]/Drafts"


def subir_emls(cfg, rutas: list) -> list:
    """Sube .eml a la carpeta de Borradores. Devuelve las rutas subidas. Nunca envía."""
    clave = os.environ.get("TUTOR_IMAP_PASS")
    if not (cfg.cuenta and clave):
        raise SystemExit("Falta la cuenta en config.toml o TUTOR_IMAP_PASS en .env: no subo borradores.")
    if not rutas:
        return []
    imap = imaplib.IMAP4_SSL(cfg.imap_host)
    imap.login(cfg.cuenta, clave)
    carpeta = _carpeta_borradores(imap)
    hechas = []
    try:
        for r in rutas:
            estado, _ = imap.append(f'"{carpeta}"', "\\Draft", imaplib.Time2Internaldate(time.time()), Path(r).read_bytes())
            if estado == "OK":
                hechas.append(r)
    finally:
        imap.logout()
    return hechas


def subir_borradores(cfg, con, unidad: str) -> int:
    pendientes = con.execute(
        "SELECT id, ruta_eml FROM devoluciones WHERE unidad=? AND borrador_subido=0 AND ruta_eml IS NOT NULL",
        (unidad,)).fetchall()
    hechas = set(subir_emls(cfg, [p["ruta_eml"] for p in pendientes]))
    for p in pendientes:
        if p["ruta_eml"] in hechas:
            con.execute("UPDATE devoluciones SET borrador_subido=1 WHERE id=?", (p["id"],))
    con.commit()
    return len(hechas)


# ---------------- mails que manda el docente (siempre como borrador) ----------------

INSTRUCCIONES_ENTREGA = """Cómo entregar: respondé ESTE mail (sin cambiar el asunto) con tu trabajo. Podés escribirlo
en el cuerpo del mail y/o adjuntar fotos, PDF o un audio.

Al final, agregá estas líneas:
IA: ¿usaste IA? ¿cuál, qué le pediste y qué corregiste vos?
Autoevaluación: ¿qué entendiste bien y qué te falta?
Mi pregunta: algo que te quedó picando sobre el tema (también se valora).

Te voy a devolver comentarios y una nota. Si no llegás a 7, corregí y respondé de nuevo este
mismo hilo con la nueva versión, y agregá:
Qué cambié: qué corregiste y por qué."""


def borrador_trabajo(cfg, con, unidad, actividad: str, curso: str) -> tuple[Path, int]:
    """Mail del trabajo para el curso (con copia oculta a los inscriptos). Registra la asignación."""
    act = unidad.actividades[actividad]
    alumnos = [r["mail"] for r in con.execute("SELECT mail FROM alumnos WHERE curso=? ORDER BY apellido", (curso,))]
    asunto = f"[{unidad.codigo} {actividad}] {unidad.materia} — {act.nombre}"
    cuerpo = f"Hola, {curso}:\n\n{act.consigna}\n\n{INSTRUCCIONES_ENTREGA}\n\n{unidad.firma}"
    m = mensaje(_remitente(cfg), "", asunto, cuerpo, cco=alumnos)
    ruta = cfg.datos / "borradores" / "trabajos" / f"{unidad.codigo}_{actividad}_{curso}.eml"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(bytes(m))
    con.execute("""INSERT INTO asignaciones (unidad, actividad, curso, asunto, message_id, fecha) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(unidad, actividad, curso) DO UPDATE SET asunto=excluded.asunto,
                   message_id=excluded.message_id, fecha=excluded.fecha""",
                (unidad.codigo, actividad, curso, asunto, m["Message-ID"], datetime.now().isoformat(sep=" ", timespec="seconds")))
    con.commit()
    return ruta, len(alumnos)


def borrador_inscripcion(cfg, materia: str = "") -> Path:
    """Mail de principio de año para que los alumnos se inscriban respondiendo."""
    asunto = f"Inscripción{' — ' + materia if materia else ''}"
    cuerpo = ("Hola:\n\nPara recibir los trabajos y las devoluciones, respondé ESTE mail desde tu cuenta del "
              "colegio con estos datos:\n\nNombre:\nApellido:\nCurso: (por ejemplo, 6A)\n\n"
              "Con eso quedás inscripto. Los trabajos te van a llegar por mail.\n\n" + cfg.firma_remitente)
    m = mensaje(_remitente(cfg), "", asunto, cuerpo)
    ruta = cfg.datos / "borradores" / "inscripcion.eml"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(bytes(m))
    return ruta
