"""Base de datos SQLite del tutor. El esquema se puede aplicar las veces que haga falta."""

import sqlite3
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS alumnos (
    mail TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    curso TEXT NOT NULL,
    inscripto_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entregas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marca_temporal TEXT NOT NULL,
    mail TEXT NOT NULL,
    nombre TEXT,
    unidad TEXT NOT NULL,
    actividad TEXT NOT NULL,
    respuesta TEXT,
    uso_ia TEXT,
    autoevaluacion TEXT,
    archivos TEXT,              -- rutas locales separadas por \\n
    procesada INTEGER NOT NULL DEFAULT 0,
    UNIQUE (marca_temporal, mail, unidad, actividad)
);
CREATE TABLE IF NOT EXISTS adjuntos (
    sha256 TEXT PRIMARY KEY,
    ruta TEXT NOT NULL,
    tipo TEXT NOT NULL,         -- pdf, docx, texto, imagen, audio, otro
    texto TEXT,
    metodo TEXT,                -- pymupdf, ocr, whisper, ...
    confianza REAL,
    requiere_vision INTEGER NOT NULL DEFAULT 0,
    imagen_vision TEXT,         -- jpg reducido que mira la IA si hace falta
    procesado_en TEXT
);
CREATE TABLE IF NOT EXISTS entrega_adjuntos (
    entrega_id INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (entrega_id, sha256)
);
CREATE TABLE IF NOT EXISTS evaluaciones (
    entrega_id INTEGER NOT NULL,
    criterio TEXT NOT NULL,
    nivel TEXT NOT NULL,
    evidencia TEXT,
    PRIMARY KEY (entrega_id, criterio)
);
CREATE TABLE IF NOT EXISTS analisis (
    entrega_id INTEGER PRIMARY KEY,
    ideas_erroneas TEXT,        -- JSON
    senal_ia INTEGER NOT NULL DEFAULT 0,
    senal_ia_motivo TEXT,
    nota_docente TEXT,
    proximo_paso TEXT,
    revisar INTEGER NOT NULL DEFAULT 0,
    revisar_motivo TEXT,
    json_raw TEXT
);
CREATE TABLE IF NOT EXISTS devoluciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entrega_id INTEGER,
    mail TEXT NOT NULL,
    unidad TEXT NOT NULL,
    actividad TEXT NOT NULL,
    tipo TEXT NOT NULL,         -- devolucion | recordatorio
    asunto TEXT NOT NULL,
    cuerpo TEXT NOT NULL,
    ruta_eml TEXT,
    borrador_subido INTEGER NOT NULL DEFAULT 0,
    creado_en TEXT NOT NULL,
    UNIQUE (mail, unidad, actividad, tipo)
);
CREATE TABLE IF NOT EXISTS rechazos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mail TEXT NOT NULL,
    motivo TEXT NOT NULL,
    origen TEXT NOT NULL,       -- inscripcion | entrega
    fecha TEXT NOT NULL,
    UNIQUE (mail, origen)
);
CREATE TABLE IF NOT EXISTS corridas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT NOT NULL,
    fin TEXT,
    unidad TEXT,
    entregas INTEGER,
    llamadas_ia INTEGER,
    tokens_entrada INTEGER,
    tokens_salida INTEGER,
    costo_usd REAL
);
"""


COLUMNAS_NUEVAS = [
    ("entregas", "estado", "TEXT NOT NULL DEFAULT 'ok'"),   # ok | cuarentena
    ("entregas", "motivo_cuarentena", "TEXT"),
    ("entregas", "flags", "TEXT"),                           # JSON: sospechas, recortes, etc.
    ("adjuntos", "alertas", "TEXT"),                         # JSON: texto oculto, límites, inyección
    ("analisis", "intento_manipulacion", "INTEGER NOT NULL DEFAULT 0"),
]


def conectar(ruta: Path) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    nueva = not ruta.exists()
    con = sqlite3.connect(ruta)
    if nueva:
        ruta.chmod(0o600)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(ESQUEMA)
    for tabla, col, tipo in COLUMNAS_NUEVAS:
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    con.commit()
    return con


def inscripto(con, mail: str) -> bool:
    return con.execute("SELECT 1 FROM alumnos WHERE mail = ?", (mail.lower(),)).fetchone() is not None
