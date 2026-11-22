"""Lee la casilla del docente y clasifica cada mail (sin IA).

- Entra por IMAP en SOLO LECTURA (select readonly + BODY.PEEK): no marca nada como leído,
  no mueve ni borra. Tampoco puede enviar (no usa SMTP).
- Inscripción: mail del dominio del colegio, de alguien no inscripto, que dice "inscrip…".
- Entrega: mail de un alumno inscripto (o con un código de actividad en el asunto). La actividad
  sale del asunto ([BIO6-ALIM A4]), de la respuesta al mail del trabajo, o de la última
  actividad asignada a su curso.
- Todo lo demás se ignora: de los mails ajenos solo queda un hash del Message-ID, para no
  volver a mirarlos.
"""

import hashlib
import html
import imaplib
import os
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, seguridad

CODIGO_RE = re.compile(r"\[?\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\s+(A\d+)\b\]?")
INSCRIP_RE = re.compile(r"inscrib|inscrip", re.I)
CURSO_RE = re.compile(r"\b([1-7])\s*(?:[°º]|to|ro|do|er|mo|vo|no)?\s*(?:a[ñn]o\s*)?[\"'“”]?([A-Ea-e])\b")
# Marcas de la cita del mail anterior y de la firma (Gmail, Outlook, Apple, celulares)
CORTES = [
    re.compile(r"^\s*El .{5,200}escribi[óo]:\s*$", re.M | re.S),
    re.compile(r"^\s*On .{5,200}wrote:\s*$", re.M | re.S),
    re.compile(r"^\s*-{2,}\s*(Mensaje original|Original Message|Forwarded message|Mensaje reenviado)", re.M | re.I),
    re.compile(r"^\s*(De|From):\s.+\n\s*(Enviado|Sent|Fecha|Date):", re.M),
    re.compile(r"^-- \s*$", re.M),
    re.compile(r"^\s*Enviado desde mi (iPhone|celular|Android|Samsung)", re.M | re.I),
]
ETIQUETA_IA = re.compile(r"^\s*[¿]?\s*(uso de (la )?ia|usaste (la )?ia|ia|inteligencia artificial)\b[^:\n]*[:?]\s*", re.I | re.M)
ETIQUETA_AUTO = re.compile(r"^\s*[¿]?\s*(autoevaluaci[óo]n|qu[ée] entend[íi]|qu[ée] me falta|lo que entend[íi])\b[^:\n]*[:?]\s*",
                           re.I | re.M)
ETIQUETA_RESP = re.compile(r"^\s*(mi )?(trabajo|respuesta)\s*:\s*", re.I | re.M)
ETIQUETA_PREG = re.compile(r"^\s*[¿]?\s*(mi )?pregunta\b[^:\n]*[:?]\s*", re.I | re.M)
ETIQUETA_CAMBIOS = re.compile(r"^\s*[¿]?\s*(qu[ée] cambi[ée]|cambios|lo que cambi[ée])\b[^:\n]*[:?]\s*", re.I | re.M)


# ---------------- lectura del mensaje ----------------

def parsear(crudo: bytes):
    return BytesParser(policy=policy.default).parsebytes(crudo)


def clave(msg, crudo: bytes = b"") -> str:
    mid = (msg.get("Message-ID") or "").strip()
    return hashlib.sha1((mid or hashlib.sha1(crudo).hexdigest()).encode()).hexdigest()


def remitente(msg) -> tuple[str, str]:
    nombre, mail = parseaddr(str(msg.get("From", "")))
    return seguridad.limpiar_texto(nombre).strip('"'), seguridad.normalizar_mail(mail)


def fecha(msg, zona: str) -> str:
    try:
        d = parsedate_to_datetime(str(msg.get("Date")))
        if d.tzinfo:
            d = d.astimezone(ZoneInfo(zona)).replace(tzinfo=None)
    except Exception:
        d = datetime.now()
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _html_a_texto(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"(?s)<blockquote.*?</blockquote>", "\n", s)  # cita del mail anterior
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def cuerpo(msg) -> str:
    parte = msg.get_body(preferencelist=("plain",))
    if parte is not None:
        return parte.get_content()
    parte = msg.get_body(preferencelist=("html",))
    return _html_a_texto(parte.get_content()) if parte is not None else ""


def sin_cita(texto: str) -> str:
    """Saca la cita del mail anterior, las líneas con '>' y la firma."""
    corte = len(texto)
    for rx in CORTES:
        m = rx.search(texto)
        if m:
            corte = min(corte, m.start())
    texto = texto[:corte]
    texto = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith(">"))
    return texto.strip()


