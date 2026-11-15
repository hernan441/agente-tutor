"""Única puerta a la IA: manda paquetes al agente `tutor` (Haiku) y recibe JSON."""

import hashlib
import json
import re
import subprocess
import time


def _extraer_json(texto: str, abre: str = "["):
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    cierra = "]" if abre == "[" else "}"
    i, j = texto.find(abre), texto.rfind(cierra)
    if i == -1:
        raise ValueError(f"la IA no devolvió JSON: {texto[:200]}")
    return json.loads(texto[i:j + 1])


def _system_prompt(cfg) -> str:
    texto = (cfg.raiz / ".claude" / "agents" / "tutor.md").read_text(encoding="utf-8")
    return texto.split("---", 2)[2].strip() if texto.startswith("---") else texto


def version_prompt(cfg) -> str:
    """Hash corto del prompt del sistema: identifica con qué versión se evaluó cada cosa."""
    return hashlib.sha1(_system_prompt(cfg).encode()).hexdigest()[:10]


def llamar(cfg, prompt: str, abre: str = "[", con_imagenes: bool = False, modelo: str | None = None) -> tuple[object, dict]:
    """Devuelve (json_respuesta, uso). La IA corre encerrada:
    --restricted (sin Bash ni web), solo la herramienta Read y solo si hay imágenes que mirar,
    sin servidores MCP/conectores (ni Gmail ni Drive), y con la carpeta de trabajo en la caché
    de imágenes: no puede leer la base, los legajos ni la configuración."""
    cache = cfg.datos / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [cfg.claude_bin, "-p", "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--tools", "Read" if con_imagenes else "", "--model", modelo or cfg.modelo_rapido,
           "--system-prompt", _system_prompt(cfg), "--output-format", "json", prompt]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cache, timeout=1800)
    ms = int((time.monotonic() - t0) * 1000)
    if r.returncode:
        raise RuntimeError(f"claude falló: {(r.stderr or r.stdout)[-500:]}")
    salida = json.loads(r.stdout)
    u = salida.get("usage", {})
    uso = {
        "entrada": (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)),
        "cache": u.get("cache_read_input_tokens", 0),
        "salida": u.get("output_tokens", 0),
        "costo": salida.get("total_cost_usd", 0.0) or 0.0,
        "ms": ms,
        "modelo": next(iter(salida.get("modelUsage") or {}), modelo or cfg.modelo_rapido),
        "prompt": version_prompt(cfg),
    }
    return _extraer_json(salida.get("result", ""), abre), uso


COMUNES = ("AVISO", "actividad", "rubrica", "solo_para_vos")


def armar_prompt(paquetes: list) -> str:
    """La parte fija (actividad, rúbrica, datos internos) va una sola vez y primero; después las entregas.
    Las tandas son de una misma actividad. Las claves internas (que empiezan con _) no viajan."""
    comun = {k: paquetes[0][k] for k in COMUNES}
    entregas = [{k: v for k, v in p.items() if k not in COMUNES and not k.startswith("_")} for p in paquetes]
    return ("Evaluá estas entregas y devolvé solo el arreglo JSON pedido (un objeto por entrega_id).\n"
            + json.dumps({"comun": comun, "entregas": entregas}, ensure_ascii=False))


def evaluar(cfg, paquetes: list, modelo: str | None = None) -> tuple[list, dict]:
    imagenes = any(p["entrega"]["imagenes_para_mirar"] for p in paquetes)
    res, uso = llamar(cfg, armar_prompt(paquetes), con_imagenes=imagenes, modelo=modelo)
    return (res if isinstance(res, list) else [res]), uso


def sintesis_informe(cfg, resumen: dict) -> tuple[dict, dict]:
    prompt = ("Esto NO es un paquete de entregas: es el resumen de una unidad para el docente. "
              "Devolvé solo un objeto JSON {\"en_una_linea\": \"...\", \"sugerencias\": [\"...\", \"...\", \"...\"]} "
              "con lo más importante a saber antes de la próxima clase y hasta 3 sugerencias concretas "
              "para retomar en clase. No inventes datos que no estén acá.\n"
              + json.dumps(resumen, ensure_ascii=False))
    return llamar(cfg, prompt, abre="{")
