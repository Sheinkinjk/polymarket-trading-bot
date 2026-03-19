"""
Auto paper-trading engine — bankroll simulator with persistent SQLite state.

At each scan this module:
  1. Identifies top accepted opportunities (strict or broad mode)
  2. Simulates position entry with bankroll-based sizing
  3. Settles resolved positions against current market prices
  4. Tracks three parallel sizing models (2%, 3%, 5%) on the same entries

Paper-trading only — no wallet, no execution, no real money.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.config import TAKER_FEE_PCT
from app.database import _connect, init_db
from app.scanner import hours_until_end, fetch_market_resolution

# ─── Constants ────────────────────────────────────────────────────────────────

STARTING_BALANCE  = 10_000.0
DEFAULT_MODE      = "strict"
DEFAULT_LIMIT     = 5

POSITION_FRACTIONS = {"2pct": 0.02, "3pct": 0.03, "5pct": 0.05}

# Strict: Tier A always OK; Tier B only if score ≥ 80
STRICT_MIN_B_SCORE = 80.0
# Broad: Tier A or B with score ≥ 75
BROAD_MIN_SCORE    = 75.0

# Don't enter a position within this many hours of expiry
MIN_HOURS_AT_ENTRY = 0.5


# ─── Slippage model ───────────────────────────────────────────────────────────

def estimate_fill_price(
    best_ask: float,
    notional: float,
    liquidity: float,
    spread: float,
) -> float:
    """
    Estimate the realistic fill price for a market-taker YES buy order.

    Model: buying into the ask side moves price by (notional / liquidity) * spread.
    This is a linear market-impact model — simple but directionally correct.

    Capped at best_ask + 2 * spread to avoid extreme estimates on thin markets.
    Returns best_ask + spread (full-spread penalty) when no liquidity data exists.
    """
    if best_ask <= 0 or best_ask >= 1:
        return best_ask
    if liquidity <= 0 or spread <= 0:
        # No depth data — penalise by the full quoted spread
        return round(min(best_ask + spread, 0.99), 6)
    market_impact = (notional / liquidity) * spread
    fill = best_ask + market_impact
    max_fill = best_ask + 2.0 * spread
    return round(min(fill, max_fill, 0.99), 6)


# ─── Session management ───────────────────────────────────────────────────────

def get_active_session() -> Optional[dict]:
    """Return the current active auto-paper session, or None."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM auto_paper_sessions WHERE status = 'active' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_session(session_id: int) -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM auto_paper_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def get_all_sessions() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM auto_paper_sessions ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_or_create_session(
    mode: str = DEFAULT_MODE,
    limit: int = DEFAULT_LIMIT,
    starting_balance: float = STARTING_BALANCE,
) -> dict:
    """Return existing active session or create a new one."""
    session = get_active_session()
    return session if session else _create_session(mode, limit, starting_balance)


