"""
Realistic sample market data used as fallback when the API is unavailable.
All prices are in the 0.87–0.95 target range, with varied liquidity/time.
"""
from datetime import datetime, timezone, timedelta

_NOW = datetime.now(timezone.utc)


def _market(
    id_: str,
    question: str,
    hours_left: float,
    yes_price: float,
    liquidity: float,
    volume_24h: float,
    spread: float,
) -> dict:
    best_bid = round(yes_price - spread / 2, 4)
    best_ask = round(yes_price + spread / 2, 4)
    end_time = (_NOW + timedelta(hours=hours_left)).isoformat()
    return {
        "id": id_,
        "question": question,
        "end_time": end_time,
        "yes_price": yes_price,
        "liquidity": liquidity,
        "volume_24h": volume_24h,
        "spread": spread,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "raw_data": {},
    }


SAMPLE_MARKETS: list[dict] = [
    _market(
        "sample-001",
        "Will the Federal Reserve hold interest rates steady at the March 2025 meeting?",
        6.5,
        0.91,
        82_000,
        18_500,
        0.010,
    ),
    _market(
        "sample-002",
        "Will Bitcoin close above $95,000 today?",
        11.2,
        0.88,
        145_000,
        42_300,
        0.012,
    ),
    _market(
        "sample-003",
        "Will the S&P 500 end the week positive?",
        14.8,
        0.93,
        67_000,
        9_800,
        0.015,
    ),
    _market(
        "sample-004",
        "Will the US jobs report beat expectations this Friday?",
        4.0,
        0.87,
        38_000,
        5_200,
        0.018,
    ),
    _market(
        "sample-005",
        "Will Nvidia stock close above $900 today?",
        8.3,
        0.90,
        220_000,
        61_000,
        0.008,
    ),
    _market(
        "sample-006",
        "Will the Euro-Dollar rate stay above 1.08 through Friday?",
        18.0,
        0.94,
        55_000,
        7_400,
        0.014,
    ),
    _market(
        "sample-007",
        "Will Apple announce a new product before March 31?",
        20.5,
        0.89,
        29_000,
        3_100,
        0.022,
    ),
    _market(
        "sample-008",
        "Will the US CPI data release show inflation below 3%?",
        3.2,
        0.92,
        91_000,
        24_700,
        0.009,
    ),
    _market(
        "sample-009",
        "Will Tesla stock close above $185 today?",
        9.7,
        0.95,
        178_000,
        39_000,
        0.007,
    ),
    _market(
        "sample-010",
        "Will gold price stay above $2,300/oz this week?",
        22.1,
        0.88,
        43_000,
        6_200,
        0.019,
    ),
    # Intentionally weak markets (low liquidity, wide spread) for variety
    _market(
        "sample-011",
        "Will some celebrity announce a new movie deal by end of day?",
        5.5,
        0.87,
        4_000,
        800,
        0.030,
    ),
    _market(
        "sample-012",
        "Will Twitter trends change dramatically over the weekend?",
        23.9,
        0.91,
        3_500,
        400,
        0.025,
    ),
]
