"""
Central configuration for the Polymarket Trading Bot.

Secrets are read in priority order:
  1. Environment variables  (local dev, Railway, Docker)
  2. Streamlit secrets      (Streamlit Community Cloud)

Set secrets in the Streamlit Cloud dashboard under
  App Settings → Secrets  (TOML format, see secrets.toml.example)

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


# ── Secret resolver ───────────────────────────────────────────────────────────

def _secret(key: str, default: str = "") -> str:
    """
    Read a secret from env vars first, then Streamlit secrets.
    Safe to call at import time — Streamlit import failure is caught silently.
    """
    val = os.getenv(key, "").strip()
    if val:
        return val
    # Fallback: Streamlit Community Cloud injects secrets via st.secrets
    try:
        import streamlit as st  # noqa: PLC0415
        v = st.secrets.get(key, "")
        return str(v).strip() if v else default
    except Exception:
        return default


def _bool_secret(key: str, default: bool) -> bool:
    val = _secret(key, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


# ── Core toggles ─────────────────────────────────────────────────────────────

# True  → fetch live data (falls back to sample on error, unless PRODUCTION_MODE)
# False → always use built-in sample data
USE_REAL_DATA: bool = _bool_secret("USE_REAL_DATA", True)

# When True, any API failure raises RuntimeError instead of silently falling
# back to sample data.  Set this before going live.
PRODUCTION_MODE: bool = _bool_secret("PRODUCTION_MODE", False)


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
TAKER_FEE_PCT: float = float(_secret("TAKER_FEE_PCT", "2.0"))


# ── Live-trading credentials ──────────────────────────────────────────────────
# Read from environment or Streamlit secrets — never hard-code values here.

POLY_PRIVATE_KEY    = _secret("POLY_PRIVATE_KEY")
POLY_API_KEY        = _secret("POLY_API_KEY")
POLY_API_SECRET     = _secret("POLY_API_SECRET")
POLY_API_PASSPHRASE = _secret("POLY_API_PASSPHRASE")

POLY_CHAIN_ID = 137   # Polygon mainnet
