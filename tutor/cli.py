"""Línea de comandos del tutor.

  ./tutor sync                     trae inscripciones y entregas (rclone o piloto)
  ./tutor procesar UNIDAD          todo el circuito: adjuntos → IA → borradores → informe
  ./tutor pendientes UNIDAD        quién no entregó (sin IA)
  ./tutor estado                   inscriptos por curso, entregas, sin inscripción
  ./tutor unidades                 lista las unidades cargadas

Opciones: --piloto (datos de prueba, sin Google ni IMAP) · --hoy AAAA-MM-DD · --sin-ia
"""

import argparse
import os
from datetime import datetime
from itertools import groupby

from . import adjuntos, aplicar, config, correo, db, ia, informe, lote, pendientes, sync, unidades


def _env(cfg):
    p = cfg.raiz / ".env"
    if p.exists():
        for linea in p.read_text().splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def cmd_sync(cfg, con, a):
    print(sync.sincronizar(cfg, con))


def cmd_estado(cfg, con, a):
    print("Inscriptos por curso:")
    for r in con.execute("SELECT curso, COUNT(*) n FROM alumnos GROUP BY curso"):
        print(f"  {r['curso']}: {r['n']}")
    for r in con.execute("""SELECT unidad, actividad, COUNT(*) n, SUM(procesada) p FROM entregas
                            GROUP BY unidad, actividad"""):
        print(f"  {r['unidad']} {r['actividad']}: {r['n']} entregas ({r['p']} procesadas)")
    sin = con.execute("SELECT DISTINCT mail FROM entregas WHERE mail NOT IN (SELECT mail FROM alumnos)").fetchall()
    if sin:
        print("Entregas SIN inscripción:", ", ".join(r[0] for r in sin))


def cmd_unidades(cfg, con, a):
    for u in unidades.todas(cfg.unidades).values():
        print(f"{u.codigo}: {u.titulo} — {u.materia} — cursos {', '.join(u.cursos)} — actividades {', '.join(u.actividades)}")


def cmd_pendientes(cfg, con, a):
    u = unidades.buscar(cfg.unidades, a.unidad)
    for f in pendientes.faltantes(con, u, a.hoy):
        print(f"{f['apellido']}, {f['nombre']} — falta {f['actividad']}")


