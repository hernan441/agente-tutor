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


def llamar(cfg, prompt: str, abre: str = "[") -> tuple[object, dict]:
    """Devuelve (json_respuesta, uso) — uso: tokens y costo."""
    cmd = [cfg.claude_bin, "-p", "--agent", "tutor", "--model", cfg.modelo,
           "--output-format", "json", "--permission-mode", "default", prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cfg.raiz, timeout=1800)
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
    prompt = ("Evaluá estos paquetes y devolvé solo el arreglo JSON pedido.\n"
              + json.dumps(paquetes, ensure_ascii=False))
    res, uso = llamar(cfg, prompt)
    return (res if isinstance(res, list) else [res]), uso


def sintesis_informe(cfg, resumen: dict) -> tuple[dict, dict]:
    prompt = ("Esto NO es un paquete de entregas: es el resumen de una unidad para el docente. "
              "Devolvé solo un objeto JSON {\"en_una_linea\": \"...\", \"sugerencias\": [\"...\", \"...\", \"...\"]} "
              "con lo más importante a saber antes de la próxima clase y hasta 3 sugerencias concretas "
              "para retomar en clase. No inventes datos que no estén acá.\n"
              + json.dumps(resumen, ensure_ascii=False))
    return llamar(cfg, prompt, abre="{")
