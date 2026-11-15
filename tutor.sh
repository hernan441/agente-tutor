#!/usr/bin/env bash
# Atajo: ./tutor.sh procesar BIO6-ALIM --piloto
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/venv/bin/python"; [ -x "$PY" ] || PY=python3
cd "$DIR" && exec "$PY" -m tutor "$@"
