"""
Live order execution via the Polymarket CLOB API.

══════════════════════════════════════════════════════════════
ACTION REQUIRED before this module can place real orders:
══════════════════════════════════════════════════════════════

Step 1 — Install the Polymarket Python client:
    pip install py-clob-client

Step 2 — Set these environment variables (never hard-code values):
    POLY_PRIVATE_KEY      Your Polygon wallet private key (hex, with or
                          without 0x prefix).  This signs every order.
    POLY_API_KEY          From https://polymarket.com/profile → "API Keys"
    POLY_API_SECRET       (same page)
    POLY_API_PASSPHRASE   (same page)

Step 3 — Verify credentials before going live:
    source .venv/bin/activate
    python -c "from app.execution import PolymarketExecutor; PolymarketExecutor().verify()"

Step 4 — Test on a tiny position first ($1 notional) and confirm the trade
    appears on your Polymarket dashboard before increasing size.

══════════════════════════════════════════════════════════════
How orders work on Polymarket
══════════════════════════════════════════════════════════════
- Polymarket runs a Central Limit Order Book (CLOB) on Polygon.
- Each binary market has two outcome tokens: YES and NO.
- Buying YES at price P means you pay P cents per share and receive
  $1 if the market resolves YES.
- Orders are limit orders — you specify a price and the CLOB fills
  at that price or better.
- We default to a limit price of best_ask + small buffer so the
  order fills immediately as a taker (crosses the spread).
- Taker fee is deducted from the gross payout on winning trades.
  Verify current rates at https://docs.polymarket.com/fees before going live.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from app.config import CLOB_BASE, POLY_CHAIN_ID, TAKER_FEE_PCT

log = logging.getLogger(__name__)

# ─── Required credential env-var names ───────────────────────────────────────

_REQUIRED_ENV_VARS = [
    "POLY_PRIVATE_KEY",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
]


# ─── Exceptions ───────────────────────────────────────────────────────────────

class CredentialsNotSet(RuntimeError):
    """Raised when one or more required credential env vars are missing."""


class ExecutionNotEnabled(RuntimeError):
    """Raised when execution is attempted without live-trading mode enabled."""


class OrderFailed(RuntimeError):
    """Raised when the CLOB API rejects an order."""


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """Returned by place_buy_yes_order() on success."""
    order_id:   str
    market_id:  str
    side:       str          # always "YES" for now
    notional:   float
    limit_price: float
    status:     str          # "live", "filled", "partial"
    filled_qty:  float = 0.0
    avg_price:   float = 0.0
    raw_response: dict = field(default_factory=dict)


@dataclass
class Position:
    """A single open position returned by get_positions()."""
    market_id:    str
    question:     str
    outcome:      str        # "YES" or "NO"
    size:         float      # number of shares held
    avg_price:    float
    current_price: float
    unrealised_pnl: float


# ─── Executor ─────────────────────────────────────────────────────────────────

class PolymarketExecutor:
    """
    Interface to the Polymarket CLOB for live order placement.

    Usage:
        executor = PolymarketExecutor()
        executor.verify()               # confirm credentials work
        result = executor.place_buy_yes_order(
            market_id="0xabc...",
            notional=50.0,              # USD to spend
            price=0.91,                 # limit price (use best_ask in practice)
        )
    """

    def __init__(self) -> None:
        self._check_credentials()
        self._client = None   # lazy-initialised on first use

    # ── Credential validation ─────────────────────────────────────────────────

    def _check_credentials(self) -> None:
        """Raise CredentialsNotSet if any required env vars are absent."""
        missing = [k for k in _REQUIRED_ENV_VARS if not os.getenv(k, "").strip()]
        if missing:
            raise CredentialsNotSet(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "See app/execution.py header for setup instructions."
            )

    @staticmethod
    def credentials_ready() -> bool:
        """Return True if all credential env vars are set (does not validate them)."""
        return all(os.getenv(k, "").strip() for k in _REQUIRED_ENV_VARS)

    # ── CLOB client (lazy) ────────────────────────────────────────────────────

    def _get_client(self):
        """Return a configured ClobClient instance (imported lazily)."""
        if self._client is not None:
            return self._client

        try:
            from py_clob_client.client import ClobClient        # type: ignore
        except ImportError as exc:
            raise ImportError(
                "py-clob-client is not installed.\n"
                "Run: pip install py-clob-client"
            ) from exc

        self._client = ClobClient(
            host=CLOB_BASE,
            chain_id=POLY_CHAIN_ID,
            key=os.getenv("POLY_PRIVATE_KEY"),
            creds={
                "api_key":        os.getenv("POLY_API_KEY"),
                "api_secret":     os.getenv("POLY_API_SECRET"),
                "api_passphrase": os.getenv("POLY_API_PASSPHRASE"),
            },
        )
        return self._client

    # ── Health check ─────────────────────────────────────────────────────────

    def verify(self) -> bool:
        """
        Ping the CLOB API to confirm credentials are valid.
        Returns True on success, raises RuntimeError on failure.
        """
        client = self._get_client()
        try:
            resp = client.get_ok()
            log.info("CLOB credential check OK: %s", resp)
            return True
        except Exception as exc:
            raise RuntimeError(
                f"CLOB credential verification failed: {exc}\n"
                "Check POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE."
            ) from exc

    # ── Token lookup ──────────────────────────────────────────────────────────

    def get_token_id(self, market_id: str, outcome: str = "YES") -> Optional[str]:
        """
        Return the CLOB token ID for a market outcome.

        market_id: Polymarket condition ID (from our internal 'id' field).
        outcome:   "YES" or "NO".

        The token ID is required for order placement.
        """
        client = self._get_client()
        try:
            market = client.get_market(market_id)
            tokens = market.get("tokens", [])
            for token in tokens:
                if token.get("outcome", "").upper() == outcome.upper():
                    return token.get("token_id")
            log.warning("No %s token found for market %s", outcome, market_id)
            return None
        except Exception as exc:
            log.error("get_token_id failed for %s: %s", market_id, exc)
            return None

    # ── Order placement ───────────────────────────────────────────────────────

    def place_buy_yes_order(
        self,
        market_id: str,
        notional: float,
        price: float,
        slippage_buffer: float = 0.002,
    ) -> OrderResult:
        """
        Place a limit buy order for YES shares.

        market_id:       Polymarket condition ID.
        notional:        USD amount to spend (position size).
        price:           Expected fill price (e.g. best_ask from scanner).
        slippage_buffer: Added to price to ensure taker fill (default 0.2¢).

        Returns an OrderResult. Raises OrderFailed if the API rejects the order.

        NOTE: On Polymarket the order size is specified in shares, not USD.
              shares = notional / limit_price
        """
        client      = self._get_client()
        token_id    = self.get_token_id(market_id, "YES")
        if not token_id:
            raise OrderFailed(f"Cannot place order — no YES token ID for {market_id}")

        limit_price = round(min(price + slippage_buffer, 0.99), 4)
        size        = round(notional / limit_price, 2)   # shares

        log.info(
            "Placing BUY YES order: market=%s  size=%.2f shares  "
            "limit=%.4f  notional=$%.2f",
            market_id, size, limit_price, notional,
        )

        try:
            from py_clob_client.order_builder.constants import BUY  # type: ignore
            resp = client.create_and_post_order(
                token_id=token_id,
                price=limit_price,
                size=size,
                side=BUY,
            )
        except Exception as exc:
            raise OrderFailed(
                f"Order rejected by CLOB: {exc}\n"
                f"market={market_id}  limit={limit_price}  size={size}"
            ) from exc

        order_id = resp.get("orderID") or resp.get("id") or ""
        status   = resp.get("status", "live")

        log.info("Order placed: order_id=%s  status=%s", order_id, status)

        return OrderResult(
            order_id=order_id,
            market_id=market_id,
            side="YES",
            notional=notional,
            limit_price=limit_price,
            status=status,
            raw_response=resp,
        )

    # ── Open orders ───────────────────────────────────────────────────────────

    def get_open_orders(self) -> list[dict]:
        """Return all open (unfilled) orders on the account."""
        client = self._get_client()
        try:
            return client.get_orders() or []
        except Exception as exc:
            log.error("get_open_orders failed: %s", exc)
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order by ID. Returns True on success."""
        client = self._get_client()
        try:
            client.cancel(order_id)
            log.info("Order %s cancelled", order_id)
            return True
        except Exception as exc:
            log.error("cancel_order failed for %s: %s", order_id, exc)
            return False

    def cancel_all_orders(self) -> int:
        """Cancel every open order. Returns count cancelled."""
        orders    = self.get_open_orders()
        cancelled = 0
        for order in orders:
            oid = order.get("id") or order.get("orderID", "")
            if oid and self.cancel_order(oid):
                cancelled += 1
        return cancelled

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        """
        Return all open positions (shares currently held).
        Maps CLOB positions to our Position dataclass.
        """
        client = self._get_client()
        try:
            raw_positions = client.get_positions() or []
        except Exception as exc:
            log.error("get_positions failed: %s", exc)
            return []

        out = []
        for p in raw_positions:
            size = float(p.get("size") or p.get("shares", 0))
            if size <= 0:
                continue
            avg   = float(p.get("avgPrice") or p.get("average_price", 0))
            curr  = float(p.get("currentPrice") or p.get("price", avg))
            upnl  = size * (curr - avg)
            out.append(Position(
                market_id=    p.get("conditionId") or p.get("market_id", ""),
                question=     p.get("question", ""),
                outcome=      p.get("outcome", "YES").upper(),
                size=         size,
                avg_price=    avg,
                current_price=curr,
                unrealised_pnl=round(upnl, 4),
            ))

        return out

    # ── Fee estimate ──────────────────────────────────────────────────────────

    @staticmethod
    def estimate_net_pnl(notional: float, fill_price: float) -> dict:
        """
        Estimate net P&L if this trade wins, accounting for fees.
        Useful for pre-trade sanity checks.
        """
        if fill_price <= 0 or fill_price >= 1:
            return {"gross": 0.0, "fee": 0.0, "net": 0.0}
        gross = notional * (1.0 - fill_price) / fill_price
        fee   = gross * (TAKER_FEE_PCT / 100.0)
        net   = gross - fee
        return {
            "gross":      round(gross, 4),
            "fee":        round(fee, 4),
            "net":        round(net, 4),
            "fee_pct":    TAKER_FEE_PCT,
            "fill_price": fill_price,
        }
