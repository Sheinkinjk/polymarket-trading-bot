"""
SQLite database layer — stores scanned markets and watchlist.
"""
import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "markets.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS markets (
                id          TEXT PRIMARY KEY,
                question    TEXT NOT NULL,
                end_time    TEXT,
                yes_price   REAL,
                liquidity   REAL,
                volume_24h  REAL,
                spread      REAL,
                best_bid    REAL,
                best_ask    REAL,
                score       REAL,
                tier        TEXT,
                accepted    INTEGER DEFAULT 0,
                explanation TEXT,
                raw_data    TEXT,
                scanned_at  TEXT,
                in_watchlist INTEGER DEFAULT 0,
                band        TEXT,
                reject_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS scan_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at      TEXT,
                total_fetched   INTEGER,
                total_accepted  INTEGER,
                source          TEXT
            );

            CREATE TABLE IF NOT EXISTS validation_runs (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at                TEXT NOT NULL,
                ends_at                   TEXT NOT NULL,
                status                    TEXT DEFAULT 'running',
                starting_balance          REAL DEFAULT 10000,
                position_percent          REAL DEFAULT 5.0,
                max_open_positions        INTEGER DEFAULT 10,
                allow_tier_a              INTEGER DEFAULT 1,
                allow_tier_b              INTEGER DEFAULT 1,
                allow_tier_c              INTEGER DEFAULT 0,
                scan_interval_minutes     INTEGER DEFAULT 15,
                stop_new_entries_minutes  INTEGER DEFAULT 20,
                last_scan_at              TEXT
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL,
                market_id       TEXT NOT NULL,
                question        TEXT,
                end_time        TEXT,
                side            TEXT DEFAULT 'YES',
                score           REAL,
                tier            TEXT,
                band            TEXT,
                hours_at_entry  REAL,
                entry_at        TEXT NOT NULL,
                entry_price     REAL NOT NULL,
                notional_1pct   REAL,
                notional_2pct   REAL,
                notional_5pct   REAL,
                status          TEXT DEFAULT 'open',
                exit_at         TEXT,
                exit_price      REAL,
                pnl_1pct        REAL,
                pnl_2pct        REAL,
                pnl_5pct        REAL,
                reason_entered  TEXT,
                FOREIGN KEY (run_id) REFERENCES validation_runs(id)
            );

            CREATE TABLE IF NOT EXISTS validation_scans (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              INTEGER NOT NULL,
                scanned_at          TEXT NOT NULL,
                total_scanned       INTEGER DEFAULT 0,
                total_primary       INTEGER DEFAULT 0,
                total_secondary     INTEGER DEFAULT 0,
                total_watchlist     INTEGER DEFAULT 0,
                total_rejected      INTEGER DEFAULT 0,
                total_accepted      INTEGER DEFAULT 0,
                new_positions       INTEGER DEFAULT 0,
                settled_positions   INTEGER DEFAULT 0,
                rejection_reasons   TEXT,
                source              TEXT,
                FOREIGN KEY (run_id) REFERENCES validation_runs(id)
            );

            CREATE TABLE IF NOT EXISTS auto_paper_sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at       TEXT NOT NULL,
                mode             TEXT DEFAULT 'strict',
                top_limit        INTEGER DEFAULT 5,
                starting_balance REAL DEFAULT 10000.0,
                status           TEXT DEFAULT 'active',
                last_scan_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS auto_paper_trades (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          INTEGER NOT NULL,
                market_id           TEXT NOT NULL,
                question            TEXT,
                chosen_side         TEXT DEFAULT 'YES',
                entry_timestamp     TEXT NOT NULL,
                entry_price         REAL NOT NULL,
                fill_price          REAL,
                hours_at_entry      REAL,
                score               REAL,
                tier                TEXT,
                band                TEXT,
                confidence          TEXT,
                decision_summary    TEXT,
                notional_2pct       REAL DEFAULT 0,
                notional_3pct       REAL DEFAULT 0,
                notional_5pct       REAL DEFAULT 0,
                status              TEXT DEFAULT 'live',
                exit_timestamp      TEXT,
                exit_price          REAL,
                resolved_outcome    TEXT,
                pnl_2pct            REAL,
                pnl_3pct            REAL,
                pnl_5pct            REAL,
                entry_mode          TEXT DEFAULT 'strict',
                reason_skipped      TEXT,
                entry_liquidity     REAL,
                entry_spread        REAL,
                FOREIGN KEY (session_id) REFERENCES auto_paper_sessions(id)
            );
        """)
        # Migrate existing tables — safe if columns already exist
        for col, tbl in [
            ("band TEXT",            "markets"),
            ("reject_reason TEXT",   "markets"),
            ("entry_liquidity REAL", "auto_paper_trades"),
            ("entry_spread REAL",    "auto_paper_trades"),
            ("fill_price REAL",      "auto_paper_trades"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col}")
            except Exception:
                pass


def upsert_markets(markets: list[dict]) -> None:
    """Insert or replace market records."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        for m in markets:
            conn.execute("""
                INSERT INTO markets
                    (id, question, end_time, yes_price, liquidity, volume_24h,
                     spread, best_bid, best_ask, score, tier, accepted,
                     explanation, raw_data, scanned_at, band, reject_reason)
                VALUES
                    (:id, :question, :end_time, :yes_price, :liquidity, :volume_24h,
                     :spread, :best_bid, :best_ask, :score, :tier, :accepted,
                     :explanation, :raw_data, :scanned_at, :band, :reject_reason)
                ON CONFLICT(id) DO UPDATE SET
                    question      = excluded.question,
                    end_time      = excluded.end_time,
                    yes_price     = excluded.yes_price,
                    liquidity     = excluded.liquidity,
                    volume_24h    = excluded.volume_24h,
                    spread        = excluded.spread,
                    best_bid      = excluded.best_bid,
                    best_ask      = excluded.best_ask,
                    score         = excluded.score,
                    tier          = excluded.tier,
                    accepted      = excluded.accepted,
                    explanation   = excluded.explanation,
                    raw_data      = excluded.raw_data,
                    scanned_at    = excluded.scanned_at,
                    band          = excluded.band,
                    reject_reason = excluded.reject_reason
            """, {
                "id":            m["id"],
                "question":      m["question"],
                "end_time":      m.get("end_time"),
                "yes_price":     m.get("yes_price"),
                "liquidity":     m.get("liquidity"),
                "volume_24h":    m.get("volume_24h"),
                "spread":        m.get("spread"),
                "best_bid":      m.get("best_bid"),
                "best_ask":      m.get("best_ask"),
                "score":         m.get("score"),
                "tier":          m.get("tier"),
                "accepted":      1 if m.get("accepted") else 0,
                "explanation":   m.get("explanation"),
                "raw_data":      json.dumps(m.get("raw_data", {})),
                "scanned_at":    now,
                "band":          m.get("band", ""),
                "reject_reason": m.get("reject_reason", ""),
            })


def log_scan(total_fetched: int, total_accepted: int, source: str) -> None:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scan_history (scanned_at, total_fetched, total_accepted, source) "
            "VALUES (?, ?, ?, ?)",
            (now, total_fetched, total_accepted, source),
        )


def get_accepted_markets() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM markets WHERE accepted = 1 ORDER BY score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_rejected_markets() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM markets WHERE accepted = 0 ORDER BY score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_watchlist() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM markets WHERE in_watchlist = 1 ORDER BY score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def toggle_watchlist(market_id: str) -> bool:
    """Toggle watchlist flag. Returns new state (True = now on watchlist)."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT in_watchlist FROM markets WHERE id = ?", (market_id,)
        ).fetchone()
        if row is None:
            return False
        new_val = 0 if row["in_watchlist"] else 1
        conn.execute(
            "UPDATE markets SET in_watchlist = ? WHERE id = ?", (new_val, market_id)
        )
        return bool(new_val)


def get_last_scan_info() -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM scan_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_all_markets() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM markets ORDER BY score DESC"
        ).fetchall()
    return [dict(r) for r in rows]
