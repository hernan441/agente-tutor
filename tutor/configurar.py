"""Asistente de configuración inicial: ./tutor.sh configurar

Pregunta el mail de la cuenta del docente (la del colegio) y deja todo en config.toml y .env,
que NUNCA van al repositorio: antes de escribir verifica que git los ignore.
"""

import getpass
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

MAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
# Dominios que se sabe que usan los servidores de Google (Gmail o Workspace se detecta aparte)
IMAP_CONOCIDOS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
}
PUBLICOS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com"}


def mail_valido(mail: str) -> bool:
    return bool(MAIL_RE.match(mail.strip()))


def dominio_de(mail: str) -> str:
    return mail.strip().lower().rsplit("@", 1)[1]


def imap_de(mail: str) -> str:
    """Servidor IMAP sugerido. Para dominios propios (colegio) se asume Google Workspace."""
    return IMAP_CONOCIDOS.get(dominio_de(mail), "imap.gmail.com")


def _toml_str(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def generar_toml(plantilla: str, valores: dict) -> str:
    """Reemplaza en config.example.toml las claves dadas, conservando comentarios y orden."""
    texto = plantilla
    for clave, valor in valores.items():
        literal = "[" + ", ".join(_toml_str(x) for x in valor) + "]" if isinstance(valor, list) else _toml_str(valor)
        patron = re.compile(rf"^({re.escape(clave)}\s*=\s*).*$", re.M)
        if not patron.search(texto):
            raise KeyError(f"la plantilla no tiene la clave {clave}")
        texto = patron.sub(lambda m: m.group(1) + literal, texto, count=1)
    texto = texto.replace("# Copiar a config.toml (que NO va a git) y completar.",
                          "# Generado por ./tutor.sh configurar. NO va a git.", 1)
    tomllib.loads(texto)  # tiene que seguir siendo TOML válido
    return texto


def ignorado_por_git(raiz: Path, nombre: str) -> bool:
    """True si git ignora el archivo, o si la carpeta no es un repo git (no hay cómo subirlo)."""
    try:
        r = subprocess.run(["git", "-C", str(raiz), "check-ignore", "-q", nombre], capture_output=True)
    except FileNotFoundError:
        return True
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return True  # 128: no es un repositorio git


def _preguntar(texto: str, defecto: str = "") -> str:
    r = input(f"{texto}{f' [{defecto}]' if defecto else ''}: ").strip()
    return r or defecto


def _escribir_privado(ruta: Path, contenido: str):
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(contenido)
    os.chmod(ruta, 0o600)


def correr(raiz: Path):
    for nombre in ("config.toml", ".env"):
        if not ignorado_por_git(raiz, nombre):
            raise SystemExit(f"ERROR: {nombre} no está en .gitignore. No escribo nada para no subir tu mail al repo.")
    destino = raiz / "config.toml"
    if destino.exists() and _preguntar("Ya existe config.toml. ¿Reemplazarlo? (s/n)", "n").lower() != "s":
        print("Sin cambios.")
        return

    print("Configuración del agente tutor. Tus datos quedan solo en esta compu (config.toml y .env, fuera de git).\n")
    mail = ""
    while not mail_valido(mail):
        mail = _preguntar("Mail de tu cuenta (la del colegio, con la que vas a crear los formularios)")
    dom = dominio_de(mail)
    if dom in PUBLICOS:
        print(f"  Ojo: {dom} es un mail personal. Los alumnos tienen que inscribirse con el dominio del colegio.")
        dominios = _preguntar("Dominio de los mails de los alumnos (ej.: colegio.edu.ar)")
    else:
        dominios = _preguntar("Dominio permitido para los alumnos (separá con comas si son varios)", dom)
    dominios = [d.strip().lower().lstrip("@") for d in dominios.split(",") if d.strip()]
    imap = _preguntar("Servidor IMAP de tu correo", imap_de(mail))
    firma = _preguntar("Firma de las devoluciones", "Cátedra")
    remoto = _preguntar("Nombre del remoto de rclone", "catedra")

    plantilla = (raiz / "config.example.toml").read_text(encoding="utf-8")
    texto = generar_toml(plantilla, {"cuenta": mail.strip(), "dominios_permitidos": dominios,
                                     "imap_host": imap, "firma_remitente": firma, "rclone_remoto": remoto})
    _escribir_privado(destino, texto)
    print(f"\n✔ config.toml guardado (solo lo puede leer tu usuario).")

    clave = getpass.getpass("Contraseña de aplicación del correo, para subir borradores (Enter para omitir): ").strip()
    if clave:
        env = raiz / ".env"
        lineas = [l for l in (env.read_text().splitlines() if env.exists() else []) if not l.startswith("TUTOR_IMAP_PASS=")]
        lineas.append(f"TUTOR_IMAP_PASS={clave}")
        _escribir_privado(env, "\n".join(lineas) + "\n")
        print("✔ .env guardado.")

    print(f"""
Siguientes pasos:
  1. Crear los dos formularios con {mail.strip()} (ver formulario.md).
  2. Autorizar el Drive en modo solo lectura:
       rclone config create {remoto} drive scope=drive.readonly
  3. Probar: ./tutor.sh sync && ./tutor.sh estado""")
