"""Tests de las partes sin IA. Correr: venv/bin/pytest -q"""

import csv
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from tutor import adjuntos, aplicar, config, db, lote, pendientes, sync, unidades

RAIZ = Path(__file__).resolve().parent.parent
PILOTO = RAIZ / "piloto"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATOS", str(tmp_path / "datos"))
    c = config.cargar(piloto=True)
    c.audio_python = ""  # los tests no transcriben audio real
    return c


@pytest.fixture
def con(cfg):
    return db.conectar(cfg.db)


@pytest.fixture
def unidad():
    return unidades.buscar(RAIZ / "unidades", "BIO6-ALIM")


def test_esquema_idempotente(cfg):
    db.conectar(cfg.db).close()
    c = db.conectar(cfg.db)
    tablas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"alumnos", "entregas", "adjuntos", "evaluaciones", "devoluciones", "corridas"} <= tablas


def test_unidad_parseada(unidad):
    assert unidad.cursos == ["6A"]
    assert unidad.actividades["A4"].criterios == ["C1", "C2", "C3", "C4"]
    assert "A1" in unidad.sin_nota
    assert set(unidad.rubrica) == {"C1", "C2", "C3", "C4"}
    assert unidad.firma == "Prof. Hernán Pinciroli"


def test_sync_piloto_sin_duplicar(cfg, con):
    r1 = sync.sincronizar(cfg, con)
    r2 = sync.sincronizar(cfg, con)
    assert r1["inscripcion"]["nuevos"] == 8 and r1["inscripcion"]["rechazados"] == 2
    assert r1["entregas"]["nuevas"] == 13 and r1["entregas"]["sin_inscripcion"] == 1
    assert r2["entregas"]["nuevas"] == 0


def test_encabezados_de_google_forms(tmp_path, con):
    p = tmp_path / "forms.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Marca temporal", "Dirección de correo electrónico", "Nombre y apellido", "Unidad",
                    "Actividad", "Tu respuesta", "(opcional) Subí un archivo o audio",
                    "¿Usaste IA? ¿Cuál, qué le pediste y qué corregiste vos?", "¿Qué entendiste bien y qué te falta?"])
        w.writerow(["17/11/2026 18:20:05", "A@B.EDU", "Ana Pérez", "BIO6-ALIM", "A4 — Auditoría", "texto",
                    "https://drive.google.com/open?id=XYZ", "ChatGPT", "bien"])
    r = sync.cargar_entregas(con, sync.leer_csv(p), tmp_path, {"XYZ": p})
    fila = con.execute("SELECT * FROM entregas").fetchone()
    assert r["nuevas"] == 1
    assert fila["mail"] == "a@b.edu" and fila["actividad"] == "A4"
    assert fila["marca_temporal"] == "2026-11-17 18:20:05"
    assert fila["archivos"] == str(p) and fila["uso_ia"] == "ChatGPT"


def test_adjuntos_sin_ia(cfg, con):
    sync.sincronizar(cfg, con)
    ids = [r["id"] for r in con.execute("SELECT id FROM entregas WHERE archivos NOT LIKE '%.ogg%'")]
    st = adjuntos.procesar_entregas(cfg, con, ids)
    assert st["pdf"] == 2 and st["imagen"] == 2 and st["requiere_vision"] == 1
    pdf = con.execute("SELECT texto FROM adjuntos WHERE ruta LIKE '%martina%'").fetchone()["texto"]
    assert "CONABIA" in pdf
    ocr = con.execute("SELECT texto, requiere_vision FROM adjuntos WHERE ruta LIKE '%etiqueta%'").fetchone()
    assert "RNPA" in ocr["texto"] and ocr["requiere_vision"] == 0
    # segunda pasada: todo desde caché
    st2 = adjuntos.procesar_entregas(cfg, con, ids)
    assert st2["cache"] == st2["archivos"]


