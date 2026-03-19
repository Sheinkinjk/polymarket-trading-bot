"""
Strategy engine — filters, scores, explains, and assigns confidence to markets.

──────────────────────────────────────────────────────────────────────────────
FILTER (hard gates — any failure → rejected immediately)
  price          0.87–0.95
  hours_to_end   0 < h ≤ 24
  liquidity      ≥ $10,000
  volume_24h     ≥ $3,000   (depth proxy)
  spread         ≤ 0.015

POST-SCORE GATE
  final_score    ≥ MIN_ACCEPT_SCORE (55)
  accepted list  capped at TOP_N (15) highest-scoring markets

──────────────────────────────────────────────────────────────────────────────
SCORING  (each component 0–100, then weighted)

  liquidity    24%  — log-scale $10K→$1M; thin markets hurt hard
  spread       22%  — non-linear; anything above 0.010 is penalised sharply
  objectivity  18%  — keyword heuristic; rewards concrete binary questions
  time         14%  — sweet spot 4–16h; very short or very long both score lower
  price_band   10%  — peaks at the midpoint 0.91; edges score lower
  depth         8%  — vol24h / liquidity ratio; measures active participation
  stability     4%  — spread tightness × price confidence composite

PENALTIES  (multiplicative reductions after weighted sum)

  low_liquidity  0–30%  — stacked on top of low liquidity component score
  wide_spread    0–20%  — stacked on top of wide spread component score
  fragility      0–30%  — market closing in < 2h
  ambiguity      0–45%  — subjective/vague question language (0.15 per hit)
  reversal_risk  0–12%  — higher price = more downside if wrong

CONFIDENCE  (assigned after final score + penalty profile)

  High    score ≥ 72, low penalties, strong liquidity and spread signals
  Medium  score ≥ 58, moderate penalties
  Low     everything else that still passes the minimum threshold

──────────────────────────────────────────────────────────────────────────────
"""
import math
from typing import Optional

from app.scanner import hours_until_end


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
PRICE_LOW        = 0.87
PRICE_HIGH       = 0.95
MAX_HOURS        = 24.0
MIN_LIQ          = 10_000     # raised from 5K — thin markets produce false signals
MIN_VOL24        = 3_000      # raised from 1K
MAX_SPREAD       = 0.015      # tightened from 0.02
MIN_ACCEPT_SCORE = 55         # post-score gate: anything below is rejected
TOP_N            = 15         # max accepted markets returned to the dashboard

# Primary tradeable band
PRIMARY_PRICE_LOW   = 0.89
PRIMARY_PRICE_HIGH  = 0.94
PRIMARY_MIN_HOURS   = 2.0
PRIMARY_MAX_HOURS   = 12.0
# Uses existing MIN_LIQ, MAX_SPREAD, MIN_VOL24, MIN_ACCEPT_SCORE

# Secondary tradeable band (wider price/time, but stricter quality bar)
SECONDARY_MIN_LIQ    = 20_000
SECONDARY_MAX_SPREAD = 0.010
SECONDARY_MIN_VOL    = 5_000
SECONDARY_MIN_OBJ    = 65      # min objectivity component score
SECONDARY_MIN_SCORE  = 60

# Watchlist band (close to valid but not tradeable)
WATCHLIST_PRICE_LOW  = 0.85
WATCHLIST_PRICE_HIGH = 0.96
WATCHLIST_MAX_HOURS  = 48.0
WATCHLIST_MIN_LIQ    = 3_000


# ---------------------------------------------------------------------------
# Question-quality keyword lists
# ---------------------------------------------------------------------------
AMBIGUOUS_KEYWORDS = [
    "best", "most popular", "favourite", "favorite", "biggest", "worst",
    "most likely", "feel", "think", "probably", "opinion", "seem",
    "celebrity", "viral", "trend", "famous", "influencer", "controversial",
    "dramatically", "huge", "major", "significantly", "notable", "interesting",
    "shocking", "surprising", "unexpected",
]

