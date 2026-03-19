"""
Report generation for 48-hour paper-trading validation runs.

Exports:
  generate_markdown_report(run_id) → str
  save_markdown_report(run_id, out_dir) → filepath
  export_csvs(run_id, out_dir) → {name: filepath}
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone

from app.validation import compute_metrics, get_permutation_stats, generate_conclusion


def generate_markdown_report(run_id: int) -> str:
    """Full markdown report for a completed or in-progress validation run."""
    metrics    = compute_metrics(run_id)
    perm       = get_permutation_stats(run_id)
    conclusion = generate_conclusion(metrics)

    if not metrics:
        return f"# Validation Report\n\nNo data found for run #{run_id}.\n"

    run      = metrics["run"]
    m1       = metrics["models"]["1pct"]
    m2       = metrics["models"]["2pct"]
    m5       = metrics["models"]["5pct"]
    trades   = metrics["trades"]
    by_tier  = metrics["by_tier"]
    by_score = metrics["by_score"]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    settled     = [t for t in trades if t["status"] in ("won", "lost")]
    by_pnl      = sorted(settled, key=lambda t: -(t.get("pnl_5pct") or 0))
    top10       = by_pnl[:10]
    worst10     = list(reversed(by_pnl[-10:])) if len(by_pnl) >= 2 else by_pnl

    # Best model
    models_ranked = sorted(
        [("1%", m1), ("2%", m2), ("5%", m5)],
        key=lambda x: x[1].get("total_pnl", 0),
        reverse=True,
    )
    best_name, best_m = models_ranked[0]

    # Rejection reasons table
    rr = perm.get("rejection_reasons", {})
    rr_rows = "\n".join(
        f"| {k.replace('_',' ').title()} | {v} |"
        for k, v in sorted(rr.items(), key=lambda x: -x[1])
        if v > 0
    ) or "| — | 0 |"

    def _trade_row(t: dict) -> str:
        q   = (t.get("question") or "")[:60]
        pnl = t.get("pnl_5pct") or 0
        sgn = "+" if pnl >= 0 else ""
        return (
            f"| {q}{'…' if len(t.get('question',''))>60 else ''} "
            f"| {t.get('entry_price',0):.3f} "
            f"| {t.get('tier','?')} "
            f"| {t.get('score',0):.0f} "
            f"| {sgn}{pnl:,.2f} "
            f"| {t.get('status','?')} |"
        )

    def _tier_table() -> str:
        rows = "\n".join(
            f"| {r['group']} | {r['trades']} | {r['wins']} | {r['win_rate']}% | ${r['pnl']:+,.2f} |"
            for r in by_tier
        )
        return rows or "| — | 0 | 0 | — | — |"

    def _score_table() -> str:
        rows = "\n".join(
            f"| {r['group']} | {r['trades']} | {r['wins']} | {r['win_rate']}% | ${r['pnl']:+,.2f} |"
            for r in by_score
        )
        return rows or "| — | 0 | 0 | — | — |"

    rigidity_map = {
        "too_rigid":  "⚠️  Too Rigid — fewer than 1% of scanned markets are accepted",
        "balanced":   "✅  Balanced — 1–15% of scanned markets are accepted",
        "too_loose":  "⚠️  Too Loose — more than 15% of scanned markets are accepted",
    }

    md = f"""# 48-Hour Paper-Trading Validation Report

Generated: {now_str}
Run ID: {run_id} | Status: {run.get('status','?').upper()} | Elapsed: {metrics['hours_elapsed']:.1f}h

---

## Headline Results

| Metric | 1% Model | 2% Model | 5% Model |
|--------|----------|----------|----------|
| Starting Balance | ${m1['starting_balance']:,.0f} | ${m2['starting_balance']:,.0f} | ${m5['starting_balance']:,.0f} |
| Final Balance | ${m1['final_balance']:,.2f} | ${m2['final_balance']:,.2f} | ${m5['final_balance']:,.2f} |
| Realized P&L | ${m1['total_pnl']:+,.2f} | ${m2['total_pnl']:+,.2f} | ${m5['total_pnl']:+,.2f} |
| ROI % | {m1['roi_pct']:+.2f}% | {m2['roi_pct']:+.2f}% | {m5['roi_pct']:+.2f}% |
| Win Rate | {m1['win_rate']:.1f}% | {m2['win_rate']:.1f}% | {m5['win_rate']:.1f}% |
| Max Drawdown | {m1['max_drawdown_pct']:.1f}% | {m2['max_drawdown_pct']:.1f}% | {m5['max_drawdown_pct']:.1f}% |
| Profit Factor | {m1['profit_factor']:.2f} | {m2['profit_factor']:.2f} | {m5['profit_factor']:.2f} |
| Avg Win ($) | ${m1['avg_win']:.2f} | ${m2['avg_win']:.2f} | ${m5['avg_win']:.2f} |
| Avg Loss ($) | ${m1['avg_loss']:.2f} | ${m2['avg_loss']:.2f} | ${m5['avg_loss']:.2f} |

