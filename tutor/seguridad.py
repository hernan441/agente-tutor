"""Seguridad, todo por script (sin IA).

1. Solo se inscriben cuentas del colegio (dominio y, opcionalmente, nómina oficial).
2. Anti-inundación: cuarentena por no inscripto, exceso por actividad o por día, ráfagas,
   duplicados; textos recortados; topes de corrida (los aplica cli.py).
3. Prompt injection: limpieza de texto y detector de patrones antes de la IA.
"""

import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

# ---------------- 1. cuentas del colegio ----------------

MAIL_RE = re.compile(r"^[a-z0-9._%-]+@([a-z0-9-]+\.)+[a-z]{2,}$")


def normalizar_mail(m: str) -> str:
    return (m or "").strip().lower()


def cargar_nomina(cfg) -> set | None:
    if not cfg.nomina:
        return None
    p = Path(cfg.nomina)
    if not p.is_absolute():
        p = cfg.raiz / p
    with open(p, newline="", encoding="utf-8-sig") as f:
        return {normalizar_mail(c) for fila in csv.reader(f) for c in fila if "@" in c}


def validar_mail(cfg, mail: str, nomina: set | None = None) -> str:
    """'' si es válido; si no, el motivo del rechazo."""
    m = normalizar_mail(mail)
    if "+" in m.split("@")[0]:
        return "alias con '+' no permitido"
    if not MAIL_RE.match(m):
        return "mail con formato inválido"
    dominio = m.split("@", 1)[1]
    permitidos = [d.lower().lstrip("@") for d in cfg.dominios_permitidos]
    if permitidos and not any(dominio == d or dominio.endswith("." + d) for d in permitidos):
        return f"dominio {dominio} no es del colegio"
    if nomina is not None and m not in nomina:
        return "no figura en la nómina del colegio"
    return ""


def rechazar(con, mail: str, motivo: str, origen: str):
    con.execute("INSERT OR IGNORE INTO rechazos (mail, motivo, origen, fecha) VALUES (?,?,?,?)",
                (normalizar_mail(mail), motivo, origen, datetime.now().isoformat(timespec="seconds")))


# ---------------- 3. prompt injection: limpieza y detector ----------------