OBJECTIVE_SIGNALS = [
    "close above", "close below", "exceed", "fall below", "reach",
    "rate", "price", "index", "report", "announce", "approve", "confirm",
    "pass", "sign", "win", "lose", "beat", "miss", "above", "below",
    "more than", "less than", "at least", "no more than",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _fmt_usd(n: float) -> str:
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n:.0f}"


# ---------------------------------------------------------------------------
# Component score functions  (each returns 0–100)
# ---------------------------------------------------------------------------

def _score_liquidity(liq: float) -> float:
    """
    Log-scale from $10K (→ 0) to $1M (→ 100).
    Anything below $25K scores under 35 — genuinely thin.
    """
    if liq <= 0:
        return 0.0
    lo = math.log10(10_000)
    hi = math.log10(1_000_000)
    return _clamp((math.log10(max(liq, 1)) - lo) / (hi - lo) * 100)


_SPREAD_REF = 0.025   # reference ceiling for scoring (wider than the filter threshold)

def _score_spread(spread: float) -> float:
    """
    Non-linear decay against a 0.025 reference so the curve is meaningful
    across the realistic 0–0.015 range that passes the filter.
      0.000 → 100   0.003 → 85   0.005 → 72   0.008 → 53   0.012 → 32   0.015 → 18
    """
    if spread <= 0:
        return 100.0
    ratio = min(spread / _SPREAD_REF, 1.0)
    return _clamp(100 * (1 - ratio) ** 1.8)


def _score_objectivity(question: str) -> float:
    """
    Rewards clear, measurable yes/no questions.
    Penalises vague, opinionated, or celebrity-driven language.
    """
    q = question.lower()
    obj_hits  = sum(1 for sig in OBJECTIVE_SIGNALS   if sig in q)
    ambig_hits = sum(1 for kw  in AMBIGUOUS_KEYWORDS if kw  in q)

    score = 50.0
    score += min(obj_hits  * 12, 50)   # up to +50 for concrete signals
    score -= min(ambig_hits * 25, 60)  # up to -60 for vague language
    return _clamp(score)


def _score_time(hours: float) -> float:
    """
    Sweet spot is 4–16h.  < 1h is nearly useless; > 20h loses urgency.
    """
    if hours <= 0:
        return 0.0
    if hours < 0.5:
        return 5.0
    if hours < 1:
        return _clamp(5 + (hours - 0.5) / 0.5 * 20)    # 5 → 25
    if hours < 4:
        return _clamp(25 + (hours - 1) / 3  * 55)        # 25 → 80
    if hours <= 16:
        return 100.0
    if hours <= 24:
        return _clamp(100 - (hours - 16) / 8 * 45)       # 100 → 55
    return 50.0


def _score_price_band(price: float) -> float:
    """Peaks at midpoint 0.91; edges of [0.87, 0.95] score near zero."""
    mid        = (PRICE_LOW + PRICE_HIGH) / 2   # 0.91
    half_range = (PRICE_HIGH - PRICE_LOW) / 2   # 0.04
    dist = abs(price - mid)
    base = max(0.0, 1 - dist / half_range)      # clamp before power — avoids complex numbers
    return _clamp(base ** 1.5 * 100)


def _score_depth(volume_24h: float, liquidity: float) -> float:
    """
    vol24h / liquidity ratio measures active participation.
    Ratio ≥ 0.40 → 100;  ratio ≤ 0.03 → 0.
    """
    if liquidity <= 0:
        return 0.0
    ratio = volume_24h / liquidity
    return _clamp((ratio - 0.03) / (0.40 - 0.03) * 100)


def _score_stability(spread: float, price: float) -> float:
    """Combines spread tightness and price conviction (distance from 0.50)."""
    conviction = abs(price - 0.5) / 0.45   # 0.87 → 0.82, 0.95 → 1.0
    return _clamp(_score_spread(spread) * 0.6 + conviction * 40)


# ---------------------------------------------------------------------------
# Penalty functions  (each returns a fraction to subtract, e.g. 0.15 = 15%)
# ---------------------------------------------------------------------------

def _penalty_low_liquidity(liq: float) -> float:
    """
    Stacked penalty on top of the already-reduced liquidity component score.
    Even if a market squeaks past the $10K filter, very thin depth is dangerous.
    """
    if liq < 12_000:
        return 0.25
    if liq < 25_000:
        return 0.15
    if liq < 50_000:
        return 0.07
    return 0.0


