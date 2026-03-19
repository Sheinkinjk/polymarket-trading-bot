#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Polymarket Trading Bot — Run a market scan in the terminal
# ─────────────────────────────────────────────────────────────
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d ".venv" ]; then
    echo "⚙️  First run detected — running setup…"
    bash setup.sh
fi

source .venv/bin/activate

echo ""
python cli.py scan "$@"
