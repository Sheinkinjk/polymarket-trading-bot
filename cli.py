"""
Command-line interface for the Polymarket Trading Bot.
Usage:
    python cli.py scan          # fetch markets and show results
    python cli.py dashboard     # open the Streamlit dashboard
    python cli.py status        # show last scan summary
"""
import os
import sys
import subprocess

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.scanner import fetch_live_markets
from app.strategy import run_pipeline
from app.database import (
    upsert_markets,
    log_scan,
    get_accepted_markets,
    get_last_scan_info,
    init_db,
)
from app.validation import (
    start_test,
    stop_test,
    get_active_run,
    get_all_runs,
    run_validation_cycle,
    should_run_cycle,
    is_test_expired,
    hours_remaining,
    compute_metrics,
    DEFAULT_CONFIG as _VAL_DEFAULT,
)
from app.validation_report import export_csvs
from app.auto_paper import (
    get_or_create_session, get_active_session, reset_session,
    run_auto_paper_entries, settle_paper_trades,
    get_live_trades, get_resolved_trades,
    compute_bankroll_metrics, generate_bankroll_summary,
    STARTING_BALANCE as _AP_BALANCE,
    DEFAULT_MODE as _AP_MODE,
    DEFAULT_LIMIT as _AP_LIMIT,
)
from app.training import compute_training_analytics, generate_what_winners_look_like, generate_insight_report

app  = typer.Typer(help="Polymarket Trading Bot — paper-trading scanner.")
cons = Console()


def _tier_style(tier: str) -> str:
    return {"A": "bold yellow", "B": "bold magenta", "C": "dim"}.get(tier, "")


def _fmt_usd(n) -> str:
    n = n or 0
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n/1_000:.0f}K"
    return f"${n:.0f}"


