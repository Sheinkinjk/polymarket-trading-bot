"""
48-hour paper-trading validation engine.

State is persisted in SQLite so the test survives app restarts.

Flow (each dashboard load or CLI refresh):
  get_active_run()           → check for a running test
  should_run_cycle(run_info) → True if >= scan_interval_minutes elapsed
  run_validation_cycle()     → fetch → settle → enter → log
  compute_metrics(run_id)    → all performance numbers
  generate_conclusion()      → plain-English summary

Paper-trade settlement:
  YES price >= 0.99  → market resolved YES  → WIN
  YES price <= 0.01  → market resolved NO   → LOSS
  End time passed, price unclear            → UNRESOLVED
  Sample data mode: MD5-hash outcome after hours_at_entry elapses
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.scanner import fetch_live_markets, hours_until_end
from app.strategy import run_pipeline, count_rejection_reasons
import app.config as _cfg

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "duration_hours":           48,
    "scan_interval_minutes":    15,
    "starting_balance":         10_000.0,
    "position_percent":         5.0,
    "max_open_positions":       10,
    "allow_tier_a":             True,
    "allow_tier_b":             True,
    "allow_tier_c":             False,
    "stop_new_entries_minutes": 20,
}

POSITION_FRACTIONS = {"1pct": 0.01, "2pct": 0.02, "5pct": 0.05}

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "markets.db")


# ── DB connection ──────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def start_test(config: Optional[dict] = None) -> int:
    """Create a new validation run in the DB. Returns the new run_id."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    now     = datetime.now(timezone.utc)
    ends_at = (now + timedelta(hours=cfg["duration_hours"])).isoformat()

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO validation_runs
                (started_at, ends_at, status, starting_balance,
                 position_percent, max_open_positions,
                 allow_tier_a, allow_tier_b, allow_tier_c,
                 scan_interval_minutes, stop_new_entries_minutes)
            VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now.isoformat(), ends_at,
                cfg["starting_balance"],
                cfg["position_percent"],
                cfg["max_open_positions"],
                1 if cfg["allow_tier_a"] else 0,
                1 if cfg["allow_tier_b"] else 0,
                1 if cfg["allow_tier_c"] else 0,
                cfg["scan_interval_minutes"],
                cfg["stop_new_entries_minutes"],
            ),
        )
        return cur.lastrowid


def stop_test(run_id: int) -> None:
    """Manually stop a running test."""
    with _connect() as conn:
        conn.execute(
            "UPDATE validation_runs SET status = 'stopped' WHERE id = ?",
            (run_id,),
        )