def _penalty_wide_spread(spread: float) -> float:
    """
    Stacked penalty when the spread itself signals poor market quality.
    A wide spread means the bid and ask are far apart — the market maker
    is pricing in high uncertainty.
    """
    if spread > 0.012:
        return 0.18
    if spread > 0.008:
        return 0.10
    if spread > 0.005:
        return 0.04
    return 0.0


def _penalty_fragility(hours: float) -> float:
    """Market closing very soon has little reaction window left."""
    if hours < 0.5:
        return 0.30
    if hours < 1.0:
        return 0.22
    if hours < 2.0:
        return 0.12
    return 0.0


def _penalty_ambiguity(question: str) -> float:
    """
    Subjective questions introduce resolution risk — will the oracle
    agree with your interpretation?  Each ambiguous keyword = 15% penalty.
    """
    q    = question.lower()
    hits = sum(1 for kw in AMBIGUOUS_KEYWORDS if kw in q)
    return _clamp(hits * 0.15, 0.0, 0.45)


def _penalty_reversal_risk(price: float) -> float:
    """
    A YES price of 0.95 leaves only 5¢ of upside.
    A reversal from 0.95 to 0.50 costs 45¢ — very asymmetric downside.
    Penalty scales linearly: 0.87 → 0%, 0.95 → 12%.
    """
    return _clamp((price - PRICE_LOW) / (PRICE_HIGH - PRICE_LOW) * 0.12, 0.0, 0.12)


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
WEIGHTS = {
    "liquidity":   0.24,
    "spread":      0.22,
    "objectivity": 0.18,
    "time":        0.14,
    "price_band":  0.10,
    "depth":       0.08,
    "stability":   0.04,
}


# ---------------------------------------------------------------------------
# Confidence level
# ---------------------------------------------------------------------------

def _confidence(score: float, penalties: dict, components: dict) -> str:
    """
    High   — score ≥ 72, total penalty < 12%, strong liquidity + spread signals
    Medium — score ≥ 58, total penalty < 28%
    Low    — everything else still above the minimum accept threshold
    """
    total_pen = sum(penalties.values())
    liq_ok    = components.get("liquidity",   0) >= 55
    spread_ok = components.get("spread",      0) >= 55
    obj_ok    = components.get("objectivity", 0) >= 55

    if score >= 72 and total_pen < 0.12 and liq_ok and spread_ok and obj_ok:
        return "High"
    if score >= 58 and total_pen < 0.28:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_market(market: dict) -> dict:
    """Compute all scores, penalties, confidence, and attach to market dict."""
    hours    = hours_until_end(market) or 0.0
    price    = float(market.get("yes_price")  or 0.0)
    liq      = float(market.get("liquidity")  or 0.0)
    vol      = float(market.get("volume_24h") or 0.0)
    spread   = float(market.get("spread")     or 0.01)
    question = market.get("question")   or ""

    components = {
        "liquidity":   _score_liquidity(liq),
        "spread":      _score_spread(spread),
        "objectivity": _score_objectivity(question),
        "time":        _score_time(hours),
        "price_band":  _score_price_band(price),
        "depth":       _score_depth(vol, liq),
        "stability":   _score_stability(spread, price),
    }

    weighted = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)

    penalties = {
        "low_liquidity":  _penalty_low_liquidity(liq),
        "wide_spread":    _penalty_wide_spread(spread),
        "fragility":      _penalty_fragility(hours),
        "ambiguity":      _penalty_ambiguity(question),
        "reversal_risk":  _penalty_reversal_risk(price),
    }
    total_penalty = min(sum(penalties.values()), 0.75)   # hard cap at 75% total reduction
    final_score   = round(_clamp(weighted * (1 - total_penalty)), 1)

    tier = "A" if final_score >= 75 else ("B" if final_score >= 60 else "C")
    confidence = _confidence(final_score, penalties, components)

    return {
        **market,
        "hours_to_end":  round(hours, 1),
        "score":         final_score,
        "tier":          tier,
        "confidence":    confidence,
        "components":    components,
        "penalties":     penalties,
        "total_penalty": round(total_penalty * 100, 1),
    }


