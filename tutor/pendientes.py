"""Quién no entregó qué, y el recordatorio con plantilla (sin IA)."""

from datetime import datetime, timedelta

PLAZO_HORAS = 48


def faltantes(con, unidad, hoy: datetime) -> list:
    """Inscriptos de los cursos de la unidad que no entregaron una actividad que ya
    vence (pasaron PLAZO_HORAS desde la primera entrega de cualquier compañero)."""
    cursos = unidad.cursos or [r["curso"] for r in con.execute("SELECT DISTINCT curso FROM alumnos")]
    marcas = ",".join("?" * len(cursos))
    alumnos = con.execute(f"SELECT * FROM alumnos WHERE curso IN ({marcas}) ORDER BY apellido", cursos).fetchall()
    out = []
    for act, primera in con.execute(
            "SELECT actividad, MIN(marca_temporal) FROM entregas WHERE unidad=? GROUP BY actividad ORDER BY actividad",
            (unidad.codigo,)):
        if hoy - datetime.fromisoformat(primera) < timedelta(hours=PLAZO_HORAS):
            continue
        for a in alumnos:
            if not con.execute("SELECT 1 FROM entregas WHERE mail=? AND unidad=? AND actividad=?",
                               (a["mail"], unidad.codigo, act)).fetchone():
                out.append({"mail": a["mail"], "nombre": a["nombre"], "apellido": a["apellido"], "actividad": act})
    return out


def recordatorio(unidad, f) -> tuple[str, str]:
    act = unidad.actividades.get(f["actividad"])
    nombre_act = f"{f['actividad']} ({act.nombre})" if act else f["actividad"]
    asunto = f"{unidad.materia} — ¿Te doy una mano con {f['actividad']}?"
    cuerpo = (f"Hola {f['nombre']}:\n\n"
              f"Vi que todavía no subiste la actividad {nombre_act}. ¿Te trabaste en algo?\n"
              "Si querés, empezá solo por la primera parte de la consigna y lo seguimos desde ahí. "
              "Podés responder escribiendo, con una foto o con un audio en el formulario.\n\n"
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
