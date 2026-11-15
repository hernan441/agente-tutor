"""Única puerta a la IA: manda paquetes al agente `tutor` (Haiku) y recibe JSON."""

import json
import re
import subprocess


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


def llamar(cfg, prompt: str, abre: str = "[", con_imagenes: bool = False) -> tuple[object, dict]:
    """Devuelve (json_respuesta, uso). La IA corre encerrada:
    --restricted (sin Bash ni web), solo la herramienta Read y solo si hay imágenes que mirar,
    sin servidores MCP/conectores (ni Gmail ni Drive), y con la carpeta de trabajo en la caché
    de imágenes: no puede leer la base, los legajos ni la configuración."""
    cache = cfg.datos / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [cfg.claude_bin, "-p", "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--tools", "Read" if con_imagenes else "", "--model", cfg.modelo,
           "--system-prompt", _system_prompt(cfg), "--output-format", "json", prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cache, timeout=1800)
    if r.returncode:
        raise RuntimeError(f"claude falló: {(r.stderr or r.stdout)[-500:]}")
    salida = json.loads(r.stdout)
    u = salida.get("usage", {})
    uso = {
        "entrada": (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)),
        "salida": u.get("output_tokens", 0),
        "costo": salida.get("total_cost_usd", 0.0) or 0.0,
    }
    return _extraer_json(salida.get("result", ""), abre), uso


def evaluar(cfg, paquetes: list) -> tuple[list, dict]:
    limpios = [{k: v for k, v in p.items() if not k.startswith("_")} for p in paquetes]
    prompt = ("Evaluá estos paquetes y devolvé solo el arreglo JSON pedido.\n"
              + json.dumps(limpios, ensure_ascii=False))
    imagenes = any(p["entrega"]["imagenes_para_mirar"] for p in paquetes)
    res, uso = llamar(cfg, prompt, con_imagenes=imagenes)
    return (res if isinstance(res, list) else [res]), uso


def sintesis_informe(cfg, resumen: dict) -> tuple[dict, dict]:
    prompt = ("Esto NO es un paquete de entregas: es el resumen de una unidad para el docente. "
              "Devolvé solo un objeto JSON {\"en_una_linea\": \"...\", \"sugerencias\": [\"...\", \"...\", \"...\"]} "
              "con lo más importante a saber antes de la próxima clase y hasta 3 sugerencias concretas "
              "para retomar en clase. No inventes datos que no estén acá.\n"
              + json.dumps(resumen, ensure_ascii=False))
    return llamar(cfg, prompt, abre="{")
