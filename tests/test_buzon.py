"""Entrada por mail, nota e iteraciones (sin IA)."""

from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import openpyxl
import pytest

from tutor import aplicar, buzon, config, correo, db, lote, planilla, sync, unidades

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATOS", str(tmp_path / "datos"))
    c = config.cargar(piloto=True)
    c.audio_python = ""
    return c


@pytest.fixture
def con(cfg):
    return db.conectar(cfg.db)


@pytest.fixture
def us(cfg):
    return unidades.todas(cfg.unidades)


def mail(de, asunto, cuerpo, fecha="Mon, 16 Nov 2026 10:00:00 -0300", mid="<x1@ejemplo.edu>", **cab):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"], m["Message-ID"] = de, "catedra@ejemplo.edu", asunto, fecha, mid
    for k, v in cab.items():
        m[k.replace("_", "-")] = v
    m.set_content(cuerpo)
    return bytes(m)


def _inscribir(con, cfg, us, quien="ana@ejemplo.edu"):
    return buzon.procesar(cfg, con, mail(f"Ana Paz <{quien}>", "Inscripción", "Nombre: Ana\nApellido: Paz\nCurso: 6° A",
                                         mid=f"<ins-{quien}>"), us, sync)


# ---------- clasificación ----------

def test_inscripcion_por_mail(cfg, con, us):
    assert _inscribir(con, cfg, us) == "inscripcion"
    a = con.execute("SELECT * FROM alumnos").fetchone()
    assert (a["nombre"], a["apellido"], a["curso"]) == ("Ana", "Paz", "6A")


def test_inscripcion_sin_formato_usa_remitente(cfg, con, us):
    buzon.procesar(cfg, con, mail("Beto Ruiz <beto@ejemplo.edu>", "hola", "Quiero inscribirme, soy de 5to B"), us, sync)
    a = con.execute("SELECT * FROM alumnos").fetchone()
    assert (a["nombre"], a["apellido"], a["curso"]) == ("Beto", "Ruiz", "5B")


def test_inscripcion_incompleta_queda_para_revisar(cfg, con, us):
    r = buzon.procesar(cfg, con, mail("ceci@ejemplo.edu", "Inscripción", "me inscribo"), us, sync)
    assert r == "revisar"
    assert "curso" in con.execute("SELECT motivo FROM mails_a_revisar").fetchone()[0]
    assert not db.inscripto(con, "ceci@ejemplo.edu")


def test_inscripcion_de_otro_dominio_se_rechaza(cfg, con, us):
    buzon.procesar(cfg, con, mail("x@hotmail.example", "Inscripción", "Nombre: X\nApellido: Y\nCurso: 6A"), us, sync)
    assert not db.inscripto(con, "x@hotmail.example")
    assert con.execute("SELECT COUNT(*) FROM rechazos").fetchone()[0] == 1


def test_mail_ajeno_se_ignora_sin_guardar_nada(cfg, con, us):
    r = buzon.procesar(cfg, con, mail("colega@ejemplo.edu", "Reunión", "El jueves a las 13"), us, sync)
    assert r == "ignorado"
    assert con.execute("SELECT COUNT(*) FROM entregas").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM mails_a_revisar").fetchone()[0] == 0
    # de un mail ajeno solo queda un hash, no el remitente ni el asunto
    fila = con.execute("SELECT * FROM buzon_vistos").fetchone()
    assert set(fila.keys()) == {"clave", "resultado", "fecha"} and "@" not in fila["clave"]


def test_mail_propio_se_ignora(cfg, con, us):
    cfg.cuenta = "catedra@ejemplo.edu"
    assert buzon.procesar(cfg, con, mail("catedra@ejemplo.edu", "[BIO6-ALIM A1] x", "y"), us, sync) == "ignorado"


def test_entrega_por_codigo_en_asunto_con_partes(cfg, con, us):
    _inscribir(con, cfg, us)
    cuerpo = ("Elegí polenta, tiene RNPA.\n\nIA: usé Claude y corregí el país\nAutoevaluación: me falta lo del evento\n\n"
              "El lun, 9 nov 2026 a las 10:00, Prof <catedra@ejemplo.edu> escribió:\n> la consigna completa\n")
    r = buzon.procesar(cfg, con, mail("Ana Paz <ana@ejemplo.edu>", "Re: [BIO6-ALIM A1] Biotecnología — Mi alimento", cuerpo), us, sync)
    assert r == "entrega"
    e = con.execute("SELECT * FROM entregas").fetchone()
    assert (e["unidad"], e["actividad"]) == ("BIO6-ALIM", "A1")
    assert e["respuesta"] == "Elegí polenta, tiene RNPA."
    assert e["uso_ia"] == "usé Claude y corregí el país" and e["autoevaluacion"] == "me falta lo del evento"
    assert "consigna completa" not in (e["respuesta"] + e["uso_ia"] + e["autoevaluacion"])
    assert e["marca_temporal"] == "2026-11-16 10:00:00" and e["nombre"] == "Ana Paz"