def separar_partes(texto: str) -> dict:
    """Divide el cuerpo en respuesta, uso de IA y autoevaluación según las etiquetas
    que pide el mail del trabajo (IA, Autoevaluación, Mi pregunta, Qué cambié). Lo que no tiene
    etiqueta es la respuesta."""
    marcas = []
    for campo, rx in (("uso_ia", ETIQUETA_IA), ("autoevaluacion", ETIQUETA_AUTO), ("respuesta", ETIQUETA_RESP),
                      ("pregunta", ETIQUETA_PREG), ("cambios", ETIQUETA_CAMBIOS)):
        for m in rx.finditer(texto):
            marcas.append((m.start(), m.end(), campo))
    marcas.sort()
    partes = {"respuesta": "", "uso_ia": "", "autoevaluacion": "", "pregunta": "", "cambios": ""}
    if not marcas:
        partes["respuesta"] = texto.strip()
        return partes
    partes["respuesta"] = texto[:marcas[0][0]].strip()
    for i, (ini, fin, campo) in enumerate(marcas):
        hasta = marcas[i + 1][0] if i + 1 < len(marcas) else len(texto)
        trozo = texto[fin:hasta].strip()
        partes[campo] = (partes[campo] + "\n" + trozo).strip() if partes[campo] else trozo
    return partes


def _nombre_archivo(n: str) -> str:
    n = os.path.basename(n or "adjunto")
    n = re.sub(r"[^\w.\-]+", "_", n, flags=re.U).strip("._") or "adjunto"
    return n[:80]


def guardar_adjuntos(msg, destino: Path, maximo: int, max_mb: float) -> tuple[list, list]:
    rutas, avisos = [], []
    for parte in msg.iter_attachments():
        if len(rutas) >= maximo:
            avisos.append(f"más de {maximo} adjuntos: se tomaron los primeros")
            break
        datos = parte.get_payload(decode=True) or b""
        if len(datos) > max_mb * 1024 * 1024:
            avisos.append(f"adjunto de más de {max_mb:g} MB descartado")
            continue
        destino.mkdir(parents=True, exist_ok=True, mode=0o700)
        p = destino / _nombre_archivo(parte.get_filename())
        p.write_bytes(datos)
        rutas.append(str(p))
    return rutas, avisos


# ---------------- inscripción ----------------

def datos_inscripcion(texto: str, nombre_remitente: str) -> dict:
    """Nombre, apellido y curso de un mail de inscripción. Lo que falta queda vacío."""
    def campo(et):
        m = re.search(rf"^\s*{et}\s*[:\-]\s*(.+)$", texto, re.I | re.M)
        return seguridad.limpiar_texto(m.group(1))[:60] if m else ""
    nombre, apellido = campo("nombres?"), campo("apellidos?")
    curso = campo("curso|divisi[óo]n|a[ñn]o")
    m = CURSO_RE.search(curso or texto)
    curso = f"{m.group(1)}{m.group(2).upper()}" if m else ""
    if nombre and not apellido and " " in nombre:
        nombre, apellido = nombre.split(" ", 1)
    if not nombre and nombre_remitente:
        partes = nombre_remitente.split()
        nombre, apellido = partes[0], apellido or " ".join(partes[1:])
    return {"nombre": nombre, "apellido": apellido, "curso": curso}


# ---------------- actividad de una entrega ----------------

def identificar_actividad(con, msg, unidades_validas: dict, curso: str, cuando: str) -> tuple[str, str, str]:
    """(unidad, actividad, cómo) o ('', '', motivo)."""
    asunto = str(msg.get("Subject", ""))
    for m in CODIGO_RE.finditer(asunto.upper()):
        u, a = m.group(1), m.group(2)
        if u in unidades_validas and a in unidades_validas[u].actividades:
            return u, a, "asunto"
    refs = " ".join(str(msg.get(h, "")) for h in ("In-Reply-To", "References"))
    for mid in re.findall(r"<[^>]+>", refs):
        r = con.execute("SELECT unidad, actividad FROM asignaciones WHERE message_id=?", (mid,)).fetchone()
        if r:
            return r["unidad"], r["actividad"], "respuesta al trabajo"
    if curso:
        r = con.execute("""SELECT unidad, actividad FROM asignaciones WHERE curso=? AND fecha<=?
                           ORDER BY fecha DESC LIMIT 1""", (curso, cuando)).fetchone()
        if r and r["unidad"] in unidades_validas:
            return r["unidad"], r["actividad"], "última actividad asignada"
    return "", "", "no se pudo saber a qué actividad corresponde"


# ---------------- clasificación ----------------

def _revisar(con, k, mail, asunto, motivo, cuando):
    con.execute("INSERT OR IGNORE INTO mails_a_revisar (clave, mail, asunto, motivo, fecha) VALUES (?,?,?,?,?)",
                (k, mail, asunto[:120], motivo, cuando))


