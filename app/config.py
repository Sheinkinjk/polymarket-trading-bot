"""
Central configuration for the Polymarket Trading Bot.

Secrets are read from environment variables.
On Streamlit Community Cloud, add secrets via:
  App Settings → Secrets  (TOML format)
Streamlit Cloud injects those secrets as environment variables automatically,
so os.getenv() works in both local and cloud environments.

Environment variable reference
────────────────────────────────────────────────────────────────────────────────
General
  USE_REAL_DATA        true/false  (default true)
  PRODUCTION_MODE      true/false  (default false)

Live-trading credentials (required only for app/execution.py)
  POLY_PRIVATE_KEY     Ethereum private key (hex, with or without 0x prefix)
  POLY_API_KEY         From https://polymarket.com/profile → API Keys
  POLY_API_SECRET      (same page)
  POLY_API_PASSPHRASE  (same page)

Fee / slippage tuning
  TAKER_FEE_PCT        % of gross winnings charged as taker fee (default 2.0)
"""
import os


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name, "").strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


# ── Core toggles ─────────────────────────────────────────────────────────────

USE_REAL_DATA:   bool = _bool_env("USE_REAL_DATA",   True)
PRODUCTION_MODE: bool = _bool_env("PRODUCTION_MODE", False)


# ── API endpoints ─────────────────────────────────────────────────────────────

GAMMA_BASE      = "https://gamma-api.polymarket.com"
CLOB_BASE       = "https://clob.polymarket.com"
REQUEST_TIMEOUT = 20
DEFAULT_LIMIT   = 250


# ── Logging ───────────────────────────────────────────────────────────────────

LOG_SKIPPED_MARKETS = True


# ── Fee / slippage ────────────────────────────────────────────────────────────

TAKER_FEE_PCT: float = float(os.getenv("TAKER_FEE_PCT", "2.0"))


# ── Live-trading credentials ──────────────────────────────────────────────────

POLY_PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY",    "")
POLY_API_KEY        = os.getenv("POLY_API_KEY",        "")
POLY_API_SECRET     = os.getenv("POLY_API_SECRET",     "")
POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE", "")

POLY_CHAIN_ID = 137   # Polygon mainnet
