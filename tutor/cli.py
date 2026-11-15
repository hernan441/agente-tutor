"""Línea de comandos del tutor.

  ./tutor sync                     trae inscripciones y entregas (rclone o piloto)
  ./tutor procesar UNIDAD          todo el circuito: adjuntos → IA → borradores → informe
  ./tutor pendientes UNIDAD        quién no entregó (sin IA)
  ./tutor estado                   inscriptos por curso, entregas, sin inscripción
  ./tutor unidades                 lista las unidades cargadas

Opciones: --piloto (datos de prueba, sin Google ni IMAP) · --hoy AAAA-MM-DD · --sin-ia
"""

import argparse
import json
import os
from datetime import datetime

from . import adjuntos, config, correo, db, evals, ia, informe, lote, motor, observabilidad, pendientes, seguridad, sync, unidades


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
    rech = con.execute("SELECT mail, motivo, origen FROM rechazos ORDER BY fecha").fetchall()
    if rech:
        print("Rechazos:")
        for r in rech:
            print(f"  {r['mail']} ({r['origen']}): {r['motivo']}")
    cua = con.execute("SELECT id, mail, actividad, motivo_cuarentena FROM entregas WHERE estado='cuarentena'").fetchall()
    if cua:
        print("En cuarentena (no van a la IA; liberar con ./tutor.sh liberar ID):")
        for r in cua:
            print(f"  #{r['id']} {r['mail']} {r['actividad']}: {r['motivo_cuarentena']}")


def cmd_unidades(cfg, con, a):
    for u in unidades.todas(cfg.unidades).values():
        print(f"{u.codigo}: {u.titulo} — {u.materia} — cursos {', '.join(u.cursos)} — actividades {', '.join(u.actividades)}")


def cmd_liberar(cfg, con, a):
    print("liberada" if seguridad.liberar(con, a.id) else "no estaba en cuarentena")


def cmd_metricas(cfg, con, a):
    print(observabilidad.metricas(con))


def cmd_calidad(cfg, con, a):
    n = observabilidad.registrar_enviados(con, observabilidad.leer_enviados_imap(cfg, a.dias))
    print(f"{n} devoluciones enviadas emparejadas con su borrador. Ver ./tutor.sh metricas")


def cmd_evaluar_agente(cfg, con, a):
    ruta = evals.correr(cfg, cfg.raiz / a.casos, a.modelo)
    print(f"resultado: {ruta}")
    print("\n".join(ruta.read_text(encoding="utf-8").splitlines()[:10]))


def cmd_purgar(cfg, con, a):
    """Retención de datos: borra o anonimiza un ciclo lectivo cerrado."""
    ids = [r[0] for r in con.execute("SELECT id FROM entregas WHERE substr(marca_temporal,1,4)=?", (str(a.ciclo),))]
    mails = sorted({r[0] for r in con.execute("SELECT mail FROM entregas WHERE substr(marca_temporal,1,4)=?", (str(a.ciclo),))})
    if not a.confirmar:
        print(f"Ciclo {a.ciclo}: {len(ids)} entregas de {len(mails)} alumnos. "
              f"Agregá --confirmar para {'anonimizar' if a.anonimizar else 'borrar'} (no se puede deshacer).")
        return
    marcas = ",".join("?" * len(ids)) or "NULL"
    if a.anonimizar:
        for i, m in enumerate(mails, 1):
            anon = f"alumno{i:03d}@anonimo.local"
            con.execute("UPDATE entregas SET mail=?, nombre=?, archivos='' WHERE mail=? AND substr(marca_temporal,1,4)=?",
                        (anon, f"Alumno {i}", m, str(a.ciclo)))
            con.execute("UPDATE devoluciones SET mail=?, cuerpo='(anonimizado)', texto_enviado=NULL WHERE mail=?", (anon, m))
            con.execute("DELETE FROM alumnos WHERE mail=?", (m,))
        con.execute(f"UPDATE evaluaciones SET evidencia='' WHERE entrega_id IN ({marcas})", ids)
    else:
        for t in ("evaluaciones", "analisis", "entrega_adjuntos"):
            con.execute(f"DELETE FROM {t} WHERE entrega_id IN ({marcas})", ids)
        con.execute(f"DELETE FROM devoluciones WHERE mail IN ({','.join('?' * len(mails)) or 'NULL'})", mails)
        con.execute(f"DELETE FROM entregas WHERE id IN ({marcas})", ids)
        con.execute(f"DELETE FROM alumnos WHERE mail IN ({','.join('?' * len(mails)) or 'NULL'})", mails)
    con.execute("DELETE FROM adjuntos WHERE sha256 NOT IN (SELECT sha256 FROM entrega_adjuntos)")
    con.execute("DELETE FROM cache_evaluaciones")
    con.commit()
    con.execute("VACUUM")
    import shutil
    for carpeta in ("legajos", "borradores", "archivos", "cache"):
        shutil.rmtree(cfg.datos / carpeta, ignore_errors=True)
    informe.escribir_legajos(cfg, con)
    print(f"Ciclo {a.ciclo}: {len(ids)} entregas {'anonimizadas' if a.anonimizar else 'borradas'}. "
          "Se conservan solo métricas agregadas (llamadas, corridas).")