# ---------------------------------------------------------------------------
# Hard filter
# ---------------------------------------------------------------------------

def passes_filter(market: dict) -> tuple:
    """
    Hard gate — any failure returns (False, human-readable reason).
    Reasons are written for a non-technical audience.
    """
    price  = float(market.get("yes_price")  or 0.0)
    liq    = float(market.get("liquidity")  or 0.0)
    vol    = float(market.get("volume_24h") or 0.0)
    spread = float(market.get("spread")     or 1.0)
    hours  = hours_until_end(market)

    if hours is None or hours <= 0:
        return False, (
            "This market has already ended or has no closing date, "
            "so there's nothing left to trade."
        )
    if hours > MAX_HOURS:
        return False, (
            f"Closes in {hours:.0f}h — we only look at markets ending within 24h "
            f"because longer windows carry too much overnight uncertainty."
        )
    if price < PRICE_LOW or price > PRICE_HIGH:
        direction = "too low" if price < PRICE_LOW else "too high"
        return False, (
            f"Price is {price:.3f} ({direction}). The target window is 0.87–0.95: "
            f"below 0.87 the probability isn't convincing enough; "
            f"above 0.95 almost no upside remains if you're right."
        )
    if liq < MIN_LIQ:
        return False, (
            f"Liquidity is only {_fmt_usd(liq)} — below the {_fmt_usd(MIN_LIQ)} minimum. "
            f"Thin markets are easy to move and prices can't be trusted."
        )
    if vol < MIN_VOL24:
        return False, (
            f"Only {_fmt_usd(vol)} traded in the last 24h — not enough activity "
            f"to confirm the price reflects real conviction."
        )
    if spread > MAX_SPREAD:
        return False, (
            f"Spread is {spread:.3f} — above the {MAX_SPREAD:.3f} limit. "
            f"A wide gap between buy and sell price means the market is "
            f"illiquid or the outcome is genuinely uncertain."
        )

    return True, ""


# ---------------------------------------------------------------------------
# Explanation builder  (causal, written for non-technical readers)
# ---------------------------------------------------------------------------