def get_active_run() -> Optional[dict]:
    """Return the most recent 'running' validation run, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM validation_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_run(run_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM validation_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def get_all_runs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM validation_runs ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_paper_trades(run_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE run_id = ? ORDER BY entry_at",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_validation_scans(run_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM validation_scans WHERE run_id = ? ORDER BY scanned_at",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Cycle scheduling ──────────────────────────────────────────────────────────

def should_run_cycle(run_info: dict) -> bool:
    """True if enough time has passed since the last scan."""
    last = run_info.get("last_scan_at")
    if not last:
        return True
    interval = run_info.get("scan_interval_minutes", 15)
    try:
        last_dt  = datetime.fromisoformat(last.replace("Z", "+00:00"))
        elapsed  = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        return elapsed >= interval
    except Exception:
        return True


def is_test_expired(run_info: dict) -> bool:
    """True if the 48-hour window has elapsed."""
    ends_at = run_info.get("ends_at", "")
    try:
        dt = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= dt
    except Exception:
        return False


def hours_remaining(run_info: dict) -> float:
    """Hours until the test ends (negative if expired)."""
    ends_at = run_info.get("ends_at", "")
    try:
        dt = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 0.0


# ── Main cycle ────────────────────────────────────────────────────────────────

def run_validation_cycle(run_id: int, run_info: dict) -> dict:
    """
    One full validation cycle:
      1. Fetch and score markets
      2. Settle any resolved open trades
      3. Enter new positions on qualifying markets
      4. Log a scan snapshot

    Returns a summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Auto-complete if time is up
    if is_test_expired(run_info):
        with _connect() as conn:
            conn.execute(
                "UPDATE validation_runs SET status = 'completed' WHERE id = ?",
                (run_id,),
            )
        return {"status": "completed", "message": "48-hour test window closed."}

    # Fetch and score markets
    raw, source = fetch_live_markets()
    processed   = run_pipeline(raw)
    accepted    = [m for m in processed if m.get("accepted")]

    # Settlement pass
    settled = _settle_open_trades(run_id, processed, source)

    # Entry pass (skip if test just expired mid-cycle)
    entered = 0
    if not is_test_expired(run_info):
        entered = _enter_new_positions(run_id, accepted, run_info)

    # Band counts for this cycle
    total_primary   = sum(1 for m in processed if m.get("band") == "primary")
    total_secondary = sum(1 for m in processed if m.get("band") == "secondary")
    total_watchlist = sum(1 for m in processed if m.get("band") == "watchlist")
    total_rejected  = sum(
        1 for m in processed
        if m.get("band") not in ("primary", "secondary", "watchlist")
    )
    rejection_counts = count_rejection_reasons(processed)

    # Persist scan log
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO validation_scans
                (run_id, scanned_at, total_scanned, total_primary, total_secondary,
                 total_watchlist, total_rejected, total_accepted,
                 new_positions, settled_positions, rejection_reasons, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, now, len(processed),
                total_primary, total_secondary, total_watchlist, total_rejected,
                len(accepted), entered, settled,
                json.dumps(rejection_counts), source,
            ),
        )
        conn.execute(
            "UPDATE validation_runs SET last_scan_at = ? WHERE id = ?",
            (now, run_id),
        )

    return {
        "status":     "ok",
        "scanned":    len(processed),
        "accepted":   len(accepted),
        "settled":    settled,
        "entered":    entered,
        "source":     source,
        "scanned_at": now,
    }


# ── Settlement ────────────────────────────────────────────────────────────────

def _sim_outcome(market_id: str, yes_price: float) -> bool:
    """Deterministic outcome for sample-data mode (MD5 hash → pseudo-random)."""
    digest  = hashlib.md5(market_id.encode()).hexdigest()[:8]
    pseudo  = int(digest, 16) % 10_000 / 10_000.0
    return pseudo < yes_price