def test_entrega_por_respuesta_al_trabajo(cfg, con, us):
    _inscribir(con, cfg, us)
    ruta, n = correo.borrador_trabajo(cfg, con, us["BIO6-ALIM"], "A4", "6A")
    mid = con.execute("SELECT message_id FROM asignaciones").fetchone()[0]
    assert n == 1 and "Bcc: ana@ejemplo.edu" in ruta.read_text()
    r = buzon.procesar(cfg, con, mail("ana@ejemplo.edu", "Re: el trabajo", "mi auditoría", In_Reply_To=mid), us, sync)
    assert r == "entrega" and con.execute("SELECT actividad FROM entregas").fetchone()[0] == "A4"


def test_entrega_sin_codigo_va_a_la_ultima_asignada(cfg, con, us):
    _inscribir(con, cfg, us)
    con.execute("INSERT INTO asignaciones (unidad, actividad, curso, asunto, message_id, fecha) VALUES "
                "('BIO6-ALIM','A1','6A','a',NULL,'2026-11-01 10:00:00'), ('BIO6-ALIM','A4','6A','b',NULL,'2026-11-10 10:00:00')")
    buzon.procesar(cfg, con, mail("ana@ejemplo.edu", "mi trabajo", "texto"), us, sync)
    assert con.execute("SELECT actividad FROM entregas").fetchone()[0] == "A4"


def test_inscripto_sin_actividad_queda_para_revisar(cfg, con, us):
    _inscribir(con, cfg, us)
    assert buzon.procesar(cfg, con, mail("ana@ejemplo.edu", "Consulta", "¿cuándo es?"), us, sync) == "revisar"
    assert con.execute("SELECT COUNT(*) FROM entregas").fetchone()[0] == 0


def test_no_inscripto_con_codigo_entra_y_seguridad_lo_acuartela(cfg, con, us):
    from tutor import seguridad
    buzon.procesar(cfg, con, mail("nadie@ejemplo.edu", "[BIO6-ALIM A1] x", "texto de varias palabras para probar"), us, sync)
    seguridad.revisar_entregas(cfg, con, "BIO6-ALIM")
    assert con.execute("SELECT motivo_cuarentena FROM entregas").fetchone()[0] == "no está inscripto"


def test_mismo_mail_no_se_procesa_dos_veces(cfg, con, us):
    _inscribir(con, cfg, us)
    m = mail("ana@ejemplo.edu", "[BIO6-ALIM A1] x", "texto")
    assert buzon.procesar(cfg, con, m, us, sync) == "entrega"
    assert buzon.procesar(cfg, con, m, us, sync) == "visto"


def test_adjuntos_se_guardan_con_nombre_seguro(cfg, con, us):
    _inscribir(con, cfg, us)
    m = buzon.parsear(mail("ana@ejemplo.edu", "[BIO6-ALIM A1] x", "va adjunto"))
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="../../etc/mi trabajo.pdf")
    buzon.procesar(cfg, con, bytes(m), us, sync)
    ruta = Path(con.execute("SELECT archivos FROM entregas").fetchone()[0])
    assert ruta.name == "mi_trabajo.pdf" and cfg.datos in ruta.parents


def test_sin_cita_html():
    t = buzon._html_a_texto("<p>Mi respuesta</p><blockquote>texto citado</blockquote>")
    assert "Mi respuesta" in t and "citado" not in t


def test_lectura_imap_es_solo_lectura():
    src = (RAIZ / "tutor" / "buzon.py").read_text()
    assert "readonly=True" in src and "BODY.PEEK" in src
    for prohibido in ("smtplib", "STORE", "EXPUNGE", "\\\\Seen", ".copy(", ".move("):
        assert prohibido not in src


# ---------- nota e iteraciones ----------