def build_explanation(market: dict) -> str:
    accepted = market.get("accepted", False)
    reason   = market.get("reject_reason", "")

    if not accepted:
        # For rejected: the reject_reason already has a full sentence — just return it
        return reason

    score      = market.get("score",       0)
    tier       = market.get("tier",        "C")
    confidence = market.get("confidence",  "Low")
    hours      = market.get("hours_to_end", 0)
    price      = market.get("yes_price",   0)
    liq        = market.get("liquidity",   0)
    spread     = market.get("spread",      0)
    vol        = market.get("volume_24h",  0)
    comps      = market.get("components",  {})
    pens       = market.get("penalties",   {})

    parts: list = []

    # ── Lead: what is this market's strongest asset? ──────────────────────
    strengths = sorted(
        [(k, comps[k]) for k in ("liquidity", "spread", "objectivity", "time", "depth")],
        key=lambda x: x[1], reverse=True,
    )
    top_signal, top_val = strengths[0]

    if top_signal == "liquidity" and top_val >= 60:
        parts.append(
            f"Deep market: {_fmt_usd(liq)} in liquidity means prices here "
            f"reflect genuine conviction from many participants, not just a few trades."
        )
    elif top_signal == "spread" and top_val >= 60:
        parts.append(
            f"Very tight spread ({spread:.3f}) — buyers and sellers agree closely "
            f"on the price, which means low friction and a more trustworthy signal."
        )
    elif top_signal == "objectivity" and top_val >= 60:
        parts.append(
            "The question has a clear, binary, measurable outcome — "
            "no interpretation needed, just wait for the result."
        )
    elif top_signal == "time" and top_val >= 70:
        parts.append(
            f"{hours:.1f}h to close is an ideal window — "
            f"enough time for the outcome to be priced in without overnight risk."
        )
    elif top_signal == "depth" and top_val >= 50:
        parts.append(
            f"Active trading: {_fmt_usd(vol)} volume in the last 24h relative to "
            f"liquidity shows real ongoing participation, not a stale market."
        )
    else:
        parts.append(
            f"Passes all filters with a score of {score}/100."
        )

    # ── Supporting details ────────────────────────────────────────────────
    # Liquidity (if not already the lead)
    if top_signal != "liquidity":
        if comps.get("liquidity", 0) >= 60:
            parts.append(f"Liquidity is solid at {_fmt_usd(liq)}.")
        elif comps.get("liquidity", 0) >= 35:
            parts.append(f"Liquidity ({_fmt_usd(liq)}) is adequate but not deep — keep position size small.")

    # Time (if not already the lead)
    if top_signal != "time":
        if 4 <= hours <= 16:
            parts.append(f"At {hours:.1f}h to close, timing is ideal.")
        elif hours < 2:
            parts.append(f"Only {hours:.1f}h left — the window is very tight.")
        elif hours > 18:
            parts.append(f"{hours:.1f}h remaining — still quite far from resolution.")

    # Price position
    if 0.89 <= price <= 0.93:
        parts.append(
            f"Price of {price:.2f} sits near the centre of the target range, "
            f"balancing upside ({(1-price):.2f}) against the cost of being wrong."
        )
    elif price >= 0.93:
        parts.append(
            f"Price is {price:.2f} — close to the top of the range. "
            f"Only {(1-price):.2f} upside if correct, but {price:.2f} at risk if wrong."
        )
    else:
        parts.append(
            f"Price of {price:.2f} is near the bottom of the range — "
            f"more upside ({(1-price):.2f}) but the market is less certain."
        )

    # ── Main concerns (from active penalties) ────────────────────────────
    active_penalties = {k: v for k, v in pens.items() if v >= 0.04}
    concerns: list = []

    if pens.get("low_liquidity", 0) >= 0.07:
        concerns.append(
            f"liquidity ({_fmt_usd(liq)}) is thinner than ideal — "
            f"a few large trades could move the price"
        )
    if pens.get("wide_spread", 0) >= 0.04:
        concerns.append(
            f"spread of {spread:.3f} is wider than preferred, "
            f"eating into any potential gain"
        )
    if pens.get("fragility", 0) >= 0.12:
        concerns.append(
            f"only {hours:.1f}h until close — very little time to react "
            f"if the market moves against you"
        )
    if pens.get("ambiguity", 0) >= 0.15:
        concerns.append(
            "the question contains language that could be interpreted "
            "differently, adding resolution risk"
        )
    if pens.get("reversal_risk", 0) >= 0.08:
        concerns.append(
            f"at {price:.2f}, a reversal would cost far more than the "
            f"{(1-price):.2f} gain if the market resolves YES"
        )

    if concerns:
        concern_str = "; ".join(concerns)
        parts.append(f"Main concern: {concern_str}.")

    # ── Verdict ───────────────────────────────────────────────────────────
    tier_verdicts = {
        "A": "Overall this looks like a strong opportunity — all key signals align.",
        "B": "Solid setup with one or two minor reservations — worth monitoring closely.",
        "C": "Passes minimum criteria but has notable weaknesses — proceed cautiously if at all.",
    }
    parts.append(tier_verdicts.get(tier, ""))

    return "  ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Band classification
# ---------------------------------------------------------------------------