def _settle_open_trades(
    run_id: int,
    current_markets: list[dict],
    source: str,
) -> int:
    """Check open trades against current market data and settle resolved ones."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE run_id = ? AND status = 'open'",
            (run_id,),
        ).fetchall()
    open_trades = [dict(r) for r in rows]
    if not open_trades:
        return 0

    market_by_id = {m["id"]: m for m in current_markets}
    now          = datetime.now(timezone.utc).isoformat()
    settled      = 0

    for trade in open_trades:
        mid         = trade["market_id"]
        entry_price = trade["entry_price"]
        outcome     = None   # 'won' | 'lost' | 'unresolved'
        exit_price  = None

        if mid in market_by_id:
            cm = market_by_id[mid]
            yp = cm.get("yes_price") or 0.5
            h  = hours_until_end(cm)

            if yp >= 0.99:
                outcome, exit_price = "won", 1.0
            elif yp <= 0.01:
                outcome, exit_price = "lost", 0.0
            elif h is not None and h <= -0.5:
                outcome, exit_price = "unresolved", yp

        else:
            # Market disappeared from scan — check if end_time has passed
            end_time_str = trade.get("end_time") or ""
            if end_time_str:
                try:
                    dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) > dt:
                        if source == "sample":
                            won = _sim_outcome(mid, entry_price)
                            outcome    = "won" if won else "lost"
                            exit_price = 1.0   if won else 0.0
                        else:
                            outcome, exit_price = "unresolved", None
                except Exception:
                    pass

        # Sample-data fallback: simulate outcome once hours_at_entry have elapsed
        if outcome is None and source == "sample":
            hours_at_entry = trade.get("hours_at_entry") or 0
            try:
                entry_dt   = datetime.fromisoformat(trade["entry_at"].replace("Z", "+00:00"))
                elapsed_h  = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                if elapsed_h >= max(hours_at_entry, 0.1):
                    won        = _sim_outcome(mid, entry_price)
                    outcome    = "won"  if won else "lost"
                    exit_price = 1.0   if won else 0.0
            except Exception:
                pass

        if outcome is None:
            continue  # Still genuinely open

        # Compute P&L for each sizing model
        def _pnl(notional: float, won: bool, ep: float) -> float:
            if not notional:
                return 0.0
            return notional * (1.0 - ep) / ep if won else -notional

        is_win = (outcome == "won")
        ep     = entry_price
        pnl_1  = _pnl(trade.get("notional_1pct") or 0, is_win, ep) if outcome != "unresolved" else 0.0
        pnl_2  = _pnl(trade.get("notional_2pct") or 0, is_win, ep) if outcome != "unresolved" else 0.0
        pnl_5  = _pnl(trade.get("notional_5pct") or 0, is_win, ep) if outcome != "unresolved" else 0.0

        with _connect() as conn:
            conn.execute(
                """
                UPDATE paper_trades
                SET status = ?, exit_at = ?, exit_price = ?,
                    pnl_1pct = ?, pnl_2pct = ?, pnl_5pct = ?
                WHERE id = ?
                """,
                (outcome, now, exit_price, pnl_1, pnl_2, pnl_5, trade["id"]),
            )
        settled += 1

    return settled


# ── Entry logic ───────────────────────────────────────────────────────────────

def _current_balance(run_id: int, model: str, starting_balance: float) -> float:
    """Current balance for a model = starting_balance + sum of settled P&L."""
    col = f"pnl_{model}"
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM({col}), 0) AS total FROM paper_trades "
            f"WHERE run_id = ? AND status IN ('won', 'lost')",
            (run_id,),
        ).fetchone()
    return starting_balance + (float(row["total"]) if row else 0.0)


def _has_open_position(run_id: int, market_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM paper_trades WHERE run_id = ? AND market_id = ? AND status = 'open'",
            (run_id, market_id),
        ).fetchone()
    return row is not None


def _count_open_positions(run_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_trades WHERE run_id = ? AND status = 'open'",
            (run_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def _enter_new_positions(
    run_id: int,
    accepted_markets: list[dict],
    run_info: dict,
) -> int:
    """Enter paper positions for qualifying accepted markets. Returns count entered."""
    sb        = float(run_info.get("starting_balance",          DEFAULT_CONFIG["starting_balance"]))
    max_open  = int(run_info.get("max_open_positions",          DEFAULT_CONFIG["max_open_positions"]))
    allow_a   = bool(run_info.get("allow_tier_a",               True))
    allow_b   = bool(run_info.get("allow_tier_b",               True))
    allow_c   = bool(run_info.get("allow_tier_c",               False))
    stop_mins = float(run_info.get("stop_new_entries_minutes",  DEFAULT_CONFIG["stop_new_entries_minutes"]))
    now       = datetime.now(timezone.utc).isoformat()
    entered   = 0

    for m in accepted_markets:
        if _count_open_positions(run_id) >= max_open:
            break

        if _has_open_position(run_id, m["id"]):
            continue

        tier = m.get("tier", "C")
        if tier == "A" and not allow_a:
            continue
        if tier == "B" and not allow_b:
            continue
        if tier == "C" and not allow_c:
            continue

        hours = m.get("hours_to_end") or 0
        if hours * 60 < stop_mins:
            continue

        # Position sizes — based on current balance per model
        bal_1 = _current_balance(run_id, "1pct", sb)
        bal_2 = _current_balance(run_id, "2pct", sb)
        bal_5 = _current_balance(run_id, "5pct", sb)
        n1    = round(bal_1 * POSITION_FRACTIONS["1pct"], 2)
        n2    = round(bal_2 * POSITION_FRACTIONS["2pct"], 2)
        n5    = round(bal_5 * POSITION_FRACTIONS["5pct"], 2)

        reason = (
            f"Score {m.get('score', 0):.0f}, "
            f"Tier {tier}, "
            f"{m.get('band', '?')} band, "
            f"{hours:.1f}h to close"
        )

        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_trades
                    (run_id, market_id, question, end_time, side,
                     score, tier, band, hours_at_entry,
                     entry_at, entry_price,
                     notional_1pct, notional_2pct, notional_5pct,
                     status, reason_entered)
                VALUES (?, ?, ?, ?, 'YES', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    run_id, m["id"], m.get("question"), m.get("end_time"),
                    m.get("score"), tier, m.get("band"), hours,
                    now, m.get("yes_price"),
                    n1, n2, n5,
                    reason,
                ),
            )
        entered += 1

    return entered


# ── Metrics ───────────────────────────────────────────────────────────────────

def _max_drawdown(equity_curve: list[float]) -> float:
    """Peak-to-trough max drawdown as a percentage of peak."""
    if len(equity_curve) < 2:
        return 0.0
    peak   = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _equity_curve(trades: list[dict], model: str, starting_balance: float) -> list[float]:
    pnl_key = f"pnl_{model}"
    settled = sorted(
        [t for t in trades if t["status"] in ("won", "lost")],
        key=lambda t: t.get("exit_at") or t.get("entry_at") or "",
    )
    balance = starting_balance
    curve   = [round(balance, 2)]
    for t in settled:
        balance += (t.get(pnl_key) or 0.0)
        curve.append(round(balance, 2))
    return curve


def compute_metrics(run_id: int) -> dict:
    """Compute all performance metrics for a validation run."""
    with _connect() as conn:
        run_row = conn.execute(
            "SELECT * FROM validation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        trade_rows = conn.execute(
            "SELECT * FROM paper_trades WHERE run_id = ? ORDER BY entry_at", (run_id,)
        ).fetchall()
        scan_rows = conn.execute(
            "SELECT * FROM validation_scans WHERE run_id = ? ORDER BY scanned_at", (run_id,)
        ).fetchall()

    if not run_row:
        return {}

    run    = dict(run_row)
    trades = [dict(r) for r in trade_rows]
    scans  = [dict(r) for r in scan_rows]
    sb     = float(run.get("starting_balance", 10_000.0))

    settled  = [t for t in trades if t["status"] in ("won", "lost")]
    open_t   = [t for t in trades if t["status"] == "open"]
    unres    = [t for t in trades if t["status"] == "unresolved"]

    def _model_stats(model: str) -> dict:
        pnl_key = f"pnl_{model}"
        not_key = f"notional_{model}"
        wins    = [t for t in settled if t["status"] == "won"]
        losses  = [t for t in settled if t["status"] == "lost"]
        w_pnls  = [t.get(pnl_key) or 0.0 for t in wins]
        l_pnls  = [t.get(pnl_key) or 0.0 for t in losses]
        all_p   = [t.get(pnl_key) or 0.0 for t in settled]

        total_pnl  = sum(all_p)
        final_bal  = sb + total_pnl
        gross_wins = sum(w_pnls)
        gross_loss = abs(sum(l_pnls))
        avg_win    = gross_wins / max(len(w_pnls), 1)
        avg_loss   = sum(l_pnls) / max(len(l_pnls), 1)

        eq     = _equity_curve(trades, model, sb)
        max_dd = _max_drawdown(eq)

        open_exp = sum(t.get(not_key) or 0.0 for t in open_t)

        return {
            "starting_balance":   sb,
            "final_balance":      round(final_bal, 2),
            "total_pnl":          round(total_pnl, 2),
            "roi_pct":            round(total_pnl / sb * 100, 2) if sb else 0.0,
            "total_trades":       len(settled),
            "open_trades":        len(open_t),
            "unresolved_trades":  len(unres),
            "wins":               len(wins),
            "losses":             len(losses),
            "win_rate":           round(len(wins) / max(len(settled), 1) * 100, 1),
            "loss_rate":          round(len(losses) / max(len(settled), 1) * 100, 1),
            "avg_win":            round(avg_win,  2),
            "avg_loss":           round(avg_loss, 2),
            "profit_factor":      round(gross_wins / max(gross_loss, 0.01), 2),
            "max_drawdown_pct":   round(max_dd, 1),
            "best_trade_pnl":     round(max(all_p, default=0.0), 2),
            "worst_trade_pnl":    round(min(all_p, default=0.0), 2),
            "open_exposure":      round(open_exp, 2),
            "equity_curve":       eq,
        }

    def _breakdown(key_fn) -> list[dict]:
        groups: dict = {}
        for t in settled:
            grp = key_fn(t)
            if grp not in groups:
                groups[grp] = {"wins": 0, "losses": 0, "pnl": 0.0}
            groups[grp]["wins"   if t["status"] == "won" else "losses"] += 1
            groups[grp]["pnl"] += t.get("pnl_5pct") or 0.0
        result = []
        for grp, d in sorted(groups.items()):
            n = d["wins"] + d["losses"]
            result.append({
                "group":    grp,
                "trades":   n,
                "wins":     d["wins"],
                "win_rate": round(d["wins"] / max(n, 1) * 100, 1),
                "pnl":      round(d["pnl"], 2),
            })
        return result

    def _score_bucket(t: dict) -> str:
        s = t.get("score") or 0
        if s >= 80: return "80-100"
        if s >= 70: return "70-79"
        if s >= 60: return "60-69"
        return "<60"

    def _price_bucket(t: dict) -> str:
        p = t.get("entry_price") or 0
        if p >= 0.93: return "0.93-0.95"
        if p >= 0.91: return "0.91-0.92"
        if p >= 0.89: return "0.89-0.90"
        return "0.87-0.88"

    def _hours_bucket(t: dict) -> str:
        h = t.get("hours_at_entry") or 0
        if h <= 4:  return "0-4h"
        if h <= 8:  return "4-8h"
        if h <= 12: return "8-12h"
        return "12-24h"

    # Time tracking
    hours_elapsed = 0.0
    try:
        s_dt = datetime.fromisoformat(run.get("started_at", "").replace("Z", "+00:00"))
        e_dt = min(
            datetime.now(timezone.utc),
            datetime.fromisoformat(run.get("ends_at", "").replace("Z", "+00:00")),
        )
        hours_elapsed = max((e_dt - s_dt).total_seconds() / 3600, 0.0)
    except Exception:
        pass

    return {
        "run":            run,
        "hours_elapsed":  round(hours_elapsed, 1),
        "models": {
            "1pct": _model_stats("1pct"),
            "2pct": _model_stats("2pct"),
            "5pct": _model_stats("5pct"),
        },
        "by_tier":   _breakdown(lambda t: {
            "A": "Tier A 🥇", "B": "Tier B 🥈", "C": "Tier C 🥉"
        }.get(t.get("tier") or "?", "?")),
        "by_score":  _breakdown(_score_bucket),
        "by_price":  _breakdown(_price_bucket),
        "by_hours":  _breakdown(_hours_bucket),
        "trades":    trades,
        "scans":     scans,
        "scan_count":                     len(scans),
        "total_scanned_across_all_cycles": sum(s.get("total_scanned", 0) for s in scans),
        "total_accepted_across_all_cycles": sum(s.get("total_accepted", 0) for s in scans),
    }


def get_permutation_stats(run_id: int) -> dict:
    """Aggregate permutation coverage across all scans in a run."""
    scans = get_validation_scans(run_id)

    total_scanned   = sum(s.get("total_scanned",   0) for s in scans)
    total_primary   = sum(s.get("total_primary",   0) for s in scans)
    total_secondary = sum(s.get("total_secondary", 0) for s in scans)
    total_watchlist = sum(s.get("total_watchlist", 0) for s in scans)
    total_rejected  = sum(s.get("total_rejected",  0) for s in scans)
    total_accepted  = sum(s.get("total_accepted",  0) for s in scans)

    agg_reasons: dict = {}
    for s in scans:
        try:
            rr = json.loads(s.get("rejection_reasons") or "{}")
            for k, v in rr.items():
                agg_reasons[k] = agg_reasons.get(k, 0) + v
        except Exception:
            pass

    pct_accepted = total_accepted / max(total_scanned, 1) * 100
    if pct_accepted < 1:
        rigidity = "too_rigid"
    elif pct_accepted > 15:
        rigidity = "too_loose"
    else:
        rigidity = "balanced"

    return {
        "total_scanned":    total_scanned,
        "total_primary":    total_primary,
        "total_secondary":  total_secondary,
        "total_watchlist":  total_watchlist,
        "total_rejected":   total_rejected,
        "total_accepted":   total_accepted,
        "pct_accepted":     round(pct_accepted, 1),
        "rejection_reasons": agg_reasons,
        "rigidity":         rigidity,
    }


# ── Plain-English conclusion ───────────────────────────────────────────────────

def generate_conclusion(metrics: dict) -> str:
    """Produce a plain-English test conclusion from computed metrics."""
    m5  = metrics.get("models", {}).get("5pct", {})
    m2  = metrics.get("models", {}).get("2pct", {})
    m1  = metrics.get("models", {}).get("1pct", {})

    total_trades = m5.get("total_trades", 0)
    win_rate     = m5.get("win_rate", 0.0)
    pnl_5        = m5.get("total_pnl", 0.0)
    pnl_2        = m2.get("total_pnl", 0.0)
    pnl_1        = m1.get("total_pnl", 0.0)
    max_dd_5     = m5.get("max_drawdown_pct", 0.0)

    by_tier  = metrics.get("by_tier",  [])
    by_score = metrics.get("by_score", [])

    lines = []

    if total_trades == 0:
        return (
            "No resolved trades yet — the test needs more time or more market scans. "
            "Visit this page again in a few hours to see results."
        )

    lines.append(f"The 48-hour test generated {total_trades} resolved trade{'s' if total_trades != 1 else ''}.")

    # Profitability
    if pnl_5 > 0:
        lines.append(f"The 5% model returned ${pnl_5:+,.2f} — the strategy appears profitable in this window.")
    elif pnl_5 < 0:
        lines.append(f"The 5% model lost ${abs(pnl_5):,.2f}. Consider tightening filters or reducing size.")

    # Best model
    best_pnl   = max(pnl_1, pnl_2, pnl_5)
    best_model = "1%" if best_pnl == pnl_1 else ("2%" if best_pnl == pnl_2 else "5%")
    lines.append(f"The {best_model} sizing model delivered the best risk-adjusted result.")

    # Win rate
    if win_rate >= 70:
        lines.append(f"Win rate was strong at {win_rate:.0f}% — the scoring model identified high-probability trades.")
    elif win_rate >= 50:
        lines.append(f"Win rate was {win_rate:.0f}% — decent, but filter tightening could improve selection quality.")
    else:
        lines.append(f"Win rate of {win_rate:.0f}% suggests the current filters are too loose — consider focusing on Tier A only.")

    # Drawdown
    if max_dd_5 > 20:
        lines.append(f"The 5% model experienced a drawdown of {max_dd_5:.1f}% — high sizing amplified losses significantly.")
    elif max_dd_5 > 10:
        lines.append(f"Max drawdown at 5% sizing was {max_dd_5:.1f}% — manageable but worth watching.")

    # Best tier
    viable_tiers = [r for r in by_tier if r.get("trades", 0) > 0]
    if viable_tiers:
        best_tier = max(viable_tiers, key=lambda r: r.get("win_rate", 0))
        lines.append(
            f"Strongest performance came from {best_tier['group']} trades "
            f"({best_tier['win_rate']:.0f}% win rate)."
        )

    # Best score bucket
    viable_scores = [r for r in by_score if r.get("trades", 0) > 0]
    if viable_scores:
        best_score = max(viable_scores, key=lambda r: r.get("win_rate", 0))
        lines.append(f"Score bucket {best_score['group']} produced the best hit rate.")

    # Recommendation
    if win_rate >= 65 and pnl_5 > 0 and max_dd_5 < 15:
        lines.append("Recommendation: continue testing — results look promising with current settings.")
    elif win_rate < 50:
        lines.append("Recommendation: tighten filters or restrict to Tier A only to improve trade quality.")
    elif max_dd_5 > 20:
        lines.append("Recommendation: reduce sizing to 2% or 1% — the strategy's variance is too high at 5%.")
    else:
        lines.append("Recommendation: run a longer test to build a larger sample of resolved trades.")

    return " ".join(lines)
