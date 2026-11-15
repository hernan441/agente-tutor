"""Tests de las capas: RAG, router, caché, observabilidad, evals y retención."""

import csv
import json
from pathlib import Path

import pytest

from tutor import cli, config, db, evals, ia, lote, motor, observabilidad, rag, router, seguridad, sync, unidades

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATOS", str(tmp_path / "datos"))
    c = config.cargar(piloto=True)
    c.audio_python = ""
    monkeypatch.setattr(config, "cargar", lambda piloto=False: c)
    return c


@pytest.fixture
def con(cfg):
    return db.conectar(cfg.db)


@pytest.fixture
def unidad():
    return unidades.buscar(RAIZ / "unidades", "BIO6-ALIM")


# ---------- RAG ----------

def test_rag_encuentra_la_seccion(unidad):
    r = rag.buscar(unidad, "CONABIA y SENASA aprueban cada evento caso por caso", 2)
    assert "circuito de aprobación" in r[0]["seccion"].lower()


def test_rag_no_usa_unidad_md(unidad):
    frags = rag.indice(unidad).frags
    assert all(f["fuente"] != "unidad.md" for f in frags)  # ahí está la respuesta esperada


def test_paquete_trae_material(cfg, con, unidad):
    sync.sincronizar(cfg, con)
    fila = con.execute("SELECT * FROM entregas WHERE nombre='Lucía' AND actividad='A4'").fetchone()
    p = lote.paquete(con, unidad, fila)
    assert p["material_de_clase"] and "seccion" in p["material_de_clase"][0]
    assert list(p)[:4] == ["AVISO", "actividad", "rubrica", "solo_para_vos"]  # lo fijo primero


def test_prompt_comun_una_vez(cfg, con, unidad):
    sync.sincronizar(cfg, con)
    filas = con.execute("SELECT * FROM entregas WHERE actividad='A4' LIMIT 3").fetchall()
    ps = [lote.paquete(con, unidad, f) for f in filas]
    for p in ps:
        p["_x"] = 1
    prompt = ia.armar_prompt(ps)
    assert prompt.count('"rubrica"') == 1 and "_x" not in prompt


# ---------- Router ----------

def _p(resp="", adj="", imgs=None, sosp=None):
    return {"entrega": {"respuesta": resp, "texto_de_adjuntos": adj, "imagenes_para_mirar": imgs or []},
            "_sospechas": sosp or [], "actividad": {"codigo": "A1"}, "rubrica": {"C1": {}}, "entrega_id": 1}


def test_router():
    assert router.elegir(_p("Fideos.")) == router.PLANTILLA
    assert router.elegir(_p("no sé")) == router.PLANTILLA
    assert router.elegir(_p("Fideos.", adj="ingredientes " * 20)) == router.RAPIDO
    assert router.elegir(_p("una respuesta normal de un alumno sobre su alimento")) == router.RAPIDO
    assert router.elegir(_p("hola", imgs=["x.jpg"])) == router.FUERTE
    assert router.elegir(_p("una respuesta normal de un alumno", sosp=["pedido de nota"])) == router.FUERTE
    assert router.elegir(_p("una respuesta normal de un alumno"), reintento=True) == router.FUERTE


# ---------- Motor: caché, router, registro de llamadas ----------

def _falsa_ia(llamadas):
    def falsa(cfg_, paquetes, modelo=None):
        llamadas.append((modelo, [p["entrega_id"] for p in paquetes]))
        res = [{"entrega_id": p["entrega_id"],
                "evaluaciones": [{"criterio": c, "nivel": "En desarrollo", "evidencia": "x"} for c in p["rubrica"]],
                "asunto": "a", "aclarar": "a", "valorar": "b", "preguntar": "c", "sugerir": "d"} for p in paquetes]
        return res, {"entrada": 10, "salida": 5, "costo": 0.001, "ms": 7, "modelo": modelo, "prompt": "v"}
    return falsa


def test_motor_router_cache_y_llamadas(cfg, con, unidad, monkeypatch):
    llamadas = []
    monkeypatch.setattr(ia, "evaluar", _falsa_ia(llamadas))
    con.execute("INSERT INTO alumnos VALUES ('a@ejemplo.edu','Ana','P','6A','x'), ('b@ejemplo.edu','Beto','Q','6A','x'), ('c@ejemplo.edu','Ceci','R','6A','x')")
    texto = "Elegí la leche. Sé que tiene RNPA y supongo que el cacao no es transgénico, no sé quién controla."
    for i, (m, t) in enumerate([("a@ejemplo.edu", texto), ("b@ejemplo.edu", "ok"), ("c@ejemplo.edu", texto)]):
        con.execute("INSERT INTO entregas (marca_temporal, mail, nombre, unidad, actividad, respuesta) VALUES (?,?,?,?,?,?)",
                    (f"2026-11-10 10:0{i}:00", m, m[0], "BIO6-ALIM", "A1", t))
    con.commit()
    filas = con.execute("SELECT * FROM entregas ORDER BY id").fetchall()
    # primera corrida: a → rápido, b → plantilla, c → rápido (misma tanda, todavía no hay caché)
    r = motor.evaluar_entregas(cfg, con, unidad, filas, "c1")
    assert r["caminos"] == {"plantilla": 1, "rapido": 2}
    assert con.execute("SELECT COUNT(*) FROM llamadas").fetchone()[0] == 1
    # segunda corrida con el mismo texto: sale de caché, sin IA
    con.execute("INSERT INTO entregas (marca_temporal, mail, nombre, unidad, actividad, respuesta) VALUES (?,?,?,?,?,?)",
                ("2026-11-11 10:00:00", "a@ejemplo.edu", "a", "BIO6-ALIM", "A1", texto + " "))
    con.commit()
    nueva = con.execute("SELECT * FROM entregas WHERE procesada=0").fetchall()
    n_antes = len(llamadas)
    r2 = motor.evaluar_entregas(cfg, con, unidad, nueva, "c2")
    assert r2["caminos"] == {"cache": 1} and len(llamadas) == n_antes


