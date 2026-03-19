"""
Training analytics — high-confidence edge detection and decision engine.

Turns resolved paper-trade data into actionable trading rules:
  - Filters noise (minimum 10 trades per bucket before any insight is shown)
  - Detects Strong / Moderate / No / Negative edges per dimension
  - Calibrates the scoring model against actual win rates
  - Surfaces "What To Trade Now" and "What To Avoid"
  - Identifies top performing trade patterns (combinations)
  - Generates exportable insight reports
"""
from __future__ import annotations

from datetime import datetime, timezone

# ─── Thresholds ───────────────────────────────────────────────────────────────

MIN_SAMPLE        = 10     # Minimum trades for any insight to be shown
STRONG_WR         = 60.0   # Win rate threshold for Strong Edge
STRONG_PF         = 1.5    # Profit factor threshold for Strong Edge
MODERATE_WR       = 55.0
MODERATE_PF       = 1.2
NEGATIVE_WR       = 45.0
NEGATIVE_PF       = 0.9


# ─── Edge classification ──────────────────────────────────────────────────────

def _edge_label(count: int, win_rate: float, profit_factor: float) -> str:
    if count < MIN_SAMPLE:
        return "Insufficient Data"
    if win_rate >= STRONG_WR and profit_factor >= STRONG_PF:
        return "Strong Edge"
    if win_rate >= MODERATE_WR and profit_factor >= MODERATE_PF:
        return "Moderate Edge"
    if win_rate < NEGATIVE_WR or profit_factor < NEGATIVE_PF:
        return "Negative Edge"
    return "No Edge"


# ─── Bucket helpers ───────────────────────────────────────────────────────────

def _score_bucket(score: float) -> str:
    if score >= 90: return "90+"
    if score >= 85: return "85–89"
    if score >= 80: return "80–84"
    if score >= 75: return "75–79"
    if score >= 70: return "70–74"
    return "65–69"


def _price_bucket(price: float) -> str:
    if price >= 0.95: return "0.95+"
    if price >= 0.92: return "0.92–0.94"
    if price >= 0.89: return "0.89–0.91"
    return "0.85–0.88"


def _hours_bucket(hours: float) -> str:
    if hours >= 12: return "12–24h"
    if hours >= 6:  return "6–12h"
    if hours >= 3:  return "3–6h"
    if hours >= 1:  return "1–3h"
    return "<1h"


def _liq_bucket(liq: float) -> str:
    if liq <= 0:       return "Unknown"
    if liq >= 500_000: return "$500K+"
    if liq >= 100_000: return "$100K–500K"
    if liq >= 25_000:  return "$25K–100K"
    if liq >= 10_000:  return "$10K–25K"
    return "<$10K"


def _spread_bucket(spread: float) -> str:
    if spread <= 0:    return "Unknown"
    if spread >= 0.012: return ">0.012"
    if spread >= 0.007: return "0.007–0.012"
    if spread >= 0.003: return "0.003–0.007"
    return "<0.003"


# ─── Core grouping with edge detection ───────────────────────────────────────

def _group_by(
    trades: list[dict],
    key_fn,
    pnl_key: str = "pnl_3pct",
) -> list[dict]:
    """
    Group resolved trades by a bucketing function.
    Computes win rate, profit factor, and edge label per group.
    Only returns groups with at least 1 trade; 'sufficient' flags >= MIN_SAMPLE.
    """
    groups: dict[str, list] = {}
    for t in trades:
        k = key_fn(t)
        groups.setdefault(k, []).append(t)

    rows = []
    for group, items in sorted(groups.items()):
        wins    = [t for t in items if t.get("status") == "win"]
        losses  = [t for t in items if t.get("status") == "loss"]
        settled = wins + losses
        if not settled:
            continue

        total_pnl  = sum(float(t.get(pnl_key) or 0) for t in settled)
        gain_sum   = sum(float(t.get(pnl_key) or 0) for t in wins)
        loss_abs   = abs(sum(float(t.get(pnl_key) or 0) for t in losses))
        pf         = (gain_sum / loss_abs) if loss_abs > 0 else (99.0 if gain_sum > 0 else 0.0)
        wr         = len(wins) / len(settled) * 100
        count      = len(settled)

        rows.append({
            "group":         group,
            "trades":        count,
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(wr, 1),
            "total_pnl":     round(total_pnl, 2),
            "avg_pnl":       round(total_pnl / count, 2),
            "profit_factor": round(min(pf, 99.0), 2),
            "edge":          _edge_label(count, wr, pf),
            "sufficient":    count >= MIN_SAMPLE,
        })
    return rows


