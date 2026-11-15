"""Capa 9 — Evaluación del agente contra el docente.

evals/casos.csv tiene entregas (ficticias o reales anonimizadas) con los niveles que puso el
docente a mano (columnas docente_C1, docente_C2, ...). Este módulo corre el agente sobre esos
casos en una base temporal (sin borradores, sin caché) y mide cuánto coincide con el docente.
"""

import csv
import json
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import aplicar, db, ia, motor, unidades
from .unidades import NIVELES

ORDEN = {n: i for i, n in enumerate(NIVELES)}


def leer_casos(ruta: Path) -> list:
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if any(k.startswith("docente_") and v.strip() for k, v in r.items())]


def comparar(casos: list, evaluaciones: dict) -> dict:
    """evaluaciones: {caso_id: {criterio: nivel}}. Devuelve métricas de acuerdo."""
    exactas = cerca = total = 0
    por_criterio = defaultdict(lambda: [0, 0])
    confusion = Counter()
    sobre = bajo = 0
    for c in casos:
        ag = evaluaciones.get(c["caso_id"], {})
        for k, v in c.items():
            if not k.startswith("docente_") or not v.strip():
                continue
            crit = k.split("_", 1)[1]
            doc = v.strip()
            if doc not in ORDEN or crit not in ag:
                continue
            a = ag[crit]
            total += 1
            dist = ORDEN[a] - ORDEN[doc]
            exactas += dist == 0
            cerca += abs(dist) <= 1
            sobre += dist > 0
            bajo += dist < 0
            por_criterio[crit][0] += dist == 0
            por_criterio[crit][1] += 1
            confusion[(doc, a)] += 1
    return {"total": total, "exactas": exactas, "a_un_nivel": cerca, "sobrevalora": sobre, "subvalora": bajo,
            "por_criterio": {k: v for k, v in sorted(por_criterio.items())}, "confusion": confusion}


def correr(cfg, ruta_casos: Path, modelo: str | None = None) -> Path:
    casos = leer_casos(ruta_casos)
    if not casos:
        raise SystemExit(f"{ruta_casos} no tiene casos con niveles del docente cargados (columnas docente_C1...).")
    tmp = Path(tempfile.mkdtemp(prefix="eval_"))
    cfg_eval = cfg
    original_datos = cfg.datos
    cfg_eval.datos = tmp
    if modelo:
        cfg_eval.modelo_rapido = modelo
        cfg_eval.modelo_fuerte = modelo
    con = db.conectar(tmp / "eval.db")
    mapa = {}
    for c in casos:
        mail = f"caso{c['caso_id']}@eval.local"
        con.execute("INSERT OR IGNORE INTO alumnos VALUES (?,?,?,?,?)", (mail, c.get("nombre") or "Alumno", "Eval", "eval", "2026-01-01"))
        cur = con.execute("""INSERT INTO entregas (marca_temporal, mail, nombre, unidad, actividad, respuesta, uso_ia, autoevaluacion)
                             VALUES (?,?,?,?,?,?,?,?)""",
                          (f"2026-01-01 00:00:{int(c['caso_id']) % 60:02d}", mail, c.get("nombre") or "Alumno",
                           c["unidad"], c["actividad"], c.get("respuesta", ""), c.get("uso_ia", ""), c.get("autoevaluacion", "")))
        mapa[cur.lastrowid] = c["caso_id"]
    con.commit()
    resultados, uso_total, filas_md = {}, {"costo": 0.0}, []
    for codigo in sorted({c["unidad"] for c in casos}):
        u = unidades.buscar(cfg.unidades, codigo)
        filas = con.execute("SELECT * FROM entregas WHERE unidad=?", (codigo,)).fetchall()
        r = motor.evaluar_entregas(cfg_eval, con, u, filas, "eval-" + datetime.now().isoformat(timespec="seconds"),
                                   guardar_cache=False)
        uso_total["costo"] += r["uso"]["costo"]
    for eid, caso in mapa.items():
        resultados[caso] = {e["criterio"]: e["nivel"] for e in con.execute(
            "SELECT criterio, nivel FROM evaluaciones WHERE entrega_id=?", (eid,))}
    revisar = con.execute("SELECT COUNT(*) FROM analisis WHERE revisar=1").fetchone()[0]
    fugas = con.execute("SELECT COUNT(*) FROM analisis WHERE revisar_motivo LIKE '%fuga%'").fetchone()[0]
    m = comparar(casos, resultados)
    cfg.datos = original_datos
    version = ia.version_prompt(cfg)
    pct = lambda a, b: f"{100 * a / b:.0f} %" if b else "—"
    o = [f"# Evaluación del agente contra el docente — {datetime.now():%d/%m/%Y %H:%M}", "",
         f"- Casos: {len(casos)} · juicios comparados (caso × criterio): {m['total']}",
         f"- Modelo rápido / fuerte: {cfg_eval.modelo_rapido} / {cfg_eval.modelo_fuerte} · prompt {version}",
         f"- **Coincidencia exacta con el docente: {pct(m['exactas'], m['total'])}**",
         f"- A un nivel de distancia o menos: {pct(m['a_un_nivel'], m['total'])}",
         f"- El agente sobrevalora en {m['sobrevalora']} juicios y subvalora en {m['subvalora']}",
         f"- Devoluciones marcadas para revisar: {revisar} · posibles fugas de la respuesta: {fugas}",
         f"- Costo: US$ {uso_total['costo']:.4f}", "",
         "## Por criterio", "| Criterio | Coincidencia exacta |", "|---|---|"]
    o += [f"| {k} | {pct(a, n)} ({a}/{n}) |" for k, (a, n) in m["por_criterio"].items()]
    o += ["", "## Matriz de confusión (filas: docente · columnas: agente)", "| | " + " | ".join(NIVELES) + " |",
          "|---|" + "---|" * len(NIVELES)]
    for d in NIVELES:
        o.append(f"| **{d}** | " + " | ".join(str(m["confusion"].get((d, a), 0)) for a in NIVELES) + " |")
    o += ["", "## Detalle por caso", "| Caso | Criterio | Docente | Agente |", "|---|---|---|---|"]
    for c in casos:
        for k, v in c.items():
            if k.startswith("docente_") and v.strip():
                crit = k.split("_", 1)[1]
                ag = resultados.get(c["caso_id"], {}).get(crit, "—")
                marca = "" if ag == v.strip() else " ⚠"
                o.append(f"| {c['caso_id']} | {crit} | {v.strip()} | {ag}{marca} |")
    salida = cfg.raiz / "evals" / "resultados" / f"{datetime.now():%Y-%m-%d_%H%M}_{(modelo or cfg_eval.modelo_rapido)}_{version}.md"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(o), encoding="utf-8")
    return salida
