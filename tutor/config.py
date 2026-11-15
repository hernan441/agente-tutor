"""Configuración: lee config.toml (real, fuera de git) o config.example.toml."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raiz: Path = RAIZ
    datos: Path = RAIZ / "datos"
    unidades: Path = RAIZ / "unidades"
    plantillas: Path = RAIZ / "plantillas"
    # Google (cuenta de la cátedra) vía rclone, solo lectura
    rclone_remoto: str = "catedra"
    hoja_inscripcion: str = "Inscripción (respuestas)"
    hoja_entregas: str = "Entregas (respuestas)"
    carpeta_archivos: str = "Entregas (File responses)"
    # Correo: solo borradores por IMAP, nunca se envía
    cuenta: str = ""
    imap_host: str = "imap.gmail.com"
    firma_remitente: str = "Cátedra"
    # Audio: python que tiene faster-whisper (puede ser otro venv)
    audio_python: str = ""
    whisper_modelo: str = "medium"
    # IA
    claude_bin: str = "claude"
    modelo: str = "haiku"
    lote_max: int = 6
    # Seguridad
    dominios_permitidos: list = field(default_factory=lambda: ["colegio.edu.ar"])
    nomina: str = ""
    max_entregas_por_actividad: int = 3
    max_entregas_por_alumno_dia: int = 10
    max_entregas_por_corrida: int = 60
    tope_usd_corrida: float = 1.00
    max_caracteres_respuesta: int = 6000
    max_archivos: int = 5
    max_mb: float = 15
    max_minutos_audio: float = 10
    rafaga_por_minuto: int = 5
    piloto: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def db(self) -> Path:
        return self.datos / "tutor.db"


def cargar(piloto: bool = False) -> Config:
    cfg = Config()
    for nombre in ("config.toml", "config.example.toml"):
        p = RAIZ / nombre
        if p.exists():
            with open(p, "rb") as f:
                d = tomllib.load(f)
            plano = {}
            for k, v in d.items():
                if isinstance(v, dict):
                    plano.update(v)
                else:
                    plano[k] = v
            for k, v in plano.items():
                if k == "datos":
                    cfg.datos = (RAIZ / v).resolve()
                elif hasattr(cfg, k) and k not in ("unidades", "plantillas", "raiz", "extra"):
                    setattr(cfg, k, v)
                else:
                    cfg.extra[k] = v
            break
    if piloto:
        cfg.piloto = True
        cfg.datos = RAIZ / "datos_piloto"
        cfg.dominios_permitidos = ["ejemplo.edu"]  # los alumnos ficticios del piloto
        cfg.nomina = ""
    if os.environ.get("TUTOR_DATOS"):
        cfg.datos = Path(os.environ["TUTOR_DATOS"])
    cfg.datos.mkdir(parents=True, exist_ok=True, mode=0o700)
    return cfg
