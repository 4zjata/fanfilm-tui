#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ ! -d ".venv" ]; then
    echo "Tworzenie izolowanego środowiska wirtualnego (venv)..."
    python3 -m venv .venv
    echo "Instalowanie zależności (textual, rich, pillow)..."
    .venv/bin/pip install -U pip
    .venv/bin/pip install textual rich pillow requests requests_cache textual-image pynacl
fi

exec .venv/bin/python fanfilm_tui.py "$@"