def reset_session(
    mode: str = DEFAULT_MODE,
    limit: int = DEFAULT_LIMIT,
    starting_balance: float = STARTING_BALANCE,
) -> dict:
    """Close any active session and start a fresh one."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE auto_paper_sessions SET status = 'completed' WHERE status = 'active'"
        )
    return _create_session(mode, limit, starting_balance)


def _create_session(mode: str, limit: int, starting_balance: float) -> dict:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO auto_paper_sessions "
            "(started_at, mode, top_limit, starting_balance, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (now, mode, limit, starting_balance),
        )
        session_id = cur.lastrowid
    return get_session(session_id)  # type: ignore[return-value]


# ─── Entry filters ────────────────────────────────────────────────────────────

def _passes_strict(market: dict) -> bool:
    tier  = market.get("tier", "C")
    score = float(market.get("score") or 0)
    return tier == "A" or (tier == "B" and score >= STRICT_MIN_B_SCORE)


def _passes_broad(market: dict) -> bool:
    tier  = market.get("tier", "C")
    score = float(market.get("score") or 0)
    return tier in ("A", "B") and score >= BROAD_MIN_SCORE


# ─── Balance helpers ──────────────────────────────────────────────────────────

def _current_balance(session_id: int, model: str, starting_balance: float) -> float:
    """Starting balance + sum of all realized PnL for this model."""
    col = f"pnl_{model}"
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM({col}), 0.0) FROM auto_paper_trades "
            f"WHERE session_id = ? AND status IN ('win', 'loss')",
            (session_id,),
        ).fetchone()
    return starting_balance + float(row[0] or 0)


def _open_market_ids(session_id: int) -> set:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT market_id FROM auto_paper_trades "
            "WHERE session_id = ? AND status = 'live'",
            (session_id,),
        ).fetchall()
    return {r[0] for r in rows}


# ─── Trade insertion ──────────────────────────────────────────────────────────

def _insert_trade(
    session_id: int,
    market: dict,
    notionals: dict,
    mode: str,
    now: str,
) -> None:
    price = float(market.get("yes_price") or 0.0)
    hours = float(market.get("hours_to_end") or 0.0)
    if hours == 0.0:
        h = hours_until_end(market)
        hours = round(h, 1) if h is not None else 0.0

    try:
        from app.labels import decision_summary as _ds, confidence_from_tier
        dsumm = _ds(market)
        conf  = confidence_from_tier(
            market.get("tier", "C"),
            float(market.get("score") or 0),
            True,
        )
    except Exception:
        dsumm = ""
        conf  = "Low"

    liq        = float(market.get("liquidity")  or 0.0)
    spread     = float(market.get("spread")     or 0.0)
    best_ask   = float(market.get("best_ask")   or price)
    # Use the largest notional (5%) to size the slippage estimate conservatively
    notional_5 = notionals.get("5pct", 0.0)
    fill_price = estimate_fill_price(best_ask, notional_5, liq, spread)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auto_paper_trades (
                session_id, market_id, question, chosen_side,
                entry_timestamp, entry_price, fill_price, hours_at_entry,
                score, tier, band, confidence, decision_summary,
                notional_2pct, notional_3pct, notional_5pct,
                status, entry_mode, entry_liquidity, entry_spread
            ) VALUES (
                ?, ?, ?, 'YES',
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                'live', ?, ?, ?
            )
            """,
            (
                session_id,
                market.get("id", ""),
                market.get("question", ""),
                now, price, fill_price, hours,
                float(market.get("score") or 0),
                market.get("tier", "C"),
                market.get("band", ""),
                conf, dsumm,
                notionals["2pct"],
                notionals["3pct"],
                notionals["5pct"],
                mode, liq, spread,
            ),
        )