def classify_market_band(scored: dict) -> str:
    """
    Classify a scored market into: primary, secondary, watchlist, or rejected.
    """
    price  = float(scored.get("yes_price")    or 0.0)
    liq    = float(scored.get("liquidity")    or 0.0)
    vol    = float(scored.get("volume_24h")   or 0.0)
    spread = float(scored.get("spread")       or 1.0)
    score  = float(scored.get("score")        or 0.0)
    hours  = float(scored.get("hours_to_end") or 0.0)

    # Get objectivity score — prefer pre-computed, fall back to computing it
    components = scored.get("components", {})
    if components:
        obj = components.get("objectivity", 0)
    else:
        obj = _score_objectivity(scored.get("question", ""))

    # Primary band
    if (
        PRIMARY_PRICE_LOW <= price <= PRIMARY_PRICE_HIGH
        and PRIMARY_MIN_HOURS <= hours <= PRIMARY_MAX_HOURS
        and liq >= MIN_LIQ
        and spread <= MAX_SPREAD
        and vol >= MIN_VOL24
        and score >= MIN_ACCEPT_SCORE
    ):
        return "primary"

    # Secondary band (only checked if not primary)
    if (
        PRICE_LOW <= price <= PRICE_HIGH
        and 0 < hours <= MAX_HOURS
        and liq >= SECONDARY_MIN_LIQ
        and spread <= SECONDARY_MAX_SPREAD
        and vol >= SECONDARY_MIN_VOL
        and obj >= SECONDARY_MIN_OBJ
        and score >= SECONDARY_MIN_SCORE
    ):
        return "secondary"

    # Watchlist band
    if (
        WATCHLIST_PRICE_LOW <= price <= WATCHLIST_PRICE_HIGH
        and 0 < hours <= WATCHLIST_MAX_HOURS
        and liq >= WATCHLIST_MIN_LIQ
    ):
        return "watchlist"

    return "rejected"


def _band_reject_reason(scored: dict, band: str) -> str:
    """
    Returns a human-readable reason why a market didn't make it into a tradeable band.
    """
    price  = scored.get("yes_price")  or 0.0
    liq    = scored.get("liquidity")  or 0.0
    vol    = scored.get("volume_24h") or 0.0
    spread = scored.get("spread")     or 1.0
    score  = scored.get("score")      or 0.0
    hours  = scored.get("hours_to_end") or 0.0

    components = scored.get("components", {})
    if components:
        obj = components.get("objectivity", 0)
    else:
        obj = _score_objectivity(scored.get("question", ""))

    reasons: list = []

    # Check price
    if price < PRICE_LOW or price > PRICE_HIGH:
        direction = "below" if price < PRICE_LOW else "above"
        reasons.append(f"price {price:.3f} is {direction} target range {PRICE_LOW}–{PRICE_HIGH}")

    # Check hours
    if hours <= 0:
        reasons.append("market has already ended or no closing date")
    elif hours > MAX_HOURS:
        reasons.append(f"closes in {hours:.0f}h (max {MAX_HOURS:.0f}h)")

    # Check liquidity
    if liq < MIN_LIQ:
        reasons.append(f"liquidity {_fmt_usd(liq)} below {_fmt_usd(MIN_LIQ)} minimum")

    # Check spread
    if spread > MAX_SPREAD:
        reasons.append(f"spread {spread:.3f} exceeds {MAX_SPREAD:.3f} limit")

    # Check volume
    if vol < MIN_VOL24:
        reasons.append(f"24h volume {_fmt_usd(vol)} below {_fmt_usd(MIN_VOL24)} minimum")

    # Check objectivity (relevant for secondary band)
    if "ambig" in (scored.get("explanation") or "").lower() or obj < SECONDARY_MIN_OBJ:
        if not any("price" in r or "liquidity" in r or "spread" in r or "volume" in r or "hours" in r or "closes" in r or "ended" in r for r in reasons):
            reasons.append(f"objectivity score {obj:.0f} below secondary band threshold {SECONDARY_MIN_OBJ}")

    # Check score
    if score < MIN_ACCEPT_SCORE:
        reasons.append(f"overall score {score}/100 below minimum {MIN_ACCEPT_SCORE}")

    if not reasons:
        reasons.append("does not meet tradeable band criteria")

    return "Not tradeable: " + "; ".join(reasons[:2]) + "."


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(raw_markets: list) -> list:
    """
    Score → classify band → explain → cap at TOP_N.
    Returns all markets (accepted and rejected) with full scoring attached.
    Accepted list is capped at TOP_N by score.
    """
    results: list = []

    for m in raw_markets:
        scored   = score_market(m)
        band     = classify_market_band(scored)
        accepted = band in ("primary", "secondary")

        if accepted:
            reject_reason = ""
        else:
            reject_reason = _band_reject_reason(scored, band)

        scored["band"]          = band
        scored["accepted"]      = accepted
        scored["reject_reason"] = reject_reason
        scored["explanation"]   = build_explanation({**scored, "accepted": accepted, "reject_reason": reject_reason})
        results.append(scored)

    accepted_list = sorted([r for r in results if r["accepted"]], key=lambda x: -x["score"])
    rejected_list = sorted([r for r in results if not r["accepted"]], key=lambda x: -x["score"])

    if len(accepted_list) > TOP_N:
        overflow = accepted_list[TOP_N:]
        for m in overflow:
            m["accepted"]      = False
            m["band"]          = "capped"
            m["reject_reason"] = f"Score {m['score']}/100 is strong, but only the top {TOP_N} opportunities are shown."
            m["explanation"]   = m["reject_reason"]
        rejected_list = overflow + rejected_list
        accepted_list  = accepted_list[:TOP_N]

    return accepted_list + rejected_list


