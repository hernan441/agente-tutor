"""Panel web: acceso, lecturas, acciones cerradas y chat que solo propone (sin IA real)."""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tutor import aplicar, config, db, ia, lote, sync, unidades
from tutor.web import servidor

RAIZ = Path(__file__).resolve().parent.parent
TOKEN = "token-de-prueba-123"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATOS", str(tmp_path / "datos"))
    c = config.cargar(piloto=True)
    c.audio_python = ""
    c.panel_ips = []
    con = db.conectar(c.db)
    sync.sincronizar(c, con)
    u = unidades.buscar(c.unidades, "BIO6-ALIM")
    for eid, nivel in ((11, "En desarrollo"), (15, "Logrado")):  # Tomás A4: versión 1 y versión 2
        fila = con.execute("SELECT * FROM entregas WHERE id=?", (eid,)).fetchone()
        p = lote.paquete(con, u, fila)
        r = {"entrega_id": eid, "evaluaciones": [{"criterio": k, "nivel": nivel, "evidencia": "x"} for k in p["rubrica"]],
             "asunto": "a", "aclarar": "a", "valorar": "b", "preguntar": "c", "sugerir": "d", "proximo_paso": "e"}
        aplicar.guardar(con, u, p, r, aplicar.validar(r, p))
    con.close()
    return c