**Best Performing Model:** {best_name} (P&L: ${best_m['total_pnl']:+,.2f})

---

## Strategy Performance

- **Resolved Trades:** {m5['total_trades']}
- **Open / Unresolved:** {m5['open_trades']} / {m5['unresolved_trades']}
- **Total Market Scans:** {metrics['scan_count']}
- **Markets Scanned (all cycles):** {metrics['total_scanned_across_all_cycles']:,}

### By Tier (5% model P&L)

| Tier | Trades | Wins | Win Rate | P&L |
|------|--------|------|----------|-----|
{_tier_table()}

### By Score Bucket (5% model P&L)

| Score | Trades | Wins | Win Rate | P&L |
|-------|--------|------|----------|-----|
{_score_table()}

---

## Permutation Coverage

- **Total Scanned (all cycles):** {perm['total_scanned']:,}
- **Primary Band:** {perm['total_primary']:,}
- **Secondary Band:** {perm['total_secondary']:,}
- **Watchlist Band:** {perm['total_watchlist']:,}
- **Rejected:** {perm['total_rejected']:,}
- **Acceptance Rate:** {perm['pct_accepted']:.1f}%
- **Assessment:** {rigidity_map.get(perm['rigidity'], perm['rigidity'])}

### Top Rejection Reasons

| Reason | Count |
|--------|-------|
{rr_rows}

---

## Top 10 Trades (5% model)

| Market | Price | Tier | Score | P&L ($) | Outcome |
|--------|-------|------|-------|---------|---------|
{chr(10).join(_trade_row(t) for t in top10) or "| No settled trades yet | — | — | — | — | — |"}

## Worst 10 Trades (5% model)

| Market | Price | Tier | Score | P&L ($) | Outcome |
|--------|-------|------|-------|---------|---------|
{chr(10).join(_trade_row(t) for t in worst10) or "| No settled trades yet | — | — | — | — | — |"}

---

## Conclusion

{conclusion}

---

*This is a paper-trading simulation. No real money was involved.
Results are for research and strategy development purposes only.*
"""
    return md


def save_markdown_report(run_id: int, out_dir: str = "data") -> str:
    """Save the markdown report to a file and return the absolute path."""
    os.makedirs(out_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(out_dir, f"validation_report_48h_{ts}.md")
    with open(path, "w") as f:
        f.write(generate_markdown_report(run_id))
    return path


def export_csvs(run_id: int, out_dir: str = "data") -> dict:
    """
    Export multiple CSV files for a validation run.
    Returns {name: filepath} mapping.
    """
    os.makedirs(out_dir, exist_ok=True)
    metrics = compute_metrics(run_id)
    trades  = metrics.get("trades", [])
    ts      = datetime.now().strftime("%Y%m%d_%H%M")

    cols = [
        "id", "market_id", "question", "side", "score", "tier", "band",
        "hours_at_entry", "entry_at", "entry_price",
        "notional_1pct", "notional_2pct", "notional_5pct",
        "status", "exit_at", "exit_price",
        "pnl_1pct", "pnl_2pct", "pnl_5pct", "reason_entered",
    ]

    def _write(fname: str, rows: list[dict]) -> str:
        path = os.path.join(out_dir, fname)
        buf  = io.StringIO()
        w    = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        with open(path, "w") as f:
            f.write(buf.getvalue())
        return path

    return {
        "all_trades":   _write(f"trades_all_{ts}.csv",        trades),
        "resolved":     _write(f"trades_resolved_{ts}.csv",   [t for t in trades if t["status"] in ("won","lost")]),
        "unresolved":   _write(f"trades_unresolved_{ts}.csv", [t for t in trades if t["status"] == "unresolved"]),
        "open":         _write(f"trades_open_{ts}.csv",       [t for t in trades if t["status"] == "open"]),
        "report_md":    save_markdown_report(run_id, out_dir),
    }