# ─── Score calibration ────────────────────────────────────────────────────────

def calibrate_scores(trades: list[dict]) -> dict:
    """
    Check whether the scoring model is actually predictive.
    Buckets trades into narrow score ranges and checks if win rate
    increases monotonically with score (= well calibrated).
    """
    resolved = [t for t in trades if t.get("status") in ("win", "loss")]

    BUCKETS = ["65–70", "70–75", "75–80", "80–85", "85–90", "90+"]

    def _cal_bucket(score: float) -> str:
        if score >= 90: return "90+"
        if score >= 85: return "85–90"
        if score >= 80: return "80–85"
        if score >= 75: return "75–80"
        if score >= 70: return "70–75"
        return "65–70"

    groups: dict[str, dict] = {b: {"wins": 0, "total": 0} for b in BUCKETS}
    for t in resolved:
        b = _cal_bucket(float(t.get("score") or 0))
        groups[b]["total"] += 1
        if t.get("status") == "win":
            groups[b]["wins"] += 1

    rows = []
    for bucket in BUCKETS:
        d   = groups[bucket]
        wr  = d["wins"] / d["total"] * 100 if d["total"] > 0 else None
        rows.append({
            "bucket":     bucket,
            "trades":     d["total"],
            "win_rate":   round(wr, 1) if wr is not None else None,
            "sufficient": d["total"] >= MIN_SAMPLE,
        })

    # Calibration verdict: do sufficient buckets show rising win rate with score?
    sufficient = [r for r in rows if r["sufficient"] and r["win_rate"] is not None]
    if len(sufficient) < 2:
        verdict = "insufficient_data"
    else:
        win_rates = [r["win_rate"] for r in sufficient]
        ups = sum(1 for a, b in zip(win_rates, win_rates[1:]) if b >= a)
        ratio = ups / (len(win_rates) - 1)
        if ratio >= 0.70:
            verdict = "well_calibrated"
        elif ratio <= 0.35:
            verdict = "inverted"
        else:
            verdict = "not_predictive"

    return {"rows": rows, "verdict": verdict}


# ─── Top trade patterns ───────────────────────────────────────────────────────

_PATTERNS: list[tuple] = [
    (
        "High Score + High Confidence",
        lambda t: float(t.get("score") or 0) >= 80 and t.get("confidence") == "High",
    ),
    (
        "High Score + Prime Timing (3–10h)",
        lambda t: float(t.get("score") or 0) >= 80
                  and 3 <= float(t.get("hours_at_entry") or 0) <= 10,
    ),
    (
        "Tier A + Ideal Price (0.89–0.93)",
        lambda t: t.get("tier") == "A"
                  and 0.89 <= float(t.get("entry_price") or 0) <= 0.93,
    ),
    (
        "Tier A + Prime Timing (2–10h)",
        lambda t: t.get("tier") == "A"
                  and 2 <= float(t.get("hours_at_entry") or 0) <= 10,
    ),
    (
        "Ideal Price + Good Timing (2–12h)",
        lambda t: 0.89 <= float(t.get("entry_price") or 0) <= 0.93
                  and 2 <= float(t.get("hours_at_entry") or 0) <= 12,
    ),
    (
        "Mid Score (70–80) + Good Timing",
        lambda t: 70 <= float(t.get("score") or 0) < 80
                  and 3 <= float(t.get("hours_at_entry") or 0) <= 12,
    ),
    (
        "Strict Mode + High Score",
        lambda t: t.get("entry_mode") == "strict"
                  and float(t.get("score") or 0) >= 80,
    ),
    (
        "High Score + Conservative Price (0.89–0.91)",
        lambda t: float(t.get("score") or 0) >= 80
                  and 0.89 <= float(t.get("entry_price") or 0) <= 0.91,
    ),
]


