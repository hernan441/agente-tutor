"""Lee unidades/<carpeta>/unidad.md y la convierte en datos (sin IA)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

NIVELES = ["Inicial", "En desarrollo", "Logrado", "Destacado"]


@dataclass
class Actividad:
    codigo: str
    nombre: str
    etapa: str
    consigna: str = ""
    esperado: str = ""
    ideas_erroneas: str = ""
    criterios: list = field(default_factory=list)


@dataclass
class Unidad:
    codigo: str
    carpeta: Path
    titulo: str
    materia: str = ""
    cursos: list = field(default_factory=list)
    actividades: dict = field(default_factory=dict)
    rubrica: dict = field(default_factory=dict)  # C1 -> {"nombre":..., "niveles": {nivel: desc}}
    parametros: str = ""
    tono: str = ""
    firma: str = ""
    sin_nota: list = field(default_factory=list)  # actividades que no llevan nota


def _campo(texto: str, etiqueta: str) -> str:
    """Valor de una línea '- **Etiqueta:** valor' (puede seguir en sub-viñetas)."""
    m = re.search(rf"^- \*\*{re.escape(etiqueta)}[^*]*:\*\*(.*?)(?=^- \*\*|\Z)", texto, re.M | re.S)
    return m.group(1).strip() if m else ""


def leer(carpeta: Path) -> Unidad:
    md = (carpeta / "unidad.md").read_text(encoding="utf-8")
    titulo = re.search(r"^# UNIDAD:\s*(.+)$", md, re.M).group(1).strip()
    secciones = re.split(r"^## ", md, flags=re.M)
    datos = next((s for s in secciones if s.startswith("1.")), "")
    u = Unidad(
        codigo=_campo(datos, "Código de unidad").split()[0] if _campo(datos, "Código de unidad") else carpeta.name,
        carpeta=carpeta,
        titulo=titulo,
        materia=_campo(datos, "Materia"),
        cursos=[c.strip() for c in _campo(datos, "Cursos").split(",") if c.strip()],
    )
    act_sec = next((s for s in secciones if s.startswith("4.")), "")
    for bloque in re.split(r"^### ", act_sec, flags=re.M)[1:]:
        cab, _, cuerpo = bloque.partition("\n")
        m = re.match(r"(A\d+)\s*[—-]\s*(.+?)(?:\((.+)\))?\s*$", cab.strip())
        if not m:
            continue
        a = Actividad(codigo=m.group(1), nombre=m.group(2).strip(), etapa=(m.group(3) or "").strip())
        a.consigna = _campo(cuerpo, "Consigna para el alumno")
        a.esperado = _campo(cuerpo, "Qué se espera ver")
        a.ideas_erroneas = _campo(cuerpo, "Ideas previas erróneas")
        a.criterios = re.findall(r"C\d+", _campo(cuerpo, "Criterios que se evalúan"))
        if "sin nota" in cuerpo.lower() or "sin nota" in a.etapa.lower():
            u.sin_nota.append(a.codigo)
        u.actividades[a.codigo] = a
    rub = next((s for s in secciones if s.startswith("5.")), "")
    for fila in re.findall(r"^\|\s*(C\d+)\s*[—-]\s*([^|]+)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|", rub, re.M):
        cod, nombre, *descs = fila
        u.rubrica[cod] = {"nombre": nombre.strip(), "niveles": dict(zip(NIVELES, (d.strip() for d in descs)))}
    u.parametros = next((s for s in secciones if s.startswith("6.")), "").partition("\n")[2].strip()
    u.tono = _campo(u.parametros, "Tono de la devolución")
    fm = re.search(r'Firma:\s*"([^"]+)"', u.tono) or re.search(r"Firma:\s*(.+)", u.tono)
    u.firma = fm.group(1).strip().rstrip(".") if fm else "La cátedra"
    if "no lleva nota" in u.parametros.lower():
        for cod in re.findall(r"(A\d+) no lleva nota", u.parametros):
            if cod not in u.sin_nota:
                u.sin_nota.append(cod)
    return u


def todas(raiz: Path) -> dict:
    out = {}
    for p in sorted(raiz.glob("*/unidad.md")):
        u = leer(p.parent)
        out[u.codigo] = u
    return out


def buscar(raiz: Path, codigo: str) -> Unidad:
    us = todas(raiz)
    if codigo not in us:
        raise SystemExit(f"No encuentro la unidad {codigo}. Hay: {', '.join(us) or 'ninguna'}")
    return us[codigo]
