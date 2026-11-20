"""Legajos e informe al docente, armados desde la base (sin IA, salvo 1 línea + sugerencias)."""

import json
from collections import Counter
from pathlib import Path

ORDEN = {"Inicial": 0, "En desarrollo": 1, "Logrado": 2, "Destacado": 3}


def legajo(con, mail: str) -> str:
    a = con.execute("SELECT * FROM alumnos WHERE mail=?", (mail,)).fetchone()
    nombre = f"{a['nombre']} {a['apellido']} — {a['curso']}" if a else f"{mail} (SIN INSCRIPCIÓN)"
    out = [f"# Legajo — {nombre}", f"Mail: {mail}", ""]
    out += ["## Estado por criterio", "| Unidad | Criterio | Nivel actual | Tendencia | Última evidencia |", "|---|---|---|---|---|"]
    filas = con.execute(
        """SELECT e.unidad, ev.criterio, ev.nivel, ev.evidencia FROM evaluaciones ev JOIN entregas e ON e.id=ev.entrega_id
           WHERE e.mail=? ORDER BY e.unidad, ev.criterio, e.marca_temporal""", (mail,)).fetchall()
    hist = {}
    for f in filas:
        hist.setdefault((f["unidad"], f["criterio"]), []).append(f)
    for (u, c), fs in hist.items():
        tend = "nuevo"
        if len(fs) > 1:
            d = ORDEN[fs[-1]["nivel"]] - ORDEN[fs[-2]["nivel"]]
            tend = "↑" if d > 0 else "↓" if d < 0 else "="
        out.append(f"| {u} | {c} | {fs[-1]['nivel']} | {tend} | \"{(fs[-1]['evidencia'] or '')[:80]}\" |")
    out += ["", "## Historial de entregas"]
    for e in con.execute(
            """SELECT e.*, an.ideas_erroneas, an.senal_ia, an.senal_ia_motivo, an.proximo_paso, an.nota
               FROM entregas e LEFT JOIN analisis an ON an.entrega_id=e.id WHERE e.mail=? ORDER BY e.marca_temporal""",
            (mail,)):
        niveles = ", ".join(f"{r['criterio']} {r['nivel']}" for r in con.execute(
            "SELECT * FROM evaluaciones WHERE entrega_id=? ORDER BY criterio", (e["id"],)))
        out += [f"### {e['marca_temporal'][:16]} — {e['unidad']} {e['actividad']}",
                f"- Niveles: {niveles or '(sin procesar)'}" + (f" · Nota: {e['nota']}/10" if e["nota"] is not None else ""),
                f"- IA declarada: {e['uso_ia'] or '(vacío)'}",
                f"- Ideas erróneas: {', '.join(json.loads(e['ideas_erroneas'] or '[]')) or 'ninguna'}",
                f"- Señal de copia acrítica: {'SÍ — ' + (e['senal_ia_motivo'] or '') if e['senal_ia'] else 'no'}",
                f"- Próximo paso propuesto: {e['proximo_paso'] or '—'}", ""]
    return "\n".join(out)


def escribir_legajos(cfg, con) -> int:
    d = cfg.datos / "legajos"
    d.mkdir(parents=True, exist_ok=True)
    mails = {r[0] for r in con.execute("SELECT mail FROM alumnos UNION SELECT mail FROM entregas")}
    for m in mails:
        (d / f"{m}.md").write_text(legajo(con, m), encoding="utf-8")
    return len(mails)