def find_top_patterns(
    trades: list[dict], pnl_key: str = "pnl_3pct"
) -> list[dict]:
    """
    Test pre-defined combinations and return the top 3 by win rate.
    Only includes patterns with >= MIN_SAMPLE trades.
    """
    resolved = [t for t in trades if t.get("status") in ("win", "loss")]
    results  = []

    for name, filter_fn in _PATTERNS:
        matching = [t for t in resolved if filter_fn(t)]
        if len(matching) < MIN_SAMPLE:
            continue

        wins      = [t for t in matching if t.get("status") == "win"]
        losses    = [t for t in matching if t.get("status") == "loss"]
        total_pnl = sum(float(t.get(pnl_key) or 0) for t in matching)
        wr        = len(wins) / len(matching) * 100
        gain_sum  = sum(float(t.get(pnl_key) or 0) for t in wins)
        loss_abs  = abs(sum(float(t.get(pnl_key) or 0) for t in losses))
        pf        = (gain_sum / loss_abs) if loss_abs > 0 else (99.0 if gain_sum > 0 else 0.0)

        results.append({
            "pattern":       name,
            "trades":        len(matching),
            "win_rate":      round(wr, 1),
            "avg_pnl":       round(total_pnl / len(matching), 2),
            "total_pnl":     round(total_pnl, 2),
            "profit_factor": round(min(pf, 99.0), 2),
            "edge":          _edge_label(len(matching), wr, pf),
        })

    results.sort(key=lambda r: (-r["win_rate"], -r["profit_factor"]))
    return results[:3]


# ─── Failure analysis ─────────────────────────────────────────────────────────

def failure_analysis(trades: list[dict]) -> dict:
    """
    Find the most common traits among losing trades.
    Only surfaces traits where ≥ 60% of trades in that bucket are losses
    and the bucket has at least 3 losses.
    """
    losses = [t for t in trades if t.get("status") == "loss"]
    wins   = [t for t in trades if t.get("status") == "win"]
    if not losses:
        return {"loss_count": 0, "traits": []}

    traits = []

    def _check(key_fn, dimension_label: str) -> None:
        loss_g: dict[str, int] = {}
        win_g:  dict[str, int] = {}
        for t in losses:
            k = key_fn(t)
            loss_g[k] = loss_g.get(k, 0) + 1
        for t in wins:
            k = key_fn(t)
            win_g[k] = win_g.get(k, 0) + 1

        for bucket, lcount in sorted(loss_g.items(), key=lambda x: -x[1]):
            if lcount < 3:
                continue
            wcount = win_g.get(bucket, 0)
            total  = lcount + wcount
            if total < 5:
                continue
            loss_rate = lcount / total * 100
            if loss_rate >= 60:
                traits.append({
                    "dimension": dimension_label,
                    "bucket":    bucket,
                    "losses":    lcount,
                    "total":     total,
                    "loss_rate": round(loss_rate, 1),
                })

    _check(lambda t: _score_bucket(float(t.get("score") or 0)),          "Score")
    _check(lambda t: _price_bucket(float(t.get("entry_price") or 0)),     "Price")
    _check(lambda t: _hours_bucket(float(t.get("hours_at_entry") or 0)),  "Timing")
    _check(lambda t: t.get("tier", "C"),                                  "Tier")
    _check(lambda t: t.get("confidence", "Low"),                          "Confidence")
    _check(lambda t: t.get("entry_mode", "strict"),                       "Mode")

    # Liquidity / spread only if we have data
    if any(float(t.get("entry_liquidity") or 0) > 0 for t in losses):
        _check(lambda t: _liq_bucket(float(t.get("entry_liquidity") or 0)), "Liquidity")
    if any(float(t.get("entry_spread") or 0) > 0 for t in losses):
        _check(lambda t: _spread_bucket(float(t.get("entry_spread") or 0)), "Spread")

    traits.sort(key=lambda x: -x["loss_rate"])
    return {"loss_count": len(losses), "traits": traits[:6]}