@pytest.fixture
def panel(cfg):
    srv = servidor.crear_servidor(cfg, 0, TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    srv.shutdown()


def pedir(base, ruta, cuerpo=None, token=TOKEN, cab=None):
    h = {"Content-Type": "application/json", **({"X-Token": token} if token else {}), **(cab or {})}
    req = urllib.request.Request(base + ruta, data=json.dumps(cuerpo).encode() if cuerpo is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            datos = r.read()
            return r.status, (json.loads(datos) if "json" in r.headers.get("Content-Type", "") else datos)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_escucha_solo_en_localhost(panel):
    assert panel[1].server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("token", [None, "", "otro"])
def test_sin_token_401(panel, token):
    assert pedir(panel[0], "/api/resumen", token=token)[0] == 401


def test_ip_no_habilitada_403(cfg):
    cfg.panel_ips = ["100.64.0.9"]
    srv = servidor.crear_servidor(cfg, 0, TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    assert pedir(base, "/api/resumen", cab={"X-Forwarded-For": "100.64.0.5"})[0] == 403
    assert pedir(base, "/api/resumen", cab={"X-Forwarded-For": "100.64.0.9"})[0] == 200
    srv.shutdown()


def test_estaticos_sin_token_y_sin_salirse(panel):
    with urllib.request.urlopen(panel[0] + "/") as r:
        assert "Agente tutor" in r.read().decode()
    for ruta in ("/../servidor.py", "/%2e%2e/servidor.py", "/../../config.toml"):
        try:
            urllib.request.urlopen(panel[0] + ruta)
            pytest.fail("se pudo leer fuera de static")
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_version_igual_en_app_y_sw():
    app = (RAIZ / "tutor/web/static/app.js").read_text()
    sw = (RAIZ / "tutor/web/static/sw.js").read_text()
    n = re.search(r"APP_NUM = (\d+)", app).group(1)
    assert f'tutor-panel-v{n}"' in sw


def test_resumen_y_entregas(panel):
    s, r = pedir(panel[0], "/api/resumen")
    assert s == 200 and r["inscriptos"] == {"6A": 8} and r["mails_a_revisar"] == 2
    it = r["unidades"][0]["iteraciones"]
    assert any("Tomás Godoy A4: 8/10 (versión 2)" in x for x in it["aprobados"])
    s, ents = pedir(panel[0], "/api/entregas?unidad=BIO6-ALIM")
    tomas = sorted([e for e in ents if e["mail"].startswith("tomas") and e["actividad"] == "A4"], key=lambda e: e["id"])
    assert [(e["version"], e["nota"]) for e in tomas] == [(1, 6), (2, 8)]


def test_legajo_valida_el_mail(panel):
    assert pedir(panel[0], "/api/legajo?mail=tomas.ficticio@ejemplo.edu")[0] == 200
    assert pedir(panel[0], "/api/legajo?mail=../../etc/passwd")[0] == 400
    assert pedir(panel[0], "/api/legajo?mail=nadie@ejemplo.edu")[0] == 404


def test_planilla_valida_la_unidad(panel):
    s, datos = pedir(panel[0], "/api/planilla?unidad=BIO6-ALIM")
    assert s == 200 and datos[:2] == b"PK"
    assert pedir(panel[0], "/api/planilla?unidad=../x")[0] == 400


@pytest.mark.parametrize("accion", [
    {"accion": "purgar"}, {"accion": "borrar todo; ls"}, {"accion": "procesar", "unidad": "../x"},
    {"accion": "asignar", "unidad": "BIO6-ALIM", "actividad": "A9", "curso": "6A"},
    {"accion": "asignar", "unidad": "BIO6-ALIM", "actividad": "A4", "curso": "6A; ls"},
    {"accion": "liberar", "id": "x"}, "texto",
])
def test_acciones_fuera_de_la_lista(cfg, accion):
    with pytest.raises(ValueError):
        servidor.validar_accion(cfg, accion)


def test_acciones_validas_arman_el_cli(cfg):
    assert servidor.validar_accion(cfg, {"accion": "asignar", "unidad": "BIO6-ALIM", "actividad": "A4", "curso": "6a", "subir": True})[0] \
        == ["asignar", "BIO6-ALIM", "A4", "--curso", "6A", "--subir"]
    # "subir" solo si es exactamente true
    assert servidor.validar_accion(cfg, {"accion": "procesar", "unidad": "BIO6-ALIM", "subir": "sí"})[0] == ["procesar", "BIO6-ALIM"]


def test_accion_corre_el_cli_en_segundo_plano(panel):
    s, r = pedir(panel[0], "/api/accion", {"accion": "liberar", "id": 7})
    assert s == 200
    for _ in range(100):
        _, t = pedir(panel[0], "/api/trabajo?id=" + r["id"])
        if t["estado"] != "corriendo":
            break
        time.sleep(0.2)
    assert t["estado"] == "ok" and "cuarentena" in t["log"]  # salida real del CLI sobre la base del piloto


def test_editar_devolucion_regenera_eml(panel, cfg):
    _, devs = pedir(panel[0], "/api/devoluciones")
    d = devs[0]
    s, r = pedir(panel[0], "/api/devolucion", {"id": d["id"], "asunto": "Asunto nuevo", "cuerpo": "Hola Tomás:\n\nTexto editado."})
    assert s == 200
    eml = next((cfg.datos / "borradores").rglob(r["eml"])).read_text()
    assert "Asunto nuevo" in eml and "Texto editado" in eml


def test_inscribir_a_mano_y_resolver(panel):
    _, mails = pedir(panel[0], "/api/mails_revisar")
    m = next(x for x in mails if x["motivo"].startswith("inscripción"))
    s, r = pedir(panel[0], "/api/inscribir", {"mail": m["mail"], "nombre": "Ramiro", "apellido": "Díaz", "curso": "6a", "clave": m["clave"]})
    assert s == 200 and r["nuevos"] == 1
    assert len(pedir(panel[0], "/api/mails_revisar")[1]) == 1
    assert pedir(panel[0], "/api/inscribir", {"mail": "x@hotmail.example", "nombre": "a", "apellido": "b", "curso": "6A"})[0] == 400


def test_clave_imap_nunca_vuelve(panel, cfg, monkeypatch):
    escrito = {}
    monkeypatch.setattr(servidor, "escribir_env", lambda raiz, k, v: escrito.update({k: v}))
    monkeypatch.setattr(servidor, "leer_env", lambda raiz: {"TUTOR_IMAP_PASS": "abcdefghijklmnop"})
    assert pedir(panel[0], "/api/clave_imap", {"clave": "abcd efgh ijkl mnop"})[0] == 200
    assert escrito == {"TUTOR_IMAP_PASS": "abcdefghijklmnop"}
    for ruta in ("/api/ajustes", "/api/resumen"):
        _, r = pedir(panel[0], ruta)
        assert "abcdefghijklmnop" not in json.dumps(r)
    assert pedir(panel[0], "/api/ajustes")[1]["clave_cargada"] is True


def test_chat_solo_propone_y_valida(cfg, monkeypatch):
    respuestas = iter([
        {"respuesta": "Te lo dejo listo.", "accion": {"accion": "asignar", "unidad": "BIO6-ALIM", "actividad": "A4", "curso": "6A"}},
        {"respuesta": "Hecho.", "accion": {"accion": "purgar"}},
    ])
    vistos = []

    def falsa(c, prompt, abre="[", con_imagenes=False, modelo=None, agente="tutor"):
        vistos.append((agente, con_imagenes, prompt))
        return next(respuestas), {"modelo": "falso", "costo": 0}
    monkeypatch.setattr(ia, "llamar", falsa)
    r = servidor.responder_chat(cfg, "asigná la A4 a 6A", [])
    assert r["propuesta"]["desc"] == "Asignar BIO6-ALIM A4 a 6A"
    r = servidor.responder_chat(cfg, "borrá todo", [])
    assert r["propuesta"] is None and "descarté" in r["respuesta"]
    agente, imgs, prompt = vistos[0]
    assert agente == "asistente" and imgs is False and '"inscriptos"' in prompt


def test_asistente_sin_herramientas_y_sin_envio():
    assert 'tools: ""' in (RAIZ / ".claude/agents/asistente.md").read_text()
    src = (RAIZ / "tutor/web/servidor.py").read_text()
    assert "smtplib" not in src
    acciones = src.split("def validar_accion")[1].split("class Trabajos")[0]
    assert "purgar" not in acciones