def _score_gate_reason(scored: dict) -> str:
    """
    Identify the two weakest signals and explain them in plain English.
    Uses absolute thresholds so the label matches the actual issue.
    """
    comps  = scored.get("components", {})
    pens   = scored.get("penalties",  {})
    price  = scored.get("yes_price",  0)
    liq    = scored.get("liquidity",  0)
    spread = scored.get("spread",     0)
    hours  = scored.get("hours_to_end", 0)

    issues: list = []

    # Use absolute values, not component scores, for the labels
    if liq < 25_000:
        issues.append(f"liquidity is very thin ({_fmt_usd(liq)})")
    if spread > 0.012:
        issues.append(f"spread is wide ({spread:.3f})")
    if pens.get("ambiguity", 0) >= 0.15:
        issues.append("the question is too ambiguous for reliable resolution")
    if hours < 1.5:
        issues.append(f"only {hours:.1f}h until close — too little time left")
    if price >= 0.94:
        issues.append(
            f"price of {price:.2f} leaves only {(1-price):.2f} upside "
            f"against {price:.2f} downside — poor risk/reward"
        )
    if comps.get("depth", 100) < 20:
        issues.append("very low 24h volume relative to liquidity — stale market")

    if issues:
        return "; ".join(issues) + " drags the score below threshold."
    # Fallback: find the single lowest component
    worst = min(comps, key=lambda k: comps[k])
    return f"weak {worst.replace('_', ' ')} signal pulls the overall score down."


# ---------------------------------------------------------------------------
# Rejection reason counting
# ---------------------------------------------------------------------------

def count_rejection_reasons(markets: list) -> dict:
    """
    For all non-accepted markets, find the primary rejection reason.
    Returns dict: {"price": N, "timing": N, "liquidity": N, "spread": N,
                   "depth": N, "ambiguity": N, "score": N, "other": N}
    """
    counts: dict = {"price": 0, "timing": 0, "liquidity": 0, "spread": 0,
                    "depth": 0, "ambiguity": 0, "score": 0, "other": 0}

    for m in markets:
        if m.get("accepted"):
            continue

        price  = m.get("yes_price")  or 0.0
        liq    = m.get("liquidity")  or 0.0
        vol    = m.get("volume_24h") or 0.0
        spread = m.get("spread")     or 0.0
        hours  = m.get("hours_to_end") or 0.0
        score  = m.get("score")      or 0.0
        expl   = (m.get("explanation") or m.get("reject_reason") or "").lower()

        if price < PRICE_LOW or price > PRICE_HIGH:
            counts["price"] += 1
        elif hours <= 0 or hours > MAX_HOURS:
            counts["timing"] += 1
        elif liq < MIN_LIQ:
            counts["liquidity"] += 1
        elif spread > MAX_SPREAD:
            counts["spread"] += 1
        elif vol < MIN_VOL24:
            counts["depth"] += 1
        elif "ambig" in expl:
            counts["ambiguity"] += 1
        elif score < MIN_ACCEPT_SCORE:
            counts["score"] += 1
        else:
            counts["other"] += 1

    return counts


# ---------------------------------------------------------------------------
# Threshold analysis
# ---------------------------------------------------------------------------

