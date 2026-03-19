#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Polymarket Trading Bot — Launch dashboard
# ─────────────────────────────────────────────────────────────
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Auto-setup if venv missing
if [ ! -d ".venv" ]; then
    echo "⚙️  First run detected — running setup…"
    bash setup.sh
fi

source .venv/bin/activate

echo ""
echo "🚀  Starting Polymarket Bot dashboard…"
echo "    Open your browser at: http://localhost:8501"
echo "    Press Ctrl+C to stop."
echo ""

streamlit run app/dashboard.py \
    --server.headless false \
    --server.port 8501 \
    --browser.gatherUsageStats false
