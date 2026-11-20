"""Motor de evaluación: caché → router → IA (rápida o fuerte) → validación → guardado.

Cada llamada a la IA queda registrada en la tabla `llamadas` (observabilidad).
"""

import hashlib
import json
import secrets
from collections import Counter
from datetime import datetime
from itertools import groupby

from . import aplicar, ia, lote, router, seguridad


def _ahora():
    return datetime.now().isoformat(timespec="seconds")


def registrar_llamada(con, corrida, tipo, camino, n, uso=None, intento=1, invalidas=0, error=""):
    uso = uso or {}
    con.execute(
        """INSERT INTO llamadas (corrida_inicio, fecha, tipo, camino, modelo, entregas, ms, tokens_entrada,
           tokens_cache, tokens_salida, costo_usd, intento, invalidas, error, version_prompt)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (corrida, _ahora(), tipo, camino, uso.get("modelo"), n, uso.get("ms"), uso.get("entrada"), uso.get("cache"),
         uso.get("salida"), uso.get("costo"), intento, invalidas, error[:300], uso.get("prompt")))
    con.commit()


def clave_cache(p: dict, version: str) -> str:
    h = seguridad.huella((p["entrega"]["respuesta"] or "") + " " + (p["entrega"]["texto_de_adjuntos"] or ""))
    if not h or p["entrega"]["imagenes_para_mirar"]:
        return ""
    return hashlib.sha1(f"{h}|{p['actividad']['codigo']}|{version}".encode()).hexdigest()


def _sospechas(con, eid) -> list:
    import os
    from . import adjuntos
    out = [f.split(":", 1)[1] for f in json.loads(
        con.execute("SELECT flags FROM entregas WHERE id=?", (eid,)).fetchone()["flags"] or "[]")
        if f.startswith("sospecha_inyeccion:")]
    for a in adjuntos.de_entrega(con, eid):
        for al in json.loads(a.get("alertas") or "[]"):
            if al.startswith("sospecha_inyeccion:") or al.startswith("texto_oculto"):
                out.append(f"{al} en {os.path.basename(a['ruta'])}")
    return out


def rondas(filas) -> list:
    """Si un alumno mandó varias versiones de la misma actividad, cada una va en una ronda distinta,
    en orden: así la versión 2 se evalúa sabiendo cómo le fue en la 1."""
    vistas, out = {}, []
    for f in sorted(filas, key=lambda f: (f["marca_temporal"], f["id"])):
        k = (f["mail"], f["actividad"])
        n = vistas.get(k, 0)
        vistas[k] = n + 1
        while len(out) <= n:
            out.append([])
        out[n].append(f)
    return out


def evaluar_entregas(cfg, con, unidad, filas, corrida: str, guardar_cache: bool = True) -> dict:
    """Devuelve {'procesadas', 'fallidas', 'uso', 'corte_tope', 'caminos'}."""
    total = {"procesadas": 0, "fallidas": [], "uso": {"llamadas": 0, "entrada": 0, "salida": 0, "costo": 0.0},
             "corte_tope": False, "caminos": Counter()}
    for ronda in rondas(filas):
        tope = cfg.tope_usd_corrida - total["uso"]["costo"]
        r = _evaluar_ronda(cfg, con, unidad, ronda, corrida, guardar_cache, tope)
        total["procesadas"] += r["procesadas"]
        total["fallidas"] += r["fallidas"]
        for k in total["uso"]:
            total["uso"][k] += r["uso"][k]
        total["caminos"].update(r["caminos"])
        if r["corte_tope"]:
            total["corte_tope"] = True
            break
    total["caminos"] = dict(total["caminos"])
    return total


def _evaluar_ronda(cfg, con, unidad, filas, corrida: str, guardar_cache: bool, tope: float) -> dict:
    uso = {"llamadas": 0, "entrada": 0, "salida": 0, "costo": 0.0}
    caminos, fallidas = Counter(), []
    procesadas, corte = 0, False
    version = ia.version_prompt(cfg)
    cache_dir = cfg.datos / "cache"

    def aplicar_uno(p, r, camino, modelo):
        nonlocal procesadas
        avisos = aplicar.validar(r, p)
        aplicar.guardar(con, unidad, p, r, avisos, {"camino": camino, "modelo": modelo, "version_prompt": version})
        procesadas += 1
        caminos[camino] += 1
        return avisos

    # Por actividad, en orden: así A4 ya ve lo evaluado en A1.
    orden = sorted(filas, key=lambda f: (f["actividad"], f["marca_temporal"]))
    for _, grupo in groupby(orden, key=lambda f: f["actividad"]):
        grupo = list(grupo)
        for i in range(0, len(grupo), cfg.lote_max):
            if uso["costo"] >= tope:
                corte = True
                break
            tanda = []
            for f in grupo[i:i + cfg.lote_max]:
                fila = con.execute("SELECT * FROM entregas WHERE id=?", (f["id"],)).fetchone()
                p = lote.paquete(con, unidad, fila, cache_dir)
                if p:
                    tanda.append(p)
            canario = "CANARIO-" + secrets.token_hex(6)
            a_ia = {router.RAPIDO: [], router.FUERTE: []}
            for p in tanda:
                p["solo_para_vos"]["canario"] = canario
                p["_calificacion"] = {"equivalencias": cfg.equivalencias, "aprobacion": cfg.nota_aprobacion}
                p["_sospechas"] = _sospechas(con, p["entrega_id"])
                # 1) caché de evaluaciones
                clave = clave_cache(p, version)
                p["_clave"] = clave
                if clave:
                    hit = con.execute("SELECT json FROM cache_evaluaciones WHERE clave=?", (clave,)).fetchone()
                    if hit:
                        r = json.loads(hit["json"])
                        r["entrega_id"] = p["entrega_id"]
                        try:
                            aplicar_uno(p, r, "cache", "—")
                            continue
                        except aplicar.Invalido:
                            pass
                # 2) router
                camino = router.elegir(p)
                if camino == router.PLANTILLA:
                    aplicar_uno(p, router.respuesta_plantilla(p), router.PLANTILLA, "—")
                else:
                    a_ia[camino].append(p)
            # 3) IA: primero rápida, lo que falle va a la fuerte (una vez)
            for camino in (router.RAPIDO, router.FUERTE):
                pend = a_ia[camino]
                intento = 1
                while pend:
                    modelo = router.modelo(cfg, camino)
                    try:
                        res, us = ia.evaluar(cfg, pend, modelo)
                    except Exception as exc:
                        registrar_llamada(con, corrida, "evaluar", camino, len(pend), {"modelo": modelo}, intento, 0, str(exc))
                        if intento >= 2:
                            fallidas += [(p["entrega_id"], f"IA: {exc}") for p in pend]
                            break
                        intento += 1
                        continue
                    uso["llamadas"] += 1
                    for k in ("entrada", "salida", "costo"):
                        uso[k] += us.get(k, 0)
                    por_id = {r.get("entrega_id"): r for r in res if isinstance(r, dict)}
                    malos = []
                    for p in pend:
                        r = por_id.get(p["entrega_id"])
                        try:
                            if r is None:
                                raise aplicar.Invalido("sin respuesta")
                            avisos = aplicar_uno(p, r, camino, us.get("modelo", modelo))
                            if guardar_cache and p["_clave"] and not avisos:
                                con.execute("INSERT OR REPLACE INTO cache_evaluaciones VALUES (?,?,?)",
                                            (p["_clave"], json.dumps(r, ensure_ascii=False), _ahora()))
                        except aplicar.Invalido as exc:
                            malos.append((p, str(exc)))
                    registrar_llamada(con, corrida, "evaluar", camino, len(pend), us, intento, len(malos))
                    if not malos:
                        break
                    if camino == router.RAPIDO:
                        a_ia[router.FUERTE] += [p for p, _ in malos]  # reintento con el modelo fuerte
                        break
                    if intento >= 2:
                        fallidas += [(p["entrega_id"], m) for p, m in malos]
                        break
                    pend, intento = [p for p, _ in malos], intento + 1
        if corte:
            break
    con.commit()
    return {"procesadas": procesadas, "fallidas": fallidas, "uso": uso, "corte_tope": corte, "caminos": dict(caminos)}