# ─── What To Trade Now ────────────────────────────────────────────────────────

def what_to_trade_now(analytics: dict) -> dict:
    """
    For each dimension, find the best-performing bucket with sufficient data.
    Only considers Strong Edge and Moderate Edge buckets.
    Returns a dict of dimension → best bucket info (or None if no signal).
    """
    dims = {
        "score":      "by_score",
        "price":      "by_price",
        "timing":     "by_hours",
        "tier":       "by_tier",
        "confidence": "by_confidence",
        "liq":        "by_liq",
        "spread":     "by_spread",
    }

    recs = {}
    for dim, key in dims.items():
        rows = analytics.get(key, [])
        good = [
            r for r in rows
            if r.get("sufficient") and r.get("edge") in ("Strong Edge", "Moderate Edge")
        ]
        if good:
            best = max(good, key=lambda r: (r["win_rate"], r["profit_factor"]))
            recs[dim] = best
        else:
            recs[dim] = None
    return recs


# ─── What To Avoid ────────────────────────────────────────────────────────────

def what_to_avoid(analytics: dict) -> list[dict]:
    """
    Find Negative Edge buckets across all dimensions.
    Only returns buckets with sufficient data (>= MIN_SAMPLE).
    """
    dims = {
        "Score":      "by_score",
        "Price":      "by_price",
        "Timing":     "by_hours",
        "Tier":       "by_tier",
        "Confidence": "by_confidence",
        "Liquidity":  "by_liq",
        "Spread":     "by_spread",
    }
    avoid = []
    for dim_label, key in dims.items():
        for r in analytics.get(key, []):
            if r.get("sufficient") and r.get("edge") == "Negative Edge":
                avoid.append({
                    "dimension": dim_label,
                    "bucket":    r["group"],
                    "win_rate":  r["win_rate"],
                    "trades":    r["trades"],
                    "avg_pnl":   r["avg_pnl"],
                })
    avoid.sort(key=lambda r: r["win_rate"])
    return avoid


# ─── Strategy learning summary ────────────────────────────────────────────────

def strategy_learning_summary(analytics: dict) -> list[str]:
    """
    Return 3–6 plain-English bullet points summarising strategy performance.
    Each bullet is a complete, actionable sentence.
    """
    wins   = analytics.get("wins", 0)
    losses = analytics.get("losses", 0)
    total  = wins + losses

    if total == 0:
        return ["Not enough resolved trades yet to generate learning insights."]

    bullets = []
    wr = wins / total * 100

    # Overall verdict
    if wr >= 65:
        bullets.append(
            f"The strategy is working well — {wr:.0f}% win rate across {total} resolved trades."
        )
    elif wr >= 50:
        bullets.append(
            f"Positive early signal — {wr:.0f}% win rate across {total} resolved trades. "
            "Continue accumulating data to confirm the edge."
        )
    else:
        bullets.append(
            f"Win rate is below 50% ({wr:.0f}% across {total} trades). "
            "Review entry conditions — consider tightening to Strict mode."
        )

    # Best tier
    by_tier = sorted(
        [r for r in analytics.get("by_tier", []) if r["sufficient"]],
        key=lambda r: -r["win_rate"],
    )
    if by_tier:
        b = by_tier[0]
        bullets.append(
            f"The system performs best in Tier {b['group']} setups "
            f"({b['win_rate']:.0f}% win rate, {b['trades']} trades)."
        )

    # Best score range
    by_score = sorted(
        [r for r in analytics.get("by_score", []) if r["sufficient"]],
        key=lambda r: -r["win_rate"],
    )
    if by_score:
        b = by_score[0]
        bullets.append(
            f"Scores in the {b['group']} range are the most reliable "
            f"({b['win_rate']:.0f}% win rate)."
        )

    # Best price band
    by_price = sorted(
        [r for r in analytics.get("by_price", []) if r["sufficient"]],
        key=lambda r: -r["win_rate"],
    )
    if by_price:
        b = by_price[0]
        bullets.append(
            f"Best performance comes from the {b['group']} price band."
        )

    # Timing insight
    wh = analytics.get("avg_hours_winners", 0)
    lh = analytics.get("avg_hours_losers",  0)
    if total >= MIN_SAMPLE:
        if lh > wh + 2:
            bullets.append(
                f"Late trades are consistently underperforming — winners average "
                f"{wh:.1f}h to expiry vs {lh:.1f}h for losers."
            )
        elif wh > lh + 2:
            bullets.append(
                f"More time to resolution helps — winners average {wh:.1f}h "
                f"vs {lh:.1f}h for losers."
            )

    # Score calibration
    cal = analytics.get("calibration", {})
    verdict = cal.get("verdict", "insufficient_data")
    if verdict == "well_calibrated":
        bullets.append(
            "The scoring model is well calibrated — higher scores reliably predict better outcomes."
        )
    elif verdict == "not_predictive":
        bullets.append(
            "The scoring model is not strongly predictive yet — "
            "more trades needed to confirm signal strength."
        )
    elif verdict == "inverted":
        bullets.append(
            "⚠️ Lower scores are currently outperforming higher scores — "
            "review scoring weights."
        )

    return bullets[:6]