def resumen(con, unidad, faltan: list) -> dict:
    filas = con.execute(
        """SELECT e.id, e.mail, e.nombre, e.actividad, an.senal_ia, an.senal_ia_motivo, an.ideas_erroneas,
                  an.nota_docente, an.revisar, an.revisar_motivo, an.nota,
                  (SELECT 1 FROM alumnos al WHERE al.mail=e.mail) AS inscripto
           FROM entregas e LEFT JOIN analisis an ON an.entrega_id=e.id
           WHERE e.unidad=? AND e.estado='ok' ORDER BY e.actividad, e.nombre""", (unidad.codigo,)).fetchall()
    tabla, ideas, alertas = [], Counter(), []
    quien_idea = {}
    for f in filas:
        ev = {r["criterio"]: r["nivel"] for r in con.execute("SELECT * FROM evaluaciones WHERE entrega_id=?", (f["id"],))}
        tabla.append({"alumno": f["nombre"] or f["mail"], "actividad": f["actividad"], "niveles": ev,
                      "senal_ia": bool(f["senal_ia"]), "inscripto": bool(f["inscripto"]), "nota": f["nota"]})
        for i in json.loads(f["ideas_erroneas"] or "[]"):
            ideas[i] += 1
            quien_idea.setdefault(i, []).append((f["nombre"] or f["mail"]).split()[0])
        if f["senal_ia"]:
            alertas.append(f"{f['nombre']} ({f['actividad']}): señal de copia acrítica — {f['senal_ia_motivo']}")
        if not f["inscripto"]:
            alertas.append(f"{f['mail']} entregó {f['actividad']} sin estar inscripto (liberada por el docente)")
    notas = [f"{f['nombre']} {f['actividad']}: {f['nota_docente']}" for f in filas if f["nota_docente"]]
    return {"unidad": unidad.codigo, "titulo": unidad.titulo, "tabla": tabla, "iteraciones": iteraciones(con, unidad),
            "ideas": [(i, n, sorted(set(quien_idea[i]))) for i, n in ideas.most_common()],
            "alertas": alertas, "no_entregaron": [f"{x['nombre']} {x['apellido']} ({x['actividad']})" for x in faltan],
            "notas": notas}


def iteraciones(con, unidad, aprobacion: int = 7, maximo: int = 6) -> dict:
    """Por alumno y actividad con nota: aprobó o sigue iterando (última versión evaluada)."""
    aprobados, iterando = [], []
    for r in con.execute(
            """SELECT e.mail, e.nombre, e.actividad, COUNT(*) n,
                      (SELECT an2.nota FROM entregas e2 JOIN analisis an2 ON an2.entrega_id=e2.id
                       WHERE e2.mail=e.mail AND e2.unidad=e.unidad AND e2.actividad=e.actividad AND e2.estado='ok'
                       ORDER BY e2.marca_temporal DESC, e2.id DESC LIMIT 1) ultima
               FROM entregas e JOIN analisis an ON an.entrega_id=e.id
               WHERE e.unidad=? AND e.estado='ok' GROUP BY e.mail, e.actividad ORDER BY e.actividad, e.nombre""",
            (unidad.codigo,)):
        if r["ultima"] is None:
            continue
        txt = f"{r['nombre']} {r['actividad']}: {r['ultima']}/10 (versión {r['n']})"
        (aprobados if r["ultima"] >= aprobacion else iterando).append(txt + ("" if r["ultima"] >= aprobacion else f" de {maximo}"))
    return {"aprobados": aprobados, "iterando": iterando}


def mails_a_revisar(con) -> list:
    return [f"{r['fecha'][:16]} {r['mail']} — «{r['asunto']}»: {r['motivo']}"
            for r in con.execute("SELECT * FROM mails_a_revisar WHERE resuelto=0 ORDER BY fecha")]


def resumen_seguridad(con, unidad, uso, cfg, corte_tope) -> dict:
    rech = [f"{r['mail']}: {r['motivo']}" for r in con.execute(
        "SELECT mail, motivo FROM rechazos WHERE origen='inscripcion' ORDER BY fecha")]
    cua = [f"#{r['id']} {r['mail']} {r['actividad']}: {r['motivo_cuarentena']}" for r in con.execute(
        "SELECT id, mail, actividad, motivo_cuarentena FROM entregas WHERE unidad=? AND estado='cuarentena'",
        (unidad.codigo,))]
    banderas = []
    for r in con.execute("SELECT nombre, actividad, flags FROM entregas WHERE unidad=? AND estado='ok'", (unidad.codigo,)):
        for f in json.loads(r["flags"] or "[]"):
            if not f.startswith("sospecha_inyeccion") and f != "liberada_por_docente":
                banderas.append(f"{r['nombre']} {r['actividad']}: {f}")
    revisar = [f"{r['nombre']} {r['actividad']}: {r['revisar_motivo']}" for r in con.execute(
        """SELECT e.nombre, e.actividad, an.revisar_motivo FROM analisis an JOIN entregas e ON e.id=an.entrega_id
           WHERE e.unidad=? AND an.revisar=1""", (unidad.codigo,))]
    return {"rechazos": rech, "cuarentena": cua, "banderas": banderas, "revisar": revisar,
            "gasto": f"US$ {uso.get('costo', 0):.4f} de un tope de US$ {cfg.tope_usd_corrida:.2f} por corrida"
                     + (" — SE CORTÓ POR TOPE" if corte_tope else "")}


