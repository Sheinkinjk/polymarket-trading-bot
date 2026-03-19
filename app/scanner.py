"""
Market scanner — fetches live data from the Polymarket Gamma API and
normalises it into the internal market format used by the rest of the system.

Data flow:
  fetch_live_markets()
      └─ _fetch_from_api()     ← httpx GET, raises on any error
          └─ _normalise_market()  ← field extraction + validation
      └─ fallback to SAMPLE_MARKETS on any exception

Field mapping (Gamma API → internal):
  endDate          → end_time       (ISO-8601 string)
  liquidityNum     → liquidity      (float, USD)
  volume24hr       → volume_24h     (float, USD)
  outcomePrices[0] → yes_price      (YES outcome price, 0-1)
  bestBid          → best_bid
  bestAsk          → best_ask
  bestAsk-bestBid  → spread         (computed when not provided)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import (
    USE_REAL_DATA,
    PRODUCTION_MODE,
    GAMMA_BASE,
    REQUEST_TIMEOUT,
    DEFAULT_LIMIT,
    LOG_SKIPPED_MARKETS,
)
from app.sample_data import SAMPLE_MARKETS

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def _safe_float(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        if v != v:          # NaN guard
            return default
        return v
    except (TypeError, ValueError):
        return default


def _parse_json_field(raw) -> list:
    """Parse a JSON string or pass through a list. Returns [] on failure."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _extract_yes_price(raw: dict) -> Optional[float]:
    """
    Extract the YES-outcome price with priority:
      1. outcomePrices[i] where outcomes[i] == 'Yes'
      2. outcomePrices[0]  (fallback — first outcome assumed YES)
      3. lastTradePrice
      4. bestAsk
    Returns None if nothing usable is found.
    """
    outcome_prices = _parse_json_field(raw.get("outcomePrices"))
    outcomes       = _parse_json_field(raw.get("outcomes"))

    if outcome_prices:
        # Try to find the YES index explicitly
        yes_index = 0  # default
        if outcomes:
            for i, o in enumerate(outcomes):
                if str(o).strip().lower() == "yes":
                    yes_index = i
                    break

        if yes_index < len(outcome_prices):
            price = _safe_float(outcome_prices[yes_index], default=-1)
            if 0 < price < 1:
                return round(price, 6)

    # Fallbacks
    for field in ("lastTradePrice", "bestAsk"):
        v = _safe_float(raw.get(field), default=-1)
        if 0 < v < 1:
            return round(v, 6)

    return None


def _parse_end_time(raw: dict) -> Optional[str]:
    """
    Return the market end time as an ISO-8601 string, or None.
    Preferred field: endDate (full timestamp).
    Fallback:        endDateIso (date only — noon UTC assumed).
    """
    end_date = raw.get("endDate")
    if end_date and isinstance(end_date, str) and len(end_date) > 8:
        return end_date

    iso_date = raw.get("endDateIso")
    if iso_date and isinstance(iso_date, str):
        # Date-only string: treat as noon UTC so hours_until is meaningful
        return f"{iso_date}T12:00:00Z"

    return None


