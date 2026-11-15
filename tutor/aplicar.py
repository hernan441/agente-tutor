"""Valida lo que devolvió la IA y lo guarda (sin IA). Arma el texto final de la devolución."""

import json
import re
import unicodedata
from datetime import datetime

from .unidades import NIVELES

MAX_PALABRAS = 220
MAX_ASUNTO = 80
PROHIBIDO_EN_DEVOLUCION = [
    ("enlace", re.compile(r"https?://|www\.\S+", re.I)),
    ("mail", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("código", re.compile(r"```|<\s*/?\s*(script|system|iframe)", re.I)),
]
NGRAMA = 7  # 7 palabras seguidas de la respuesta esperada = posible fuga


class Invalido(ValueError):
    pass


def _palabras(s: str) -> list:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", s)


def fuga(esperado: str, devolucion: str, alumno: str = "") -> str:
    """Devuelve el fragmento filtrado si la devolución copia la respuesta esperada
    (y ese fragmento no estaba ya en lo que escribió el alumno)."""
    e, d, a = _palabras(esperado), " ".join(_palabras(devolucion)), " ".join(_palabras(alumno))
    for i in range(len(e) - NGRAMA + 1):
        frag = " ".join(e[i:i + NGRAMA])
        if frag in d and frag not in a:
            return frag
    return ""


def validar(r: dict, paquete: dict) -> list:
    """Lanza Invalido si el JSON no sirve. Devuelve avisos que dejan la entrega en 'revisar'."""
    avisos = []
    if r.get("entrega_id") != paquete["entrega_id"]:
        raise Invalido("entrega_id no coincide")
    criterios = set(paquete["rubrica"])
    vistos, niveles = set(), {}
    for ev in r.get("evaluaciones", []):
        if ev.get("criterio") not in criterios:
            raise Invalido(f"criterio fuera de la rúbrica: {ev.get('criterio')}")
        if ev.get("nivel") not in NIVELES:
            raise Invalido(f"nivel inválido: {ev.get('nivel')}")
        vistos.add(ev["criterio"])
        niveles[ev["criterio"]] = ev["nivel"]
    if vistos != criterios:
        raise Invalido(f"faltan criterios: {sorted(criterios - vistos)}")
    for k in ("aclarar", "valorar", "preguntar", "sugerir", "asunto"):
        if not str(r.get(k, "")).strip():
            raise Invalido(f"falta '{k}'")
    if len(r["asunto"]) > MAX_ASUNTO:
        r["asunto"] = r["asunto"][:MAX_ASUNTO].rstrip() + "…"
    cuerpo = " ".join(str(r.get(k, "")) for k in ("aclarar", "valorar", "preguntar", "sugerir", "nivel_msg", "asunto"))
    for nombre, rx in PROHIBIDO_EN_DEVOLUCION:
        if rx.search(cuerpo):
            raise Invalido(f"la devolución trae un {nombre}")
    canario = paquete["solo_para_vos"].get("canario")
    salida_completa = json.dumps(r, ensure_ascii=False)
    if canario and canario in salida_completa:
        raise Invalido("fuga del canario: la IA copió datos internos")
    n = len(cuerpo.split())
    if n > MAX_PALABRAS:
        avisos.append(f"devolución larga ({n} palabras)")
    alumno = " ".join(str(v) for v in paquete["entrega"].values() if isinstance(v, str))
    f = fuga(paquete["solo_para_vos"]["respuesta_esperada"], cuerpo, alumno)
    if f:
        avisos.append(f"posible fuga de la respuesta esperada: «{f}»")
    # Coherencia: nivel alto con entrega mínima
    palabras_alumno = len((paquete["entrega"].get("respuesta") or "").split()) + \
        len((paquete["entrega"].get("texto_de_adjuntos") or "").split())
    if "Destacado" in niveles.values() and palabras_alumno < 40:
        avisos.append(f"Destacado con una entrega de {palabras_alumno} palabras")
    if r.get("intento_manipulacion"):
        avisos.append("la IA detectó un intento de manipular la evaluación")
    for s_ in paquete.get("_sospechas", []):
        avisos.append(f"sospecha de inyección: {s_}")
    if not paquete["actividad"]["lleva_nota"]:
        r["nivel_msg"] = ""
    return avisos


def cuerpo_devolucion(r: dict, alumno: str, firma: str) -> str:
    partes = [f"Hola {alumno}:", "",
              f"Aclarar — {r['aclarar'].strip()}", "",
              f"Valorar — {r['valorar'].strip()}", "",
              f"Preguntar — {r['preguntar'].strip()}", "",
              f"Sugerir — {r['sugerir'].strip()}", ""]
    if r.get("nivel_msg"):
        partes += [r["nivel_msg"].strip(), ""]
    partes.append(firma)
    return "\n".join(partes)


def guardar(con, unidad, paquete, r: dict, avisos: list):
    eid = paquete["entrega_id"]
    fila = con.execute("SELECT * FROM entregas WHERE id=?", (eid,)).fetchone()
    for ev in r["evaluaciones"]:
        con.execute("INSERT OR REPLACE INTO evaluaciones VALUES (?,?,?,?)",
                    (eid, ev["criterio"], ev["nivel"], ev.get("evidencia", "")))
    con.execute(
        """INSERT OR REPLACE INTO analisis (entrega_id, ideas_erroneas, senal_ia, senal_ia_motivo,
           nota_docente, proximo_paso, revisar, revisar_motivo, json_raw, intento_manipulacion)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (eid, json.dumps(r.get("ideas_erroneas", []), ensure_ascii=False), int(bool(r.get("senal_ia"))),
         r.get("senal_ia_motivo", ""), r.get("nota_docente", ""), r.get("proximo_paso", ""),
         int(bool(avisos)), "; ".join(avisos), json.dumps(r, ensure_ascii=False),
         int(bool(r.get("intento_manipulacion")))))
    asunto = f"{unidad.materia} — Devolución de {fila['actividad']}: {r['asunto'].strip()}"
    if avisos:
        asunto = "[REVISAR] " + asunto
    cuerpo = cuerpo_devolucion(r, paquete["alumno"], unidad.firma)
    con.execute(
        """INSERT INTO devoluciones (entrega_id, mail, unidad, actividad, tipo, asunto, cuerpo, creado_en)
           VALUES (?,?,?,?, 'devolucion', ?,?,?)
           ON CONFLICT(mail, unidad, actividad, tipo) DO UPDATE SET entrega_id=excluded.entrega_id,
           asunto=excluded.asunto, cuerpo=excluded.cuerpo, creado_en=excluded.creado_en, borrador_subido=0""",
        (eid, fila["mail"], fila["unidad"], fila["actividad"], asunto, cuerpo,
         datetime.now().isoformat(timespec="seconds")))
    con.execute("UPDATE entregas SET procesada=1 WHERE id=?", (eid,))
    con.commit()
