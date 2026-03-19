"""
Polymarket Trading Bot — Premium Decision Dashboard  (v3)
Run with: streamlit run app/dashboard.py
"""
from __future__ import annotations
import io
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import app.config as _cfg
import app.strategy as _strat
from app.scanner import fetch_live_markets, hours_until_end
from app.strategy import run_pipeline, score_market
from app.simulation import simulate_portfolio, STARTING_BALANCE
from app.auto_paper import (
    get_or_create_session, get_active_session, reset_session,
    run_auto_paper_entries, settle_paper_trades,
    get_live_trades, get_resolved_trades, get_all_auto_trades,
    compute_bankroll_metrics, generate_bankroll_summary,
    STARTING_BALANCE as _AP_STARTING_BALANCE,
    DEFAULT_MODE as _AP_DEFAULT_MODE,
    DEFAULT_LIMIT as _AP_DEFAULT_LIMIT,
)
from app.training import compute_training_analytics, generate_what_winners_look_like
from app.validation import (
    start_test, stop_test, get_active_run, get_all_runs,
    run_validation_cycle, should_run_cycle, is_test_expired, hours_remaining,
    compute_metrics, get_permutation_stats, generate_conclusion,
    DEFAULT_CONFIG as _VAL_DEFAULT,
)
from app.validation_report import export_csvs
from app.database import (
    init_db, upsert_markets, log_scan,
    get_all_markets, get_last_scan_info, toggle_watchlist,
)
from app.labels import (
    action_label, action_color, confidence_from_tier, confidence_color,
    confidence_badge_class, tier_color, decision_summary, why_ranked_here,
    what_needs_to_improve, rejection_category, category_order, signal_label,
    penalty_name, penalty_tip, strength_tags, risk_tags,
    fmt_usd, fmt_hours, fmt_score,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polymarket Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Base */
[data-testid="stAppViewContainer"] {
    background: linear-gradient(160deg,#0c0e16 0%,#13162a 60%,#0c0e16 100%);
}
[data-testid="stSidebar"] {
    background:#10121e;
    border-right:1px solid #1e2235;
}
h1,h2,h3,h4 { color:#e8ecf4; font-weight:700; letter-spacing:-.3px; }
p,li { color:#9aa3b8; }
hr { border-color:#1e2235; margin:12px 0; }

/* Metric card */
.metric-card {
    background:#161929;
    border:1px solid #1e2235;
    border-radius:10px;
    padding:16px 20px;
    height:100%;
}
.metric-label { font-size:11px; font-weight:600; letter-spacing:.8px;
                text-transform:uppercase; color:#5a6380; margin-bottom:4px; }
.metric-value { font-size:26px; font-weight:800; color:#e8ecf4; line-height:1.1; }
.metric-sub   { font-size:12px; color:#5a6380; margin-top:2px; }

/* Section card */
.section-card {
    background:#161929;
    border:1px solid #1e2235;
    border-radius:12px;
    padding:22px 26px;
    margin-bottom:18px;
}

/* Tier left-border */
.tier-A { border-left:3px solid #f0b429; }
.tier-B { border-left:3px solid #7c6af6; }
.tier-C { border-left:3px solid #3a3f58; }

/* Badge */
.badge {
    display:inline-flex; align-items:center; gap:4px;
    padding:2px 9px; border-radius:20px;
    font-size:11px; font-weight:700; letter-spacing:.4px;
}
.badge-A    { background:#f0b42922; color:#f0b429; border:1px solid #f0b42944; }
.badge-B    { background:#7c6af622; color:#a99df5; border:1px solid #7c6af644; }
.badge-C    { background:#3a3f5822; color:#6a708a; border:1px solid #3a3f5844; }
.badge-high { background:#22c55e22; color:#22c55e; border:1px solid #22c55e44; }
.badge-med  { background:#f59e0b22; color:#f59e0b; border:1px solid #f59e0b44; }
.badge-low  { background:#ef444422; color:#ef4444; border:1px solid #ef444444; }
.badge-act-strong { background:#22c55e22; color:#22c55e; border:1px solid #22c55e44; }
.badge-act-watch  { background:#f59e0b22; color:#f59e0b; border:1px solid #f59e0b44; }
.badge-act-lower  { background:#4a5260;   color:#9aa3b8; border:1px solid #5a6080; }
.badge-act-avoid  { background:#ef444418; color:#ef4444; border:1px solid #ef444440; }
.badge-live   { background:#22c55e22; color:#22c55e; border:1px solid #22c55e44; }
.badge-sample { background:#f59e0b22; color:#f59e0b; border:1px solid #f59e0b44; }

/* Score bar */
.bar-wrap { background:#1e2235; border-radius:4px; height:5px; width:100%; }
.bar-fill  { height:5px; border-radius:4px; }

/* Detail panel */
.detail-header {
    background:#1a1d2e; border:1px solid #252838;
    border-radius:10px; padding:20px 24px; margin-bottom:14px;
}
.detail-section {
    background:#161929; border:1px solid #1e2235;
    border-radius:8px; padding:16px 20px; margin-bottom:10px;
}
.detail-section-title {
    font-size:11px; font-weight:700; letter-spacing:.8px;
    text-transform:uppercase; color:#5a6380; margin-bottom:12px;
}
.comp-row {
    display:flex; align-items:center; gap:10px; margin-bottom:9px;
}
.comp-name { width:130px; font-size:13px; color:#7a8299; }
.comp-score { width:32px; text-align:right; font-size:13px;
              color:#c8cfdf; font-weight:600; }
.comp-label { font-size:12px; color:#5a6380; }

/* Improve item */
.improve-item {
    padding:6px 10px; background:#1e2235; border-radius:6px;
    font-size:13px; color:#9aa3b8; margin-bottom:5px;
    border-left:3px solid #f59e0b;
}

/* Sidebar filters label */
[data-testid="stSlider"] { padding-bottom:4px; }
div.stButton > button {
    border-radius:7px; font-weight:600; border:none;
}
div.stButton > button:first-child {
    background:linear-gradient(90deg,#4060f0,#6080f8);
    color:#fff; width:100%;
}

/* Confidence badge classes */
.conf-vh { background:#10b98122; color:#10b981; border:1px solid #10b98144; }
.conf-h  { background:#22c55e22; color:#22c55e; border:1px solid #22c55e44; }
.conf-m  { background:#f59e0b22; color:#f59e0b; border:1px solid #f59e0b44; }
.conf-l  { background:#ef444422; color:#ef4444; border:1px solid #ef444444; }
.conf-n  { background:#3a3f5822; color:#5a6380; border:1px solid #3a3f5844; }

/* Strength / risk tags */
.tag-strength {
    display:inline-block; padding:2px 8px; border-radius:20px;
    font-size:10px; font-weight:700; letter-spacing:.3px;
    background:#22c55e18; color:#22c55e; border:1px solid #22c55e33;
    margin-right:4px; margin-top:4px;
}
.tag-risk {
    display:inline-block; padding:2px 8px; border-radius:20px;
    font-size:10px; font-weight:700; letter-spacing:.3px;
    background:#ef444418; color:#ef4444; border:1px solid #ef444433;
    margin-right:4px; margin-top:4px;
}

/* Accepted card glow */
.card-accepted-a {
    box-shadow:0 0 0 1px #f0b42966, 0 4px 24px #f0b42918;
}
.card-accepted-b {
    box-shadow:0 0 0 1px #7c6af666, 0 4px 20px #7c6af618;
}
</style>
""", unsafe_allow_html=True)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _load_markets() -> list[dict]:
    """Load all markets from DB, compute hours_to_end, add display fields."""
    rows = get_all_markets()
    for m in rows:
        h = hours_until_end(m)
        m["hours_to_end"]  = round(h, 1) if h is not None else 0.0
        m["confidence"]    = confidence_from_tier(
            m.get("tier", "C"),
            m.get("score", 0) or 0,
            bool(m.get("accepted")),
        )
        m["action"]        = action_label(m)
        m["reject_reason"] = m.get("reject_reason") or ""
    return rows


def _apply_filters(markets: list[dict], f: dict) -> list[dict]:
    out = markets
    if f.get("accepted_only"):
        out = [m for m in out if m.get("accepted")]
    if f.get("min_score", 0) > 0:
        out = [m for m in out if (m.get("score") or 0) >= f["min_score"]]
    if f.get("tiers"):
        out = [m for m in out if m.get("tier") in f["tiers"]]
    lo, hi = f.get("price_range", (0.80, 1.0))
    out = [m for m in out if lo <= (m.get("yes_price") or 0) <= hi]
    if f.get("max_hours"):
        out = [m for m in out if 0 < (m.get("hours_to_end") or 0) <= f["max_hours"]]
    if f.get("min_liquidity", 0) > 0:
        out = [m for m in out if (m.get("liquidity") or 0) >= f["min_liquidity"]]
    if f.get("max_spread", 1.0) < 1.0:
        out = [m for m in out if (m.get("spread") or 1) <= f["max_spread"]]
    if f.get("keyword"):
        kw = f["keyword"].strip().lower()
        out = [m for m in out if kw in (m.get("question") or "").lower()]
    return out


def _compute_stats(all_m: list[dict]) -> dict:
    accepted  = [m for m in all_m if m.get("accepted")]
    rejected  = [m for m in all_m if not m.get("accepted")]
    watchlist = [m for m in all_m if m.get("in_watchlist")]
    scores    = sorted([m.get("score") or 0 for m in accepted], reverse=True)
    return {
        "n_scanned":   len(all_m),
        "n_valid":     len(accepted),
        "n_watchlist": len(watchlist),
        "n_rejected":  len(rejected),
        "best_score":  scores[0] if scores else 0,
        "avg_top10":   sum(scores[:10]) / max(len(scores[:10]), 1),
        "avg_liq":     sum(m.get("liquidity") or 0 for m in accepted) / max(len(accepted), 1),
        "avg_spread":  sum(m.get("spread") or 0 for m in accepted) / max(len(accepted), 1),
        "avg_hours":   sum(m.get("hours_to_end") or 0 for m in accepted) / max(len(accepted), 1),
        "tier_a":      sum(1 for m in accepted if m.get("tier") == "A"),
        "tier_b":      sum(1 for m in accepted if m.get("tier") == "B"),
        "tier_c":      sum(1 for m in accepted if m.get("tier") == "C"),
    }


def _do_scan(use_real: bool) -> tuple[list[dict], str]:
    import app.scanner as _s
    _s.USE_REAL_DATA = use_real
    raw, source   = fetch_live_markets()
    processed     = run_pipeline(raw)
    upsert_markets(processed)
    accepted = sum(1 for m in processed if m.get("accepted"))
    log_scan(len(processed), accepted, source)
    return processed, source


def _plotly_dark() -> dict:
    return dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#161929",
        font=dict(color="#9aa3b8", size=12),
        margin=dict(l=10, r=10, t=36, b=10),
        xaxis=dict(gridcolor="#1e2235", zerolinecolor="#1e2235"),
        yaxis=dict(gridcolor="#1e2235", zerolinecolor="#1e2235"),
    )


# ── Render: metric cards ──────────────────────────────────────────────────────

def _metric(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="metric-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="metric-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


def render_stats(stats: dict) -> None:
    cols = st.columns(6)
    cards = [
        ("Markets Scanned",   str(stats["n_scanned"]),     "from last scan"),
        ("Valid Opportunities", str(stats["n_valid"]),     "pass all filters"),
        ("On Watchlist",       str(stats["n_watchlist"]),  "manually added"),
        ("Rejected",          str(stats["n_rejected"]),    "filtered out"),
        ("Best Score",        fmt_score(stats["best_score"]), "out of 100"),
        ("Avg Score (Top 10)", fmt_score(stats["avg_top10"]), "top-10 accepted"),
    ]
    for col, (lbl, val, sub) in zip(cols, cards):
        col.markdown(_metric(lbl, val, sub), unsafe_allow_html=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    cols2 = st.columns(6)
    cards2 = [
        ("Avg Liquidity",       fmt_usd(stats["avg_liq"]),           "accepted mkts"),
        ("Avg Spread",          f'{stats["avg_spread"]:.3f}',         "accepted mkts"),
        ("Avg Hours to Close",  fmt_hours(stats["avg_hours"]),        "accepted mkts"),
        ("Tier A  🥇",          str(stats["tier_a"]),                 "top picks"),
        ("Tier B  🥈",          str(stats["tier_b"]),                 "watch closely"),
        ("Tier C  🥉",          str(stats["tier_c"]),                 "lower quality"),
    ]
    for col, (lbl, val, sub) in zip(cols2, cards2):
        col.markdown(_metric(lbl, val, sub), unsafe_allow_html=True)

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)


# ── Render: opportunities table ───────────────────────────────────────────────

def _build_table_df(markets: list[dict]) -> pd.DataFrame:
    rows = []
    for i, m in enumerate(markets, 1):
        q   = m.get("question") or ""
        act = action_label(m)
        conf = confidence_from_tier(
            m.get("tier", "C"),
            m.get("score", 0) or 0,
            bool(m.get("accepted")),
        )
        rows.append({
            "#":          i,
            "Market Question": q[:75] + ("…" if len(q) > 75 else ""),
            "Side":       "YES",
            "Price":      round(m.get("yes_price") or 0, 3),
            "Score":      m.get("score") or 0,
            "Tier":       m.get("tier") or "—",
            "Hours":      m.get("hours_to_end") or 0,
            "Liquidity":  int(m.get("liquidity") or 0),
            "Spread":     round(m.get("spread") or 0, 3),
            "Confidence": conf,
            "Action":     act,
        })
    return pd.DataFrame(rows)


def render_opportunities_table(markets: list[dict]) -> int | None:
    """
    Render the main opportunities table. Returns index of selected market or None.
    """
    if not markets:
        st.markdown("""
        <div class="section-card" style="text-align:center;padding:48px">
            <div style="font-size:40px;margin-bottom:10px">🔍</div>
            <div style="font-size:17px;font-weight:600;color:#dde3f0">No markets found</div>
            <div style="font-size:13px;color:#5a6380;margin-top:6px">
                Click <b>Scan Markets</b> in the sidebar or adjust your filters.
            </div>
        </div>
        """, unsafe_allow_html=True)
        return None

    df = _build_table_df(markets)

    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "#": st.column_config.NumberColumn("#", width="small"),
            "Market Question": st.column_config.TextColumn("Market Question", width="large"),
            "Side": st.column_config.TextColumn("Side", width="small"),
            "Price": st.column_config.NumberColumn("Price", format="%.3f", width="small"),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d", width="small"
            ),
            "Tier": st.column_config.TextColumn("Tier", width="small"),
            "Hours": st.column_config.NumberColumn("Hours", format="%.1f", width="small"),
            "Liquidity": st.column_config.NumberColumn(
                "Liquidity", format="$%d", width="medium"
            ),
            "Spread": st.column_config.NumberColumn("Spread", format="%.3f", width="small"),
            "Confidence": st.column_config.TextColumn("Confidence", width="small"),
            "Action": st.column_config.TextColumn("Action", width="medium"),
        },
    )

    selected = event.selection.rows if hasattr(event, "selection") else []
    return selected[0] if selected else None


# ── Render: detail panel ──────────────────────────────────────────────────────

def _score_bar(score: float, color: str) -> str:
    w = min(max(int(score), 0), 100)
    return (
        f'<div class="bar-wrap">'
        f'<div class="bar-fill" style="width:{w}%;background:{color}"></div>'
        f'</div>'
    )


def _score_gauge(score: float, color: str, size: int = 52) -> str:
    """Compact SVG arc gauge showing score/100."""
    r = (size - 6) / 2
    cx = size / 2
    cy = size / 2
    circumference = 3.14159 * r  # half circle arc (180°)
    pct = min(max(score, 0), 100) / 100
    dash = pct * circumference
    gap  = circumference - dash
    return (
        f'<svg width="{size}" height="{size // 2 + 4}" viewBox="0 0 {size} {size // 2 + 4}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block">'
        f'<path d="M 3,{cy} A {r},{r} 0 0 1 {size-3},{cy}" '
        f'fill="none" stroke="#1e2235" stroke-width="5" stroke-linecap="round"/>'
        f'<path d="M 3,{cy} A {r},{r} 0 0 1 {size-3},{cy}" '
        f'fill="none" stroke="{color}" stroke-width="5" stroke-linecap="round" '
        f'stroke-dasharray="{dash:.1f} {gap + circumference:.1f}"/>'
        f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" '
        f'font-size="11" font-weight="700" fill="{color}">{score:.0f}</text>'
        f'</svg>'
    )


def render_detail_panel(market: dict) -> None:
    # Re-score to get fresh components and penalties
    enriched = score_market(market)
    comps    = enriched.get("components", {})
    pens     = enriched.get("penalties",  {})
    score    = enriched.get("score",      market.get("score", 0))
    tier     = market.get("tier", "C")
    accepted = bool(market.get("accepted"))
    conf     = confidence_from_tier(tier, score, accepted)
    conf_cls = confidence_badge_class(conf)
    act      = action_label(market)
    act_col  = action_color(act)
    t_col    = tier_color(tier)
    question = market.get("question", "")
    enriched_market = {**market, "components": comps, "penalties": pens, "hours_to_end": enriched.get("hours_to_end", market.get("hours_to_end", 0))}
    s_tags   = strength_tags(enriched_market)
    r_tags   = risk_tags(enriched_market)

    st.markdown("---")
    st.markdown("#### 🔍 Market Detail")

    # A) Header
    stag_html = "".join(f'<span class="tag-strength">{t}</span>' for t in s_tags)
    rtag_html = "".join(f'<span class="tag-risk">{t}</span>'     for t in r_tags)
    gauge_html = _score_gauge(score, t_col, size=60)
    st.markdown(
        f'<div class="detail-header tier-{tier}">'
        f'<div style="font-size:16px;font-weight:700;color:#e8ecf4;margin-bottom:12px">{question}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
        f'<span class="badge badge-{tier}">Tier {tier}</span>'
        f'<span class="badge {conf_cls}">{conf} Confidence</span>'
        f'<span style="display:inline-flex;align-items:center;padding:2px 9px;border-radius:20px;'
        f'font-size:11px;font-weight:700;background:{act_col}22;color:{act_col};border:1px solid {act_col}44">{act}</span>'
        f'<span style="margin-left:auto">{gauge_html}</span>'
        f'</div>'
        f'<div style="margin-top:8px">{stag_html}{rtag_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # B) Quick metrics
    price  = market.get("yes_price",    0) or 0
    liq    = market.get("liquidity",    0) or 0
    spread = market.get("spread",       0) or 0
    hours  = enriched.get("hours_to_end", market.get("hours_to_end", 0)) or 0
    bid    = market.get("best_bid",     0) or 0
    ask    = market.get("best_ask",     0) or 0

    mc = st.columns(6)
    mc[0].metric("Price",         f"{price:.3f}")
    mc[1].metric("Liquidity",     fmt_usd(liq))
    mc[2].metric("Spread",        f"{spread:.3f}")
    mc[3].metric("Hours to Close", fmt_hours(hours))
    mc[4].metric("Best Bid",      f"{bid:.3f}")
    mc[5].metric("Best Ask",      f"{ask:.3f}")

    # C+D) Score breakdown + Penalties in two columns
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown('<div class="detail-section">', unsafe_allow_html=True)
        st.markdown('<div class="detail-section-title">Score Breakdown</div>', unsafe_allow_html=True)
        comp_order = ["liquidity", "spread", "objectivity", "time", "price_band", "depth", "stability"]
        comp_display = {
            "liquidity": "Liquidity", "spread": "Spread", "objectivity": "Clarity",
            "time": "Timing", "price_band": "Price Band", "depth": "Market Depth",
            "stability": "Stability",
        }
        for key in comp_order:
            val = comps.get(key, 0)
            lbl = signal_label(key, val)
            bar_color = "#22c55e" if val >= 70 else "#f59e0b" if val >= 45 else "#ef4444"
            st.markdown(
                f'<div class="comp-row">'
                f'<span class="comp-name">{comp_display.get(key, key)}</span>'
                f'<div style="flex:1">{_score_bar(val, bar_color)}</div>'
                f'<span class="comp-score">{val:.0f}</span>'
                f'<span class="comp-label">{lbl}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with col_r:
        st.markdown('<div class="detail-section">', unsafe_allow_html=True)
        st.markdown('<div class="detail-section-title">Penalties Applied</div>', unsafe_allow_html=True)
        has_pen = False
        for key, val in pens.items():
            if val > 0.005:
                has_pen = True
                pct = val * 100
                tip = penalty_tip(key)
                st.markdown(
                    f'<div style="margin-bottom:10px">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
                    f'<span style="font-size:13px;color:#9aa3b8">{penalty_name(key)}</span>'
                    f'<span style="font-size:13px;font-weight:700;color:#ef4444">−{pct:.0f}%</span>'
                    f'</div>'
                    f'<div style="font-size:11px;color:#5a6380">{tip}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        if not has_pen:
            st.markdown('<div style="color:#5a6380;font-size:13px">No significant penalties.</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # E) Final decision section
    summary = decision_summary({**market, "components": comps, "penalties": pens})
    ranked  = why_ranked_here({**market, "components": comps, "penalties": pens, "score": score})

    st.markdown('<div class="detail-section">', unsafe_allow_html=True)
    st.markdown('<div class="detail-section-title">Decision</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:14px;color:#dde3f0;font-weight:600;margin-bottom:8px">{summary}</div>',
        unsafe_allow_html=True,
    )
    if ranked:
        st.markdown(
            f'<div style="font-size:13px;color:#7a8299;line-height:1.6">{ranked}</div>',
            unsafe_allow_html=True,
        )

    # Watchlist button
    st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)
    on_wl  = bool(market.get("in_watchlist"))
    btn_lbl = "★ Remove from Watchlist" if on_wl else "☆ Add to Watchlist"
    col_btn, _ = st.columns([1, 4])
    with col_btn:
        if st.button(btn_lbl, key=f"det_wl_{market.get('id')}"):
            toggle_watchlist(market["id"])
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# ── Render: compact opportunity card (used on Overview) ───────────────────────

def render_compact_card(m: dict, rank: int) -> None:
    tier     = m.get("tier", "C")
    score    = m.get("score", 0) or 0
    accepted = bool(m.get("accepted"))
    t_col    = tier_color(tier)
    act      = action_label(m)
    act_c    = action_color(act)
    q        = m.get("question", "")
    summ     = decision_summary(m)
    s_tags   = strength_tags(m)
    r_tags   = risk_tags(m)
    gauge    = _score_gauge(score, t_col, size=50)

    # Extra glow for accepted Tier A and B cards
    glow_cls = ""
    if accepted and tier == "A":
        glow_cls = "card-accepted-a"
    elif accepted and tier == "B":
        glow_cls = "card-accepted-b"

    stag_html = "".join(f'<span class="tag-strength">{t}</span>' for t in s_tags)
    rtag_html = "".join(f'<span class="tag-risk">{t}</span>'     for t in r_tags)
    tags_html = (stag_html + rtag_html) or ""

    st.markdown(
        f'<div class="section-card tier-{tier} {glow_cls}" style="padding:14px 18px;margin-bottom:10px">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'  <div style="flex:1;padding-right:12px">'
        f'    <div style="font-size:13px;font-weight:600;color:#dde3f0;margin-bottom:4px">'
        f'      #{rank} {q[:80]}{"…" if len(q)>80 else ""}'
        f'    </div>'
        f'    <div style="font-size:12px;color:#6a7390">{summ}</div>'
        f'    {"<div style=margin-top:6px>" + tags_html + "</div>" if tags_html else ""}'
        f'  </div>'
        f'  <div style="text-align:center;min-width:56px">'
        f'    {gauge}'
        f'  </div>'
        f'</div>'
        f'<div style="display:flex;gap:16px;margin-top:8px;flex-wrap:wrap">'
        f'  <span style="font-size:12px;color:#5a6380">💵 {(m.get("yes_price") or 0):.3f}</span>'
        f'  <span style="font-size:12px;color:#5a6380">⏱ {fmt_hours(m.get("hours_to_end"))}</span>'
        f'  <span style="font-size:12px;color:#5a6380">💧 {fmt_usd(m.get("liquidity"))}</span>'
        f'  <span style="font-size:12px;color:#5a6380">↔ {(m.get("spread") or 0):.3f}</span>'
        f'  <span style="margin-left:auto;font-size:11px;font-weight:700;color:{act_c}">{act}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── CSV export helper ─────────────────────────────────────────────────────────

def _to_csv(markets: list[dict]) -> str:
    cols = ["question", "yes_price", "score", "tier", "hours_to_end",
            "liquidity", "spread", "best_bid", "best_ask", "volume_24h",
            "accepted", "in_watchlist", "explanation"]
    rows = [{c: m.get(c, "") for c in cols} for m in markets]
    buf  = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue()


# ── Spotlight helpers ─────────────────────────────────────────────────────────

def _spotlight_card(icon: str, label: str, headline: str, sub: str, color: str = "#4060f0") -> str:
    return (
        f'<div class="metric-card" style="border-left:3px solid {color}">'
        f'<div style="font-size:18px;margin-bottom:6px">{icon}</div>'
        f'<div class="metric-label" style="margin-bottom:4px">{label}</div>'
        f'<div style="font-size:13px;font-weight:700;color:#dde3f0;line-height:1.4">{headline}</div>'
        f'<div style="font-size:11px;color:#5a6380;margin-top:4px">{sub}</div>'
        f'</div>'
    )


def _render_spotlight_cards(
    accepted: list[dict],
    rejected: list[dict],
    watchlist: list[dict],
) -> None:
    """4 spotlight insight cards shown at the top of the Overview page."""
    cols = st.columns(4)

    # 1 — Best setup right now
    with cols[0]:
        if accepted:
            best = accepted[0]
            t    = best.get("tier", "C")
            col  = tier_color(t)
            q    = (best.get("question") or "")[:55]
            sub  = f"Score {fmt_score(best.get('score'))} · Tier {t} · {fmt_hours(best.get('hours_to_end'))}"
            st.markdown(
                _spotlight_card("🥇", "Best Setup Right Now", q + ("…" if len(best.get("question",""))>55 else ""), sub, col),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_spotlight_card("🥇", "Best Setup Right Now", "No accepted markets yet", "Run a scan to populate", "#4a4e6a"), unsafe_allow_html=True)

    # 2 — Most common rejection problem
    with cols[1]:
        if rejected:
            cats: dict[str, int] = {}
            for m in rejected:
                c = rejection_category(m)
                cats[c] = cats.get(c, 0) + 1
            top_cat = max(cats, key=lambda c: cats[c])
            pct     = cats[top_cat] / len(rejected) * 100
            st.markdown(
                _spotlight_card("❌", "Most Common Rejection", top_cat, f"{cats[top_cat]} markets ({pct:.0f}% of rejected)", "#ef4444"),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_spotlight_card("❌", "Most Common Rejection", "No rejected markets yet", "Run a scan to populate", "#4a4e6a"), unsafe_allow_html=True)

    # 3 — Most fragile accepted setup
    with cols[2]:
        fragile = sorted(
            [m for m in accepted if (m.get("score") or 0) < 70],
            key=lambda m: m.get("score") or 0,
        )
        if fragile:
            m   = fragile[0]
            q   = (m.get("question") or "")[:55]
            sub = f"Score {fmt_score(m.get('score'))} · {fmt_hours(m.get('hours_to_end'))} left"
            st.markdown(
                _spotlight_card("⚠️", "Most Fragile Accepted", q + ("…" if len(m.get("question",""))>55 else ""), sub, "#f59e0b"),
                unsafe_allow_html=True,
            )
        elif accepted:
            st.markdown(_spotlight_card("⚠️", "Most Fragile Accepted", "All accepted markets are solid", "No fragile setups", "#22c55e"), unsafe_allow_html=True)
        else:
            st.markdown(_spotlight_card("⚠️", "Most Fragile Accepted", "No accepted markets yet", "Run a scan to populate", "#4a4e6a"), unsafe_allow_html=True)

    # 4 — Best watchlist candidate
    with cols[3]:
        candidates = sorted(
            [m for m in watchlist if not m.get("accepted")],
            key=lambda m: -(m.get("score") or 0),
        )
        if not candidates:
            # Fall back to near-misses (score >= 35, not accepted, not in watchlist)
            candidates = sorted(
                [m for m in [] ],  # placeholder — near-misses computed in page_watchlist
                key=lambda m: -(m.get("score") or 0),
            )
        if candidates:
            m   = candidates[0]
            q   = (m.get("question") or "")[:55]
            sub = f"Score {fmt_score(m.get('score'))} · ☆ Watchlisted"
            st.markdown(
                _spotlight_card("👀", "Best Watchlist Candidate", q + ("…" if len(m.get("question",""))>55 else ""), sub, "#7c6af6"),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_spotlight_card("👀", "Best Watchlist Candidate", "Watchlist is empty", "Add markets from Top Opportunities", "#4a4e6a"), unsafe_allow_html=True)


# ── Permutation coverage helper ───────────────────────────────────────────────

def _render_permutation_coverage(all_markets: list) -> None:
    primary   = sum(1 for m in all_markets if m.get("band") == "primary")
    secondary = sum(1 for m in all_markets if m.get("band") == "secondary")
    watchlist = sum(1 for m in all_markets if m.get("band") == "watchlist")
    rejected  = sum(1 for m in all_markets if m.get("band") not in ("primary", "secondary", "watchlist") or not m.get("band"))
    total = len(all_markets)

    st.markdown("### 🎯 Permutation Coverage")
    cols = st.columns(5)
    card_data = [
        ("Total Scanned", str(total), "#4060f0"),
        ("Primary Band", str(primary), "#22c55e"),
        ("Secondary Band", str(secondary), "#7c6af6"),
        ("Watchlist Band", str(watchlist), "#f59e0b"),
        ("Outside All Bands", str(rejected), "#ef4444"),
    ]
    for col, (label, val, color) in zip(cols, card_data):
        col.markdown(
            f'<div class="metric-card" style="border-left:3px solid {color}">'
            f'<div class="metric-label">{label}</div>'
            f'<div class="metric-value" style="color:{color}">{val}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Navigation tile bar ───────────────────────────────────────────────────────

_NAV_PAGES = [
    ("📊", "Overview",          "📊 Overview",              "#3b82f6"),
    ("🏆", "Top Picks",         "🏆 Top Opportunities",     "#f59e0b"),
    ("👀", "Watchlist",         "👀 Watchlist",             "#8b5cf6"),
    ("❌", "Rejected",          "❌ Rejected Markets",      "#ef4444"),
    ("📋", "Permutation",       "📋 Permutation Report",    "#06b6d4"),
    ("💰", "Bankroll",          "💰 Bankroll Simulator",    "#10b981"),
    ("📍", "Live Positions",    "📍 Live Positions",        "#f97316"),
    ("🎓", "What Winners Look Like", "🎓 What Winners Look Like", "#a78bfa"),
    ("🧪", "48h Validation",    "🧪 48h Validation",        "#f472b6"),
]

def _set_nav(full_label: str) -> None:
    """on_click callback — runs before the next script execution."""
    st.session_state["nav"] = full_label


def _render_nav_tiles() -> None:
    """Render a horizontal row of navigation shortcut tiles."""
    st.markdown(
        '<div style="font-size:12px;color:#8892b0;margin-bottom:6px;'
        'font-weight:600;letter-spacing:0.06em;text-transform:uppercase">'
        'Quick Navigation</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(len(_NAV_PAGES))
    for col, (icon, short_label, full_label, color) in zip(cols, _NAV_PAGES):
        with col:
            st.markdown(
                f'<div style="background:#161929;border:1px solid {color}33;border-radius:10px;'
                f'padding:10px 4px;text-align:center">'
                f'<div style="font-size:20px">{icon}</div>'
                f'<div style="font-size:10px;color:{color};font-weight:600;margin-top:2px;'
                f'line-height:1.2">{short_label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.button(
                "Go",
                key=f"nav_tile_{short_label}",
                use_container_width=True,
                help=f"Go to {full_label}",
                on_click=_set_nav,
                args=(full_label,),
            )


# ── Page: Overview ────────────────────────────────────────────────────────────

def page_overview(all_markets: list, stats: dict) -> None:
    st.markdown("# 📊 Market Overview")
    st.markdown("A snapshot of current market quality — your at-a-glance command centre.")

    _render_nav_tiles()
    st.markdown("---")

    render_stats(stats)
    st.markdown("---")

    accepted  = [m for m in all_markets if m.get("accepted")]
    watchlist = [m for m in all_markets if m.get("in_watchlist")]
    rejected  = [m for m in all_markets if not m.get("accepted")]

    # ── Spotlight cards ──────────────────────────────────────────────────────
    _render_spotlight_cards(accepted, rejected, watchlist)
    st.markdown("---")

    # ── Permutation Coverage ─────────────────────────────────────────────────
    _render_permutation_coverage(all_markets)
    st.markdown("---")

    # Top 5 + Watchlist top 5
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("### 🏆 Top Opportunities")
        if accepted:
            for i, m in enumerate(accepted[:5], 1):
                render_compact_card(m, i)
        else:
            st.info("No accepted markets yet — run a scan.")
    with col_r:
        st.markdown("### 👀 Watchlist")
        if watchlist:
            for i, m in enumerate(watchlist[:5], 1):
                render_compact_card(m, i)
        else:
            st.markdown(
                '<div class="section-card" style="text-align:center;padding:32px;color:#5a6380">'
                '☆ Watchlist is empty</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # Charts row
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.markdown("#### Rejection Breakdown")
        if rejected:
            cats = {}
            for m in rejected:
                c = rejection_category(m)
                cats[c] = cats.get(c, 0) + 1
            order = [c for c in category_order() if c in cats]
            y_vals = [cats[c] for c in order]
            colors = ["#4060f0", "#7c6af6", "#f0b429", "#ef4444", "#22c55e", "#f59e0b", "#9aa3b8", "#5a6380"]
            fig = go.Figure(go.Bar(
                x=y_vals, y=order,
                orientation="h",
                marker_color=colors[:len(order)],
                text=y_vals, textposition="outside",
                textfont=dict(color="#9aa3b8", size=12),
            ))
            fig.update_layout(
                **_plotly_dark(),
                title=dict(text="Why markets were rejected", font=dict(color="#9aa3b8", size=13)),
                xaxis_title=None, yaxis_title=None,
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No rejected markets in the database yet.")

    with col_chart2:
        st.markdown("#### Score Distribution")
        all_scores = [m.get("score") or 0 for m in all_markets if (m.get("score") or 0) > 0]
        if all_scores:
            fig2 = go.Figure(go.Histogram(
                x=all_scores, nbinsx=20,
                marker_color="#4060f0", opacity=0.85,
            ))
            fig2.update_layout(
                **_plotly_dark(),
                title=dict(text="Distribution of final scores", font=dict(color="#9aa3b8", size=13)),
                xaxis_title=None, yaxis_title=None,
                height=300,
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No score data yet.")

    # Hours + Price distributions
    col_chart3, col_chart4 = st.columns(2)
    with col_chart3:
        st.markdown("#### Hours to Close (Accepted)")
        h_vals = [m.get("hours_to_end") or 0 for m in accepted if (m.get("hours_to_end") or 0) > 0]
        if h_vals:
            fig3 = go.Figure(go.Histogram(x=h_vals, nbinsx=12, marker_color="#7c6af6", opacity=0.85))
            fig3.update_layout(**_plotly_dark(), height=240, xaxis_title=None, yaxis_title=None)
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("No timing data.")

    with col_chart4:
        st.markdown("#### Price Band (All Scanned)")
        p_vals = [m.get("yes_price") or 0 for m in all_markets if (m.get("yes_price") or 0) > 0]
        if p_vals:
            fig4 = go.Figure(go.Histogram(x=p_vals, nbinsx=15, marker_color="#f0b429", opacity=0.85))
            fig4.update_layout(**_plotly_dark(), height=240, xaxis_title=None, yaxis_title=None)
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.info("No price data.")


# ── Page: Top Opportunities ───────────────────────────────────────────────────

def page_opportunities(all_markets: list[dict], stats: dict, filters: dict) -> None:
    st.markdown("# 🏆 Top Opportunities")
    st.markdown(
        "Markets that pass every filter, ranked by final score. "
        "Only **YES** prices between **0.87–0.95** closing within **24 hours**."
    )
    st.markdown("---")

    render_stats(stats)
    st.markdown("---")

    base = [m for m in all_markets if m.get("accepted")]
    if filters.get("show_watchlist_too"):
        wl_ids = {m["id"] for m in all_markets if m.get("in_watchlist")}
        extras = [m for m in all_markets if m.get("id") in wl_ids and not m.get("accepted")]
        base   = base + extras

    display = _apply_filters(base, filters)
    display.sort(key=lambda x: -(x.get("score") or 0))

    # Filter summary
    if any(filters.get(k) for k in ["min_score", "tiers", "keyword", "max_hours", "min_liquidity", "max_spread"]):
        active = []
        if filters.get("min_score", 0) > 0:
            active.append(f"Score ≥ {filters['min_score']}")
        if filters.get("tiers"):
            active.append("Tier: " + "/".join(filters["tiers"]))
        if filters.get("keyword"):
            active.append(f'"{filters["keyword"]}"')
        st.markdown(
            f'<div style="font-size:12px;color:#5a6380;margin-bottom:12px">'
            f'Filters active: {" · ".join(active)} — showing {len(display)} markets'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Export
    exp_col, _, _ = st.columns([1, 4, 1])
    with exp_col:
        st.download_button(
            "⬇ Export CSV", data=_to_csv(display),
            file_name="opportunities.csv", mime="text/csv",
        )

    # Table + detail
    selected_idx = render_opportunities_table(display)
    if selected_idx is not None and selected_idx < len(display):
        render_detail_panel(display[selected_idx])


# ── Page: Watchlist ───────────────────────────────────────────────────────────

def page_watchlist(all_markets: list[dict]) -> None:
    st.markdown("# 👀 Watchlist")
    st.markdown("Markets you've bookmarked, plus near-misses worth monitoring.")
    st.markdown("---")

    pinned   = [m for m in all_markets if m.get("in_watchlist")]
    rejected = [m for m in all_markets if not m.get("accepted") and not m.get("in_watchlist")]
    near     = [m for m in rejected if (m.get("score") or 0) >= 35]
    near.sort(key=lambda x: -(x.get("score") or 0))

    # Pinned
    st.markdown(f"### 📌 My Watchlist ({len(pinned)} markets)")
    if not pinned:
        st.markdown(
            '<div class="section-card" style="text-align:center;padding:36px;color:#5a6380">'
            '☆ Empty — add markets from the <b>Top Opportunities</b> page.</div>',
            unsafe_allow_html=True,
        )
    else:
        rows = []
        for m in pinned:
            issues = what_needs_to_improve(m)
            rows.append({
                "Market":            (m.get("question") or "")[:70] + "…",
                "Score":             m.get("score") or 0,
                "Action":            action_label(m),
                "What Needs to Improve": " · ".join(issues[:2]),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True, column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "Market": st.column_config.TextColumn("Market", width="large"),
            "What Needs to Improve": st.column_config.TextColumn("What Needs to Improve", width="large"),
        })

        for m in pinned:
            issues = what_needs_to_improve(m)
            with st.expander(f"{'✅' if m.get('accepted') else '⚠️'} {(m.get('question') or '')[:80]}"):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Score",    fmt_score(m.get("score")))
                c2.metric("Price",    f"{(m.get('yes_price') or 0):.3f}")
                c3.metric("Liq",      fmt_usd(m.get("liquidity")))
                c4.metric("Spread",   f"{(m.get('spread') or 0):.3f}")
                st.markdown("**What needs to improve:**")
                for issue in issues:
                    st.markdown(
                        f'<div class="improve-item">{issue}</div>',
                        unsafe_allow_html=True,
                    )
                btn_col, _ = st.columns([1, 5])
                with btn_col:
                    if st.button("★ Remove", key=f"wl_rm_{m.get('id')}"):
                        toggle_watchlist(m["id"])
                        st.rerun()

    # Export watchlist
    if pinned:
        st.download_button("⬇ Export Watchlist CSV", data=_to_csv(pinned),
                           file_name="watchlist.csv", mime="text/csv")

    st.markdown("---")

    # Near misses
    st.markdown(f"### 🔭 Near Misses — Markets Close to Qualifying ({len(near)})")
    if not near:
        st.info("No near-miss markets at the moment.")
    else:
        st.caption("These didn't pass all filters but are the closest to qualifying. Monitor them.")
        for m in near[:10]:
            issues = what_needs_to_improve(m)
            with st.expander(f"⚠️ Score {fmt_score(m.get('score'))} — {(m.get('question') or '')[:80]}"):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Score",   fmt_score(m.get("score")))
                c2.metric("Price",   f"{(m.get('yes_price') or 0):.3f}")
                c3.metric("Liq",     fmt_usd(m.get("liquidity")))
                c4.metric("Spread",  f"{(m.get('spread') or 0):.3f}")
                st.markdown("**What needs to improve:**")
                for issue in issues:
                    st.markdown(f'<div class="improve-item">{issue}</div>', unsafe_allow_html=True)
                btn_col, _ = st.columns([1, 5])
                with btn_col:
                    if st.button("☆ Add to Watchlist", key=f"wl_add_{m.get('id')}"):
                        toggle_watchlist(m["id"])
                        st.rerun()


# ── Page: Rejected Markets ────────────────────────────────────────────────────

def page_rejected(all_markets: list[dict]) -> None:
    st.markdown("# ❌ Rejected Markets")
    st.markdown("Every market scanned but not accepted, grouped by the primary reason.")
    st.markdown("---")

    rejected = [m for m in all_markets if not m.get("accepted")]
    if not rejected:
        st.markdown(
            '<div class="section-card" style="text-align:center;padding:48px">'
            '<div style="font-size:36px;margin-bottom:8px">✅</div>'
            '<div style="font-size:16px;color:#dde3f0;font-weight:600">No rejected markets</div>'
            '<div style="font-size:13px;color:#5a6380;margin-top:6px">Run a scan to populate this page.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Category breakdown
    cats: dict[str, list[dict]] = {}
    for m in rejected:
        c = rejection_category(m)
        cats.setdefault(c, []).append(m)

    # Donut / bar chart
    cat_counts = {c: len(cats[c]) for c in cats}
    order = [c for c in category_order() if c in cat_counts]

    col_chart, col_legend = st.columns([2, 1])
    with col_chart:
        colors = ["#4060f0", "#ef4444", "#f0b429", "#f59e0b", "#7c6af6", "#22c55e", "#9aa3b8", "#5a6380"]
        fig = go.Figure(go.Pie(
            labels=[c for c in order],
            values=[cat_counts[c] for c in order],
            hole=0.55,
            marker_colors=colors[:len(order)],
            textinfo="label+percent",
            textfont=dict(color="#e8ecf4", size=12),
            insidetextorientation="radial",
        ))
        fig.update_layout(
            **_plotly_dark(),
            showlegend=False,
            height=300,
            title=dict(text="Rejection breakdown", font=dict(color="#9aa3b8", size=13)),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_legend:
        st.markdown("<br>", unsafe_allow_html=True)
        for c in order:
            n = cat_counts[c]
            pct = n / len(rejected) * 100
            st.markdown(
                f'<div style="margin-bottom:10px">'
                f'<div style="font-size:13px;color:#dde3f0;font-weight:600">{c}</div>'
                f'<div style="font-size:12px;color:#5a6380">{n} market{"s" if n!=1 else ""} ({pct:.0f}%)</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.download_button("⬇ Export Rejected CSV", data=_to_csv(rejected),
                       file_name="rejected.csv", mime="text/csv")

    st.markdown("---")

    # Grouped sections
    for cat in order:
        group = sorted(cats[cat], key=lambda x: -(x.get("score") or 0))
        with st.expander(f"**{cat}**  ·  {len(group)} market{'s' if len(group)!=1 else ''}", expanded=False):
            rows = []
            for m in group:
                q = m.get("question") or ""
                rows.append({
                    "Market": q[:80] + ("…" if len(q) > 80 else ""),
                    "Price":  round(m.get("yes_price") or 0, 3),
                    "Score":  m.get("score") or 0,
                    "Hours":  m.get("hours_to_end") or 0,
                    "Liq":    int(m.get("liquidity") or 0),
                    "Reason": decision_summary(m),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True, column_config={
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                "Market": st.column_config.TextColumn("Market", width="large"),
                "Price":  st.column_config.NumberColumn("Price", format="%.3f"),
                "Hours":  st.column_config.NumberColumn("Hours", format="%.1f"),
                "Liq":    st.column_config.NumberColumn("Liquidity", format="$%d"),
                "Reason": st.column_config.TextColumn("Why Rejected", width="large"),
            })


# ── Page: Permutation Report ──────────────────────────────────────────────────

def page_permutation_report(all_markets: list) -> None:
    st.markdown("# 📋 Permutation Report")
    st.markdown("Deep-dive into how markets are classified across trading bands.")
    st.markdown("---")

    # Section 1: Band Summary
    st.markdown("### Band Summary")
    primary   = sum(1 for m in all_markets if m.get("band") == "primary")
    secondary = sum(1 for m in all_markets if m.get("band") == "secondary")
    watchlist_band = sum(1 for m in all_markets if m.get("band") == "watchlist")
    capped    = sum(1 for m in all_markets if m.get("band") == "capped")
    rejected_band = sum(1 for m in all_markets if m.get("band") not in ("primary", "secondary", "watchlist", "capped") or not m.get("band"))
    total = len(all_markets)

    cols = st.columns(5)
    band_cards = [
        ("Total Scanned", str(total), "#4060f0"),
        ("Primary Band", str(primary), "#22c55e"),
        ("Secondary Band", str(secondary), "#7c6af6"),
        ("Watchlist Band", str(watchlist_band), "#f59e0b"),
        ("Outside All Bands", str(rejected_band + capped), "#ef4444"),
    ]
    for col, (label, val, color) in zip(cols, band_cards):
        col.markdown(
            f'<div class="metric-card" style="border-left:3px solid {color}">'
            f'<div class="metric-label">{label}</div>'
            f'<div class="metric-value" style="color:{color}">{val}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # Section 2: Rejection Breakdown
    st.markdown("### Rejection Breakdown")
    reason_counts = _strat.count_rejection_reasons(all_markets)
    n_rejected_total = sum(1 for m in all_markets if not m.get("accepted"))

    reason_labels = {
        "price": "Price Out of Range",
        "timing": "Timing",
        "liquidity": "Liquidity",
        "spread": "Spread",
        "depth": "Depth",
        "ambiguity": "Ambiguity",
        "score": "Score",
        "other": "Other",
    }
    colors_bar = ["#4060f0", "#f0b429", "#ef4444", "#f59e0b", "#7c6af6", "#22c55e", "#9aa3b8", "#5a6380"]

    reasons_ordered = ["price", "timing", "liquidity", "spread", "depth", "ambiguity", "score", "other"]
    y_labels = [reason_labels[r] for r in reasons_ordered]
    x_vals   = [reason_counts[r] for r in reasons_ordered]

    fig_rej = go.Figure(go.Bar(
        x=x_vals, y=y_labels,
        orientation="h",
        marker_color=colors_bar[:len(y_labels)],
        text=x_vals, textposition="outside",
        textfont=dict(color="#9aa3b8", size=12),
    ))
    fig_rej.update_layout(
        **_plotly_dark(),
        title=dict(text="Primary reason markets were not accepted", font=dict(color="#9aa3b8", size=13)),
        xaxis_title=None, yaxis_title=None,
        height=320,
    )
    st.plotly_chart(fig_rej, use_container_width=True)

    # Mini table
    table_rows = []
    for r in reasons_ordered:
        n = reason_counts[r]
        pct = round(n / n_rejected_total * 100, 1) if n_rejected_total > 0 else 0.0
        table_rows.append({"Reason": reason_labels[r], "Count": n, "% of Rejected": pct})
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    st.markdown("---")

    # Section 3: What-if Threshold Analysis
    st.markdown("### What-if Threshold Analysis")
    threshold_results = _strat.run_threshold_analysis(all_markets)

    if threshold_results:
        analysis_rows = []
        for r in threshold_results:
            analysis_rows.append({
                "Ruleset":              r["name"],
                "Description":         r["desc"],
                "Primary Candidates":  r["primary"],
                "Secondary Candidates": r["secondary"],
                "Total Accepted":      r["accepted"],
                "Watchlist":           r["watchlist"],
                "Avg Score":           r["avg_score"],
            })
        st.dataframe(
            pd.DataFrame(analysis_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Ruleset":              st.column_config.TextColumn("Ruleset", width="medium"),
                "Description":         st.column_config.TextColumn("Description", width="large"),
                "Primary Candidates":  st.column_config.NumberColumn("Primary", width="small"),
                "Secondary Candidates": st.column_config.NumberColumn("Secondary", width="small"),
                "Total Accepted":      st.column_config.NumberColumn("Total Accepted", width="small"),
                "Watchlist":           st.column_config.NumberColumn("Watchlist", width="small"),
                "Avg Score":           st.column_config.NumberColumn("Avg Score", format="%.1f", width="small"),
            },
        )

        # Bar chart comparing accepted counts
        ruleset_names  = [r["name"] for r in threshold_results]
        accepted_counts = [r["accepted"] for r in threshold_results]
        fig_thresh = go.Figure(go.Bar(
            x=ruleset_names,
            y=accepted_counts,
            marker_color=["#4060f0", "#22c55e", "#7c6af6"],
            text=accepted_counts, textposition="outside",
            textfont=dict(color="#9aa3b8", size=12),
        ))
        fig_thresh.update_layout(
            **_plotly_dark(),
            title=dict(text="Accepted markets per ruleset", font=dict(color="#9aa3b8", size=13)),
            xaxis_title=None, yaxis_title="Accepted Markets",
            height=280,
        )
        st.plotly_chart(fig_thresh, use_container_width=True)
        st.caption(
            "Looser rules = more candidates but lower average quality. "
            "Tighter rules = fewer but higher conviction."
        )
    else:
        st.info("No market data available for threshold analysis.")

    st.markdown("---")

    # Section 4: Where Rigidity Excludes Markets
    st.markdown("### Where Rigidity Excludes Markets")
    watchlist_markets = [m for m in all_markets if m.get("band") == "watchlist"]
    if watchlist_markets:
        st.caption(
            "These markets are close to qualifying. They may pass if market conditions improve."
        )
        wl_rows = []
        for m in watchlist_markets:
            q = m.get("question") or ""
            wl_rows.append({
                "Question":   q[:75] + ("…" if len(q) > 75 else ""),
                "Price":      round(m.get("yes_price") or 0, 3),
                "Hours":      round(m.get("hours_to_end") or 0, 1),
                "Liquidity":  int(m.get("liquidity") or 0),
                "Spread":     round(m.get("spread") or 0, 3),
                "Score":      m.get("score") or 0,
            })
        st.dataframe(
            pd.DataFrame(wl_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Question":  st.column_config.TextColumn("Question", width="large"),
                "Price":     st.column_config.NumberColumn("Price", format="%.3f"),
                "Hours":     st.column_config.NumberColumn("Hours", format="%.1f"),
                "Liquidity": st.column_config.NumberColumn("Liquidity", format="$%d"),
                "Spread":    st.column_config.NumberColumn("Spread", format="%.3f"),
                "Score":     st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            },
        )
    else:
        st.info("No markets are currently in the watchlist band.")


# ── Page: Bankroll Simulator ──────────────────────────────────────────────────

def _ap_metric(label: str, value: str, color: str = "#e8ecf4", sub: str = "") -> str:
    sub_html = f'<div style="font-size:11px;color:#5a6380;margin-top:2px">{sub}</div>' if sub else ""
    return (
        f'<div class="metric-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value" style="font-size:22px;color:{color}">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


def _run_ap_scan_cycle(session: dict, all_markets: list) -> str:
    """Run one auto-paper cycle: entry + settlement. Returns status message."""
    result  = run_auto_paper_entries(all_markets, session)
    settled = settle_paper_trades(all_markets, session["id"])
    return (
        f"✅ Entered {result['entered']} new position(s), "
        f"settled {settled} trade(s). "
        f"({result['candidates']} candidate(s) from {result['accepted']} accepted markets)"
    )


def page_bankroll_simulator(all_markets: list) -> None:
    st.markdown("# 💰 Bankroll Simulator")
    st.markdown(
        "Automatically takes paper positions in top opportunities at each scan. "
        "Tracks three parallel sizing models (2%, 3%, 5%) on identical entries.  \n"
        "**Paper-trading only — no real money involved.**"
    )
    st.markdown("---")

    # ── Session controls ──────────────────────────────────────────────────────
    with st.expander("⚙️ Session Settings", expanded=False):
        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            mode_choice = st.selectbox(
                "Entry Mode",
                ["strict", "broad"],
                help=(
                    "Strict: Tier A always + Tier B ≥80.  "
                    "Broad: Tier A or B ≥75."
                ),
            )
        with col_s2:
            limit_choice = st.selectbox(
                "Top Opportunities Limit",
                [3, 5, 10],
                index=1,
                help="Max positions entered per scan cycle.",
            )
        with col_s3:
            bal_choice = st.number_input(
                "Starting Balance ($)",
                min_value=1_000, max_value=1_000_000,
                value=int(_AP_STARTING_BALANCE), step=1_000,
            )

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("🔄 Reset Session (new bankroll)", use_container_width=True):
                reset_session(mode_choice, limit_choice, float(bal_choice))
                st.success("New session started.")
                st.rerun()
        with col_b2:
            if st.button("📊 Update Settings on Active Session", use_container_width=True):
                from app.database import _connect
                with _connect() as c:
                    c.execute(
                        "UPDATE auto_paper_sessions SET mode=?, top_limit=? WHERE status='active'",
                        (mode_choice, limit_choice),
                    )
                st.success("Settings updated.")
                st.rerun()

    session = get_or_create_session()

    # ── Action buttons ────────────────────────────────────────────────────────
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        if st.button("▶ Run Auto Paper Entries", use_container_width=True, type="primary"):
            msg = _run_ap_scan_cycle(session, all_markets)
            st.success(msg)
            st.rerun()
    with col_a2:
        if st.button("🔁 Settle Resolved Trades", use_container_width=True):
            n = settle_paper_trades(all_markets, session["id"])
            st.success(f"Settled {n} trade(s).")
            st.rerun()
    with col_a3:
        if st.button("🔃 Refresh Bankroll", use_container_width=True):
            st.rerun()

    st.markdown(
        f'<div style="font-size:11px;color:#5a6380;margin-bottom:8px">'
        f'Session #{session["id"]} · Mode: <b>{session.get("mode","strict").upper()}</b> · '
        f'Top limit: <b>{session.get("top_limit", 5)}</b> · '
        f'Started: {(session.get("started_at") or "")[:16]} UTC'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown("---")

    metrics = compute_bankroll_metrics(session["id"])
    if not metrics:
        st.info("No data yet. Click 'Run Auto Paper Entries' to start.")
        return

    models = metrics["models"]

    # ── Summary metric cards (3 models × 4 metrics = 12 cards) ───────────────
    st.markdown("### Model Comparison")
    for model_key, label, color in [
        ("2pct", "Conservative 2%", "#4ade80"),
        ("3pct", "Balanced 3%",     "#60a5fa"),
        ("5pct", "Aggressive 5%",   "#fb923c"),
    ]:
        m = models[model_key]
        pnl_color = "#4ade80" if m["total_pnl"] >= 0 else "#f87171"
        st.markdown(
            f'<div style="font-size:12px;font-weight:600;color:{color};'
            f'margin:6px 0 4px">● {label}</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(6)
        cards = [
            ("Balance",      f"${m['final_balance']:,.0f}",     color,    f"started ${m['starting_balance']:,.0f}"),
            ("Realized P&L", f"${m['total_pnl']:+,.2f}",        pnl_color, f"ROI {m['roi_pct']:+.1f}%"),
            ("Win Rate",     f"{m['win_rate']:.0f}%",           color,    f"{m['wins']}W / {m['losses']}L"),
            ("Live",         str(m["live_trades"]),              "#e8ecf4", "open positions"),
            ("Max Drawdown", f"{m['max_drawdown_pct']:.1f}%",   "#f87171" if m["max_drawdown_pct"] > 15 else "#e8ecf4", "peak to trough"),
            ("Profit Factor",f"{m['profit_factor']:.2f}" if m["profit_factor"] != float("inf") else "∞", color, "gains / losses"),
        ]
        for col, (lbl, val, clr, sub) in zip(cols, cards):
            col.markdown(_ap_metric(lbl, val, clr, sub), unsafe_allow_html=True)

    st.markdown("---")

    # ── Equity curves ─────────────────────────────────────────────────────────
    st.markdown("### Equity Curves")
    fig_eq  = go.Figure()
    colors  = {"2pct": "#4ade80", "3pct": "#60a5fa", "5pct": "#fb923c"}
    labels  = {"2pct": "2% Conservative", "3pct": "3% Balanced", "5pct": "5% Aggressive"}
    max_pts = 1

    for key in ("2pct", "3pct", "5pct"):
        curve = models[key]["equity_curve"]
        if len(curve) > 1:
            fig_eq.add_trace(go.Scatter(
                x=list(range(len(curve))),
                y=curve,
                mode="lines",
                name=labels[key],
                line=dict(color=colors[key], width=2),
            ))
            max_pts = max(max_pts, len(curve))

    start_bal = float(session.get("starting_balance") or _AP_STARTING_BALANCE)
    fig_eq.add_trace(go.Scatter(
        x=[0, max_pts - 1],
        y=[start_bal, start_bal],
        mode="lines",
        name="Starting Balance",
        line=dict(color="#5a6380", width=1, dash="dash"),
    ))
    fig_eq.update_layout(
        **_plotly_dark(),
        xaxis_title="Trade # (settled)",
        yaxis_title="Balance ($)",
        height=360,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    st.markdown("---")

    # ── Comparison table ──────────────────────────────────────────────────────
    st.markdown("### Sizing Model Breakdown")
    tbl_rows = []
    for key, lbl in [("2pct", "2% Conservative"), ("3pct", "3% Balanced"), ("5pct", "5% Aggressive")]:
        m = models[key]
        tbl_rows.append({
            "Model":          lbl,
            "Trades":         m["total_trades"],
            "Win Rate (%)":   m["win_rate"],
            "Total P&L ($)":  m["total_pnl"],
            "Final Bal ($)":  m["final_balance"],
            "ROI (%)":        m["roi_pct"],
            "Max DD (%)":     m["max_drawdown_pct"],
            "Avg Win ($)":    m["avg_win"],
            "Avg Loss ($)":   m["avg_loss"],
        })
    st.dataframe(
        pd.DataFrame(tbl_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Model":         st.column_config.TextColumn("Model"),
            "Trades":        st.column_config.NumberColumn("Trades"),
            "Win Rate (%)":  st.column_config.NumberColumn("Win Rate", format="%.1f%%"),
            "Total P&L ($)": st.column_config.NumberColumn("Total P&L", format="$%.2f"),
            "Final Bal ($)": st.column_config.NumberColumn("Balance", format="$%.2f"),
            "ROI (%)":       st.column_config.NumberColumn("ROI", format="%.2f%%"),
            "Max DD (%)":    st.column_config.NumberColumn("Max DD", format="%.1f%%"),
            "Avg Win ($)":   st.column_config.NumberColumn("Avg Win", format="$%.2f"),
            "Avg Loss ($)":  st.column_config.NumberColumn("Avg Loss", format="$%.2f"),
        },
    )

    st.markdown("---")

    # ── Plain-English summary ─────────────────────────────────────────────────
    summary = generate_bankroll_summary(metrics)
    st.markdown(
        f'<div style="background:#161929;border-radius:10px;padding:16px 20px;'
        f'border-left:4px solid #60a5fa;font-size:14px;color:#c8d0e8;line-height:1.7">'
        f'💬 {summary}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown(
        '<div style="font-size:12px;color:#5a6380;padding:12px;background:#161929;'
        'border-radius:8px;border-left:3px solid #f59e0b">'
        "⚠️ Paper-trading only. No real money is involved. "
        "Outcomes reflect actual market resolution prices — this is not a random simulation."
        "</div>",
        unsafe_allow_html=True,
    )


# ── Page: Live Positions ───────────────────────────────────────────────────────

def page_live_positions() -> None:
    st.markdown("# 📍 Live Positions")
    st.markdown("All currently open paper trades — positions entered but not yet resolved.")
    st.markdown("---")

    session = get_active_session()
    if not session:
        st.info("No active bankroll session. Go to 💰 Bankroll Simulator to start one.")
        return

    all_markets = _load_markets()

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        if st.button("🔁 Settle Resolved Trades", use_container_width=True):
            n = settle_paper_trades(all_markets, session["id"])
            st.success(f"Settled {n} trade(s).")
            st.rerun()
    with col_b2:
        if st.button("▶ Run Auto Paper Entries", use_container_width=True):
            msg = _run_ap_scan_cycle(session, all_markets)
            st.success(msg)
            st.rerun()

    st.markdown("---")
    live = get_live_trades(session["id"])

    if not live:
        st.info("No open positions. Click 'Run Auto Paper Entries' to enter new positions.")
        return

    st.markdown(f"**{len(live)} open position(s)**")

    rows = []
    for t in live:
        rows.append({
            "Market":       (t.get("question") or "")[:70],
            "Tier":         t.get("tier", "?"),
            "Score":        float(t.get("score") or 0),
            "Side":         t.get("chosen_side", "YES"),
            "Entry Price":  float(t.get("entry_price") or 0),
            "Hours@Entry":  float(t.get("hours_at_entry") or 0),
            "Mode":         t.get("entry_mode", "strict"),
            "Pos 2% ($)":   float(t.get("notional_2pct") or 0),
            "Pos 3% ($)":   float(t.get("notional_3pct") or 0),
            "Pos 5% ($)":   float(t.get("notional_5pct") or 0),
            "Entered":      (t.get("entry_timestamp") or "")[:16],
        })
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Market":      st.column_config.TextColumn("Market", width="large"),
            "Tier":        st.column_config.TextColumn("Tier", width="small"),
            "Score":       st.column_config.NumberColumn("Score", format="%.0f"),
            "Side":        st.column_config.TextColumn("Side", width="small"),
            "Entry Price": st.column_config.NumberColumn("Entry Price", format="%.3f"),
            "Hours@Entry": st.column_config.NumberColumn("Hrs@Entry", format="%.1f"),
            "Mode":        st.column_config.TextColumn("Mode", width="small"),
            "Pos 2% ($)":  st.column_config.NumberColumn("Pos 2%", format="$%.2f"),
            "Pos 3% ($)":  st.column_config.NumberColumn("Pos 3%", format="$%.2f"),
            "Pos 5% ($)":  st.column_config.NumberColumn("Pos 5%", format="$%.2f"),
            "Entered":     st.column_config.TextColumn("Entered (UTC)"),
        },
    )

    # Resolved trades section below
    st.markdown("---")
    st.markdown("### Resolved Trades")
    resolved = get_resolved_trades(session["id"])
    if not resolved:
        st.info("No resolved trades yet.")
        return

    wins   = [t for t in resolved if t.get("status") == "win"]
    losses = [t for t in resolved if t.get("status") == "loss"]
    unres  = [t for t in resolved if t.get("status") == "unresolved"]

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Total Resolved", len(resolved))
    mc2.metric("Wins ✅", len(wins))
    mc3.metric("Losses ❌", len(losses))
    mc4.metric("Unresolved ⏳", len(unres))

    res_rows = []
    for t in resolved:
        pnl3 = float(t.get("pnl_3pct") or 0)
        res_rows.append({
            "Market":       (t.get("question") or "")[:65],
            "Tier":         t.get("tier", "?"),
            "Score":        float(t.get("score") or 0),
            "Entry Price":  float(t.get("entry_price") or 0),
            "Exit Price":   float(t.get("exit_price") or 0) if t.get("exit_price") is not None else None,
            "P&L 3% ($)":   pnl3,
            "Status":       (t.get("status") or "").upper(),
            "Settled":      (t.get("exit_timestamp") or "")[:16],
        })
    st.dataframe(
        pd.DataFrame(res_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Market":      st.column_config.TextColumn("Market", width="large"),
            "Tier":        st.column_config.TextColumn("Tier", width="small"),
            "Score":       st.column_config.NumberColumn("Score", format="%.0f"),
            "Entry Price": st.column_config.NumberColumn("Entry", format="%.3f"),
            "Exit Price":  st.column_config.NumberColumn("Exit", format="%.3f"),
            "P&L 3% ($)":  st.column_config.NumberColumn("P&L (3%)", format="$%.2f"),
            "Status":      st.column_config.TextColumn("Status", width="small"),
            "Settled":     st.column_config.TextColumn("Settled (UTC)"),
        },
    )


# ── Page: What Winners Look Like ──────────────────────────────────────────────

def _insight_card(
    title: str, body: str, border: str = "#60a5fa", icon: str = ""
) -> str:
    return (
        f'<div style="background:#161929;border-radius:10px;padding:16px 20px;'
        f'border-left:4px solid {border};margin-bottom:10px">'
        f'<div style="font-size:11px;color:#8892b0;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:6px">'
        f'{icon} {title}</div>'
        f'<div style="font-size:14px;color:#c8d0e8;line-height:1.65">{body}</div>'
        f'</div>'
    )


def _edge_badge(edge: str) -> str:
    styles = {
        "Strong Edge":       ("background:#166534;color:#4ade80", "● Strong Edge"),
        "Moderate Edge":     ("background:#1e3a5f;color:#60a5fa", "● Moderate Edge"),
        "No Edge":           ("background:#1f2937;color:#9aa3b8", "○ No Edge"),
        "Negative Edge":     ("background:#450a0a;color:#f87171", "✕ Negative Edge"),
        "Insufficient Data": ("background:#1c1c1c;color:#5a6380",  "⏳ Need more data"),
    }
    style, label = styles.get(edge, ("background:#1f2937;color:#9aa3b8", edge))
    return (
        f'<span style="{style};padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600">{label}</span>'
    )


def _ww_bar_chart(title: str, data: list[dict], chart_key: str) -> None:
    """Bar chart for win rate — only shows bars, no clutter."""
    sufficient = [r for r in data if r.get("sufficient")]
    insufficient = [r for r in data if not r.get("sufficient")]
    if not data:
        st.caption("No data yet.")
        return

    fig = go.Figure()
    color_map = {
        "Strong Edge":       "#4ade80",
        "Moderate Edge":     "#60a5fa",
        "No Edge":           "#6b7280",
        "Negative Edge":     "#f87171",
        "Insufficient Data": "#374151",
    }

    if sufficient:
        colors_s = [color_map.get(r.get("edge", ""), "#6b7280") for r in sufficient]
        fig.add_bar(
            x=[r["group"] for r in sufficient],
            y=[r["win_rate"] for r in sufficient],
            marker_color=colors_s,
            text=[
                f"{r['win_rate']:.0f}%<br><span style='font-size:9px'>"
                f"n={r['trades']}</span>"
                for r in sufficient
            ],
            textposition="outside",
            name="Sufficient data",
        )

    if insufficient:
        fig.add_bar(
            x=[r["group"] for r in insufficient],
            y=[r["trades"] for r in insufficient],
            marker_color="#1f2937",
            marker_line_color="#374151",
            marker_line_width=1,
            text=[f"Need {10 - r['trades']} more" for r in insufficient],
            textposition="outside",
            name="Insufficient data",
            opacity=0.5,
        )

    fig.add_hline(y=50, line_dash="dash", line_color="#374151", line_width=1)
    fig.add_hline(y=60, line_dash="dot",  line_color="#166534",  line_width=1,
                  annotation_text="Strong Edge", annotation_position="right",
                  annotation_font_color="#4ade80", annotation_font_size=10)

    fig.update_layout(
        **_plotly_dark(),
        title=dict(text=title, font=dict(color="#9aa3b8", size=12)),
        yaxis=dict(title="Win Rate (%)", range=[0, 120]),
        showlegend=False,
        height=260,
        margin=dict(t=40, b=20, l=20, r=20),
    )
    st.plotly_chart(fig, use_container_width=True, key=chart_key)


def page_what_winners_look_like() -> None:
    st.markdown("# 🎓 Decision Engine")
    st.markdown(
        "High-confidence edge detection — only shows insights backed by ≥10 trades.  \n"
        "Everything below updates automatically as more paper trades resolve."
    )

    session = get_active_session()
    if not session:
        st.info("No active bankroll session. Go to 💰 Bankroll Simulator to start one.")
        return

    trades   = get_all_auto_trades(session["id"])
    resolved = [t for t in trades if t.get("status") in ("win", "loss")]
    live_ct  = len([t for t in trades if t.get("status") == "live"])

    # ── Data gate ─────────────────────────────────────────────────────────────
    if not resolved:
        st.info(
            f"No resolved trades yet ({live_ct} currently live). "
            "Keep running scans — insights appear automatically once trades settle."
        )
        return

    from app.training import generate_insight_report
    analytics = compute_training_analytics(trades)
    wins   = analytics.get("wins", 0)
    losses = analytics.get("losses", 0)
    total  = wins + losses
    wr     = wins / total * 100 if total else 0

    # ── Section 1: Three core questions ───────────────────────────────────────
    st.markdown("---")
    st.markdown("### The Three Questions")
    q1, q2, q3 = st.columns(3)

    # Q1: Is this working?
    if total < 10:
        q1_color, q1_val, q1_sub = "#f59e0b", "EARLY DATA", f"{total} resolved trades — need 10+"
    elif wr >= 60:
        q1_color, q1_val, q1_sub = "#4ade80", "YES ✅", f"{wr:.0f}% win rate ({total} trades)"
    elif wr >= 50:
        q1_color, q1_val, q1_sub = "#f59e0b", "MIXED ⚠", f"{wr:.0f}% win rate ({total} trades)"
    else:
        q1_color, q1_val, q1_sub = "#f87171", "NO ✕", f"{wr:.0f}% win rate — below breakeven"

    with q1:
        st.markdown(
            f'<div style="background:#161929;border-radius:10px;padding:20px;text-align:center;'
            f'border:1px solid {q1_color}44">'
            f'<div style="font-size:11px;color:#8892b0;text-transform:uppercase;'
            f'letter-spacing:0.08em">Is this working?</div>'
            f'<div style="font-size:28px;font-weight:700;color:{q1_color};margin:8px 0">'
            f'{q1_val}</div>'
            f'<div style="font-size:12px;color:#5a6380">{q1_sub}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Q2: What should I trade?
    wtt    = analytics.get("what_to_trade_now", {})
    best_score = wtt.get("score")
    best_price = wtt.get("price")
    best_time  = wtt.get("timing")
    wtt_lines  = []
    if best_score: wtt_lines.append(f"Score: {best_score['group']} ({best_score['win_rate']:.0f}% WR)")
    if best_price: wtt_lines.append(f"Price: {best_price['group']} ({best_price['win_rate']:.0f}% WR)")
    if best_time:  wtt_lines.append(f"Time: {best_time['group']} ({best_time['win_rate']:.0f}% WR)")
    wtt_body = "<br>".join(wtt_lines) if wtt_lines else "Need ≥10 trades per bucket"
    best_tier = wtt.get("tier")
    if best_tier: wtt_body += f"<br>Tier: {best_tier['group']}"

    with q2:
        st.markdown(
            f'<div style="background:#161929;border-radius:10px;padding:20px;text-align:center;'
            f'border:1px solid #4ade8044">'
            f'<div style="font-size:11px;color:#8892b0;text-transform:uppercase;'
            f'letter-spacing:0.08em">What to trade?</div>'
            f'<div style="font-size:13px;font-weight:600;color:#4ade80;margin:8px 0;'
            f'line-height:1.7">{wtt_body}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Q3: What should I avoid?
    avoid  = analytics.get("what_to_avoid", [])
    av_lines = [f"{a['dimension']}: {a['bucket']} ({a['win_rate']:.0f}% WR)" for a in avoid[:3]]
    av_body  = "<br>".join(av_lines) if av_lines else "No confirmed negative edges yet"

    with q3:
        st.markdown(
            f'<div style="background:#161929;border-radius:10px;padding:20px;text-align:center;'
            f'border:1px solid #f8717144">'
            f'<div style="font-size:11px;color:#8892b0;text-transform:uppercase;'
            f'letter-spacing:0.08em">What to avoid?</div>'
            f'<div style="font-size:13px;font-weight:600;color:#f87171;margin:8px 0;'
            f'line-height:1.7">{av_body}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Section 2: What To Trade Right Now ───────────────────────────────────
    st.markdown("### 🟢 What To Trade Right Now")

    recs = []
    for dim, label, fmt in [
        ("tier",       "Tier",       lambda r: r["group"]),
        ("score",      "Score",      lambda r: r["group"]),
        ("price",      "Price",      lambda r: r["group"]),
        ("timing",     "Time Window", lambda r: r["group"]),
        ("liq",        "Liquidity",  lambda r: r["group"]),
        ("spread",     "Spread",     lambda r: r["group"]),
        ("confidence", "Confidence", lambda r: r["group"]),
    ]:
        rec = wtt.get(dim)
        if rec:
            recs.append((label, fmt(rec), rec["win_rate"], rec["edge"], rec["trades"]))

    if recs:
        rec_html = "".join(
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:7px 0;border-bottom:1px solid #1e2235">'
            f'<span style="color:#8892b0;font-size:13px;width:120px">{lbl}</span>'
            f'<span style="color:#e8ecf4;font-size:14px;font-weight:600">{val}</span>'
            f'<span style="color:#4ade80;font-size:12px">{wr:.0f}% win rate</span>'
            f'<span style="color:#6b7280;font-size:11px">{cnt} trades</span>'
            f'</div>'
            for lbl, val, wr, edge, cnt in recs
        )
        st.markdown(
            f'<div style="background:#0d1b0d;border-radius:10px;padding:16px 20px;'
            f'border:1px solid #166534">{rec_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("Not enough data yet — need ≥10 resolved trades per condition to recommend.")

    st.markdown("---")

    # ── Section 3: What To Avoid ─────────────────────────────────────────────
    st.markdown("### 🔴 What To Avoid")
    if avoid:
        av_html = "".join(
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:7px 0;border-bottom:1px solid #1e2235">'
            f'<span style="color:#8892b0;font-size:13px;width:120px">{a["dimension"]}</span>'
            f'<span style="color:#e8ecf4;font-size:14px;font-weight:600">{a["bucket"]}</span>'
            f'<span style="color:#f87171;font-size:12px">{a["win_rate"]:.0f}% win rate</span>'
            f'<span style="color:#6b7280;font-size:11px">{a["avg_pnl"]:+.2f} avg P&L</span>'
            f'</div>'
            for a in avoid
        )
        st.markdown(
            f'<div style="background:#1a0909;border-radius:10px;padding:16px 20px;'
            f'border:1px solid #450a0a">{av_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.success("No confirmed negative edges — no conditions to avoid yet.")

    st.markdown("---")

    # ── Section 4: Score Calibration ─────────────────────────────────────────
    st.markdown("### 📐 Score Calibration — Is the Scoring Model Predictive?")
    cal     = analytics.get("calibration", {})
    verdict = cal.get("verdict", "insufficient_data")
    cal_map = {
        "well_calibrated":   ("✅ Scores are well calibrated — higher scores reliably predict wins.", "#4ade80"),
        "not_predictive":    ("⚠️ Scores are not strongly predictive yet — more data needed.", "#f59e0b"),
        "inverted":          ("🔴 Score calibration is inverted — lower scores are currently winning more.", "#f87171"),
        "insufficient_data": ("⏳ Not enough data per score bucket — need 10+ trades each.", "#5a6380"),
    }
    cal_text, cal_color = cal_map.get(verdict, ("—", "#5a6380"))
    st.markdown(
        f'<div style="background:#161929;border-left:4px solid {cal_color};'
        f'border-radius:8px;padding:12px 16px;font-size:14px;color:{cal_color};'
        f'margin-bottom:12px">{cal_text}</div>',
        unsafe_allow_html=True,
    )

    cal_rows = [r for r in cal.get("rows", []) if r["trades"] > 0]
    if cal_rows:
        fig_cal = go.Figure()
        colors_cal = ["#4ade80" if r["sufficient"] else "#374151" for r in cal_rows]
        fig_cal.add_bar(
            x=[r["bucket"] for r in cal_rows],
            y=[r["win_rate"] if r["win_rate"] is not None else 0 for r in cal_rows],
            marker_color=colors_cal,
            text=[
                f"{r['win_rate']:.0f}%" if r["win_rate"] is not None
                else f"n={r['trades']}"
                for r in cal_rows
            ],
            textposition="outside",
            name="Actual Win Rate",
        )
        fig_cal.add_hline(y=50, line_dash="dash", line_color="#374151")
        fig_cal.add_hline(y=60, line_dash="dot", line_color="#166534",
                          annotation_text="Strong Edge",
                          annotation_position="right",
                          annotation_font_color="#4ade80",
                          annotation_font_size=10)
        fig_cal.update_layout(
            **_plotly_dark(),
            title=dict(text="Actual Win Rate by Score Range", font=dict(color="#9aa3b8", size=12)),
            xaxis_title="Score Range",
            yaxis=dict(title="Win Rate (%)", range=[0, 120]),
            height=280,
            showlegend=False,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig_cal, use_container_width=True, key="cal_chart")

    st.markdown("---")

    # ── Section 5: Edge Detection Charts ─────────────────────────────────────
    st.markdown("### ⚡ Edge Detection — Performance by Condition")
    st.caption(
        f"Gray bars = fewer than 10 trades (ignored). "
        f"Green = Strong Edge (≥60% WR, profit factor ≥1.5). "
        f"Blue = Moderate Edge (≥55% WR, PF ≥1.2). "
        f"Red = Negative Edge."
    )

    c1, c2 = st.columns(2)
    with c1:
        _ww_bar_chart("By Tier", analytics.get("by_tier", []), "ww2_tier")
    with c2:
        _ww_bar_chart("By Score Range", analytics.get("by_score", []), "ww2_score")

    c3, c4 = st.columns(2)
    with c3:
        _ww_bar_chart("By Price Band", analytics.get("by_price", []), "ww2_price")
    with c4:
        _ww_bar_chart("By Timing (Hrs to Expiry)", analytics.get("by_hours", []), "ww2_hours")

    if analytics.get("by_liq"):
        c5, c6 = st.columns(2)
        with c5:
            _ww_bar_chart("By Liquidity", analytics.get("by_liq", []), "ww2_liq")
        with c6:
            _ww_bar_chart("By Spread", analytics.get("by_spread", []), "ww2_spread")

    st.markdown("---")

    # ── Section 6: Top Trade Patterns ────────────────────────────────────────
    st.markdown("### 🏆 Top Trade Patterns")
    patterns = analytics.get("top_patterns", [])
    if patterns:
        for p in patterns:
            edge_color = {
                "Strong Edge":   "#4ade80",
                "Moderate Edge": "#60a5fa",
            }.get(p["edge"], "#6b7280")
            st.markdown(
                f'<div style="background:#161929;border-radius:8px;padding:12px 16px;'
                f'border-left:3px solid {edge_color};margin-bottom:8px;'
                f'display:flex;align-items:center;justify-content:space-between">'
                f'<div>'
                f'<div style="font-size:13px;font-weight:600;color:#e8ecf4">{p["pattern"]}</div>'
                f'<div style="font-size:11px;color:#5a6380;margin-top:2px">'
                f'{p["trades"]} trades · {p["win_rate"]:.0f}% win rate · '
                f'avg P&L ${p["avg_pnl"]:+,.2f}</div>'
                f'</div>'
                f'{_edge_badge(p["edge"])}'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.info(
            f"Top patterns require ≥{10} trades per combination. "
            "Keep running scans — patterns surface automatically."
        )

    st.markdown("---")

    # ── Section 7: Failure Analysis ──────────────────────────────────────────
    st.markdown("### 🔍 Failure Analysis — Where Losses Concentrate")
    failures = analytics.get("failure_analysis", {})
    fail_traits = failures.get("traits", [])
    if fail_traits:
        for t in fail_traits:
            st.markdown(
                f'<div style="background:#1a0909;border-radius:8px;padding:10px 14px;'
                f'border-left:3px solid #f87171;margin-bottom:6px">'
                f'<span style="color:#f87171;font-weight:600">{t["dimension"]}: {t["bucket"]}</span>'
                f' — <span style="color:#c8d0e8">{t["loss_rate"]:.0f}% loss rate '
                f'({t["losses"]} losses out of {t["total"]} trades)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.success("No dominant failure patterns identified — losses are spread evenly.")

    st.markdown("---")

    # ── Section 8: Strategy Learning Summary ─────────────────────────────────
    st.markdown("### 💬 Strategy Learning Summary")
    bullets = analytics.get("learning_summary", [])
    bullet_html = "".join(
        f'<div style="padding:5px 0;border-bottom:1px solid #1e2235;color:#c8d0e8;'
        f'font-size:13px">▸ {b}</div>'
        for b in bullets
    )
    st.markdown(
        f'<div style="background:#161929;border-radius:10px;padding:16px 20px;'
        f'border-left:4px solid #a78bfa">{bullet_html}</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── Section 9: Winner vs Loser Averages ──────────────────────────────────
    with st.expander("📊 Winner vs Loser Averages"):
        ws = analytics.get("avg_score_winners",  0)
        ls = analytics.get("avg_score_losers",   0)
        wh = analytics.get("avg_hours_winners",  0)
        lh = analytics.get("avg_hours_losers",   0)
        wp = analytics.get("avg_price_winners",  0)
        lp = analytics.get("avg_price_losers",   0)
        comp_cols = st.columns(3)
        for col, (label, wval, lval, fmt) in zip(comp_cols, [
            ("Avg Score",        ws, ls, "{:.0f}"),
            ("Avg Hours@Entry",  wh, lh, "{:.1f}h"),
            ("Avg Entry Price",  wp, lp, "{:.3f}"),
        ]):
            col.markdown(
                f'<div style="background:#0f111a;border-radius:8px;padding:12px;text-align:center">'
                f'<div style="font-size:11px;color:#8892b0;text-transform:uppercase">{label}</div>'
                f'<div style="display:flex;justify-content:space-around;margin-top:8px">'
                f'<div><div style="font-size:10px;color:#4ade80">Winners</div>'
                f'<div style="font-size:20px;font-weight:700;color:#4ade80">{fmt.format(wval)}</div></div>'
                f'<div><div style="font-size:10px;color:#f87171">Losers</div>'
                f'<div style="font-size:20px;font-weight:700;color:#f87171">{fmt.format(lval)}</div></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    # ── Section 10: Export ────────────────────────────────────────────────────
    st.markdown("---")
    report_md = generate_insight_report(analytics)
    st.download_button(
        "📥 Export Insight Report (Markdown)",
        data=report_md,
        file_name="training_insights_report.md",
        mime="text/markdown",
        use_container_width=True,
    )


# ── Page: 48-Hour Validation ──────────────────────────────────────────────────

def _val_metric(label: str, value: str, color: str = "#e8ecf4", sub: str = "") -> str:
    sub_html = f'<div style="font-size:11px;color:#5a6380;margin-top:2px">{sub}</div>' if sub else ""
    return (
        f'<div class="metric-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value" style="font-size:22px;color:{color}">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


def page_validation() -> None:
    st.markdown("# 🧪 48-Hour Paper-Trading Validation")
    st.markdown(
        "Live paper-trading test that runs for 48 hours, scanning every 15 minutes.  \n"
        "**No real money is involved.** All entries and exits are simulated."
    )
    st.markdown("---")

    active = get_active_run()

    # ── Controls ─────────────────────────────────────────────────────────────
    ctrl_l, ctrl_r = st.columns([3, 1])
    with ctrl_l:
        if active:
            hr_left = hours_remaining(active)
            expired = is_test_expired(active)
            status  = active.get("status", "running")
            col_s   = "#22c55e" if not expired else "#f59e0b"
            label   = (
                f"Test running — {hr_left:.1f}h remaining"
                if not expired else
                "Test window complete — awaiting final settlement"
            )
            st.markdown(
                f'<div style="font-size:14px;font-weight:600;color:{col_s};margin-bottom:8px">'
                f'● {label}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="font-size:14px;color:#5a6380;margin-bottom:8px">'
                'No active test. Configure and start a new 48-hour run.</div>',
                unsafe_allow_html=True,
            )

    with ctrl_r:
        if not active:
            with st.expander("⚙️ Settings", expanded=False):
                sb   = st.number_input("Starting Balance ($)", 1_000, 500_000,
                                       int(_VAL_DEFAULT["starting_balance"]), 1_000)
                dur  = st.number_input("Duration (hours)", 1, 168,
                                       int(_VAL_DEFAULT["duration_hours"]), 1)
                intv = st.number_input("Scan interval (min)", 5, 60,
                                       int(_VAL_DEFAULT["scan_interval_minutes"]), 5)
                stop = st.number_input("Stop entries within (min of close)", 5, 120,
                                       int(_VAL_DEFAULT["stop_new_entries_minutes"]), 5)
                allow_a = st.checkbox("Allow Tier A", value=True)
                allow_b = st.checkbox("Allow Tier B", value=True)
                allow_c = st.checkbox("Allow Tier C", value=False)
            if st.button("▶ Start 48-Hour Test", use_container_width=True):
                cfg = {
                    "starting_balance":         float(sb),
                    "duration_hours":            int(dur),
                    "scan_interval_minutes":     int(intv),
                    "stop_new_entries_minutes":  int(stop),
                    "allow_tier_a":              allow_a,
                    "allow_tier_b":              allow_b,
                    "allow_tier_c":              allow_c,
                }
                with st.spinner("Starting test and running first scan…"):
                    run_id  = start_test(cfg)
                    run_inf = {"id": run_id, **cfg,
                               "started_at": "", "ends_at": "", "last_scan_at": None}
                    run_validation_cycle(run_id, run_inf)
                st.success(f"Test started (run #{run_id}). Refresh this page to see results.")
                st.rerun()
        else:
            run_id = active["id"]
            if st.button("🔄 Refresh / Run Scan", use_container_width=True):
                if should_run_cycle(active):
                    with st.spinner("Running validation cycle…"):
                        result = run_validation_cycle(run_id, active)
                    st.success(
                        f"Scan complete: {result.get('scanned',0)} markets, "
                        f"{result.get('entered',0)} new positions, "
                        f"{result.get('settled',0)} settled."
                    )
                else:
                    st.info("Last scan was recent. Check back in a few minutes.")
                st.rerun()
            if st.button("⏹ Stop Test", use_container_width=True):
                stop_test(run_id)
                st.warning("Test stopped.")
                st.rerun()

    if not active:
        # Show past runs
        past = get_all_runs()
        if past:
            st.markdown("---")
            st.markdown("### Past Runs")
            past_rows = []
            for r in past:
                past_rows.append({
                    "Run #": r["id"],
                    "Started": (r.get("started_at") or "")[:16].replace("T", " "),
                    "Status":  r.get("status", "?"),
                    "Balance": f"${r.get('starting_balance',0):,.0f}",
                    "Duration": f"{r.get('duration_hours', 48)}h" if r.get("duration_hours") else "48h",
                })
            st.dataframe(pd.DataFrame(past_rows), use_container_width=True, hide_index=True)

            sel_run = st.number_input(
                "View run #", min_value=1,
                max_value=int(past[0]["id"]),
                value=int(past[0]["id"]), step=1,
            )
            active = {"id": sel_run, **{k: past[0].get(k) for k in past[0]}}
            run_id = sel_run
        else:
            return

    run_id  = active["id"]
    metrics = compute_metrics(run_id)
    if not metrics:
        st.info("No metrics yet — run the first scan cycle.")
        return

    m1 = metrics["models"]["1pct"]
    m2 = metrics["models"]["2pct"]
    m5 = metrics["models"]["5pct"]
    sb = float(active.get("starting_balance", 10_000))

    st.markdown("---")

    # ── A) Summary cards ─────────────────────────────────────────────────────
    st.markdown("### Summary")
    cols = st.columns(4)
    pnl5   = m5.get("total_pnl", 0.0)
    pnl_c  = "#22c55e" if pnl5 >= 0 else "#ef4444"
    roi5   = m5.get("roi_pct", 0.0)
    cols[0].markdown(_val_metric("Starting Balance", f"${sb:,.0f}"), unsafe_allow_html=True)
    cols[1].markdown(_val_metric("Balance (5% model)", f"${m5.get('final_balance',sb):,.2f}",
                                 pnl_c, f"{roi5:+.2f}% ROI"), unsafe_allow_html=True)
    cols[2].markdown(_val_metric("Realized P&L (5%)", f"${pnl5:+,.2f}", pnl_c,
                                 f"Profit factor {m5.get('profit_factor',0):.2f}"), unsafe_allow_html=True)
    cols[3].markdown(_val_metric("Win Rate (5%)", f"{m5.get('win_rate',0):.0f}%",
                                 "#22c55e" if m5.get("win_rate",0)>=55 else "#ef4444",
                                 f"{m5.get('wins',0)}W / {m5.get('losses',0)}L"), unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    cols2 = st.columns(4)
    cols2[0].markdown(_val_metric("Max Drawdown (5%)", f"{m5.get('max_drawdown_pct',0):.1f}%",
                                  "#f59e0b" if m5.get("max_drawdown_pct",0) > 10 else "#22c55e",
                                  "peak-to-trough"), unsafe_allow_html=True)
    cols2[1].markdown(_val_metric("Open Trades",     str(m5.get("open_trades",0)),    "#4060f0", "currently open"), unsafe_allow_html=True)
    cols2[2].markdown(_val_metric("Resolved Trades", str(m5.get("total_trades",0)),   "#7c6af6", "won + lost"), unsafe_allow_html=True)
    cols2[3].markdown(_val_metric("Test Progress",   f"{metrics.get('hours_elapsed',0):.1f}h",
                                  "#f0b429", f"of {active.get('duration_hours', active.get('ends_at','48')[:2])}h total"), unsafe_allow_html=True)

    st.markdown("---")

    # ── B) Equity curves ─────────────────────────────────────────────────────
    st.markdown("### Equity Curves")
    line_colors = {"1pct": "#4060f0", "2pct": "#7c6af6", "5pct": "#f0b429"}
    fig_eq = go.Figure()
    for model, label in [("1pct","1%"), ("2pct","2%"), ("5pct","5%")]:
        curve = metrics["models"][model].get("equity_curve", [])
        if len(curve) > 1:
            fig_eq.add_trace(go.Scatter(
                x=list(range(len(curve))), y=curve,
                mode="lines", name=label,
                line=dict(color=line_colors[model], width=2),
            ))
    max_len = max((len(metrics["models"][m].get("equity_curve",[])) for m in line_colors), default=2)
    fig_eq.add_trace(go.Scatter(
        x=[0, max(max_len - 1, 1)], y=[sb, sb],
        mode="lines", name="Starting Balance",
        line=dict(color="#5a6380", width=1, dash="dash"),
    ))
    fig_eq.update_layout(
        **_plotly_dark(),
        title=dict(text="Paper-trade equity curves (1%, 2%, 5% position size)",
                   font=dict(color="#9aa3b8", size=13)),
        xaxis_title="Trade Number", yaxis_title="Balance ($)",
        height=340,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    st.markdown("---")

    # ── C) Trade log ─────────────────────────────────────────────────────────
    st.markdown("### Trade Log")
    trades = metrics.get("trades", [])
    if trades:
        log_rows = []
        for t in reversed(trades):  # newest first
            status_disp = {"won": "✅ Won", "lost": "❌ Lost",
                           "open": "🔵 Open", "unresolved": "❓ Unresolved"}.get(
                t.get("status", "open"), t.get("status", "?"))
            log_rows.append({
                "Entered":   (t.get("entry_at") or "")[:16].replace("T", " "),
                "Market":    (t.get("question") or "")[:55],
                "Side":      t.get("side", "YES"),
                "Score":     t.get("score") or 0,
                "Tier":      t.get("tier", "?"),
                "Price":     t.get("entry_price") or 0,
                "Size (5%)": t.get("notional_5pct") or 0,
                "Status":    status_disp,
                "P&L (5%)":  t.get("pnl_5pct") or 0,
            })
        st.dataframe(
            pd.DataFrame(log_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Entered":  st.column_config.TextColumn("Entered",  width="small"),
                "Market":   st.column_config.TextColumn("Market",   width="large"),
                "Side":     st.column_config.TextColumn("Side",     width="small"),
                "Score":    st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                "Tier":     st.column_config.TextColumn("Tier",     width="small"),
                "Price":    st.column_config.NumberColumn("Price",   format="%.3f"),
                "Size (5%)": st.column_config.NumberColumn("Size (5%)", format="$%.2f"),
                "Status":   st.column_config.TextColumn("Status",   width="medium"),
                "P&L (5%)": st.column_config.NumberColumn("P&L (5%)", format="$%.2f"),
            },
        )
    else:
        st.info("No trades entered yet.")

    st.markdown("---")

    # ── D) Breakdown charts ───────────────────────────────────────────────────
    st.markdown("### Performance Breakdown (5% model)")
    c1, c2 = st.columns(2)

    def _breakdown_chart(data: list[dict], title: str, col) -> None:
        if not data:
            col.info("No data yet.")
            return
        groups = [r["group"] for r in data]
        wins   = [r["wins"] for r in data]
        pnls   = [r["pnl"] for r in data]
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Wins", x=groups, y=wins,
                             marker_color="#22c55e", opacity=0.85))
        fig.add_trace(go.Bar(name="P&L ($)", x=groups, y=pnls,
                             marker_color=["#22c55e" if p >= 0 else "#ef4444" for p in pnls],
                             opacity=0.7, yaxis="y2"))
        fig.update_layout(
            **_plotly_dark(),
            title=dict(text=title, font=dict(color="#9aa3b8", size=12)),
            barmode="group", height=260,
            yaxis2=dict(overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        col.plotly_chart(fig, use_container_width=True)

    _breakdown_chart(metrics.get("by_tier",  []), "P&L by Tier",        c1)
    _breakdown_chart(metrics.get("by_score", []), "P&L by Score Bucket", c2)

    c3, c4 = st.columns(2)
    _breakdown_chart(metrics.get("by_price", []), "P&L by Price Band",    c3)
    _breakdown_chart(metrics.get("by_hours", []), "P&L by Hours to Close", c4)

    st.markdown("---")

    # ── E) Permutation coverage ───────────────────────────────────────────────
    st.markdown("### Permutation Coverage (all cycles)")
    perm = get_permutation_stats(run_id)

    p_cols = st.columns(5)
    perm_cards = [
        ("Total Scanned",   str(perm["total_scanned"]),   "#4060f0"),
        ("Primary Band",    str(perm["total_primary"]),    "#22c55e"),
        ("Secondary Band",  str(perm["total_secondary"]),  "#7c6af6"),
        ("Watchlist Band",  str(perm["total_watchlist"]),  "#f59e0b"),
        ("Accepted Rate",   f"{perm['pct_accepted']:.1f}%", "#f0b429"),
    ]
    for col, (lbl, val, clr) in zip(p_cols, perm_cards):
        col.markdown(
            f'<div class="metric-card" style="border-left:3px solid {clr}">'
            f'<div class="metric-label">{lbl}</div>'
            f'<div class="metric-value" style="color:{clr};font-size:20px">{val}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    rigidity_map = {
        "too_rigid": ("⚠️ Too Rigid", "#f59e0b",
                      "< 1% of scanned markets are accepted. Consider loosening filters."),
        "balanced":  ("✅ Balanced",  "#22c55e",
                      "1–15% acceptance rate. Filters are well-calibrated."),
        "too_loose": ("⚠️ Too Loose", "#ef4444",
                      "> 15% of scanned markets accepted. Consider tightening filters."),
    }
    rig_label, rig_color, rig_tip = rigidity_map.get(
        perm["rigidity"], ("?", "#5a6380", ""))
    st.markdown(
        f'<div style="margin-top:12px;padding:10px 14px;background:#161929;border-radius:8px;'
        f'border-left:3px solid {rig_color}">'
        f'<span style="font-weight:700;color:{rig_color}">{rig_label}</span>'
        f' — {rig_tip}</div>',
        unsafe_allow_html=True,
    )

    # Rejection reasons bar
    rr = perm.get("rejection_reasons", {})
    if rr:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        rr_order = ["price", "timing", "liquidity", "spread", "depth", "ambiguity", "score", "other"]
        rr_labels = {"price": "Price OOR", "timing": "Timing", "liquidity": "Liquidity",
                     "spread": "Spread", "depth": "Depth", "ambiguity": "Ambiguity",
                     "score": "Score", "other": "Other"}
        y_lbl = [rr_labels.get(k, k) for k in rr_order if rr.get(k, 0) > 0]
        x_val = [rr[k] for k in rr_order if rr.get(k, 0) > 0]
        if x_val:
            fig_rr = go.Figure(go.Bar(
                x=x_val, y=y_lbl, orientation="h",
                marker_color="#ef4444", opacity=0.75,
                text=x_val, textposition="outside",
                textfont=dict(color="#9aa3b8", size=11),
            ))
            fig_rr.update_layout(
                **_plotly_dark(),
                title=dict(text="Rejection reasons (all cycles)", font=dict(color="#9aa3b8", size=12)),
                height=200 + len(y_lbl) * 22,
                xaxis_title=None, yaxis_title=None,
            )
            st.plotly_chart(fig_rr, use_container_width=True)

    st.markdown("---")

    # ── F) Conclusion ─────────────────────────────────────────────────────────
    st.markdown("### Validation Conclusion")
    conclusion = generate_conclusion(metrics)
    st.markdown(
        f'<div style="padding:16px 20px;background:#1a1d2e;border-radius:10px;'
        f'border-left:4px solid #4060f0;font-size:14px;color:#dde3f0;line-height:1.7">'
        f'{conclusion}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── G) Export ─────────────────────────────────────────────────────────────
    st.markdown("### Export")
    exp_l, exp_r = st.columns(2)
    with exp_l:
        if st.button("📥 Export All Reports & CSVs", use_container_width=True):
            with st.spinner("Generating exports…"):
                paths = export_csvs(run_id)
            st.success("Exported to /data/ folder:")
            for name, path in paths.items():
                st.markdown(f"- `{os.path.basename(path)}`")

    with exp_r:
        # Quick markdown download in browser
        from app.validation_report import generate_markdown_report as _gen_md
        md_content = _gen_md(run_id)
        st.download_button(
            "📄 Download Report (Markdown)",
            data=md_content,
            file_name=f"validation_report_run{run_id}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    st.markdown(
        '<div style="font-size:12px;color:#5a6380;padding:10px 14px;background:#161929;'
        'border-radius:8px;border-left:3px solid #f59e0b;margin-top:8px">'
        "⚠️ Paper-trading only. No real money is involved. "
        "Outcomes are simulated. This is for research purposes only."
        "</div>",
        unsafe_allow_html=True,
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

def build_sidebar() -> tuple:
    """
    Builds sidebar. Returns (page_name, filters_dict, use_real_data).
    """
    with st.sidebar:
        st.markdown("## 📈 Polymarket Bot")
        st.markdown('<div style="font-size:11px;color:#5a6380;margin-top:-8px">Decision Dashboard</div>', unsafe_allow_html=True)
        st.markdown("---")

        # Data source toggle
        use_real = st.toggle(
            "Use live Polymarket data",
            value=_cfg.USE_REAL_DATA,
            help="ON = live API (sample fallback on error). OFF = built-in sample data.",
        )
        if use_real != _cfg.USE_REAL_DATA:
            _cfg.USE_REAL_DATA = use_real
            import app.scanner as _s
            _s.USE_REAL_DATA = use_real

        src_cls = "live" if use_real else "sample"
        src_txt = "Live data" if use_real else "Sample data"
        st.markdown(
            f'<div class="badge badge-{src_cls}" style="margin-bottom:8px">{src_txt}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("---")

        # Navigation
        st.markdown(
            '<div style="font-size:11px;font-weight:600;color:#8892b0;'
            'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">'
            'Navigate</div>',
            unsafe_allow_html=True,
        )
        page = st.radio(
            "Page",
            ["📊 Overview", "🏆 Top Opportunities", "👀 Watchlist",
             "❌ Rejected Markets", "📋 Permutation Report",
             "💰 Bankroll Simulator", "📍 Live Positions",
             "🎓 What Winners Look Like", "🧪 48h Validation"],
            label_visibility="collapsed",
            key="nav",
        )

        st.markdown("---")

        # Scan buttons
        if st.button("🔍 Scan Markets", use_container_width=True):
            with st.spinner("Fetching and analysing markets…"):
                _do_scan(use_real)
            st.success("Scan complete!")
            st.rerun()

        if st.button("🔄 Refresh Rankings", use_container_width=True):
            all_m = get_all_markets()
            if all_m:
                with st.spinner("Re-scoring…"):
                    run_pipeline([m for m in all_m])  # re-score keeps existing raw_data
                st.success("Rankings refreshed.")
                st.rerun()
            else:
                st.info("No data — run a scan first.")

        st.markdown("---")

        # Smart filters
        with st.expander("🔧 Smart Filters", expanded=False):
            min_score = st.slider("Min Score", 0, 100, 0, 5)
            tiers     = st.multiselect("Tier", ["A", "B", "C"], default=[])
            price_lo, price_hi = st.slider("Price Range", 0.80, 1.0, (0.80, 1.0), 0.01)
            max_hours = st.slider("Max Hours to Close", 1, 24, 24, 1)
            min_liq   = st.number_input("Min Liquidity ($)", 0, 1_000_000, 0, 1_000)
            max_spread = st.slider("Max Spread", 0.005, 0.020, 0.020, 0.001, format="%.3f")
            keyword   = st.text_input("Search keyword", placeholder="e.g. Bitcoin, Fed, election")
            accepted_only = st.checkbox("Accepted markets only", value=True)
            show_wl   = st.checkbox("Include my watchlist", value=False)

        filters = {
            "min_score":       min_score,
            "tiers":           tiers,
            "price_range":     (price_lo, price_hi),
            "max_hours":       max_hours,
            "min_liquidity":   int(min_liq),
            "max_spread":      max_spread,
            "keyword":         keyword.strip() if keyword else "",
            "accepted_only":   accepted_only,
            "show_watchlist_too": show_wl,
        }

        st.markdown("---")

        # Last scan info
        info = get_last_scan_info()
        if info:
            ts = info.get("scanned_at", "")
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = dt.strftime("%b %d %H:%M UTC")
            except Exception:
                pass
            sc  = info.get("source", "?")
            cls = "live" if sc == "live" else "sample"
            st.markdown(
                f'<div style="font-size:11px;color:#5a6380">'
                f'Last scan: <strong style="color:#8899aa">{ts}</strong><br>'
                f'Source: <span class="badge badge-{cls}">{sc}</span><br>'
                f'Accepted: <strong style="color:#22c55e">{info.get("total_accepted",0)}</strong>'
                f' / {info.get("total_fetched",0)}'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="font-size:12px;color:#5a6380">No scan yet.<br>Click <b>Scan Markets</b>.</div>',
                unsafe_allow_html=True,
            )

    return page, filters, use_real


# ── Main ──────────────────────────────────────────────────────────────────────

init_db()
page, filters, _ = build_sidebar()

all_markets = _load_markets()
stats       = _compute_stats(all_markets)

if page == "📊 Overview":
    page_overview(all_markets, stats)
elif page == "🏆 Top Opportunities":
    page_opportunities(all_markets, stats, filters)
elif page == "👀 Watchlist":
    page_watchlist(all_markets)
elif page == "❌ Rejected Markets":
    page_rejected(all_markets)
elif page == "📋 Permutation Report":
    page_permutation_report(all_markets)
elif page == "💰 Bankroll Simulator":
    page_bankroll_simulator(all_markets)
elif page == "📍 Live Positions":
    page_live_positions()
elif page == "🎓 What Winners Look Like":
    page_what_winners_look_like()
elif page == "🧪 48h Validation":
    page_validation()
