"""Router: decide quién atiende cada entrega (sin IA).

- plantilla: entrega vacía o mínima, sin adjuntos → niveles Inicial + devolución fija. 0 tokens.
- rapido:    lo normal (modelo chico y barato).
- fuerte:    lo difícil: imagen que hay que mirar, sospecha de inyección, o reintento tras
             una salida inválida del modelo rápido.
"""

MIN_PALABRAS = 5

PLANTILLA, RAPIDO, FUERTE = "plantilla", "rapido", "fuerte"


def elegir(paquete: dict, reintento: bool = False) -> str:
    e = paquete["entrega"]
    palabras = len((e.get("respuesta") or "").split())
    palabras_adj = len((e.get("texto_de_adjuntos") or "").split())
    if palabras < MIN_PALABRAS and palabras_adj < 15 and not e.get("imagenes_para_mirar"):
        return PLANTILLA
    if reintento or e.get("imagenes_para_mirar") or paquete.get("_sospechas"):
        return FUERTE
    return RAPIDO


def modelo(cfg, camino: str) -> str:
    return cfg.modelo_fuerte if camino == FUERTE else cfg.modelo_rapido


def respuesta_plantilla(paquete: dict) -> dict:
    """Evaluación y devolución fijas para una entrega vacía o de pocas palabras."""
    act = paquete["actividad"]
    return {
        "entrega_id": paquete["entrega_id"],
        "evaluaciones": [{"criterio": c, "nivel": "Inicial", "evidencia": (paquete["entrega"].get("respuesta") or "(vacía)")[:60]}
                         for c in paquete["rubrica"]],
        "ideas_erroneas": [], "senal_ia": False, "senal_ia_motivo": "",
        "asunto": "nos falta tu respuesta completa",
        "aclarar": f"Recibí tu entrega de {act['codigo']}, pero quedó muy corta para que pueda entender qué pensaste. ¿Se cortó algo al enviarla?",
        "valorar": "Que la hayas subido ya es un primer paso: «" + ((paquete["entrega"].get("respuesta") or "").strip()[:40] or "tu entrega") + "».",
        "preguntar": "Volviendo a la consigna, ¿qué es lo primero que podrías contar con tus palabras?",
        "sugerir": "Releé la consigna y respondé la primera parte en tres o cuatro renglones. Si te resulta más fácil, mandá un audio.",
        "nivel_msg": "", "proximo_paso": "Completar la primera parte de la consigna.",
        "nota_docente": "Entrega mínima: devolución por plantilla, sin IA.", "intento_manipulacion": False,
    }