# ─── Main analytics entry point ───────────────────────────────────────────────

def compute_training_analytics(trades: list[dict]) -> dict:
    """
    Full analytics pipeline. Accepts trade dicts from get_all_auto_trades().
    Returns a comprehensive dict used by the dashboard and report generator.
    """
    resolved = [t for t in trades if t.get("status") in ("win", "loss")]
    if not resolved:
        return {"resolved_count": 0, "wins": 0, "losses": 0}

    wins   = [t for t in resolved if t.get("status") == "win"]
    losses = [t for t in resolved if t.get("status") == "loss"]

    def _avg(lst: list, key: str) -> float:
        vals = [float(t.get(key) or 0) for t in lst]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    # Core breakdowns
    by_score = _group_by(resolved, lambda t: _score_bucket(float(t.get("score") or 0)))
    by_price = _group_by(resolved, lambda t: _price_bucket(float(t.get("entry_price") or 0)))
    by_hours = _group_by(resolved, lambda t: _hours_bucket(float(t.get("hours_at_entry") or 0)))
    by_tier  = _group_by(resolved, lambda t: t.get("tier", "C"))
    by_conf  = _group_by(resolved, lambda t: t.get("confidence", "Low"))
    by_mode  = _group_by(resolved, lambda t: t.get("entry_mode", "strict"))
    by_band  = _group_by(resolved, lambda t: t.get("band", "unknown"))

    # Liquidity / spread only when data exists
    has_liq    = any(float(t.get("entry_liquidity") or 0) > 0 for t in resolved)
    has_spread = any(float(t.get("entry_spread")    or 0) > 0 for t in resolved)

    by_liq    = _group_by(
        [t for t in resolved if float(t.get("entry_liquidity") or 0) > 0],
        lambda t: _liq_bucket(float(t.get("entry_liquidity") or 0)),
    ) if has_liq else []

    by_spread = _group_by(
        [t for t in resolved if float(t.get("entry_spread") or 0) > 0],
        lambda t: _spread_bucket(float(t.get("entry_spread") or 0)),
    ) if has_spread else []

    calibration = calibrate_scores(trades)
    patterns    = find_top_patterns(trades)
    failures    = failure_analysis(trades)

    analytics = {
        "resolved_count":    len(resolved),
        "wins":              len(wins),
        "losses":            len(losses),
        # Breakdowns
        "by_score":          by_score,
        "by_price":          by_price,
        "by_hours":          by_hours,
        "by_tier":           by_tier,
        "by_confidence":     by_conf,
        "by_mode":           by_mode,
        "by_band":           by_band,
        "by_liq":            by_liq,
        "by_spread":         by_spread,
        # Aggregates
        "avg_score_winners": _avg(wins,   "score"),
        "avg_score_losers":  _avg(losses, "score"),
        "avg_price_winners": _avg(wins,   "entry_price"),
        "avg_price_losers":  _avg(losses, "entry_price"),
        "avg_hours_winners": _avg(wins,   "hours_at_entry"),
        "avg_hours_losers":  _avg(losses, "hours_at_entry"),
        # Higher-level analysis
        "calibration":       calibration,
        "top_patterns":      patterns,
        "failure_analysis":  failures,
    }

    # Derive recommendations and bullets last (they read the analytics dict)
    analytics["what_to_trade_now"] = what_to_trade_now(analytics)
    analytics["what_to_avoid"]     = what_to_avoid(analytics)
    analytics["learning_summary"]  = strategy_learning_summary(analytics)

    return analytics


