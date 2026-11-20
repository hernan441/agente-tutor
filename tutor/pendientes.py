"""Quién no entregó qué, y el recordatorio con plantilla (sin IA)."""

from datetime import datetime, timedelta

PLAZO_HORAS = 48


def faltantes(con, unidad, hoy: datetime) -> list:
    """Inscriptos que no entregaron una actividad que ya vence: pasaron PLAZO_HORAS desde que el
    docente la asignó a su curso (./tutor.sh asignar) o, si no se registró, desde la primera
    entrega de cualquier compañero."""
    cursos = unidad.cursos or [r["curso"] for r in con.execute("SELECT DISTINCT curso FROM alumnos")]
    marcas = ",".join("?" * len(cursos))
    alumnos = con.execute(f"SELECT * FROM alumnos WHERE curso IN ({marcas}) ORDER BY apellido", cursos).fetchall()
    inicio = {}
    for act, primera in con.execute(
            "SELECT actividad, MIN(marca_temporal) FROM entregas WHERE unidad=? GROUP BY actividad", (unidad.codigo,)):
        inicio[(act, None)] = primera
    for r in con.execute("SELECT actividad, curso, fecha FROM asignaciones WHERE unidad=?", (unidad.codigo,)):
        inicio[(r["actividad"], r["curso"])] = r["fecha"]
    out = []
    for act in sorted({a for a, _ in inicio}):
        for a in alumnos:
            desde = inicio.get((act, a["curso"])) or inicio.get((act, None))
            if not desde or hoy - datetime.fromisoformat(desde) < timedelta(hours=PLAZO_HORAS):
                continue
            if not con.execute("SELECT 1 FROM entregas WHERE mail=? AND unidad=? AND actividad=?",
                               (a["mail"], unidad.codigo, act)).fetchone():
                out.append({"mail": a["mail"], "nombre": a["nombre"], "apellido": a["apellido"], "actividad": act})
    return out


def recordatorio(unidad, f) -> tuple[str, str]:
    act = unidad.actividades.get(f["actividad"])
    nombre_act = f"{f['actividad']} ({act.nombre})" if act else f["actividad"]
    asunto = f"[{unidad.codigo} {f['actividad']}] {unidad.materia} — ¿Te doy una mano?"
    cuerpo = (f"Hola {f['nombre']}:\n\n"
              f"Vi que todavía no me mandaste la actividad {nombre_act}. ¿Te trabaste en algo?\n"
              "Si querés, empezá solo por la primera parte de la consigna y lo seguimos desde ahí. "
              "Respondé este mail con lo que tengas: texto, una foto o un audio.\n\n"
              f"{unidad.firma}")
    return asunto, cuerpo


def generar(con, unidad, hoy: datetime) -> list:
    hechos = []
    for f in faltantes(con, unidad, hoy):
        asunto, cuerpo = recordatorio(unidad, f)
        cur = con.execute(
            """INSERT OR IGNORE INTO devoluciones (mail, unidad, actividad, tipo, asunto, cuerpo, creado_en)
               VALUES (?,?,?, 'recordatorio', ?,?,?)""",
            (f["mail"], unidad.codigo, f["actividad"], asunto, cuerpo, hoy.isoformat(timespec="seconds")))
        f["nuevo"] = bool(cur.rowcount)
        hechos.append(f)
    con.commit()
    return hechos
