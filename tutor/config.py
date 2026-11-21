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
    # Correo: se LEE la casilla (solo lectura) y se dejan BORRADORES por IMAP; nunca se envía
    cuenta: str = ""
    imap_host: str = "imap.gmail.com"
    carpeta: str = "INBOX"
    desde: str = ""                      # AAAA-MM-DD: no se leen mails anteriores
    firma_remitente: str = "Cátedra"
    zona_horaria: str = "America/Argentina/Buenos_Aires"
    # Calificación: nivel de rúbrica -> nota; promedio redondeado
    nota_inicial: int = 4
    nota_en_desarrollo: int = 6
    nota_logrado: int = 8
    nota_destacado: int = 10
    nota_aprobacion: int = 7
    # Audio: python que tiene faster-whisper (puede ser otro venv)
    audio_python: str = ""
    whisper_modelo: str = "medium"
    # IA
    claude_bin: str = "claude"
    modelo_rapido: str = "claude-haiku-5-5"   # fijado por versión: resultados reproducibles
    modelo_fuerte: str = "claude-sonnet-5-5"
    lote_max: int = 6
    # Seguridad
    dominios_permitidos: list = field(default_factory=lambda: ["colegio.edu.ar"])
    nomina: str = ""
    max_iteraciones: int = 6              # versiones de una misma actividad por alumno
    max_entregas_por_alumno_dia: int = 10
    max_entregas_por_corrida: int = 60
    tope_usd_corrida: float = 1.00
    max_caracteres_respuesta: int = 6000
    max_archivos: int = 5
    max_mb: float = 15
    max_minutos_audio: float = 10
    rafaga_por_minuto: int = 5
    # Panel web: solo estas IP de Tailscale (vacío = cualquiera con el token)
    panel_ips: list = field(default_factory=list)
    piloto: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def db(self) -> Path:
        return self.datos / "tutor.db"

    @property
    def equivalencias(self) -> dict:
        return {"Inicial": self.nota_inicial, "En desarrollo": self.nota_en_desarrollo,
                "Logrado": self.nota_logrado, "Destacado": self.nota_destacado}


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
                if k == "modelo":  # compatibilidad con configs viejas
                    cfg.modelo_rapido = v
                elif k == "max_entregas_por_actividad":
                    cfg.max_iteraciones = v
                elif k == "datos":
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