def escribir_informe(cfg, unidad, res: dict, sintesis: dict, hoy, uso: dict) -> Path:
    criterios = sorted(unidad.rubrica)
    o = [f"# Informe al docente — {unidad.codigo} — {hoy:%d/%m/%Y}", "",
         "## En una línea", sintesis.get("en_una_linea", "—"), ""]
    seg = res.get("seguridad", {})
    if seg.get("revisar"):
        o += ["## ⚠ Revisar antes de enviar"] + [f"- {x}" for x in seg["revisar"]] + [""]
    it = res.get("iteraciones") or {}
    if it:
        o += ["## Notas e iteraciones",
              "**Aprobaron:** " + ("; ".join(it["aprobados"]) or "nadie todavía"), "",
              "**Siguen iterando (devolución con nota menor a la de aprobación):** " + ("; ".join(it["iterando"]) or "nadie"), ""]
    if res.get("mails_a_revisar"):
        o += ["## Mails a revisar (no se procesaron)"] + [f"- {x}" for x in res["mails_a_revisar"]] + [""]
    o += [
         "## Entregas", "| Alumno | Actividad | " + " | ".join(criterios) + " | Nota | Señal IA |",
         "|---|---|" + "---|" * len(criterios) + "---|---|"]
    for t in res["tabla"]:
        celdas = [t["niveles"].get(c, "—") for c in criterios]
        nombre = t["alumno"] + ("" if t["inscripto"] else " ⚠ sin inscripción")
        nota = "" if t.get("nota") is None else str(t["nota"])
        o.append(f"| {nombre} | {t['actividad']} | " + " | ".join(celdas) + f" | {nota} | {'**sí**' if t['senal_ia'] else 'no'} |")
    o += ["", f"**No entregaron (recordatorio armado):** {', '.join(res['no_entregaron']) or 'nadie'}", "",
          "## Ideas erróneas más repetidas"]
    o += [f"{k}. {i} — {n} ({', '.join(q)})" for k, (i, n, q) in enumerate(res["ideas"][:6], 1)] or ["Ninguna."]
    o += ["", "## Alertas"] + ([f"- {a}" for a in res["alertas"]] or ["- Ninguna."])
    o += ["", "## Sugerencias para la próxima clase"] + [f"- {s}" for s in sintesis.get("sugerencias", [])][:3]
    if seg:
        o += ["", "## Seguridad",
              "**Inscripciones rechazadas:** " + ("; ".join(seg["rechazos"]) or "ninguna"),
              "", "**En cuarentena (no pasaron por la IA):** " + ("; ".join(seg["cuarentena"]) or "ninguna"),
              "", "**Otras marcas:** " + ("; ".join(seg["banderas"]) or "ninguna"),
              "", f"**Gasto de IA:** {seg['gasto']}"]
    o += ["", "## Pendiente de revisión del docente",
          f"Devoluciones y recordatorios: `borradores/{unidad.codigo}/`. Ninguno fue enviado.", "",
          f"_Cómo se resolvió cada entrega: {', '.join(f'{k}: {v}' for k, v in (uso.get('caminos') or {}).items()) or '—'} "
          "(plantilla y caché = sin IA; rápido = modelo chico; fuerte = modelo grande)._", "",
          f"_Uso de IA en esta corrida: {uso.get('llamadas', 0)} llamadas, {uso.get('entrada', 0):,} tokens de entrada, "
          f"{uso.get('salida', 0):,} de salida, US$ {uso.get('costo', 0):.4f}._"]
    ruta = cfg.datos / "informes" / f"{unidad.codigo}_{hoy:%Y-%m-%d}.md"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(o), encoding="utf-8")
    return ruta