_INVISIBLES = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def limpiar_texto(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = _INVISIBLES.sub("", s)
    s = _CONTROL.sub(" ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()


PATRONES = [
    ("orden de ignorar instrucciones",
     r"\b(ignor|olvid|descart|omit|saltea|pasa por alto)\w*\s+"
     r"(?:(?:todo|todos|todas|las?|los|tus|sus|cualquier|lo|esas?|estas?)\s+){0,3}"
     r"(anterior|previ|instruccion|indicacion|regla|consigna|prompt|rubrica|orden)"),
    ("orden de ignorar instrucciones (en)", r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)"),
    ("cambio de rol",
     r"\b(ahora\s+)?(sos|eres|seras|actua\w*\s+como)\s+(un|una|el|la)\s+(asistente|ia|inteligencia|modelo|evaluador|corrector|profesor|tutor|sistema)"),
    ("cambio de rol (en)", r"\b(you are now|act as|pretend to be|from now on you)\b"),
    ("pedido de nota", r"\b(pone|ponga|poneme|ponme|pongan|asigna|asignale|asignar|dame|darme|calificame|calificalo|marca)\w*\s+"
                      r"(me\s+|le\s+)?(un\s+|una\s+|el\s+|la\s+|nivel\s+)?(destacado|logrado|10\b|diez|nota maxima|maxima nota)"),
    ("pedido de nota en todos los criterios", r"(destacado|logrado)\s+en\s+todos?\s+los\s+criterios"),
    ("referencia al prompt del sistema", r"system\s*prompt|prompt del sistema|developer message|jailbreak|modo desarrollador"),
    ("pide revelar lo oculto", r"\b(revela|mostra|mostrame|decime|deci|copia|imprimi|repeti)\w*\s+(me\s+)?(la\s+|las\s+|el\s+|tus\s+)?"
                               r"(respuesta esperada|instrucciones|rubrica completa|solo_para_vos|canario|prompt)"),
    ("le habla al evaluador", r"\b(querido|estimado|hola|atencion)\s+(evaluador|corrector|modelo|asistente|claude|chatgpt|gpt|ia)\b"),
    ("le habla al evaluador", r"\bsi (sos|eres) (una|un) (ia|inteligencia artificial|modelo)\b|\bif you are an? (ai|llm|language model)\b"),
    ("etiquetas de rol", r"<\s*/?\s*(system|assistant|user|instructions?|sistema)\s*>|\[/?inst\]|^\s*#{2,}\s*(instrucciones|system|sistema)\b"),
    ("bloque de código", r"```"),
    ("enlace", r"https?://|\bwww\.\S+"),
    ("bloque codificado", r"[A-Za-z0-9+/]{120,}={0,2}"),
]
_COMPILADOS = [(m, re.compile(p, re.M)) for m, p in PATRONES]


def detectar_inyeccion(texto: str) -> list:
    t = _sin_tildes(limpiar_texto(texto))
    crudo = texto or ""
    motivos = []
    for motivo, rx in _COMPILADOS:
        objetivo = crudo if motivo in ("bloque codificado",) else t
        if rx.search(objetivo) and motivo not in motivos:
            motivos.append(motivo)
    if _INVISIBLES.search(crudo):
        motivos.append("caracteres invisibles")
    return motivos


def huella(texto: str) -> str:
    palabras = re.findall(r"[a-z0-9]+", _sin_tildes(texto or ""))
    return hashlib.sha1(" ".join(palabras).encode()).hexdigest() if len(palabras) >= 8 else ""


# ---------------- 2. anti-inundación + marcado de entregas ----------------

def _flags(fila) -> list:
    return json.loads(fila["flags"] or "[]")


def _guardar(con, eid, estado=None, flags=None, motivo=None):
    if flags is not None:
        con.execute("UPDATE entregas SET flags=? WHERE id=?", (json.dumps(sorted(set(flags)), ensure_ascii=False), eid))
    if estado:
        con.execute("UPDATE entregas SET estado=?, motivo_cuarentena=? WHERE id=?", (estado, motivo, eid))


def revisar_entregas(cfg, con, unidad_codigo: str) -> dict:
    """Marca/acuartela las entregas pendientes de la unidad. Idempotente."""
    stats = {"cuarentena": 0, "sospecha_inyeccion": 0, "recortadas": 0, "texto_identico": 0}
    nomina = cargar_nomina(cfg)
    filas = con.execute(
        "SELECT * FROM entregas WHERE unidad=? AND procesada=0 AND estado != 'cuarentena' ORDER BY marca_temporal, id",
        (unidad_codigo,)).fetchall()
    for f in filas:
        eid, mail = f["id"], f["mail"]
        flags = [x for x in _flags(f) if not x.startswith("sospecha_inyeccion")]
        motivo = ""
        liberada = "liberada_por_docente" in flags
        # cuenta del colegio + inscripción
        m = validar_mail(cfg, mail, nomina)
        if m:
            motivo = m
        elif not con.execute("SELECT 1 FROM alumnos WHERE mail=?", (mail,)).fetchone():
            motivo = "no está inscripto"
        # tope de iteraciones por actividad (cuenta las anteriores, procesadas o no, fuera de cuarentena)
        if not motivo:
            n = con.execute("""SELECT COUNT(*) FROM entregas WHERE mail=? AND unidad=? AND actividad=?
                               AND estado != 'cuarentena' AND (marca_temporal < ? OR (marca_temporal = ? AND id < ?))""",
                            (mail, f["unidad"], f["actividad"], f["marca_temporal"], f["marca_temporal"], eid)).fetchone()[0]
            if n >= cfg.max_iteraciones:
                motivo = f"más de {cfg.max_iteraciones} versiones de {f['actividad']}: revisalo vos"
        # exceso por día y ráfaga
        if not motivo:
            t = datetime.fromisoformat(f["marca_temporal"])
            dia = con.execute("""SELECT COUNT(*) FROM entregas WHERE mail=? AND substr(marca_temporal,1,10)=?
                                 AND estado != 'cuarentena' AND id != ?""", (mail, f["marca_temporal"][:10], eid)).fetchone()[0]
            if dia >= cfg.max_entregas_por_alumno_dia:
                motivo = f"más de {cfg.max_entregas_por_alumno_dia} entregas en el día"
            desde = (t - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            rafaga = con.execute("""SELECT COUNT(*) FROM entregas WHERE mail=? AND marca_temporal BETWEEN ? AND ?""",
                                 (mail, desde, f["marca_temporal"])).fetchone()[0]
            if not motivo and rafaga > cfg.rafaga_por_minuto:
                motivo = f"ráfaga: {rafaga} entregas en un minuto (posible abuso)"
        # duplicado del mismo alumno
        h = huella(f["respuesta"])
        if not motivo and h:
            for otra in con.execute("""SELECT id, mail, respuesta FROM entregas WHERE unidad=? AND id != ?
                                       AND estado != 'cuarentena' AND id < ?""", (f["unidad"], eid, eid)):
                if huella(otra["respuesta"]) == h:
                    if otra["mail"] == mail:
                        motivo = "entrega duplicada"
                    else:
                        flags.append(f"texto_identico_a:{otra['mail']}")
                        stats["texto_identico"] += 1
                    break
        if motivo and not liberada:
            _guardar(con, eid, "cuarentena", flags, motivo)
            stats["cuarentena"] += 1
            continue
        # recorte de textos largos
        if len(f["respuesta"] or "") > cfg.max_caracteres_respuesta:
            con.execute("UPDATE entregas SET respuesta=? WHERE id=?",
                        (f["respuesta"][:cfg.max_caracteres_respuesta], eid))
            flags.append("respuesta_recortada")
            stats["recortadas"] += 1
        # inyección en lo que escribió el alumno
        texto = " \n".join(filter(None, (f["respuesta"], f["uso_ia"], f["autoevaluacion"], f["nombre"])))
        for motivo_iny in detectar_inyeccion(texto):
            flags.append(f"sospecha_inyeccion:{motivo_iny}")
        if any(x.startswith("sospecha_inyeccion") for x in flags):
            stats["sospecha_inyeccion"] += 1
        _guardar(con, eid, None, flags)
    con.commit()
    return stats


def liberar(con, eid: int) -> bool:
    cur = con.execute("UPDATE entregas SET estado='ok', motivo_cuarentena=NULL WHERE id=? AND estado='cuarentena'", (eid,))
    con.execute("UPDATE entregas SET flags=json_insert(COALESCE(flags,'[]'), '$[#]', 'liberada_por_docente') WHERE id=?", (eid,))
    con.commit()
    return bool(cur.rowcount)