def _insert_skipped(session_id: int, market: dict, reason: str, now: str) -> None:
    price = float(market.get("yes_price") or 0.0)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auto_paper_trades (
                session_id, market_id, question, chosen_side,
                entry_timestamp, entry_price, score, tier,
                notional_2pct, notional_3pct, notional_5pct,
                status, reason_skipped, entry_mode
            ) VALUES (
                ?, ?, ?, 'YES',
                ?, ?, ?, ?,
                0, 0, 0,
                'skipped', ?, 'skipped'
            )
            """,
            (
                session_id,
                market.get("id", ""),
                market.get("question", ""),
                now, price,
                float(market.get("score") or 0),
                market.get("tier", "C"),
                reason,
            ),
        )


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_auto_paper_entries(markets: list[dict], session: dict) -> dict:
    """
    Simulate entering positions in the top accepted opportunities.
    Returns a summary dict with counts.
    """
    session_id       = session["id"]
    mode             = session.get("mode", DEFAULT_MODE)
    limit            = int(session.get("top_limit", DEFAULT_LIMIT))
    starting_balance = float(session.get("starting_balance", STARTING_BALANCE))

    accepted  = [m for m in markets if m.get("accepted")]
    filter_fn = _passes_strict if mode == "strict" else _passes_broad
    candidates = [m for m in accepted if filter_fn(m)]
    candidates = sorted(
        candidates, key=lambda m: -(float(m.get("score") or 0))
    )[:limit]

    open_ids = _open_market_ids(session_id)
    now      = datetime.now(timezone.utc).isoformat()
    entered  = 0
    skipped  = 0

    for market in candidates:
        market_id = market.get("id", "")

        # Duplicate guard — silently skip if already open
        if market_id in open_ids:
            continue

        # Timing guard
        hours = float(market.get("hours_to_end") or 0.0)
        if hours == 0.0:
            h = hours_until_end(market)
            hours = round(h, 1) if h is not None else 0.0
        if hours < MIN_HOURS_AT_ENTRY:
            _insert_skipped(session_id, market, "too_close_to_expiry", now)
            skipped += 1
            continue

        # Compute notionals per model based on current balance
        notionals = {
            model: _current_balance(session_id, model, starting_balance) * frac
            for model, frac in POSITION_FRACTIONS.items()
        }

        _insert_trade(session_id, market, notionals, mode, now)
        open_ids.add(market_id)
        entered += 1

    # Update session last_scan_at
    ts = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE auto_paper_sessions SET last_scan_at = ? WHERE id = ?",
            (ts, session_id),
        )

    return {
        "entered":    entered,
        "skipped":    skipped,
        "candidates": len(candidates),
        "accepted":   len(accepted),
    }


# ─── Settlement ───────────────────────────────────────────────────────────────

def _pnl(notional: float, fill_price: float, outcome: str) -> float:
    """
    Prediction market P&L math including taker fee.

    fill_price: slippage-adjusted entry price (use entry_price for old trades).
    Gross win  = notional * (1 - fill_price) / fill_price
    Net win    = gross_win * (1 - TAKER_FEE_PCT / 100)
    """
    if outcome == "win":
        if fill_price <= 0 or fill_price >= 1:
            return 0.0
        gross = notional * (1.0 - fill_price) / fill_price
        fee   = gross * (TAKER_FEE_PCT / 100.0)
        return round(gross - fee, 4)
    if outcome == "loss":
        return -notional
    return 0.0


def _resolve_from_market(market: dict) -> tuple:
    """
    Derive settlement outcome from a normalised market dict.

    A market is only settled as win/loss when it is explicitly closed
    (closed=True) or when the price is definitively at a resolution value
    AND the market is past its end time.

    Returns (outcome, exit_price) or (None, None) if still live.
    """
    closed    = market.get("closed", False)
    yes_price = float(market.get("yes_price") or 0)
    h         = hours_until_end(market)
    past_end  = h is not None and h < -0.25   # 15-min grace period

    if closed or past_end:
        if yes_price >= 0.99:
            return "win",        1.0
        if yes_price <= 0.01:
            return "loss",       0.0
        return "unresolved",     yes_price

    return None, None


def settle_paper_trades(markets: list[dict], session_id: int) -> int:
    """
    Check all live trades and settle resolved ones.

    Resolution priority:
      1. Market is in current scan and 'closed=True' or past end_time.
      2. Market is NOT in the scan → fetch it individually from the API to get
         its final state (avoids relying on price proxies for closed markets).
      3. Market can't be fetched → mark unresolved after a generous timeout.

    P&L uses fill_price (slippage-adjusted) when available, falls back to
    entry_price for trades created before slippage tracking was added.
    Taker fees are deducted from winning P&L.
    """
    market_map = {m.get("id", ""): m for m in markets}

    with _connect() as conn:
        live_rows = conn.execute(
            "SELECT * FROM auto_paper_trades "
            "WHERE session_id = ? AND status = 'live'",
            (session_id,),
        ).fetchall()

    settled = 0

    for row in live_rows:
        trade      = dict(row)
        market_id  = trade["market_id"]
        outcome    = None
        exit_price = None

        market = market_map.get(market_id)

        if market:
            # Market is in this scan — check resolution flags
            outcome, exit_price = _resolve_from_market(market)
        else:
            # Market has dropped out of the active scan — it may have closed.
            # Only make the individual API call once the position is overdue.
            try:
                entry_dt   = datetime.fromisoformat(trade["entry_timestamp"])
                elapsed_h  = (
                    datetime.now(timezone.utc) - entry_dt
                ).total_seconds() / 3600
                hours_at   = float(trade.get("hours_at_entry") or 24)
            except Exception:
                elapsed_h = 0
                hours_at  = 24

            if elapsed_h > hours_at + 1.0:
                # Time is up — fetch definitive resolution from the API
                resolved_market = fetch_market_resolution(market_id)
                if resolved_market:
                    outcome, exit_price = _resolve_from_market(resolved_market)
                    # If still not closed, treat as unresolved after double timeout
                    if outcome is None and elapsed_h > hours_at + 4.0:
                        outcome    = "unresolved"
                        exit_price = float(
                            resolved_market.get("yes_price")
                            or trade.get("entry_price")
                            or 0.5
                        )
                else:
                    # API unavailable — mark unresolved after a generous buffer
                    if elapsed_h > hours_at + 4.0:
                        outcome    = "unresolved"
                        exit_price = float(trade.get("entry_price") or 0.5)

        if outcome is None:
            continue

        # Use slippage-adjusted fill_price for P&L; fall back to entry_price
        # for legacy rows created before fill_price tracking was added.
        fill_price = float(
            trade.get("fill_price") or trade.get("entry_price") or 0.5
        )
        pnl_2 = _pnl(float(trade.get("notional_2pct") or 0), fill_price, outcome)
        pnl_3 = _pnl(float(trade.get("notional_3pct") or 0), fill_price, outcome)
        pnl_5 = _pnl(float(trade.get("notional_5pct") or 0), fill_price, outcome)

        now = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE auto_paper_trades SET
                    status = ?, exit_timestamp = ?, exit_price = ?,
                    resolved_outcome = ?,
                    pnl_2pct = ?, pnl_3pct = ?, pnl_5pct = ?
                WHERE id = ?
                """,
                (outcome, now, exit_price, outcome,
                 pnl_2, pnl_3, pnl_5, trade["id"]),
            )
        settled += 1

    return settled


