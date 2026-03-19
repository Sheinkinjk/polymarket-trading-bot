#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Polymarket Trading Bot — One-time setup script
#  Run this ONCE before doing anything else.
# ─────────────────────────────────────────────────────────────
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Polymarket Bot — Setup                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 1. Check Python 3.11+
PYTHON=$(command -v python3.11 || command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "❌  Python not found. Install Python 3.11 from https://python.org"
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
echo "✅  Python found: $PY_VERSION  ($PYTHON)"

# 2. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "📦  Creating virtual environment…"
    $PYTHON -m venv .venv
    echo "✅  Virtual environment created."
else
    echo "✅  Virtual environment already exists."
fi

# 3. Activate and install
echo "📥  Installing dependencies…"
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✅  Setup complete!                    ║"
echo "║                                          ║"
echo "║   Next steps:                            ║"
echo "║     ./run.sh          → open dashboard   ║"
echo "║     ./scan.sh         → scan markets     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