@app.command()
def scan(
    limit: int = typer.Option(200, "--limit", "-l", help="Max markets to fetch"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show rejected markets too"),
):
    """Fetch live markets, filter, and rank opportunities."""
    init_db()
    cons.rule("[bold blue]Polymarket Trading Bot — Market Scan")

    with cons.status("[cyan]Fetching markets from Polymarket…"):
        raw, source = fetch_live_markets(limit=limit)

    source_tag = "[green]LIVE[/green]" if source == "live" else "[yellow]SAMPLE DATA[/yellow]"
    cons.print(f"\n  Source: {source_tag}   Markets fetched: [bold]{len(raw)}[/bold]\n")

    with cons.status("[cyan]Analysing markets…"):
        processed = run_pipeline(raw)

    upsert_markets(processed)
    accepted = [m for m in processed if m.get("accepted")]
    log_scan(len(processed), len(accepted), source)

    # ── Accepted table ──────────────────────────────────────────────────────
    table = Table(
        title=f"✅  Accepted Opportunities ({len(accepted)})",
        box=box.ROUNDED,
        border_style="bright_blue",
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("#",        style="dim",         width=3,  justify="right")
    table.add_column("Tier",     justify="center",    width=6)
    table.add_column("Score",    justify="right",     width=6)
    table.add_column("Price",    justify="right",     width=6)
    table.add_column("Hours",    justify="right",     width=6)
    table.add_column("Liq",      justify="right",     width=8)
    table.add_column("Spread",   justify="right",     width=7)
    table.add_column("Question", min_width=40, max_width=70)

    for i, m in enumerate(accepted, 1):
        tier  = m.get("tier", "C")
        score = m.get("score", 0)
        table.add_row(
            str(i),
            Text(f" {tier} ", style=_tier_style(tier)),
            Text(str(score), style=_tier_style(tier)),
            f"{m.get('yes_price',0):.2f}",
            f"{m.get('hours_to_end',0):.1f}h",
            _fmt_usd(m.get("liquidity")),
            f"{m.get('spread',0):.3f}",
            (m.get("question") or "")[:70],
        )

    if accepted:
        cons.print(table)
    else:
        cons.print(Panel(
            "[yellow]No markets passed all filters right now.\n"
            "Try again later or check the dashboard for rejected details.[/yellow]",
            title="No Opportunities Found",
        ))

    # ── Rejected summary ────────────────────────────────────────────────────
    rejected = [m for m in processed if not m.get("accepted")]
    cons.print(f"\n  [dim]Rejected: {len(rejected)}  |  Total processed: {len(processed)}[/dim]")

    if verbose and rejected:
        cons.rule("[dim]Rejected Markets[/dim]")
        for m in rejected[:20]:
            cons.print(
                f"  [dim]· {(m.get('question') or '')[:60]}"
                f"  →  {m.get('explanation','')}[/dim]"
            )

    cons.rule()
    cons.print(
        "  [dim]Open the dashboard for full details:  "
        "[bold cyan]streamlit run app/dashboard.py[/bold cyan][/dim]\n"
    )


@app.command()
def status():
    """Show a summary of the last scan."""
    init_db()
    info = get_last_scan_info()
    if not info:
        cons.print("[yellow]No scans found. Run [bold]python cli.py scan[/bold] first.[/yellow]")
        raise typer.Exit()

    accepted_markets = get_accepted_markets()

    cons.rule("[bold blue]Last Scan Summary")
    cons.print(f"  Scanned at:    [cyan]{info.get('scanned_at','?')}[/cyan]")
    cons.print(f"  Source:        [cyan]{info.get('source','?')}[/cyan]")
    cons.print(f"  Total fetched: [cyan]{info.get('total_fetched','?')}[/cyan]")
    cons.print(f"  Accepted:      [green]{info.get('total_accepted','?')}[/green]")

    tier_a = sum(1 for m in accepted_markets if m.get("tier") == "A")
    tier_b = sum(1 for m in accepted_markets if m.get("tier") == "B")
    cons.print(f"  Tier A:        [yellow]{tier_a}[/yellow]")
    cons.print(f"  Tier B:        [magenta]{tier_b}[/magenta]")
    cons.rule()


@app.command()
def dashboard():
    """Launch the Streamlit dashboard in your browser."""
    dashboard_path = os.path.join(os.path.dirname(__file__), "app", "dashboard.py")
    cons.print("[cyan]Starting dashboard…  Press Ctrl+C to stop.[/cyan]")
    subprocess.run([sys.executable, "-m", "streamlit", "run", dashboard_path], check=False)


# ── 48-Hour Validation Commands ───────────────────────────────────────────────

@app.command("start-48h-test")
def start_48h_test(
    balance: float = typer.Option(10_000.0, "--balance", "-b", help="Starting balance ($)"),
    position_pct: float = typer.Option(5.0, "--position-pct", "-p", help="Position size %"),
    max_positions: int = typer.Option(10, "--max-positions", help="Max open positions"),
    allow_c: bool = typer.Option(False, "--allow-tier-c", help="Allow Tier C markets"),
):
    """Start a new 48-hour paper-trading validation test."""
    init_db()

    active = get_active_run()
    if active:
        cons.print(
            f"[yellow]A validation run is already active (ID {active['id']}).\n"
            f"Use [bold]python cli.py test-status[/bold] to check progress or "
            f"[bold]python cli.py stop-48h-test[/bold] to stop it.[/yellow]"
        )
        raise typer.Exit()

    cfg = dict(_VAL_DEFAULT)
    cfg["starting_balance"]   = balance
    cfg["position_percent"]   = position_pct
    cfg["max_open_positions"] = max_positions
    cfg["allow_tier_c"]       = allow_c

    run_id = start_test(cfg)

    cons.rule("[bold green]48-Hour Paper-Trading Test Started")
    cons.print(f"  Run ID:          [cyan]{run_id}[/cyan]")
    cons.print(f"  Starting balance:[cyan] ${balance:,.0f}[/cyan]")
    cons.print(f"  Position size:   [cyan]{position_pct}%[/cyan]")
    cons.print(f"  Max positions:   [cyan]{max_positions}[/cyan]")
    cons.print(f"  Tier C allowed:  [cyan]{allow_c}[/cyan]")
    cons.print()
    cons.print(
        "  The test scans every 15 minutes [bold]on demand[/bold] — "
        "either via the dashboard or [bold]python cli.py test-status[/bold]."
    )
    cons.print(
        "  Monitor live: open the dashboard → [bold cyan]🧪 48h Validation[/bold cyan] page."
    )
    cons.rule()


@app.command("test-status")
def test_status(
    run_id: int = typer.Option(0, "--run-id", "-r", help="Specific run ID (default: active run)"),
    run_cycle: bool = typer.Option(True, "--scan/--no-scan", help="Trigger a scan cycle if due"),
):
    """Show status of the active (or specified) 48-hour validation run."""
    init_db()

    run_info = get_active_run() if run_id == 0 else None
    if run_id:
        all_runs = get_all_runs()
        run_info = next((r for r in all_runs if r["id"] == run_id), None)

    if not run_info:
        cons.print("[yellow]No active validation run found.[/yellow]")
        cons.print("Start one with: [bold]python cli.py start-48h-test[/bold]")
        raise typer.Exit()

    # Optionally trigger a scan cycle
    if run_cycle and run_info.get("status") == "running":
        if should_run_cycle(run_info):
            with cons.status("[cyan]Running validation scan cycle…"):
                raw, source = fetch_live_markets(limit=300)
                processed   = run_pipeline(raw)
                upsert_markets(processed)
                result = run_validation_cycle(run_info["id"], run_info)
            cons.print(
                f"  [green]Scan complete:[/green] "
                f"{result.get('new_positions', 0)} new positions, "
                f"{result.get('settled_positions', 0)} settled."
            )
        elif is_test_expired(run_info):
            stop_test(run_info["id"])
            run_info = {**run_info, "status": "completed"}

    metrics = compute_metrics(run_info["id"])
    if not metrics:
        cons.print(f"[yellow]No metrics yet for run #{run_info['id']}.[/yellow]")
        raise typer.Exit()

    m5  = metrics["models"]["5pct"]
    run = metrics["run"]

    cons.rule(f"[bold blue]Validation Run #{run_info['id']} — {run.get('status','?').upper()}")
    cons.print(f"  Hours elapsed:   [cyan]{metrics['hours_elapsed']:.1f}h / 48h[/cyan]")
    cons.print(f"  Hours remaining: [cyan]{hours_remaining(run_info):.1f}h[/cyan]")
    cons.print()

    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", show_lines=False)
    table.add_column("Model",           width=8)
    table.add_column("Trades",          justify="right", width=8)
    table.add_column("Win Rate",        justify="right", width=10)
    table.add_column("P&L",             justify="right", width=12)
    table.add_column("ROI",             justify="right", width=8)
    table.add_column("Max Drawdown",    justify="right", width=14)

    for key, label in (("1pct", "1%"), ("2pct", "2%"), ("5pct", "5%")):
        m = metrics["models"][key]
        pnl_color = "green" if m["total_pnl"] >= 0 else "red"
        table.add_row(
            label,
            str(m["total_trades"]),
            f"{m['win_rate']:.1f}%",
            f"[{pnl_color}]${m['total_pnl']:+,.2f}[/{pnl_color}]",
            f"{m['roi_pct']:+.2f}%",
            f"{m['max_drawdown_pct']:.1f}%",
        )

    cons.print(table)
    cons.print(f"  Open positions:  [cyan]{m5['open_trades']}[/cyan]")
    cons.print(f"  Total scans:     [cyan]{metrics['scan_count']}[/cyan]")
    cons.rule()


@app.command("stop-48h-test")
def stop_48h_test(
    run_id: int = typer.Option(0, "--run-id", "-r", help="Run ID to stop (default: active run)"),
):
    """Stop the active 48-hour validation test early."""
    init_db()

    if run_id == 0:
        active = get_active_run()
        if not active:
            cons.print("[yellow]No active validation run to stop.[/yellow]")
            raise typer.Exit()
        run_id = active["id"]

    stop_test(run_id)
    cons.print(f"[green]Run #{run_id} stopped.[/green]  Export results with:")
    cons.print(f"  [bold cyan]python cli.py export-48h-report --run-id {run_id}[/bold cyan]")


@app.command("export-48h-report")
def export_48h_report(
    run_id: int = typer.Option(0, "--run-id", "-r", help="Run ID (default: most recent run)"),
    out_dir: str = typer.Option("data", "--out-dir", "-o", help="Output directory"),
):
    """Export CSV files and a markdown report for a validation run."""
    init_db()

    if run_id == 0:
        all_runs = get_all_runs()
        if not all_runs:
            cons.print("[yellow]No validation runs found.[/yellow]")
            raise typer.Exit()
        run_id = all_runs[0]["id"]

    with cons.status(f"[cyan]Exporting run #{run_id} to {out_dir}/…"):
        paths = export_csvs(run_id, out_dir)

    cons.rule(f"[bold green]Export Complete — Run #{run_id}")
    for name, path in paths.items():
        cons.print(f"  [cyan]{name:<14}[/cyan]  {path}")
    cons.rule()


# ── Auto Paper Trading Commands ───────────────────────────────────────────────

@app.command("run-auto-paper")
def run_auto_paper(
    mode: str  = typer.Option("strict", "--mode", "-m", help="strict or broad"),
    limit: int = typer.Option(5, "--limit", "-l", help="Max positions per scan"),
    balance: float = typer.Option(_AP_BALANCE, "--balance", "-b", help="Starting balance ($)"),
    settle: bool   = typer.Option(True, "--settle/--no-settle", help="Also settle open trades"),
):
    """Fetch live markets and auto-enter paper positions in top opportunities."""
    init_db()

    with cons.status("[cyan]Fetching markets…"):
        raw, source = fetch_live_markets(limit=300)
    processed = run_pipeline(raw)
    upsert_markets(processed)
    accepted  = [m for m in processed if m.get("accepted")]
    log_scan(len(processed), len(accepted), source)

    session = get_or_create_session(mode=mode, limit=limit, starting_balance=balance)
    source_tag = "[green]LIVE[/green]" if source == "live" else "[yellow]SAMPLE[/yellow]"

    cons.print(f"\n  Source: {source_tag}   Fetched: [bold]{len(processed)}[/bold]   "
               f"Accepted: [bold]{len(accepted)}[/bold]   Session: #{session['id']}\n")

    result = run_auto_paper_entries(accepted, session)
    cons.print(
        f"  [green]Entries:[/green]   {result['entered']} new position(s)  "
        f"({result['skipped']} skipped, {result['candidates']} candidate(s))"
    )

    if settle:
        n = settle_paper_trades(processed, session["id"])
        cons.print(f"  [green]Settled:[/green]   {n} trade(s) resolved")

    cons.rule()
    cons.print("  Run [bold cyan]python cli.py show-bankroll[/bold cyan] to see performance.\n")


@app.command("show-bankroll")
def show_bankroll():
    """Display current bankroll metrics across all three sizing models."""
    init_db()

    session = get_active_session()
    if not session:
        cons.print("[yellow]No active paper-trading session.[/yellow]")
        cons.print("Start one with: [bold]python cli.py run-auto-paper[/bold]")
        raise typer.Exit()

    metrics = compute_bankroll_metrics(session["id"])
    if not metrics:
        cons.print("[yellow]No metrics yet for this session.[/yellow]")
        raise typer.Exit()

    models = metrics["models"]
    cons.rule(f"[bold blue]Bankroll — Session #{session['id']} "
              f"({session.get('mode','strict').upper()} mode)")

    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", show_lines=False)
    table.add_column("Model",         width=20)
    table.add_column("Balance",       justify="right", width=12)
    table.add_column("P&L",           justify="right", width=12)
    table.add_column("ROI",           justify="right", width=8)
    table.add_column("Trades",        justify="right", width=8)
    table.add_column("Win Rate",      justify="right", width=10)
    table.add_column("Max DD",        justify="right", width=10)
    table.add_column("Live",          justify="right", width=6)

    for key, label in [
        ("2pct", "Conservative 2%"),
        ("3pct", "Balanced 3%"),
        ("5pct", "Aggressive 5%"),
    ]:
        m = models[key]
        pnl_col = "green" if m["total_pnl"] >= 0 else "red"
        table.add_row(
            label,
            f"${m['final_balance']:,.2f}",
            f"[{pnl_col}]${m['total_pnl']:+,.2f}[/{pnl_col}]",
            f"{m['roi_pct']:+.1f}%",
            str(m["total_trades"]),
            f"{m['win_rate']:.1f}%",
            f"{m['max_drawdown_pct']:.1f}%",
            str(m["live_trades"]),
        )

    cons.print(table)
    cons.print()
    summary = generate_bankroll_summary(metrics)
    cons.print(f"  [dim]{summary}[/dim]")
    cons.rule()


@app.command("show-live-positions")
def show_live_positions():
    """List all currently open paper trades."""
    init_db()

    session = get_active_session()
    if not session:
        cons.print("[yellow]No active paper-trading session.[/yellow]")
        raise typer.Exit()

    live = get_live_trades(session["id"])
    cons.rule(f"[bold blue]Live Positions — Session #{session['id']}")

    if not live:
        cons.print("  [dim]No open positions.[/dim]")
        cons.rule()
        raise typer.Exit()

    table = Table(box=box.ROUNDED, border_style="bright_blue",
                  header_style="bold cyan", show_lines=True)
    table.add_column("#",         width=3,  justify="right", style="dim")
    table.add_column("Tier",      width=6,  justify="center")
    table.add_column("Score",     width=6,  justify="right")
    table.add_column("Price",     width=7,  justify="right")
    table.add_column("Pos 3% ($)", width=10, justify="right")
    table.add_column("Hrs@Entry", width=10, justify="right")
    table.add_column("Market",    min_width=40, max_width=70)

    for i, t in enumerate(live, 1):
        tier = t.get("tier", "C")
        table.add_row(
            str(i),
            Text(f" {tier} ", style=_tier_style(tier)),
            f"{float(t.get('score') or 0):.0f}",
            f"{float(t.get('entry_price') or 0):.3f}",
            f"${float(t.get('notional_3pct') or 0):,.2f}",
            f"{float(t.get('hours_at_entry') or 0):.1f}h",
            (t.get("question") or "")[:70],
        )

    cons.print(table)
    cons.print(f"\n  Total: [bold]{len(live)}[/bold] open position(s)")
    cons.rule()


@app.command("settle-paper-trades")
def settle_paper_trades_cmd():
    """Settle resolved paper trades against current market prices."""
    init_db()

    session = get_active_session()
    if not session:
        cons.print("[yellow]No active paper-trading session.[/yellow]")
        raise typer.Exit()

    with cons.status("[cyan]Fetching latest market prices…"):
        raw, source = fetch_live_markets(limit=300)
        processed   = run_pipeline(raw)
        upsert_markets(processed)

    n = settle_paper_trades(processed, session["id"])
    cons.print(f"[green]Settled {n} trade(s).[/green]  "
               f"Run [bold cyan]python cli.py show-bankroll[/bold cyan] to see updated P&L.")


@app.command("export-training-report")
def export_training_report(
    out_dir: str = typer.Option("data", "--out-dir", "-o", help="Output directory"),
    session_id: int = typer.Option(0, "--session-id", "-s", help="Session ID (default: active session)"),
):
    """Export a markdown training insights report to a file."""
    init_db()

    if session_id == 0:
        session = get_active_session()
        if not session:
            cons.print("[yellow]No active paper-trading session.[/yellow]")
            cons.print("Start one with: [bold]python cli.py run-auto-paper[/bold]")
            raise typer.Exit()
    else:
        from app.auto_paper import get_session
        session = get_session(session_id)
        if not session:
            cons.print(f"[yellow]Session #{session_id} not found.[/yellow]")
            raise typer.Exit()

    from app.auto_paper import get_all_auto_trades
    from datetime import datetime

    with cons.status("[cyan]Generating training insights report…"):
        trades    = get_all_auto_trades(session["id"])
        analytics = compute_training_analytics(trades)
        report_md = generate_insight_report(analytics)

    os.makedirs(out_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(out_dir, f"training_insights_{ts}.md")

    with open(path, "w") as f:
        f.write(report_md)

    resolved = analytics.get("resolved_count", 0)
    wins     = analytics.get("wins", 0)
    losses   = analytics.get("losses", 0)
    wr       = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    cons.rule("[bold green]Training Insights Report Exported")
    cons.print(f"  Session:        [cyan]#{session['id']} ({session.get('mode','strict').upper()} mode)[/cyan]")
    cons.print(f"  Resolved trades:[cyan] {resolved}[/cyan]  "
               f"([green]{wins} wins[/green] / [red]{losses} losses[/red]  —  {wr:.1f}% WR)")
    cons.print(f"  Output:         [cyan]{path}[/cyan]")
    cons.rule()


if __name__ == "__main__":
    app()
