"""Transcribe audios con faster-whisper, local. Se corre con el python que tenga faster-whisper.

Uso: python whisper_cli.py MODELO "vocabulario" audio1 [audio2 ...]
Salida: JSON [{"archivo":..., "texto":...} | {"archivo":..., "error":...}]
No importa nada del proyecto a propósito: puede vivir en otro venv.
"""

import json
import sys


def main():
    modelo_nombre, vocab, *archivos = sys.argv[1:]
    from faster_whisper import WhisperModel

    modelo = WhisperModel(modelo_nombre, device="cpu", compute_type="int8")
    salida = []
    for a in archivos:
        try:
            segs, _ = modelo.transcribe(a, language="es", initial_prompt=vocab or None, vad_filter=True)
            salida.append({"archivo": a, "texto": " ".join(s.text.strip() for s in segs).strip()})
        except Exception as exc:
            salida.append({"archivo": a, "error": str(exc)})
    json.dump(salida, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