# ─── Plain-English narrative ──────────────────────────────────────────────────

def generate_what_winners_look_like(analytics: dict) -> str:
    """Single-paragraph summary. Kept for backward compatibility."""
    if analytics.get("resolved_count", 0) == 0:
        return "Not enough resolved trades yet. Keep running scans and let positions settle."

    wins   = analytics.get("wins", 0)
    losses = analytics.get("losses", 0)
    total  = wins + losses
    wr     = wins / total * 100 if total else 0
    lines  = [f"Across {total} resolved trades, the overall win rate is {wr:.0f}%."]

    by_tier = sorted(
        [r for r in analytics.get("by_tier", []) if r.get("sufficient")],
        key=lambda r: -r["win_rate"],
    )
    if by_tier:
        b = by_tier[0]
        lines.append(
            f"Tier {b['group']} is performing best ({b['win_rate']:.0f}% win rate)."
        )

    by_score = sorted(
        [r for r in analytics.get("by_score", []) if r.get("sufficient")],
        key=lambda r: -r["win_rate"],
    )
    if by_score:
        b = by_score[0]
        lines.append(f"Best score range: {b['group']} ({b['win_rate']:.0f}% win rate).")

    wh = analytics.get("avg_hours_winners", 0)
    lh = analytics.get("avg_hours_losers",  0)
    if lh > wh + 2 and total >= MIN_SAMPLE:
        lines.append(
            f"Late trades underperforming — winners avg {wh:.1f}h vs losers {lh:.1f}h."
        )

    return "  ".join(lines)


# ─── Exportable insight report ────────────────────────────────────────────────