def hours_until_end(market: dict) -> Optional[float]:
    """Hours between now (UTC) and the market's end_time. None if unparseable."""
    end_time_str = market.get("end_time")
    if not end_time_str:
        return None
    try:
        ts = end_time_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return round(delta, 2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _skip(market_id: str, reason: str) -> None:
    if LOG_SKIPPED_MARKETS:
        log.info("SKIP  id=%-12s  reason=%s", market_id, reason)


def _normalise_market(raw: dict) -> Optional[dict]:
    """
    Convert one raw Gamma API dict into the internal market format.
    Returns None (and logs the reason) if required fields are missing.

    Required:
      - id        (non-empty)
      - question  (non-empty)
      - yes_price (must be extractable and 0 < price < 1)

    Optional but captured:
      - end_time, liquidity, volume_24h, spread, best_bid, best_ask
    """
    market_id = str(raw.get("id", "")).strip()
    if not market_id:
        _skip("(no id)", "missing id field")
        return None

    question = str(raw.get("question", "")).strip()
    if not question:
        _skip(market_id, "missing question field")
        return None

    # ── Price ────────────────────────────────────────────────────────────────
    yes_price = _extract_yes_price(raw)
    if yes_price is None:
        _skip(market_id, f"no usable YES price  outcomePrices={raw.get('outcomePrices')!r}")
        return None

    # ── End time ─────────────────────────────────────────────────────────────
    end_time = _parse_end_time(raw)
    if end_time is None:
        _skip(market_id, "missing endDate and endDateIso")
        # Not a hard fail — the market can still be scored (hours will be None)

    # ── Spread ───────────────────────────────────────────────────────────────
    # Prefer the pre-computed field; fall back to bestAsk − bestBid
    best_bid = _safe_float(raw.get("bestBid"))
    best_ask = _safe_float(raw.get("bestAsk"))

    api_spread = _safe_float(raw.get("spread"), default=-1)
    if api_spread >= 0:
        spread = round(api_spread, 6)
    elif best_ask > 0 and best_bid > 0 and best_ask >= best_bid:
        spread = round(best_ask - best_bid, 6)
    else:
        # Can't compute — estimate from yes_price ± 0.005
        spread = 0.010
        best_bid = round(yes_price - 0.005, 6)
        best_ask = round(yes_price + 0.005, 6)

    # ── Liquidity & volume ───────────────────────────────────────────────────
    # liquidityNum is already a float; liquidity is a decimal string
    liquidity  = _safe_float(raw.get("liquidityNum") or raw.get("liquidity"))
    volume_24h = _safe_float(raw.get("volume24hr") or raw.get("volume24hrClob"))

    # ── Resolution status ────────────────────────────────────────────────────
    # 'closed' is set by the API when a market has ended (resolved or expired).
    # We capture it here so settlement can use the authoritative flag instead
    # of relying solely on price proxies.
    closed = bool(raw.get("closed", False))

    return {
        "id":         market_id,
        "question":   question,
        "end_time":   end_time,
        "yes_price":  yes_price,
        "liquidity":  liquidity,
        "volume_24h": volume_24h,
        "spread":     spread,
        "best_bid":   best_bid,
        "best_ask":   best_ask,
        "closed":     closed,
        "raw_data":   raw,          # kept for re-scoring without re-fetching
    }


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------

def _fetch_from_api(limit: int) -> list[dict]:
    """
    Fetch raw market dicts from the Gamma API.
    Raises on any network or parsing error.
    """
    params = {
        "active":     "true",
        "closed":     "false",
        "limit":      str(limit),
        "order":      "volume24hr",
        "ascending":  "false",
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        resp = client.get(f"{GAMMA_BASE}/markets", params=params)
        resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Expected list from API, got {type(data).__name__}")
    if len(data) == 0:
        raise ValueError("API returned an empty list")

    log.info("API returned %d raw markets", len(data))
    return data


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_live_markets(
    limit: int = DEFAULT_LIMIT,
    live_only: bool = False,
) -> tuple[list[dict], str]:
    """
    Fetch and normalise markets.

    Returns:
        (markets, source)
        source is 'live' when API data was used, 'sample' otherwise.

    Args:
        limit:     Max markets to fetch from the API.
        live_only: When True (or PRODUCTION_MODE is set), any API failure raises
                   RuntimeError instead of silently falling back to sample data.
                   Always pass live_only=True for production / real-money usage.
    """
    hard_fail = live_only or PRODUCTION_MODE

    if not USE_REAL_DATA:
        if hard_fail:
            raise RuntimeError(
                "USE_REAL_DATA is False but live_only/PRODUCTION_MODE is set. "
                "Set USE_REAL_DATA=true (or unset PRODUCTION_MODE) to fetch live data."
            )
        log.info("USE_REAL_DATA=False — loading sample data")
        return list(SAMPLE_MARKETS), "sample"

    try:
        raw_list = _fetch_from_api(limit)
    except Exception as exc:
        if hard_fail:
            raise RuntimeError(
                f"Live market fetch failed and PRODUCTION_MODE is active — "
                f"refusing to fall back to sample data. Error: {exc}"
            ) from exc
        log.warning("API fetch failed (%s) — falling back to sample data", exc)
        return list(SAMPLE_MARKETS), "sample"

    markets: list[dict] = []
    skipped = 0
    for raw in raw_list:
        normalised = _normalise_market(raw)
        if normalised is None:
            skipped += 1
        else:
            markets.append(normalised)

    log.info(
        "Normalised %d markets  (%d skipped, %d usable)",
        len(raw_list), skipped, len(markets),
    )

    if len(markets) == 0:
        if hard_fail:
            raise RuntimeError(
                "API returned data but no markets survived normalisation. "
                "Check the Polymarket API response format."
            )
        log.warning("No usable markets after normalisation — falling back to sample data")
        return list(SAMPLE_MARKETS), "sample"

    return markets, "live"


# ---------------------------------------------------------------------------
# Individual market resolution lookup
# ---------------------------------------------------------------------------

def fetch_market_resolution(market_id: str) -> Optional[dict]:
    """
    Fetch a single market by ID regardless of active/closed status.

    Used by the settlement engine to check whether a position has resolved
    after the market disappears from the main active scan.

    Returns a normalised market dict (with 'closed' and 'yes_price' set) or
    None if the request fails.
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(f"{GAMMA_BASE}/markets/{market_id}")
            resp.raise_for_status()

        data = resp.json()
        # Gamma API may return a single object or a list with one element
        raw = data[0] if isinstance(data, list) and data else data
        if not isinstance(raw, dict):
            return None

        return _normalise_market(raw)

    except Exception as exc:
        log.warning("Resolution check failed for market %s: %s", market_id, exc)
        return None