def run_threshold_analysis(db_markets: list) -> list:
    """
    Compare three rulesets (Current, Looser, Tighter) against DB markets.
    Returns list of dicts: {name, desc, primary, secondary, watchlist, rejected, accepted, total, avg_score}
    """
    VARIANTS = {
        "Current Rules": {
            "desc": "Your current filter settings",
            "prim_p_lo": 0.89, "prim_p_hi": 0.94,
            "prim_h_lo": 2.0, "prim_h_hi": 12.0,
            "prim_liq": 10_000, "prim_spread": 0.015, "prim_vol": 3_000, "prim_score": 55,
            "sec_liq": 20_000, "sec_spread": 0.010, "sec_vol": 5_000, "sec_obj": 65, "sec_score": 60,
        },
        "Looser Rules": {
            "desc": "Wider price band, more timing flexibility, lower quality bar",
            "prim_p_lo": 0.88, "prim_p_hi": 0.95,
            "prim_h_lo": 1.0, "prim_h_hi": 16.0,
            "prim_liq": 7_000, "prim_spread": 0.020, "prim_vol": 1_500, "prim_score": 50,
            "sec_liq": 12_000, "sec_spread": 0.015, "sec_vol": 3_000, "sec_obj": 55, "sec_score": 55,
        },
        "Tighter Rules": {
            "desc": "Narrower price band, shorter time window, higher quality bar",
            "prim_p_lo": 0.90, "prim_p_hi": 0.93,
            "prim_h_lo": 3.0, "prim_h_hi": 10.0,
            "prim_liq": 15_000, "prim_spread": 0.010, "prim_vol": 5_000, "prim_score": 65,
            "sec_liq": 30_000, "sec_spread": 0.008, "sec_vol": 8_000, "sec_obj": 75, "sec_score": 70,
        },
    }

    results: list = []

    for name, v in VARIANTS.items():
        n_primary = n_secondary = n_watchlist = n_rejected = 0
        accepted_scores: list = []

        for m in db_markets:
            price  = m.get("yes_price")  or 0.0
            liq    = m.get("liquidity")  or 0.0
            vol    = m.get("volume_24h") or 0.0
            spread = m.get("spread")     or 1.0
            score  = m.get("score")      or 0.0

            # Compute hours — use stored value or recompute
            if "hours_to_end" in m and m["hours_to_end"] is not None:
                hours = m["hours_to_end"]
            else:
                h = hours_until_end(m)
                hours = h if h is not None else 0.0

            # Compute objectivity since components aren't stored in DB
            obj = _score_objectivity(m.get("question", ""))

            # Primary band classification with variant params
            is_primary = (
                v["prim_p_lo"] <= price <= v["prim_p_hi"]
                and v["prim_h_lo"] <= hours <= v["prim_h_hi"]
                and liq >= v["prim_liq"]
                and spread <= v["prim_spread"]
                and vol >= v["prim_vol"]
                and score >= v["prim_score"]
            )

            # Secondary band classification with variant params
            is_secondary = (
                not is_primary
                and PRICE_LOW <= price <= PRICE_HIGH
                and 0 < hours <= MAX_HOURS
                and liq >= v["sec_liq"]
                and spread <= v["sec_spread"]
                and vol >= v["sec_vol"]
                and obj >= v["sec_obj"]
                and score >= v["sec_score"]
            )

            # Watchlist band (same for all variants)
            is_watchlist = (
                not is_primary and not is_secondary
                and WATCHLIST_PRICE_LOW <= price <= WATCHLIST_PRICE_HIGH
                and 0 < hours <= WATCHLIST_MAX_HOURS
                and liq >= WATCHLIST_MIN_LIQ
            )

            if is_primary:
                n_primary += 1
                accepted_scores.append(score)
            elif is_secondary:
                n_secondary += 1
                accepted_scores.append(score)
            elif is_watchlist:
                n_watchlist += 1
            else:
                n_rejected += 1

        n_accepted = n_primary + n_secondary
        avg_score = round(sum(accepted_scores) / len(accepted_scores), 1) if accepted_scores else 0.0

        results.append({
            "name":      name,
            "desc":      v["desc"],
            "primary":   n_primary,
            "secondary": n_secondary,
            "watchlist": n_watchlist,
            "rejected":  n_rejected,
            "accepted":  n_accepted,
            "total":     len(db_markets),
            "avg_score": avg_score,
        })

    return results
