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
            """SELECT e.*, an.ideas_erroneas, an.senal_ia, an.senal_ia_motivo, an.proximo_paso
               FROM entregas e LEFT JOIN analisis an ON an.entrega_id=e.id WHERE e.mail=? ORDER BY e.marca_temporal""",
            (mail,)):
        niveles = ", ".join(f"{r['criterio']} {r['nivel']}" for r in con.execute(
            "SELECT * FROM evaluaciones WHERE entrega_id=? ORDER BY criterio", (e["id"],)))
        out += [f"### {e['marca_temporal'][:16]} — {e['unidad']} {e['actividad']}",
                f"- Niveles: {niveles or '(sin procesar)'}",
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
                  an.nota_docente, an.revisar, an.revisar_motivo,
                  (SELECT 1 FROM alumnos al WHERE al.mail=e.mail) AS inscripto
           FROM entregas e LEFT JOIN analisis an ON an.entrega_id=e.id
           WHERE e.unidad=? ORDER BY e.actividad, e.nombre""", (unidad.codigo,)).fetchall()
    tabla, ideas, alertas = [], Counter(), []
    quien_idea = {}
    for f in filas:
        ev = {r["criterio"]: r["nivel"] for r in con.execute("SELECT * FROM evaluaciones WHERE entrega_id=?", (f["id"],))}
        tabla.append({"alumno": f["nombre"] or f["mail"], "actividad": f["actividad"], "niveles": ev,
                      "senal_ia": bool(f["senal_ia"]), "inscripto": bool(f["inscripto"])})
        for i in json.loads(f["ideas_erroneas"] or "[]"):
            ideas[i] += 1
            quien_idea.setdefault(i, []).append((f["nombre"] or f["mail"]).split()[0])
        if f["senal_ia"]:
            alertas.append(f"{f['nombre']} ({f['actividad']}): señal de copia acrítica — {f['senal_ia_motivo']}")
        if f["revisar"]:
            alertas.append(f"{f['nombre']} ({f['actividad']}): revisar la devolución antes de enviarla — {f['revisar_motivo']}")
        if not f["inscripto"]:
            alertas.append(f"{f['mail']} entregó {f['actividad']} sin estar inscripto")
    notas = [f"{f['nombre']} {f['actividad']}: {f['nota_docente']}" for f in filas if f["nota_docente"]]
    return {"unidad": unidad.codigo, "titulo": unidad.titulo, "tabla": tabla,
            "ideas": [(i, n, sorted(set(quien_idea[i]))) for i, n in ideas.most_common()],
            "alertas": alertas, "no_entregaron": [f"{x['nombre']} {x['apellido']} ({x['actividad']})" for x in faltan],
            "notas": notas}


def escribir_informe(cfg, unidad, res: dict, sintesis: dict, hoy, uso: dict) -> Path:
    criterios = sorted(unidad.rubrica)
    o = [f"# Informe al docente — {unidad.codigo} — {hoy:%d/%m/%Y}", "",
         "## En una línea", sintesis.get("en_una_linea", "—"), "",
         "## Entregas", "| Alumno | Actividad | " + " | ".join(criterios) + " | Señal IA |",
         "|---|---|" + "---|" * len(criterios) + "---|"]
    for t in res["tabla"]:
        celdas = [t["niveles"].get(c, "—") for c in criterios]
        nombre = t["alumno"] + ("" if t["inscripto"] else " ⚠ sin inscripción")
        o.append(f"| {nombre} | {t['actividad']} | " + " | ".join(celdas) + f" | {'**sí**' if t['senal_ia'] else 'no'} |")
    o += ["", f"**No entregaron (recordatorio armado):** {', '.join(res['no_entregaron']) or 'nadie'}", "",
          "## Ideas erróneas más repetidas"]
    o += [f"{k}. {i} — {n} ({', '.join(q)})" for k, (i, n, q) in enumerate(res["ideas"][:6], 1)] or ["Ninguna."]
    o += ["", "## Alertas"] + ([f"- {a}" for a in res["alertas"]] or ["- Ninguna."])
    o += ["", "## Sugerencias para la próxima clase"] + [f"- {s}" for s in sintesis.get("sugerencias", [])][:3]
    o += ["", "## Pendiente de revisión del docente",
          f"Devoluciones y recordatorios: `borradores/{unidad.codigo}/`. Ninguno fue enviado.", "",
          f"_Uso de IA en esta corrida: {uso.get('llamadas', 0)} llamadas, {uso.get('entrada', 0):,} tokens de entrada, "
          f"{uso.get('salida', 0):,} de salida, US$ {uso.get('costo', 0):.4f}._"]
    ruta = cfg.datos / "informes" / f"{unidad.codigo}_{hoy:%Y-%m-%d}.md"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(o), encoding="utf-8")
    return ruta
