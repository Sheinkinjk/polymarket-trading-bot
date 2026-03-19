"""
Plain-English label helpers.

Pure Python — no Streamlit imports. Translates technical scores, tiers,
and penalties into language a non-technical founder can read immediately.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Component signal vocabulary
# ---------------------------------------------------------------------------
_SIGNALS: dict[str, list[tuple[float, str]]] = {
    "liquidity": [
        (82, "deep liquidity"),
        (60, "solid liquidity"),
        (38, "thin liquidity"),
        (0,  "very thin liquidity"),
    ],
    "spread": [
        (80, "tight spread"),
        (55, "acceptable spread"),
        (32, "wide spread"),
        (0,  "very wide spread"),
    ],
    "objectivity": [
        (80, "clear binary outcome"),
        (55, "reasonably clear outcome"),
        (30, "somewhat ambiguous"),
        (0,  "very ambiguous question"),
    ],
    "time": [
        (82, "ideal timing window"),
        (55, "good timing"),
        (30, "tight timing"),
        (0,  "very little time left"),
    ],
    "price_band": [
        (80, "ideal price position"),
        (55, "good price position"),
        (30, "edge of target range"),
        (0,  "outside ideal range"),
    ],
    "depth": [
        (80, "high trading activity"),
        (55, "decent activity"),
        (28, "low trading activity"),
        (0,  "stale market"),
    ],
    "stability": [
        (80, "very stable signal"),
        (55, "mostly stable"),
        (30, "unstable pricing"),
        (0,  "unreliable signal"),
    ],
}

_PENALTY_NAMES = {
    "low_liquidity": "Liquidity penalty",
    "wide_spread":   "Spread penalty",
    "fragility":     "Closing-soon penalty",
    "ambiguity":     "Ambiguity penalty",
    "reversal_risk": "Reversal-risk penalty",
}

_PENALTY_TIPS = {
    "low_liquidity": "Market is thin — prices can shift with one large trade.",
    "wide_spread":   "Gap between buy and sell prices eats into potential profit.",
    "fragility":     "Market closes very soon — almost no reaction window left.",
    "ambiguity":     "Question language is vague — resolution could surprise you.",
    "reversal_risk": "Price is near 0.95 — almost no upside, large downside if wrong.",
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def signal_label(component: str, score: float) -> str:
    """'liquidity', 82 → 'deep liquidity'"""
    levels = _SIGNALS.get(component, [(80, "strong"), (55, "adequate"), (30, "weak"), (0, "very weak")])
    for threshold, label in levels:
        if score >= threshold:
            return label
    return levels[-1][1]


def penalty_name(key: str) -> str:
    return _PENALTY_NAMES.get(key, key.replace("_", " ").title())


def penalty_tip(key: str) -> str:
    return _PENALTY_TIPS.get(key, "")


# ---------------------------------------------------------------------------
# Action / confidence
# ---------------------------------------------------------------------------

def action_label(market: dict) -> str:
    if not market.get("accepted"):
        return "Do Not Trade"
    band  = market.get("band", "")
    score = market.get("score") or 0
    tier  = market.get("tier", "C")
    # Primary band with strong score → Strong Candidate
    if band == "primary" and score >= 65:
        return "Strong Candidate"
    # Backward compat: no band stored, use tier
    if not band and tier == "A":
        return "Strong Candidate"
    # Everything else that's accepted → Watch Closely
    return "Watch Closely"


def action_color(action: str) -> str:
    return {
        "Strong Candidate": "#22c55e",
        "Watch Closely":    "#f59e0b",
        "Do Not Trade":     "#ef4444",
    }.get(action, "#7a8299")


def confidence_from_tier(tier: str, score: float = 0, accepted: bool = True) -> str:
    """
    Confidence levels:
      Very High  — Tier A and score >= 90
      High       — Tier A and score < 90
      Medium     — Tier B
      Low        — Tier C (accepted)
      None       — Rejected
    """
    if not accepted:
        return "None"
    if tier == "A":
        return "Very High" if score >= 90 else "High"
    if tier == "B":
        return "Medium"
    return "Low"


def confidence_color(conf: str) -> str:
    return {
        "Very High": "#10b981",
        "High":      "#22c55e",
        "Medium":    "#f59e0b",
        "Low":       "#ef4444",
        "None":      "#3a3f58",
    }.get(conf, "#5a6380")


def confidence_badge_class(conf: str) -> str:
    return {
        "Very High": "conf-vh",
        "High":      "conf-h",
        "Medium":    "conf-m",
        "Low":       "conf-l",
        "None":      "conf-n",
    }.get(conf, "conf-n")


def tier_color(tier: str) -> str:
    return {"A": "#f0b429", "B": "#7c6af6", "C": "#4a4e6a"}.get(tier, "#4a4e6a")


# ---------------------------------------------------------------------------
# Strength tags  (up to 3, only when the signal is genuinely strong)
# ---------------------------------------------------------------------------

def strength_tags(market: dict) -> list[str]:
    """
    Return up to 3 strength tag strings for a market.
    Each tag appears only when the relevant score is strong enough to mean it.
    """
    comps = market.get("components") or {}
    pens  = market.get("penalties")  or {}
    tags: list[tuple[float, str]] = []  # (score, tag) sorted by score desc

    if comps.get("liquidity", 0) >= 75:
        tags.append((comps["liquidity"], "Deep Liquidity"))
    if comps.get("spread", 0) >= 70:
        tags.append((comps["spread"], "Tight Spread"))
    if comps.get("time", 0) >= 80:
        tags.append((comps["time"], "Good Timing"))
    if comps.get("objectivity", 0) >= 75:
        tags.append((comps["objectivity"], "Clean Rules"))
    if comps.get("price_band", 0) >= 75:
        tags.append((comps["price_band"], "Ideal Price"))
    if comps.get("depth", 0) >= 70:
        tags.append((comps["depth"], "Good Depth"))
    if comps.get("stability", 0) >= 72:
        tags.append((comps["stability"], "Stable Price"))

    tags.sort(key=lambda x: -x[0])
    return [t for _, t in tags[:3]]


# ---------------------------------------------------------------------------
# Risk tags  (up to 3, only when a weakness is material)
# ---------------------------------------------------------------------------

def risk_tags(market: dict) -> list[str]:
    """
    Return up to 3 risk tag strings for a market.
    Each tag appears only when the concern is material enough to flag.
    """
    comps = market.get("components") or {}
    pens  = market.get("penalties")  or {}
    hours = market.get("hours_to_end", 0) or 0
    tags: list[tuple[float, str]] = []  # (severity, tag)

    if comps.get("liquidity", 100) < 45 or pens.get("low_liquidity", 0) >= 0.07:
        tags.append((pens.get("low_liquidity", 0) + (100 - comps.get("liquidity", 100)) / 100, "Weak Liquidity"))

    if comps.get("spread", 100) < 45 or pens.get("wide_spread", 0) >= 0.04:
        tags.append((pens.get("wide_spread", 0) + (100 - comps.get("spread", 100)) / 100, "Wide Spread"))

    if comps.get("objectivity", 100) < 45 or pens.get("ambiguity", 0) >= 0.10:
        tags.append((pens.get("ambiguity", 0) + (100 - comps.get("objectivity", 100)) / 100, "Ambiguous"))

    if pens.get("fragility", 0) >= 0.10 or (0 < hours < 2):
        tags.append((pens.get("fragility", 0) + (2 - min(hours, 2)) / 2, "Fragile"))

    if comps.get("time", 100) < 45 and hours > 18:
        tags.append((100 - comps.get("time", 100), "Late Timing"))

    if comps.get("depth", 100) < 25:
        tags.append((100 - comps.get("depth", 100), "Stale Data"))

    tags.sort(key=lambda x: -x[0])
    return [t for _, t in tags[:3]]


# ---------------------------------------------------------------------------
# Decision summary  (one sentence, decision-first)
# ---------------------------------------------------------------------------

def decision_summary(market: dict) -> str:
    """
    One crisp sentence following the patterns:
      Accepted Tier A:  "Strong candidate with X, Y, and Z."
      Accepted Tier B:  "Decent setup but X is weaker and Y is less attractive."
      Accepted Tier C:  "Marginal setup — X keeps this below the top tier."
      Near-miss:        "Close to valid, but X."
      Rejected:         "Avoid due to X and Y."
    """
    accepted = market.get("accepted", False)
    tier     = market.get("tier", "C")
    comps    = market.get("components") or {}
    pens     = market.get("penalties")  or {}

    # ── Rejected ─────────────────────────────────────────────────────────────
    if not accepted:
        expl = (market.get("explanation") or "").lower()

        # Near-miss: score >= 40 and failed by a single clear criterion
        score = market.get("score") or 0
        price = market.get("yes_price") or 0

        if price and (price < 0.87 or price > 0.95):
            dir_ = "above 0.95" if price > 0.95 else "below 0.87"
            return f"Close to valid, but price ({price:.3f}) is {dir_}."

        # Multi-reason rejected → "Avoid due to X and Y"
        reasons: list[str] = []
        if "liquidity" in expl and any(x in expl for x in ["below", "thin", "minimum"]):
            liq = market.get("liquidity") or 0
            reasons.append(f"low liquidity (${liq:,.0f})")
        if "spread" in expl and any(x in expl for x in ["wide", "above", "too wide"]):
            sp = market.get("spread") or 0
            reasons.append(f"wide spread ({sp:.3f})")
        if "ambigui" in expl or "unclear" in expl:
            reasons.append("question ambiguity")
        if "window" in expl or "24h" in expl or "ended" in expl:
            h = market.get("hours_to_end") or 0
            reasons.append(f"timing ({h:.0f}h window)")
        if "volume" in expl or "depth" in expl:
            reasons.append("insufficient depth")
        if "score" in expl and "below" in expl:
            s = market.get("score") or 0
            reasons.append(f"low overall score ({s:.0f}/100)")

        if reasons:
            return "Avoid due to " + _join(reasons[:2]) + "."
        return "Avoid — did not pass all required criteria."

    # ── Accepted ─────────────────────────────────────────────────────────────
    strengths = [
        signal_label(k, v)
        for k, v in sorted(comps.items(), key=lambda x: -x[1])
        if v >= 62
    ][:3]

    weaknesses: list[str] = []
    # Penalty-driven concerns take priority over component scores
    if pens.get("ambiguity", 0) >= 0.15:
        weaknesses.append("question ambiguity")
    if pens.get("fragility", 0) >= 0.12:
        h = market.get("hours_to_end", 0) or 0
        weaknesses.append(f"very little time ({h:.1f}h left)")
    if pens.get("low_liquidity", 0) >= 0.07:
        weaknesses.append("thinner-than-ideal liquidity")
    # Component-driven concerns
    weak_comps = sorted([(k, v) for k, v in comps.items() if v < 48], key=lambda x: x[1])
    for k, v in weak_comps[:2]:
        lbl = signal_label(k, v)
        if lbl not in weaknesses:
            weaknesses.append(lbl)

    if tier == "A":
        s = _join(strengths) if strengths else "strong overall setup"
        return f"Strong candidate with {s}."

    if tier == "B":
        s = strengths[0] if strengths else "decent setup"
        if weaknesses:
            w1 = weaknesses[0]
            w2 = f" and {weaknesses[1]} is less attractive" if len(weaknesses) > 1 else ""
            return f"Decent setup but {w1} is weaker{w2}."
        return f"Decent setup — {s}."

    # Tier C
    w = weaknesses[0] if weaknesses else "marginal signals"
    return f"Marginal setup — {w} keeps this below the top tier."


# ---------------------------------------------------------------------------
# Why ranked here
# ---------------------------------------------------------------------------

def why_ranked_here(market: dict) -> str:
    if not market.get("accepted"):
        return ""

    comps = market.get("components") or {}
    pens  = market.get("penalties")  or {}
    score = market.get("score", 0)

    top    = sorted([(k, v) for k, v in comps.items() if v >= 60], key=lambda x: -x[1])[:3]
    bottom = sorted([(k, v) for k, v in comps.items() if v < 48], key=lambda x:  x[1])[:2]

    intro = (
        "Ranked highly because"    if score >= 72 else
        "Ranked moderately because" if score >= 58 else
        "Ranked lower because, despite passing all filters,"
    )

    parts: list[str] = []
    if top:
        parts.append("it has " + _join(signal_label(k, v) for k, v in top))
    if bottom:
        phrase = _join(signal_label(k, v) for k, v in bottom)
        connector = "but" if parts else "it has"
        parts.append(f"{connector} {phrase}")

    active_pens = [(k, v) for k, v in pens.items() if v >= 0.10]
    if active_pens:
        pen_k = max(active_pens, key=lambda x: x[1])[0]
        parts.append(f"with a {penalty_name(pen_k).lower()} applied")

    if not parts:
        return f"Score {score}/100 — meets all filters without standout signals."
    return f"{intro} {' '.join(parts)}."


def _join(items) -> str:
    lst = list(items)
    if not lst:
        return ""
    if len(lst) == 1:
        return lst[0]
    return ", ".join(lst[:-1]) + f", and {lst[-1]}"


# ---------------------------------------------------------------------------
# What needs to improve
# ---------------------------------------------------------------------------

def what_needs_to_improve(market: dict) -> list[str]:
    price  = market.get("yes_price",    0)
    liq    = market.get("liquidity",    0)
    spread = market.get("spread",       0)
    hours  = market.get("hours_to_end", 0)
    vol    = market.get("volume_24h",   0)
    pens   = market.get("penalties",   {})
    score  = market.get("score",        0)

    issues: list[str] = []

    if price < 0.87:
        issues.append(f"Price must rise above 0.87 (now {price:.3f})")
    elif price > 0.95:
        issues.append(f"Price must fall below 0.95 (now {price:.3f})")

    if liq < 10_000:
        issues.append(f"Liquidity must reach $10K+ (now ${liq:,.0f})")
    elif liq < 30_000:
        issues.append(f"Liquidity is thin (${liq:,.0f}) — ideally above $30K")

    if spread > 0.015:
        issues.append(f"Spread must tighten to ≤0.015 (now {spread:.3f})")
    elif spread > 0.010:
        issues.append(f"Spread is wide ({spread:.3f}) — ideally below 0.010")

    if hours is not None:
        if hours > 24:
            issues.append(f"Event is {hours:.0f}h away — monitor as it enters the 24h window")
        elif 0 < hours < 1.5:
            issues.append(f"Only {hours:.1f}h to close — too little time to act")

    if vol < 3_000:
        issues.append(f"More depth needed (only ${vol:,.0f} in 24h volume)")

    if pens.get("ambiguity", 0) >= 0.15:
        issues.append("Ambiguity too high — question language is unclear")

    if score < 55 and not issues:
        issues.append("Multiple marginal signals keep score below the 55 threshold")

    return issues or ["No critical issues — monitor for changes"]


# ---------------------------------------------------------------------------
# Rejection category
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = [
    "Price Out of Range",
    "Low Liquidity",
    "Wide Spread",
    "Poor Timing",
    "Insufficient Depth",
    "Ambiguity",
    "Low Score",
    "Other",
]


def rejection_category(market: dict) -> str:
    expl   = (market.get("explanation", "") + " " + market.get("reject_reason", "")).lower()
    price  = market.get("yes_price",    0) or 0
    liq    = market.get("liquidity",    0) or 0
    spread = market.get("spread",       0) or 0
    hours  = market.get("hours_to_end", 0) or 0
    pens   = market.get("penalties",   {})

    if any(x in expl for x in ["price is", "outside the target", "too high", "too low", "price must", "close to valid"]):
        return "Price Out of Range"
    if "liquidity" in expl and any(x in expl for x in ["below", "thin", "minimum", "must reach", "must increase"]):
        return "Low Liquidity"
    if "spread" in expl and any(x in expl for x in ["wide", "above", "too wide", "must tighten"]):
        return "Wide Spread"
    if any(x in expl for x in ["24h window", "outside 24h", "already ended", "no closing", "too little time"]):
        return "Poor Timing"
    if any(x in expl for x in ["volume", "depth", "activity", "24h volume"]):
        return "Insufficient Depth"
    if "ambigui" in expl or pens.get("ambiguity", 0) >= 0.20:
        return "Ambiguity"
    if "score" in expl and "below" in expl:
        return "Low Score"

    if price and (price < 0.87 or price > 0.95):
        return "Price Out of Range"
    if liq and liq < 10_000:
        return "Low Liquidity"
    if spread and spread > 0.015:
        return "Wide Spread"
    if hours and hours > 24:
        return "Poor Timing"
    return "Other"


def category_order() -> list[str]:
    return list(_CATEGORY_ORDER)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def fmt_usd(n: float | None) -> str:
    n = n or 0
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:,.0f}K"
    return f"${n:,.0f}"


def fmt_hours(h: float | None) -> str:
    h = h or 0
    if h <= 0:
        return "—"
    if h < 1:
        return f"{int(h * 60)}m"
    return f"{h:.1f}h"


def fmt_score(s: float | None) -> str:
    return f"{s:.0f}" if s is not None else "—"