def cmd_pendientes(cfg, con, a):
    u = unidades.buscar(cfg.unidades, a.unidad)
    for f in pendientes.faltantes(con, u, a.hoy):
        print(f"{f['apellido']}, {f['nombre']} — falta {f['actividad']}")


def cmd_procesar(cfg, con, a):
    u = unidades.buscar(cfg.unidades, a.unidad)
    inicio = datetime.now()
    if not a.sin_sync:
        print("sync:", sync.sincronizar(cfg, con))
    print("seguridad (sin IA):", seguridad.revisar_entregas(cfg, con, u.codigo))
    filas = lote.pendientes(con, u, cfg.max_entregas_por_corrida)
    print(f"{len(filas)} entregas para evaluar de {u.codigo} (máx. {cfg.max_entregas_por_corrida} por corrida)")
    st = adjuntos.procesar_entregas(cfg, con, [f["id"] for f in filas], f"{u.materia}. {u.titulo}")
    print("adjuntos (sin IA):", st)

    corrida = inicio.isoformat(timespec="seconds")
    uso = {"llamadas": 0, "entrada": 0, "salida": 0, "costo": 0.0}
    procesadas, corte_tope, caminos = 0, False, {}
    if not a.sin_ia:
        r = motor.evaluar_entregas(cfg, con, u, filas, corrida)
        uso, procesadas, corte_tope, caminos = r["uso"], r["procesadas"], r["corte_tope"], r["caminos"]
        print(f"evaluadas: {procesadas} · caminos: {caminos} · fallidas: {r['fallidas'] or 0}")
        if corte_tope:
            print(f"CORTE: se alcanzó el tope de US$ {cfg.tope_usd_corrida:.2f} por corrida; el resto queda para la próxima")
    uso["caminos"] = caminos

    faltan = pendientes.generar(con, u, a.hoy)
    print(f"recordatorios (sin IA): {len(faltan)}")
    for d in con.execute("SELECT id FROM devoluciones WHERE unidad=? AND borrador_subido=0", (u.codigo,)).fetchall():
        correo.escribir_eml(cfg, con, d["id"])
    n_leg = informe.escribir_legajos(cfg, con)
    res = informe.resumen(con, u, faltan)
    res["seguridad"] = informe.resumen_seguridad(con, u, uso, cfg, corte_tope)
    sintesis = {"en_una_linea": "Sin entregas nuevas para evaluar.", "sugerencias": []}
    if any(t["niveles"] for t in res["tabla"]) and not a.sin_ia:
        try:
            sintesis, us = ia.sintesis_informe(cfg, {k: res[k] for k in ("titulo", "ideas", "alertas", "no_entregaron", "notas")})
            motor.registrar_llamada(con, corrida, "sintesis", "rapido", 0, us)
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
    p = sub.add_parser("liberar", help="saca una entrega de cuarentena"); p.add_argument("id", type=int)
    sub.add_parser("metricas", help="costos, latencia, router, calidad")
    p = sub.add_parser("calidad", help="compara borradores con lo enviado (IMAP, solo lectura)")
    p.add_argument("--dias", type=int, default=30)
    p = sub.add_parser("evaluar-agente", help="mide al agente contra los niveles del docente")
    p.add_argument("--casos", default="evals/casos.csv"); p.add_argument("--modelo", default=None)
    p = sub.add_parser("purgar", help="borra o anonimiza un ciclo cerrado")
    p.add_argument("--ciclo", type=int, required=True); p.add_argument("--anonimizar", action="store_true")
    p.add_argument("--confirmar", action="store_true")
    a = ap.parse_args(argv)
    cfg = config.cargar(piloto=a.piloto)
    _env(cfg)
    con = db.conectar(cfg.db)
    {"sync": cmd_sync, "estado": cmd_estado, "unidades": cmd_unidades,
     "pendientes": cmd_pendientes, "procesar": cmd_procesar, "liberar": cmd_liberar, "metricas": cmd_metricas,
     "calidad": cmd_calidad, "evaluar-agente": cmd_evaluar_agente, "purgar": cmd_purgar}[a.cmd](cfg, con, a)


if __name__ == "__main__":
    main()