def test_invalida_en_rapido_reintenta_en_fuerte(cfg, con, unidad, monkeypatch):
    llamadas = []
    buena = _falsa_ia(llamadas)

    def mala_primero(cfg_, paquetes, modelo=None):
        if modelo == cfg.modelo_rapido:
            llamadas.append((modelo, []))
            return [{"entrega_id": p["entrega_id"], "evaluaciones": []} for p in paquetes], {"costo": 0, "modelo": modelo}
        return buena(cfg_, paquetes, modelo)

    monkeypatch.setattr(ia, "evaluar", mala_primero)
    con.execute("INSERT INTO alumnos VALUES ('a@ejemplo.edu','Ana','P','6A','x')")
    con.execute("INSERT INTO entregas (marca_temporal, mail, nombre, unidad, actividad, respuesta) VALUES (?,?,?,?,?,?)",
                ("2026-11-10 10:00:00", "a@ejemplo.edu", "Ana", "BIO6-ALIM", "A1", "una respuesta de varias palabras sobre la leche"))
    con.commit()
    r = motor.evaluar_entregas(cfg, con, unidad, con.execute("SELECT * FROM entregas").fetchall(), "c")
    assert r["caminos"] == {"fuerte": 1}
    assert [m for m, _ in llamadas] == [cfg.modelo_rapido, cfg.modelo_fuerte]
    assert con.execute("SELECT invalidas FROM llamadas ORDER BY id").fetchall()[0][0] == 1


# ---------- Observabilidad ----------

def test_porcentaje_edicion():
    assert observabilidad.porcentaje_edicion("Hola Ana: muy bien.", "Hola Ana: muy bien.") == 0
    assert observabilidad.porcentaje_edicion("Hola Ana: muy bien.", "Algo totalmente distinto escrito por el profe") > 50


def test_registrar_enviados_y_metricas(con):
    con.execute("""INSERT INTO devoluciones (mail, unidad, actividad, tipo, asunto, cuerpo, creado_en)
                   VALUES ('a@ejemplo.edu','U','A1','devolucion','[REVISAR] Bio — Devolución de A1: x','Hola Ana: bien hecho.','t')""")
    n = observabilidad.registrar_enviados(con, [("A@ejemplo.edu", "Bio — Devolución de A1: x", "Hola Ana: bien hecho!")])
    assert n == 1
    assert "Edición media" in observabilidad.metricas(con)


# ---------- Evals ----------

def test_comparar_evals():
    casos = [{"caso_id": "1", "docente_C1": "Logrado", "docente_C2": "Inicial"},
             {"caso_id": "2", "docente_C1": "Destacado", "docente_C2": ""}]
    ag = {"1": {"C1": "Logrado", "C2": "En desarrollo"}, "2": {"C1": "Inicial"}}
    m = evals.comparar(casos, ag)
    assert m["total"] == 3 and m["exactas"] == 1 and m["a_un_nivel"] == 2
    assert m["sobrevalora"] == 1 and m["subvalora"] == 1


def test_evaluar_agente_de_punta_a_punta(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(ia, "evaluar", _falsa_ia([]))
    casos = tmp_path / "casos.csv"
    with open(casos, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["caso_id", "unidad", "actividad", "nombre", "respuesta", "uso_ia", "autoevaluacion", "docente_C1", "docente_C4"])
        w.writerow(["1", "BIO6-ALIM", "A1", "Ana", "Elegí la leche y sé que tiene RNPA, supongo que no es transgénica", "", "", "En desarrollo", "Logrado"])
    ruta = evals.correr(cfg, casos)
    texto = ruta.read_text(encoding="utf-8")
    assert "Coincidencia exacta con el docente: 50 %" in texto
    ruta.unlink()


def test_casos_csv_del_repo_tiene_columnas():
    with open(RAIZ / "evals" / "casos.csv", encoding="utf-8") as f:
        cab = next(csv.reader(f))
    assert {"docente_C1", "docente_C2", "docente_C3", "docente_C4"} <= set(cab)


# ---------- Retención ----------

def test_purgar_anonimiza(cfg, con, capsys):
    sync.sincronizar(cfg, con)
    cli.main(["--piloto", "purgar", "--ciclo", "2026", "--anonimizar", "--confirmar"])
    c2 = db.conectar(cfg.db)
    assert c2.execute("SELECT COUNT(*) FROM entregas WHERE mail LIKE '%ejemplo.edu'").fetchone()[0] == 0
    assert c2.execute("SELECT COUNT(*) FROM entregas").fetchone()[0] == 14