@pytest.mark.parametrize("niveles,nota", [
    (["Logrado"] * 4, 8), (["Inicial", "En desarrollo"], 5), (["En desarrollo", "Logrado", "Logrado", "En desarrollo"], 7),
    (["Destacado", "Logrado"], 9), ([], None),
])
def test_calcular_nota(niveles, nota):
    assert aplicar.calcular_nota(niveles) == nota


def test_linea_nota():
    assert "Aprobada" in aplicar.linea_nota(8, 7, 2) and "versión 2" in aplicar.linea_nota(8, 7, 2)
    assert "respondé este mail" in aplicar.linea_nota(6, 7, 1)
    assert aplicar.linea_nota(None, 7, 1) == ""


def _evaluar(con, unidad, eid, nivel):
    fila = con.execute("SELECT * FROM entregas WHERE id=?", (eid,)).fetchone()
    p = lote.paquete(con, unidad, fila)
    r = {"entrega_id": eid, "evaluaciones": [{"criterio": c, "nivel": nivel, "evidencia": "x"} for c in p["rubrica"]],
         "asunto": "a", "aclarar": "a", "valorar": "b", "preguntar": "c", "sugerir": "d", "proximo_paso": "e"}
    aplicar.guardar(con, unidad, p, r, aplicar.validar(r, p))
    return p


def test_iteracion_hasta_aprobar(cfg, con, us):
    u = us["BIO6-ALIM"]
    _inscribir(con, cfg, us)
    buzon.procesar(cfg, con, mail("ana@ejemplo.edu", "[BIO6-ALIM A4] x", "primera versión corta", mid="<v1@e>"), us, sync)
    p1 = _evaluar(con, u, 1, "En desarrollo")
    d = con.execute("SELECT * FROM devoluciones").fetchone()
    assert p1["iteracion"] == 1 and "Nota: 6/10" in d["cuerpo"] and d["asunto"].startswith("[BIO6-ALIM A4]")
    buzon.procesar(cfg, con, mail("ana@ejemplo.edu", "Re: [BIO6-ALIM A4] Devolución", "segunda versión mejorada",
                                  "Wed, 18 Nov 2026 10:00:00 -0300", "<v2@e>"), us, sync)
    p2 = _evaluar(con, u, 2, "Logrado")
    assert p2["iteracion"] == 2 and p2["antecedentes"]["version_anterior"]["nota"] == 6
    d = con.execute("SELECT * FROM devoluciones").fetchone()   # misma fila: reemplaza el borrador anterior
    assert "Nota: 8/10 — ¡Aprobada! (versión 2)" in d["cuerpo"]
    # el borrador responde en el hilo del alumno
    eml = correo.escribir_eml(cfg, con, d["id"]).read_text()
    assert "In-Reply-To: <v2@e>" in eml
    # planilla: última nota, 2 versiones, fecha dd/mm/aaaa
    ws = openpyxl.load_workbook(planilla.escribir(cfg, con, u)).active
    cab = [c.value for c in ws[1]]
    fila = [c.value for c in ws[2]]
    i = cab.index("A4 nota")
    assert fila[i:i + 4][0] == 8 and fila[i + 1] == 2 and fila[i + 3] == "aprobada"
    assert ws.cell(2, i + 3).number_format == "DD/MM/YYYY"
    # A1 sin entrega: celdas vacías, sin ceros
    j = cab.index("A1 nota (sin nota)")
    assert fila[j:j + 4] == [None, None, None, None]


def test_actividad_sin_nota_no_lleva_nota(cfg, con, us):
    u = us["BIO6-ALIM"]
    _inscribir(con, cfg, us)
    buzon.procesar(cfg, con, mail("ana@ejemplo.edu", "[BIO6-ALIM A1] x", "mi alimento"), us, sync)
    _evaluar(con, u, 1, "Logrado")
    assert con.execute("SELECT nota FROM analisis").fetchone()[0] is None
    assert "Nota:" not in con.execute("SELECT cuerpo FROM devoluciones").fetchone()[0]


def test_rondas_separan_versiones_del_mismo_alumno():
    from tutor import motor
    f = lambda i, m, a, t: {"id": i, "mail": m, "actividad": a, "marca_temporal": t}
    r = motor.rondas([f(1, "a", "A4", "1"), f(2, "b", "A4", "2"), f(3, "a", "A4", "3"), f(4, "a", "A1", "4")])
    assert [[x["id"] for x in ronda] for ronda in r] == [[1, 2, 4], [3]]