# ─── Trade queries ────────────────────────────────────────────────────────────

def get_live_trades(session_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM auto_paper_trades "
            "WHERE session_id = ? AND status = 'live' "
            "ORDER BY entry_timestamp DESC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_resolved_trades(session_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM auto_paper_trades "
            "WHERE session_id = ? AND status IN ('win', 'loss', 'unresolved') "
            "ORDER BY exit_timestamp DESC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_auto_trades(session_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM auto_paper_trades "
            "WHERE session_id = ? AND status != 'skipped' "
            "ORDER BY entry_timestamp DESC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Metrics ──────────────────────────────────────────────────────────────────

def _max_drawdown(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    peak   = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _equity_curve(trades: list[dict], model: str, starting_balance: float) -> list[float]:
    settled = sorted(
        [t for t in trades if t.get("status") in ("win", "loss")],
        key=lambda t: t.get("exit_timestamp") or "",
    )
    curve = [starting_balance]
    bal   = starting_balance
    for t in settled:
        bal += float(t.get(f"pnl_{model}") or 0)
        curve.append(round(bal, 2))
    return curve


def _model_metrics(
    trades: list[dict], model: str, starting_balance: float
) -> dict:
    all_t      = [t for t in trades if t.get("status") != "skipped"]
    live       = [t for t in all_t if t["status"] == "live"]
    wins       = [t for t in all_t if t["status"] == "win"]
    losses     = [t for t in all_t if t["status"] == "loss"]
    unresolved = [t for t in all_t if t["status"] == "unresolved"]
    settled    = wins + losses

    pnl_key     = f"pnl_{model}"
    notional_key = f"notional_{model}"

    total_pnl     = sum(float(t.get(pnl_key) or 0) for t in settled)
    final_balance = starting_balance + total_pnl
    roi_pct       = (total_pnl / starting_balance * 100) if starting_balance else 0

    win_pnls  = [float(t.get(pnl_key) or 0) for t in wins]
    loss_pnls = [float(t.get(pnl_key) or 0) for t in losses]
    avg_win   = sum(win_pnls)  / len(win_pnls)   if win_pnls  else 0.0
    avg_loss  = sum(loss_pnls) / len(loss_pnls)  if loss_pnls else 0.0
    win_rate  = len(wins) / len(settled) * 100   if settled   else 0.0

    gains  = sum(p for p in win_pnls  if p > 0)
    losses_ = abs(sum(p for p in loss_pnls if p < 0))
    profit_factor = (gains / losses_) if losses_ > 0 else float("inf")

    curve  = _equity_curve(all_t, model, starting_balance)
    max_dd = _max_drawdown(curve)

    unresolved_exposure = sum(
        float(t.get(notional_key) or 0) for t in live + unresolved
    )

    return {
        "starting_balance":    starting_balance,
        "final_balance":       round(final_balance, 2),
        "total_pnl":           round(total_pnl, 2),
        "roi_pct":             round(roi_pct, 2),
        "total_trades":        len(settled),
        "live_trades":         len(live),
        "wins":                len(wins),
        "losses":              len(losses),
        "unresolved":          len(unresolved),
        "win_rate":            round(win_rate, 1),
        "avg_win":             round(avg_win, 2),
        "avg_loss":            round(avg_loss, 2),
        "profit_factor":       round(profit_factor, 2),
        "max_drawdown_pct":    max_dd,
        "equity_curve":        curve,
        "unresolved_exposure": round(unresolved_exposure, 2),
    }


def compute_bankroll_metrics(session_id: int) -> dict:
    """Full performance metrics across all three sizing models."""
    session = get_session(session_id)
    if not session:
        return {}
    starting = float(session.get("starting_balance") or STARTING_BALANCE)
    trades   = get_all_auto_trades(session_id)
    return {
        "session": session,
        "models": {
            "2pct": _model_metrics(trades, "2pct", starting),
            "3pct": _model_metrics(trades, "3pct", starting),
            "5pct": _model_metrics(trades, "5pct", starting),
        },
        "trades": trades,
    }


def generate_bankroll_summary(metrics: dict) -> str:
    """Generate a plain-English summary paragraph for the bankroll dashboard."""
    if not metrics:
        return "No data yet — run a scan to start tracking positions."

    m2   = metrics["models"]["2pct"]
    m3   = metrics["models"]["3pct"]
    m5   = metrics["models"]["5pct"]
    sess = metrics["session"]

    lines = []

    # Overall profitability (use 3% as baseline)
    if m3["total_trades"] == 0 and m3["live_trades"] == 0:
        lines.append("No positions entered yet — click 'Run Auto Paper Entries' to start.")
    elif m3["total_trades"] == 0:
        lines.append(
            f"{m3['live_trades']} position(s) are currently live. "
            "Waiting for markets to resolve before P&L can be calculated."
        )
    elif m3["total_pnl"] > 0:
        lines.append(
            f"The system is currently profitable in paper trading "
            f"(+${m3['total_pnl']:,.2f} on the 3% model), with a "
            f"{m3['win_rate']:.0f}% win rate across {m3['total_trades']} resolved trades."
        )
    else:
        lines.append(
            f"The system is currently at a loss "
            f"(${m3['total_pnl']:,.2f} on the 3% model). "
            f"Win rate is {m3['win_rate']:.0f}% across {m3['total_trades']} resolved trades."
        )

    # Sizing comparison (only when we have resolved trades)
    if m3["total_trades"] > 0:
        if m5["max_drawdown_pct"] > m3["max_drawdown_pct"] + 5:
            lines.append(
                f"Aggressive 5% sizing is producing larger drawdowns "
                f"({m5['max_drawdown_pct']:.1f}%) vs 3% ({m3['max_drawdown_pct']:.1f}%) "
                f"and 2% ({m2['max_drawdown_pct']:.1f}%)."
            )
        if m5["total_pnl"] > m3["total_pnl"] and m5["total_pnl"] > 0:
            lines.append(
                f"The 5% model is generating the highest absolute P&L "
                f"(${m5['total_pnl']:+,.2f}) at the cost of higher risk."
            )

    # Mode
    mode = sess.get("mode", "strict")
    if mode == "strict":
        lines.append(
            "Strict mode is active — only Tier A and high-scoring Tier B (≥80) "
            "markets are eligible, prioritising quality over quantity."
        )
    else:
        lines.append(
            "Broad mode is active — Tier A and Tier B (≥75) are eligible, "
            "generating more trades with slightly lower average quality."
        )

    # Live exposure
    if m3["live_trades"] > 0:
        lines.append(
            f"{m3['live_trades']} position(s) live with "
            f"${m3['unresolved_exposure']:,.2f} unresolved exposure (3% model)."
        )

    return "  ".join(lines)