def generate_insight_report(analytics: dict) -> str:
    """
    Full markdown insight report — suitable for export as training_insights_report.md
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    wins    = analytics.get("wins", 0)
    losses  = analytics.get("losses", 0)
    total   = wins + losses
    wr      = wins / total * 100 if total else 0

    cal     = analytics.get("calibration", {})
    cal_verdicts = {
        "well_calibrated":   "✅ Scores are well calibrated — higher scores predict better outcomes.",
        "not_predictive":    "⚠️ Scores are not strongly predictive yet — more data needed.",
        "inverted":          "🔴 Score calibration is inverted — lower scores are currently winning more.",
        "insufficient_data": "⏳ Not enough data to assess score calibration yet.",
    }
    cal_text = cal_verdicts.get(cal.get("verdict", "insufficient_data"), "—")

    def _edge_table(rows: list[dict]) -> str:
        if not rows:
            return "| — | — | — | — | — | — |\n"
        lines = []
        for r in rows:
            suf = "✓" if r.get("sufficient") else f"⚠ need {MIN_SAMPLE - r['trades']} more"
            lines.append(
                f"| {r['group']} | {r['trades']} | "
                f"{r['win_rate']:.1f}% | {r['profit_factor']:.2f} | "
                f"${r['avg_pnl']:+,.2f} | **{r['edge']}** {suf} |"
            )
        return "\n".join(lines)

    # What to trade / avoid
    wtt    = analytics.get("what_to_trade_now", {})
    avoid  = analytics.get("what_to_avoid",    [])
    trades_recs = []
    for dim, label in [
        ("tier", "Tier"), ("score", "Score"), ("price", "Price"),
        ("timing", "Timing"), ("liq", "Liquidity"), ("spread", "Spread"),
    ]:
        rec = wtt.get(dim)
        if rec:
            trades_recs.append(f"- **{label}:** {rec['group']} ({rec['win_rate']:.0f}% win rate)")
    if not trades_recs:
        trades_recs = ["- Not enough data yet — need ≥10 trades per bucket."]

    avoid_lines = [
        f"- **{a['dimension']}:** {a['bucket']} ({a['win_rate']:.0f}% win rate, {a['trades']} trades)"
        for a in avoid
    ] or ["- No clearly negative buckets identified yet."]

    # Patterns
    patterns    = analytics.get("top_patterns", [])
    pattern_rows = "\n".join(
        f"| {p['pattern']} | {p['trades']} | {p['win_rate']:.1f}% | "
        f"${p['avg_pnl']:+,.2f} | {p['edge']} |"
        for p in patterns
    ) or "| Not enough data | — | — | — | — |"

    # Failures
    failures    = analytics.get("failure_analysis", {})
    fail_traits = failures.get("traits", [])
    fail_lines  = "\n".join(
        f"- **{t['dimension']} {t['bucket']}:** {t['loss_rate']:.0f}% loss rate "
        f"({t['losses']} losses out of {t['total']} trades)"
        for t in fail_traits
    ) or "- No dominant failure patterns identified yet."

    # Learning summary
    bullets = analytics.get("learning_summary", ["No insights yet."])
    bullet_md = "\n".join(f"- {b}" for b in bullets)

    # Score calibration table
    def _cal_row(r: dict) -> str:
        wr_str  = (f"{r['win_rate']:.1f}%") if r["win_rate"] is not None else "—"
        suf_str = "✓" if r["sufficient"] else ("need " + str(MIN_SAMPLE - r["trades"]) + " more")
        return f"| {r['bucket']} | {r['trades']} | {wr_str} | {suf_str} |"

    cal_rows = "\n".join(
        _cal_row(r) for r in cal.get("rows", [])
    ) or "| — | — | — | — |"

    return f"""# Training Insights Report

Generated: {now_str}
Total Resolved Trades: {total}  |  Wins: {wins}  |  Losses: {losses}  |  Win Rate: {wr:.1f}%

---

## Strategy Learning Summary

{bullet_md}

---

## What To Trade Right Now

{chr(10).join(trades_recs)}

## What To Avoid

{chr(10).join(avoid_lines)}

---

## Score Calibration

{cal_text}

| Score Range | Trades | Win Rate | Data Status |
|-------------|--------|----------|-------------|
{cal_rows}

---

## Top Trade Patterns

| Pattern | Trades | Win Rate | Avg P&L | Edge |
|---------|--------|----------|---------|------|
{pattern_rows}

---

## Failure Analysis

{fail_lines}

---

## Edge Detection by Dimension

### By Tier
| Bucket | Trades | Win Rate | Profit Factor | Avg P&L | Edge |
|--------|--------|----------|--------------|---------|------|
{_edge_table(analytics.get("by_tier", []))}

### By Score
| Bucket | Trades | Win Rate | Profit Factor | Avg P&L | Edge |
|--------|--------|----------|--------------|---------|------|
{_edge_table(analytics.get("by_score", []))}

### By Price Band
| Bucket | Trades | Win Rate | Profit Factor | Avg P&L | Edge |
|--------|--------|----------|--------------|---------|------|
{_edge_table(analytics.get("by_price", []))}

### By Timing
| Bucket | Trades | Win Rate | Profit Factor | Avg P&L | Edge |
|--------|--------|----------|--------------|---------|------|
{_edge_table(analytics.get("by_hours", []))}

---

*Paper-trading only. Not financial advice. For research purposes.*
"""
