"""Propuesta de grupos para la próxima clase (sin IA).

Dos tipos, para que el tiempo de clase sea trabajo entre personas:
- grupos de discusión: alumnos que comparten la misma idea errónea, para trabajarla juntos;
- grupos mixtos: sobre el criterio más flojo del curso, cada grupo mezcla a quien ya lo domina
  con quien todavía no (reparto en "serpiente": 1-2-3-3-2-1…).
Solo se usa la última versión evaluada de cada alumno.
"""

import json

ORDEN = {"Inicial": 0, "En desarrollo": 1, "Logrado": 2, "Destacado": 3}


def ultimas(con, unidad) -> list:
    """Última versión evaluada por alumno y actividad: {mail, nombre, actividad, niveles, ideas}."""
    out = []
    for r in con.execute(
            """SELECT e.id, e.mail, e.nombre, e.actividad, an.ideas_erroneas FROM entregas e
               JOIN analisis an ON an.entrega_id=e.id
               WHERE e.unidad=? AND e.estado='ok' AND e.id = (
                 SELECT e2.id FROM entregas e2 JOIN analisis a2 ON a2.entrega_id=e2.id
                 WHERE e2.mail=e.mail AND e2.unidad=e.unidad AND e2.actividad=e.actividad AND e2.estado='ok'
                 ORDER BY e2.marca_temporal DESC, e2.id DESC LIMIT 1)""", (unidad.codigo,)):
        niveles = {x["criterio"]: x["nivel"] for x in con.execute(
            "SELECT criterio, nivel FROM evaluaciones WHERE entrega_id=?", (r["id"],))}
        out.append({"mail": r["mail"], "nombre": r["nombre"] or r["mail"], "actividad": r["actividad"],
                    "niveles": niveles, "ideas": json.loads(r["ideas_erroneas"] or "[]")})
    return out


def serpiente(ordenados: list, tamano: int) -> list:
    """Reparte de mayor a menor en ida y vuelta: cada grupo queda con fuertes y flojos."""
    n = max(1, round(len(ordenados) / tamano))
    grupos = [[] for _ in range(n)]
    for i, x in enumerate(ordenados):
        vuelta, pos = divmod(i, n)
        grupos[pos if vuelta % 2 == 0 else n - 1 - pos].append(x)
    return [g for g in grupos if g]


def proponer(filas: list, tamano: int = 4) -> dict:
    """filas: salida de ultimas(). Devuelve {criterio_flojo, mixtos: [[nombres]], discusion: [(idea, [nombres])]}."""
    out = {"actividad": "", "criterio_flojo": "", "mixtos": [], "discusion": []}
    if not filas:
        return out
    # La actividad más reciente con más entregas evaluadas y criterios con nota
    por_act = {}
    for f in filas:
        por_act.setdefault(f["actividad"], []).append(f)
    act = max(por_act, key=lambda a: (len(por_act[a][0]["niveles"]), len(por_act[a]), a))
    grupo = por_act[act]
    out["actividad"] = act
    criterios = sorted({c for f in grupo for c in f["niveles"]})
    if len(grupo) >= 2 and criterios:
        flojo = min(criterios, key=lambda c: (sum(ORDEN.get(f["niveles"].get(c), 0) for f in grupo) / len(grupo), c))
        out["criterio_flojo"] = flojo
        orden = sorted(grupo, key=lambda f: (-ORDEN.get(f["niveles"].get(flojo), 0), f["nombre"]))
        out["mixtos"] = [[f"{f['nombre']} ({f['niveles'].get(flojo, '—')})" for f in g] for g in serpiente(orden, tamano)]
    ideas = {}
    for f in filas:
        for i in f["ideas"]:
            ideas.setdefault(i, set()).add(f["nombre"])
    out["discusion"] = sorted(((i, sorted(n)) for i, n in ideas.items() if len(n) >= 2), key=lambda x: -len(x[1]))[:4]
    return out
