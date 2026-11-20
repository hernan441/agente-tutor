"""Planilla de calificaciones para el docente (sin IA): una fila por alumno, por actividad la
última nota, cuántas versiones mandó y la fecha. Celdas vacías si no entregó (nada de ceros)."""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def datos(con, unidad, aprobacion: int) -> list:
    alumnos = con.execute("SELECT * FROM alumnos ORDER BY curso, apellido, nombre").fetchall()
    filas = []
    for a in alumnos:
        fila = {"apellido": a["apellido"], "nombre": a["nombre"], "curso": a["curso"], "mail": a["mail"], "act": {}}
        for cod in unidad.actividades:
            ents = con.execute(
                """SELECT e.marca_temporal, an.nota FROM entregas e JOIN analisis an ON an.entrega_id=e.id
                   WHERE e.mail=? AND e.unidad=? AND e.actividad=? AND e.estado='ok' AND e.procesada=1
                   ORDER BY e.marca_temporal""", (a["mail"], unidad.codigo, cod)).fetchall()
            if ents:
                ult = ents[-1]
                nota = ult["nota"]
                fila["act"][cod] = {"nota": nota, "versiones": len(ents),
                                    "fecha": datetime.fromisoformat(ult["marca_temporal"]),
                                    "estado": ("entregada" if nota is None else
                                               "aprobada" if nota >= aprobacion else "en revisión")}
        filas.append(fila)
    return filas


def escribir(cfg, con, unidad) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = unidad.codigo[:31]
    acts = list(unidad.actividades)
    cab = ["Apellido", "Nombre", "Curso"]
    for cod in acts:
        sin = " (sin nota)" if cod in unidad.sin_nota else ""
        cab += [f"{cod} nota{sin}", f"{cod} versiones", f"{cod} fecha", f"{cod} estado"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    verde, amarillo = PatternFill("solid", fgColor="D9EAD3"), PatternFill("solid", fgColor="FFF2CC")
    for f in datos(con, unidad, cfg.nota_aprobacion):
        ws.append([f["apellido"], f["nombre"], f["curso"]])
        r = ws.max_row
        for i, cod in enumerate(acts):
            col = 4 + i * 4
            d = f["act"].get(cod)
            if not d:
                continue  # no entregó: celdas vacías
            if d["nota"] is not None:
                ws.cell(r, col, d["nota"]).fill = verde if d["estado"] == "aprobada" else amarillo
            ws.cell(r, col + 1, d["versiones"])
            c = ws.cell(r, col + 2, d["fecha"].date())
            c.number_format = "DD/MM/YYYY"
            ws.cell(r, col + 3, d["estado"])
    for i in range(1, len(cab) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14 if i > 3 else 16
    ws.freeze_panes = "D2"
    ruta = cfg.datos / "informes" / f"calificaciones_{unidad.codigo}.xlsx"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
    ruta.chmod(0o600)
    return ruta
