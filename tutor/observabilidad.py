"""Métricas de uso, costo y calidad (sin IA).

- metricas(): costo por alumno/unidad/modelo, latencia, validaciones fallidas, caminos del router.
- calidad: cuánto edita el docente cada borrador antes de enviarlo (borrador vs. Enviados por IMAP).
"""

import difflib
import email
import imaplib
import os
import re
from email.header import decode_header, make_header


def _fila(con, sql, *args):
    return con.execute(sql, args).fetchone()


def metricas(con) -> str:
    o = ["# Métricas del tutor", ""]
    t = _fila(con, """SELECT COUNT(*) n, COALESCE(SUM(costo_usd),0) c, COALESCE(AVG(ms),0) ms,
                      COALESCE(SUM(tokens_entrada),0) te, COALESCE(SUM(tokens_cache),0) tc, COALESCE(SUM(tokens_salida),0) ts,
                      COALESCE(SUM(invalidas),0) inv, COALESCE(SUM(entregas),0) ent,
                      SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) err FROM llamadas""")
    evaluadas = _fila(con, "SELECT COUNT(*) FROM analisis")[0]
    alumnos = _fila(con, "SELECT COUNT(DISTINCT e.mail) FROM analisis a JOIN entregas e ON e.id=a.entrega_id")[0] or 1
    o += ["## Totales",
          f"- Llamadas a la IA: {t['n']} (errores: {t['err'] or 0}) · latencia media: {t['ms'] / 1000:.1f} s",
          f"- Tokens: {t['te']:,} de entrada ({t['tc']:,} desde caché) · {t['ts']:,} de salida",
          f"- Costo total: US$ {t['c']:.4f} · por entrega evaluada: US$ {t['c'] / max(evaluadas, 1):.4f} · por alumno: US$ {t['c'] / alumnos:.4f}",
          f"- Respuestas de la IA que no pasaron la validación: {t['inv']} de {t['ent']} ({100 * t['inv'] / max(t['ent'], 1):.1f} %)", ""]
    o += ["## Por modelo", "| Modelo | Llamadas | Entregas | US$ | Latencia media |", "|---|---|---|---|---|"]
    for r in con.execute("""SELECT modelo, COUNT(*) n, SUM(entregas) e, SUM(costo_usd) c, AVG(ms) ms FROM llamadas
                            WHERE error = '' OR error IS NULL GROUP BY modelo"""):
        o.append(f"| {r['modelo']} | {r['n']} | {r['e'] or 0} | {r['c'] or 0:.4f} | {(r['ms'] or 0) / 1000:.1f} s |")
    o += ["", "## Router: cómo se resolvió cada entrega", "| Camino | Entregas |", "|---|---|"]
    for r in con.execute("SELECT COALESCE(camino,'(sin dato)') camino, COUNT(*) n FROM analisis GROUP BY camino ORDER BY n DESC"):
        o.append(f"| {r['camino']} | {r['n']} |")
    o += ["", "## Por unidad", "| Unidad | Entregas | Evaluadas | Cuarentena | Revisar |", "|---|---|---|---|---|"]
    for r in con.execute("""SELECT e.unidad, COUNT(*) n, SUM(e.procesada) p, SUM(e.estado='cuarentena') q,
                            SUM(COALESCE(a.revisar,0)) rv FROM entregas e LEFT JOIN analisis a ON a.entrega_id=e.id
                            GROUP BY e.unidad"""):
        o.append(f"| {r['unidad']} | {r['n']} | {r['p']} | {r['q']} | {r['rv']} |")
    o += ["", "## Versiones del prompt usadas"]
    for r in con.execute("SELECT version_prompt, COUNT(*) n, MIN(fecha) d FROM llamadas GROUP BY version_prompt"):
        o.append(f"- {r['version_prompt']}: {r['n']} llamadas (desde {r['d'][:10]})")
    ed = con.execute("SELECT edicion_pct FROM devoluciones WHERE edicion_pct IS NOT NULL").fetchall()
    o += ["", "## Calidad: cuánto edita el docente los borradores"]
    if ed:
        vals = [r[0] for r in ed]
        sin_cambios = sum(v < 5 for v in vals)
        o += [f"- Devoluciones enviadas y comparadas: {len(vals)}",
              f"- Edición media: {sum(vals) / len(vals):.1f} % · enviadas casi sin tocar (<5 %): {sin_cambios}",
              f"- Reescritas a fondo (>50 %): {sum(v > 50 for v in vals)}"]
    else:
        o.append("- Todavía no hay devoluciones enviadas para comparar (se mide en modo real con `./tutor.sh calidad`).")
    return "\n".join(o)


def _normalizar(cuerpo: str) -> str:
    # Saca la cita del mensaje anterior y la firma de Gmail si las hubiera
    cuerpo = re.split(r"\n(El .+ escribió:|On .+ wrote:)", cuerpo)[0]
    return re.sub(r"\s+", " ", cuerpo).strip()


def porcentaje_edicion(borrador: str, enviado: str) -> float:
    """0 = se mandó tal cual; 100 = no queda nada del borrador."""
    ratio = difflib.SequenceMatcher(None, _normalizar(borrador), _normalizar(enviado)).ratio()
    return round((1 - ratio) * 100, 1)


def _cuerpo(msg) -> str:
    if msg.is_multipart():
        for parte in msg.walk():
            if parte.get_content_type() == "text/plain":
                return parte.get_payload(decode=True).decode(parte.get_content_charset() or "utf-8", "replace")
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace")


def _asunto_base(asunto: str) -> str:
    return re.sub(r"^\s*(\[REVISAR\]\s*)", "", asunto).strip().lower()


def registrar_enviados(con, mensajes) -> int:
    """mensajes: [(para, asunto, cuerpo)] de la carpeta Enviados. Empareja con los borradores y mide la edición."""
    n = 0
    for para, asunto, cuerpo in mensajes:
        for d in con.execute("SELECT id, asunto, cuerpo FROM devoluciones WHERE lower(mail)=? AND texto_enviado IS NULL",
                             (para.lower(),)).fetchall():
            if _asunto_base(d["asunto"]) == _asunto_base(asunto):
                con.execute("UPDATE devoluciones SET texto_enviado=?, edicion_pct=? WHERE id=?",
                            (cuerpo, porcentaje_edicion(d["cuerpo"], cuerpo), d["id"]))
                n += 1
                break
    con.commit()
    return n


def leer_enviados_imap(cfg, dias: int = 30) -> list:
    clave = os.environ.get("TUTOR_IMAP_PASS")
    if not (cfg.cuenta and clave):
        raise SystemExit("Falta la cuenta o TUTOR_IMAP_PASS: no puedo leer Enviados.")
    imap = imaplib.IMAP4_SSL(cfg.imap_host)
    imap.login(cfg.cuenta, clave)
    _, carpetas = imap.list()
    enviados = next((re.search(r'"([^"]+)"$', c.decode()).group(1) for c in carpetas if b"\\Sent" in c), "[Gmail]/Sent Mail")
    imap.select(f'"{enviados}"', readonly=True)  # solo lectura
    from datetime import datetime, timedelta
    desde = (datetime.now() - timedelta(days=dias)).strftime("%d-%b-%Y")
    _, ids = imap.search(None, f'(SINCE "{desde}")')
    out = []
    for i in ids[0].split():
        _, datos = imap.fetch(i, "(RFC822)")
        msg = email.message_from_bytes(datos[0][1])
        para = email.utils.parseaddr(msg.get("To", ""))[1]
        out.append((para, str(make_header(decode_header(msg.get("Subject", "")))), _cuerpo(msg)))
    imap.logout()
    return out
