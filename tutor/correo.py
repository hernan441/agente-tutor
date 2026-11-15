"""Arma .eml y los sube como BORRADORES por IMAP. Este módulo no puede enviar: no usa SMTP."""

import imaplib
import os
import re
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


def mensaje(remitente: str, para: str, asunto: str, cuerpo: str) -> EmailMessage:
    m = EmailMessage()
    m["From"] = remitente
    m["To"] = para
    m["Subject"] = asunto
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid()
    m.set_content(cuerpo)
    return m


def escribir_eml(cfg, con, dev_id: int) -> Path:
    d = con.execute("SELECT * FROM devoluciones WHERE id=?", (dev_id,)).fetchone()
    remitente = f"{cfg.firma_remitente} <{cfg.cuenta}>" if cfg.cuenta else cfg.firma_remitente
    m = mensaje(remitente, d["mail"], d["asunto"], d["cuerpo"])
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


def subir_borradores(cfg, con, unidad: str) -> int:
    clave = os.environ.get("TUTOR_IMAP_PASS")
    if not (cfg.cuenta and clave):
        raise SystemExit("Falta la cuenta en config.toml o TUTOR_IMAP_PASS en .env: no subo borradores.")
    pendientes = con.execute(
        "SELECT id, ruta_eml FROM devoluciones WHERE unidad=? AND borrador_subido=0 AND ruta_eml IS NOT NULL",
        (unidad,)).fetchall()
    if not pendientes:
        return 0
    imap = imaplib.IMAP4_SSL(cfg.imap_host)
    imap.login(cfg.cuenta, clave)
    carpeta = _carpeta_borradores(imap)
    n = 0
    for p in pendientes:
        datos = Path(p["ruta_eml"]).read_bytes()
        estado, _ = imap.append(f'"{carpeta}"', "\\Draft", imaplib.Time2Internaldate(time.time()), datos)
        if estado == "OK":
            con.execute("UPDATE devoluciones SET borrador_subido=1 WHERE id=?", (p["id"],))
            n += 1
    con.commit()
    imap.logout()
    return n
