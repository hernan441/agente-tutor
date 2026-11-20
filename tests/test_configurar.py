"""Configuración inicial y garantía de que ningún mail real llega al repositorio."""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from tutor import configurar

RAIZ = Path(__file__).resolve().parent.parent


def test_dominio_e_imap():
    assert configurar.dominio_de(" Juan.Perez@Colegio.Ejemplo.edu ") == "colegio.ejemplo.edu"
    assert configurar.imap_de("x@colegio.ejemplo.edu") == "imap.gmail.com"   # Workspace por defecto
    assert configurar.imap_de("x@" + "outlook.com") == "outlook.office365.com"


@pytest.mark.parametrize("mail,ok", [("a@colegio.ejemplo.edu", True), ("a@b", False), ("sin-arroba", False), ("a b@c.example", False)])
def test_mail_valido(mail, ok):
    assert configurar.mail_valido(mail) is ok


def test_generar_toml_desde_la_plantilla():
    plantilla = (RAIZ / "config.example.toml").read_text(encoding="utf-8")
    texto = configurar.generar_toml(plantilla, {"cuenta": "doc@colegio.ejemplo.edu", "dominios_permitidos": ["colegio.edu.ar", "alumnos.colegio.edu.ar"],
                                                "imap_host": "imap.gmail.com", "firma_remitente": 'Prof. "H"', "desde": "2027-03-01"})
    d = tomllib.loads(texto)
    assert d["correo"]["cuenta"] == "doc@colegio.ejemplo.edu"
    assert d["correo"]["firma_remitente"] == 'Prof. "H"'
    assert d["seguridad"]["dominios_permitidos"] == ["colegio.edu.ar", "alumnos.colegio.edu.ar"]
    assert d["correo"]["desde"] == "2027-03-01"
    assert d["seguridad"]["max_mb"] == 15          # el resto de la plantilla queda igual


def _es_repo():
    return subprocess.run(["git", "-C", str(RAIZ), "rev-parse"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _es_repo(), reason="no es un repositorio git")
@pytest.mark.parametrize("nombre", ["config.toml", ".env", "datos/tutor.db", "datos_piloto/tutor.db"])
def test_datos_privados_fuera_de_git(nombre):
    assert configurar.ignorado_por_git(RAIZ, nombre), f"{nombre} tiene que estar en .gitignore"


# Dominios ficticios que usan los tests y el piloto
FICTICIOS = {"b.edu", "anonimo.local"}


def _ficticio(dom):
    dom = dom.lower()
    return dom in FICTICIOS or dom.endswith(".example") or "ejemplo" in dom


@pytest.mark.skipif(not _es_repo(), reason="no es un repositorio git")
def test_ningun_mail_real_en_archivos_versionados():
    r = subprocess.run(["git", "-C", str(RAIZ), "grep", "-IhoE", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"],
                       capture_output=True, text=True)
    reales = sorted({m for m in r.stdout.split() if not _ficticio(m.rsplit("@", 1)[1])})
    assert not reales, f"mails reales en el repo: {reales}"