def procesar(cfg, con, crudo: bytes, unidades_validas: dict, sync_mod) -> str:
    """Clasifica un mail y lo carga. Devuelve: inscripcion | entrega | revisar | ignorado | visto."""
    msg = parsear(crudo)
    k = clave(msg, crudo)
    if con.execute("SELECT 1 FROM buzon_vistos WHERE clave=?", (k,)).fetchone():
        return "visto"
    nombre_r, mail = remitente(msg)
    asunto = seguridad.limpiar_texto(str(msg.get("Subject", "")))
    cuando = fecha(msg, cfg.zona_horaria)
    texto = sin_cita(cuerpo(msg))
    dominio_ok = not seguridad.validar_mail(cfg, mail) if mail else False
    tiene_codigo = bool(CODIGO_RE.search(asunto.upper()))
    resultado = "ignorado"

    if not mail or mail == seguridad.normalizar_mail(cfg.cuenta):
        resultado = "ignorado"
    elif not db.inscripto(con, mail) and INSCRIP_RE.search(asunto + " " + texto[:500]) and not tiene_codigo:
        d = datos_inscripcion(texto, nombre_r)
        if dominio_ok and not (d["nombre"] and d["apellido"] and d["curso"]):
            faltan = [c for c in ("nombre", "apellido", "curso") if not d[c]]
            _revisar(con, k, mail, asunto, f"inscripción incompleta: falta {', '.join(faltan)}", cuando)
            resultado = "revisar"
        else:
            sync_mod.cargar_inscripciones(con, [{**d, "mail": mail, "marca_temporal": cuando}], cfg)
            resultado = "inscripcion"
    elif db.inscripto(con, mail) or tiene_codigo:  # un no inscripto con código queda en cuarentena
        al = con.execute("SELECT * FROM alumnos WHERE mail=?", (mail,)).fetchone()
        u, a, como = identificar_actividad(con, msg, unidades_validas, al["curso"] if al else "", cuando)
        if not u:
            if al:
                _revisar(con, k, mail, asunto, como, cuando)
                resultado = "revisar"
        else:
            partes = separar_partes(texto)
            rutas, avisos = guardar_adjuntos(msg, cfg.datos / "archivos" / k[:12], cfg.max_archivos, cfg.max_mb)
            nombre = f"{al['nombre']} {al['apellido']}" if al else (nombre_r or mail)
            sync_mod.cargar_entregas(con, [{"marca_temporal": cuando, "mail": mail, "nombre": nombre, "unidad": u,
                                            "actividad": a, **partes, "archivos": rutas,
                                            "message_id": str(msg.get("Message-ID", "")).strip(), "avisos": avisos}])
            resultado = "entrega"
    con.execute("INSERT OR IGNORE INTO buzon_vistos (clave, resultado, fecha) VALUES (?,?,?)",
                (k, resultado, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return resultado


# ---------------- origen de los mails ----------------

def mensajes_piloto(cfg):
    for p in sorted((cfg.raiz / "piloto" / "buzon").glob("*.eml")):
        yield p.read_bytes()


def _desde_imap(desde: str) -> str:
    meses = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    d = datetime.fromisoformat(desde)
    return f"{d.day:02d}-{meses[d.month - 1]}-{d.year}"


def mensajes_imap(cfg, ya_visto=lambda k: False):
    """Solo lectura: readonly + BODY.PEEK. Primero mira los encabezados y baja el mail completo
    solo si puede ser de un alumno (dominio del colegio o código de actividad en el asunto)."""
    clave_imap = os.environ.get("TUTOR_IMAP_PASS")
    if not (cfg.cuenta and clave_imap):
        raise SystemExit("Falta la cuenta en config.toml o TUTOR_IMAP_PASS en .env (./tutor.sh configurar).")
    imap = imaplib.IMAP4_SSL(cfg.imap_host)
    try:
        imap.login(cfg.cuenta, clave_imap)
        estado, _ = imap.select(f'"{cfg.carpeta}"', readonly=True)
        if estado != "OK":
            raise SystemExit(f"No pude abrir la carpeta {cfg.carpeta}")
        criterio = ["SINCE", _desde_imap(cfg.desde)] if cfg.desde else ["ALL"]
        _, datos = imap.uid("SEARCH", None, *criterio)
        uids = (datos[0] or b"").split()
        for uid in uids:
            _, enc = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])")
            if not enc or not isinstance(enc[0], tuple):
                continue
            cab = parsear(enc[0][1])
            if cab.get("Message-ID") and ya_visto(clave(cab)):
                continue
            _, mail = remitente(cab)
            asunto = str(cab.get("Subject", "")).upper()
            if seguridad.validar_mail(cfg, mail) and not CODIGO_RE.search(asunto):
                continue  # no es del colegio ni trae código: ni se baja
            _, completo = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if completo and isinstance(completo[0], tuple):
                yield completo[0][1]
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def leer(cfg, con, unidades_validas: dict, sync_mod) -> dict:
    def ya_visto(k):
        return con.execute("SELECT 1 FROM buzon_vistos WHERE clave=?", (k,)).fetchone() is not None
    origen = mensajes_piloto(cfg) if cfg.piloto else mensajes_imap(cfg, ya_visto)
    cuenta = {"inscripcion": 0, "entrega": 0, "revisar": 0, "ignorado": 0, "visto": 0}
    for crudo in origen:
        cuenta[procesar(cfg, con, crudo, unidades_validas, sync_mod)] += 1
    return cuenta