WHISPER_PY = os.environ.get("TUTOR_WHISPER_PY", "")


@pytest.mark.skipif(not (WHISPER_PY and Path(WHISPER_PY).exists()),
                    reason="definí TUTOR_WHISPER_PY con un python que tenga faster-whisper")
def test_audio_transcripto(cfg, con):
    cfg.audio_python = WHISPER_PY
    cfg.whisper_modelo = "base"
    sync.sincronizar(cfg, con)
    ids = [r["id"] for r in con.execute("SELECT id FROM entregas WHERE archivos LIKE '%.ogg%'")]
    adjuntos.procesar_entregas(cfg, con, ids)
    t = con.execute("SELECT texto FROM adjuntos WHERE tipo='audio'").fetchone()["texto"]
    assert "FDA" in t.upper()


def _paquete(con, cfg, unidad, nombre="Tomás", act="A4"):
    sync.sincronizar(cfg, con)
    fila = con.execute("SELECT * FROM entregas WHERE nombre=? AND actividad=?", (nombre, act)).fetchone()
    return lote.paquete(con, unidad, fila)


def test_paquete_compacto(cfg, con, unidad):
    p = _paquete(con, cfg, unidad)
    assert set(p["rubrica"]) == {"C1", "C2", "C3", "C4"}
    assert p["actividad"]["lleva_nota"] is True
    assert any("Gemini" in x for x in p["antecedentes"]["uso_ia_declarado_antes"])


def _respuesta_ok(p):
    return {"entrega_id": p["entrega_id"],
            "evaluaciones": [{"criterio": c, "nivel": "En desarrollo", "evidencia": "x"} for c in p["rubrica"]],
            "ideas_erroneas": [], "senal_ia": False, "asunto": "a", "aclarar": "a", "valorar": "b",
            "preguntar": "c", "sugerir": "d", "nivel_msg": "", "proximo_paso": "d", "nota_docente": ""}


def test_validar_rechaza_nivel_y_criterios(cfg, con, unidad):
    p = _paquete(con, cfg, unidad)
    r = _respuesta_ok(p)
    assert aplicar.validar(r, p) == []
    r["evaluaciones"][0]["nivel"] = "Excelente"
    with pytest.raises(aplicar.Invalido):
        aplicar.validar(r, p)
    r = _respuesta_ok(p)
    r["evaluaciones"].pop()
    with pytest.raises(aplicar.Invalido):
        aplicar.validar(r, p)


def test_validar_detecta_fuga_de_respuesta(cfg, con, unidad):
    p = _paquete(con, cfg, unidad, "Lucía")
    r = _respuesta_ok(p)
    r["sugerir"] = ("En Argentina la aprobación comercial de un evento pasa por tres instancias "
                    "y la resolución final la firma la Secretaría de Agricultura.")
    avisos = aplicar.validar(r, p)
    assert any("fuga" in a for a in avisos)


def test_pendientes_y_recordatorio(cfg, con, unidad):
    sync.sincronizar(cfg, con)
    hechos = pendientes.generar(con, unidad, datetime(2026, 11, 20))
    faltan = {(h["nombre"], h["actividad"]) for h in hechos}
    assert faltan == {("Valentina", "A1"), ("Valentina", "A4"), ("Joaquín", "A4"), ("Bruno", "A1"), ("Camila", "A1")}
    # antes del plazo de 48 h, la A4 todavía no se reclama
    assert all(f["actividad"] == "A1" for f in pendientes.faltantes(con, unidad, datetime(2026, 11, 18, 12)))
    # no duplica
    pendientes.generar(con, unidad, datetime(2026, 11, 21))
    assert con.execute("SELECT COUNT(*) FROM devoluciones WHERE tipo='recordatorio'").fetchone()[0] == 5


def test_correo_no_puede_enviar():
    src = (RAIZ / "tutor" / "correo.py").read_text()
    assert "smtplib" not in src and "send_message" not in src
