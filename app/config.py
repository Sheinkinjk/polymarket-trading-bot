"""
Central configuration for the Polymarket Trading Bot.

Environment variable reference
────────────────────────────────────────────────────────────────────────────────
General
  USE_REAL_DATA        true/false  (default true)  — live vs sample data
  PRODUCTION_MODE      true/false  (default false) — when true, any API failure
                                                     raises instead of falling
                                                     back to sample data

Live-trading credentials (required only for app/execution.py)
  POLY_PRIVATE_KEY     Ethereum private key (hex, with or without 0x prefix)
  POLY_API_KEY         From https://polymarket.com/profile → API Keys
  POLY_API_SECRET      (same page)
  POLY_API_PASSPHRASE  (same page)

Fee / slippage tuning
  TAKER_FEE_PCT        % of gross winnings charged as taker fee (default 2.0)
                       Verify the current rate at polymarket.com before going live.
"""
import os


# ── Core toggles ─────────────────────────────────────────────────────────────

def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name, "").strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


# True  → fetch live data (falls back to sample on error, unless PRODUCTION_MODE)
# False → always use built-in sample data
USE_REAL_DATA: bool = _bool_env("USE_REAL_DATA", True)

# When True, any API failure raises RuntimeError instead of silently falling
# back to sample data.  Set this before going live.
PRODUCTION_MODE: bool = _bool_env("PRODUCTION_MODE", False)


# ── API endpoints ─────────────────────────────────────────────────────────────

GAMMA_BASE      = "https://gamma-api.polymarket.com"
CLOB_BASE       = "https://clob.polymarket.com"
REQUEST_TIMEOUT = 20          # seconds
DEFAULT_LIMIT   = 250         # markets to fetch per scan


# ── Logging ───────────────────────────────────────────────────────────────────

LOG_SKIPPED_MARKETS = True


# ── Fee / slippage ────────────────────────────────────────────────────────────

# Percentage of gross winnings deducted as taker fee.
# Default 2.0 is a conservative estimate — verify actual rate before going live.
TAKER_FEE_PCT: float = float(os.getenv("TAKER_FEE_PCT", "2.0"))


# ── Live-trading credentials ──────────────────────────────────────────────────
# Read from environment — never hard-code values here.

POLY_PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY",    "")
POLY_API_KEY        = os.getenv("POLY_API_KEY",        "")
POLY_API_SECRET     = os.getenv("POLY_API_SECRET",     "")
POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE", "")

POLY_CHAIN_ID = 137   # Polygon mainnet
