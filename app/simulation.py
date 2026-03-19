"""
Paper-trading bankroll simulation.

Simulates three position-size modes (1%, 2%, 5%) across accepted markets.
Outcomes are deterministic (hash of market ID) so the dashboard shows
consistent results across page loads.

Methodology:
  - Yes price P is treated as the market's implied win probability
  - A win pays: position_size * (1 - P) / P   (prediction market math)
  - A loss costs: position_size
  - Deterministic outcome: MD5(market_id) modulo 10000 / 10000 < P → win
"""
import hashlib
from typing import Optional

STARTING_BALANCE = 10_000.0
POSITION_MODES = {"1%": 0.01, "2%": 0.02, "5%": 0.05}


def _market_win(market_id: str, yes_price: float) -> bool:
    """Deterministic simulated outcome. Same market ID → same result."""
    digest = hashlib.md5(market_id.encode()).hexdigest()[:8]
    pseudo = int(digest, 16) % 10_000 / 10_000.0
    return pseudo < yes_price


def simulate_mode(
    markets: list,
    fraction: float,
    starting_balance: float = STARTING_BALANCE,
) -> dict:
    """
    Simulate one position-size mode across a list of accepted markets.
    Returns metrics and equity curve.
    """
    balance      = starting_balance
    equity_curve = [round(balance, 2)]
    wins = losses = 0
    total_pnl    = 0.0
    peak         = starting_balance
    max_drawdown = 0.0
    trade_log: list = []

    for m in markets:
        price  = m.get("yes_price") or 0.9
        mid    = str(m.get("id") or id(m))
        pos    = balance * fraction
        won    = _market_win(mid, price)

        if won:
            pnl = pos * (1.0 - price) / price
            wins += 1
        else:
            pnl = -pos
            losses += 1

        balance     += pnl
        total_pnl   += pnl
        equity_curve.append(round(balance, 2))

        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        trade_log.append({
            "question": (m.get("question") or "")[:60],
            "price":    price,
            "position": round(pos, 2),
            "won":      won,
            "pnl":      round(pnl, 2),
            "balance":  round(balance, 2),
        })

    n = wins + losses
    return {
        "fraction":      fraction,
        "label":         f"{fraction*100:.0f}%",
        "trades":        n,
        "wins":          wins,
        "losses":        losses,
        "win_rate":      round(wins / n * 100, 1) if n > 0 else 0.0,
        "total_pnl":     round(total_pnl, 2),
        "final_balance": round(balance, 2),
        "equity_curve":  equity_curve,
        "max_drawdown":  round(max_drawdown * 100, 1),
        "roi":           round((balance - starting_balance) / starting_balance * 100, 2),
        "trade_log":     trade_log,
    }


def simulate_portfolio(
    markets: list,
    starting_balance: float = STARTING_BALANCE,
) -> dict:
    """
    Run all three position-size modes on accepted markets.
    Returns {"1%": {...}, "2%": {...}, "5%": {...}}.
    """
    accepted = [m for m in markets if m.get("accepted")]
    if not accepted:
        return {}
    return {
        name: simulate_mode(accepted, frac, starting_balance)
        for name, frac in POSITION_MODES.items()
    }


def expected_pnl_per_trade(yes_price: float, position_size: float) -> float:
    """
    Theoretical expected P&L per trade under the efficient-market assumption
    (yes_price = true win probability). Should be near zero.
    """
    win_pnl  = position_size * (1 - yes_price) / yes_price
    exp_pnl  = yes_price * win_pnl - (1 - yes_price) * position_size
    return round(exp_pnl, 4)