def cmd_procesar(cfg, con, a):
    u = unidades.buscar(cfg.unidades, a.unidad)
    inicio = datetime.now()
    if not a.sin_sync:
        print("sync:", sync.sincronizar(cfg, con))
    filas = lote.pendientes(con, u)
    print(f"{len(filas)} entregas nuevas de {u.codigo}")
    st = adjuntos.procesar_entregas(cfg, con, [f["id"] for f in filas], f"{u.materia}. {u.titulo}")
    print("adjuntos (sin IA):", st)

    uso = {"llamadas": 0, "entrada": 0, "salida": 0, "costo": 0.0}
    procesadas, fallidas = 0, []
    if not a.sin_ia:
        # Por actividad, en orden: así A4 ya ve lo evaluado en A1.
        for act, grupo in groupby(sorted(filas, key=lambda f: (f["actividad"], f["marca_temporal"])),
                                  key=lambda f: f["actividad"]):
            grupo = list(grupo)
            for i in range(0, len(grupo), cfg.lote_max):
                tanda = [lote.paquete(con, u, con.execute("SELECT * FROM entregas WHERE id=?", (f["id"],)).fetchone())
                         for f in grupo[i:i + cfg.lote_max]]
                tanda = [p for p in tanda if p]
                if not tanda:
                    continue
                pendientes_tanda = tanda
                for intento in range(2):
                    try:
                        res, us = ia.evaluar(cfg, pendientes_tanda)
                    except Exception as exc:
                        print(f"  IA falló ({exc}); reintento" if intento == 0 else f"  IA falló: {exc}")
                        continue
                    uso["llamadas"] += 1
                    for k in ("entrada", "salida", "costo"):
                        uso[k] += us[k]
                    por_id = {r.get("entrega_id"): r for r in res if isinstance(r, dict)}
                    reintentar = []
                    for p in pendientes_tanda:
                        r = por_id.get(p["entrega_id"])
                        try:
                            if r is None:
                                raise aplicar.Invalido("sin respuesta")
                            avisos = aplicar.validar(r, p)
                            aplicar.guardar(con, u, p, r, avisos)
                            procesadas += 1
                        except aplicar.Invalido as exc:
                            reintentar.append(p)
                            if intento:
                                fallidas.append((p["entrega_id"], str(exc)))
                    if not reintentar:
                        break
                    pendientes_tanda = reintentar
        print(f"IA: {procesadas} evaluadas, {len(fallidas)} fallidas {fallidas or ''}")

    faltan = pendientes.generar(con, u, a.hoy)
    print(f"recordatorios (sin IA): {len(faltan)}")
    for d in con.execute("SELECT id FROM devoluciones WHERE unidad=? AND borrador_subido=0", (u.codigo,)).fetchall():
        correo.escribir_eml(cfg, con, d["id"])
    n_leg = informe.escribir_legajos(cfg, con)
    res = informe.resumen(con, u, faltan)
    sintesis = {"en_una_linea": "Sin entregas nuevas para evaluar.", "sugerencias": []}
    if any(t["niveles"] for t in res["tabla"]) and not a.sin_ia:
        try:
            sintesis, us = ia.sintesis_informe(cfg, {k: res[k] for k in ("titulo", "ideas", "alertas", "no_entregaron", "notas")})
            uso["llamadas"] += 1
            for k in ("entrada", "salida", "costo"):
                uso[k] += us[k]
        except Exception as exc:
            sintesis = {"en_una_linea": f"(No se pudo generar la síntesis: {exc})", "sugerencias": []}
    ruta = informe.escribir_informe(cfg, u, res, sintesis, a.hoy, uso)
    con.execute("""INSERT INTO corridas (inicio, fin, unidad, entregas, llamadas_ia, tokens_entrada, tokens_salida, costo_usd)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (inicio.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"), u.codigo,
                 len(filas), uso["llamadas"], uso["entrada"], uso["salida"], uso["costo"]))
    con.commit()
    print(f"legajos: {n_leg} · informe: {ruta}")
    if cfg.piloto:
        print("modo piloto: borradores solo como .eml locales, no se sube nada")
    elif a.subir:
        print("borradores subidos por IMAP:", correo.subir_borradores(cfg, con, u.codigo))
    else:
        print("borradores en .eml; para subirlos a la casilla: ./tutor procesar ... --subir")
    print(f"uso IA: {uso['llamadas']} llamadas · {uso['entrada']:,} tokens entrada · {uso['salida']:,} salida · US$ {uso['costo']:.4f}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tutor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--piloto", action="store_true")
    ap.add_argument("--hoy", type=lambda s: datetime.fromisoformat(s), default=datetime.now())
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync")
    sub.add_parser("estado")
    sub.add_parser("unidades")
    p = sub.add_parser("pendientes"); p.add_argument("unidad")
    p = sub.add_parser("procesar"); p.add_argument("unidad")
    p.add_argument("--sin-ia", action="store_true"); p.add_argument("--sin-sync", action="store_true")
    p.add_argument("--subir", action="store_true", help="sube los borradores por IMAP (nunca envía)")
    a = ap.parse_args(argv)
    cfg = config.cargar(piloto=a.piloto)
    _env(cfg)
    con = db.conectar(cfg.db)
    {"sync": cmd_sync, "estado": cmd_estado, "unidades": cmd_unidades,
     "pendientes": cmd_pendientes, "procesar": cmd_procesar}[a.cmd](cfg, con, a)


if __name__ == "__main__":
    main()
