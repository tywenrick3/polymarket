"""Local SQLite cache for Polymarket price series and event metadata.

Sits between the API layer and commands. Accumulates historical data
beyond the CLOB's 28-day window — each fetch adds new points, old ones
persist. TTL controls re-fetch frequency, not data retention.

Database lives at ~/.polymarket/data.db (WAL mode for concurrent access).
"""

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".polymarket" / "data.db"

# TTL defaults (seconds)
TTL_EVENTS_LIST = 300        # 5 min — event rankings shift slowly
TTL_EVENT_DETAIL = 300       # 5 min
TTL_SEARCH = 600             # 10 min
TTL_PRICE_SERIES = {
    "1d": 300,               # 5 min — dashboard deltas need freshness
    "1w": 600,               # 10 min — recommend strategies
    "max": 1800,             # 30 min — backtest deep history
}

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price_points (
    token_id    TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    price       REAL    NOT NULL,
    PRIMARY KEY (token_id, timestamp)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS event_snapshots (
    event_id    TEXT    NOT NULL,
    fetched_at  INTEGER NOT NULL,
    slug        TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    volume      REAL    NOT NULL,
    volume_24hr REAL    NOT NULL,
    liquidity   REAL    NOT NULL,
    end_date    TEXT    NOT NULL DEFAULT '',
    raw_json    TEXT    NOT NULL,
    PRIMARY KEY (event_id, fetched_at)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_event_slug
    ON event_snapshots(slug, fetched_at DESC);

CREATE TABLE IF NOT EXISTS fetch_log (
    source_key  TEXT    PRIMARY KEY NOT NULL,
    fetched_at  INTEGER NOT NULL,
    ttl_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS external_data (
    source      TEXT    NOT NULL,
    series_id   TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    value       REAL    NOT NULL,
    metadata    TEXT    NOT NULL DEFAULT '{}',
    PRIMARY KEY (source, series_id, timestamp)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  INTEGER NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """Open (or create) the database with WAL mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, int(time.time())),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fetch log (TTL tracking)
# ---------------------------------------------------------------------------

def is_fresh(conn: sqlite3.Connection, source_key: str) -> bool:
    """True if source_key was fetched within its TTL."""
    row = conn.execute(
        "SELECT fetched_at, ttl_seconds FROM fetch_log WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if row is None:
        return False
    fetched_at, ttl = row
    return (time.time() - fetched_at) < ttl


def mark_fetched(conn: sqlite3.Connection, source_key: str, ttl: int) -> None:
    """Record that source_key was just fetched."""
    conn.execute(
        "INSERT OR REPLACE INTO fetch_log (source_key, fetched_at, ttl_seconds) "
        "VALUES (?, ?, ?)",
        (source_key, int(time.time()), ttl),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Price series cache
# ---------------------------------------------------------------------------

def _series_key(token_id: str, interval: str) -> str:
    return f"clob:series:{token_id}:{interval}"


def get_cached_series(
    conn: sqlite3.Connection,
    token_id: str,
    interval: str,
) -> list[dict] | None:
    """Return cached price series if fresh, else None."""
    key = _series_key(token_id, interval)
    if not is_fresh(conn, key):
        return None

    rows = conn.execute(
        "SELECT timestamp, price FROM price_points "
        "WHERE token_id = ? ORDER BY timestamp ASC",
        (token_id,),
    ).fetchall()

    if not rows:
        return None

    return [{"t": t, "p": p} for t, p in rows]


def store_series(
    conn: sqlite3.Connection,
    token_id: str,
    interval: str,
    points: list[dict],
) -> None:
    """Merge new price points into the cache (INSERT OR IGNORE for dedup)."""
    if not points:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO price_points (token_id, timestamp, price) "
        "VALUES (?, ?, ?)",
        [(token_id, p["t"], p["p"]) for p in points],
    )
    ttl = TTL_PRICE_SERIES.get(interval, 600)
    mark_fetched(conn, _series_key(token_id, interval), ttl)


def get_cached_batch_series(
    conn: sqlite3.Connection,
    token_ids: list[str],
    interval: str,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Check cache for multiple tokens.

    Returns (cached_results, missing_token_ids).
    """
    cached: dict[str, list[dict]] = {}
    missing: list[str] = []

    for tid in token_ids:
        series = get_cached_series(conn, tid, interval)
        if series is not None:
            cached[tid] = series
        else:
            missing.append(tid)

    return cached, missing


def get_full_history(
    conn: sqlite3.Connection,
    token_id: str,
    since: int | None = None,
) -> list[dict]:
    """Return ALL cached price points for a token, ignoring TTL.

    Used by backtest to access accumulated data beyond the CLOB window.
    """
    if since is not None:
        rows = conn.execute(
            "SELECT timestamp, price FROM price_points "
            "WHERE token_id = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (token_id, since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT timestamp, price FROM price_points "
            "WHERE token_id = ? ORDER BY timestamp ASC",
            (token_id,),
        ).fetchall()

    return [{"t": t, "p": p} for t, p in rows]


# ---------------------------------------------------------------------------
# Event metadata cache
# ---------------------------------------------------------------------------

def _events_key(source_key: str) -> str:
    return source_key


def get_cached_events(
    conn: sqlite3.Connection,
    source_key: str,
) -> list[dict] | None:
    """Return cached event list if fresh, else None.

    Returns raw JSON dicts (caller passes through _parse_event).
    """
    if not is_fresh(conn, source_key):
        return None

    # Get the event_ids associated with this source_key from the fetch_log timestamp
    row = conn.execute(
        "SELECT fetched_at FROM fetch_log WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if row is None:
        return None

    fetched_at = row[0]

    rows = conn.execute(
        "SELECT raw_json FROM event_snapshots WHERE fetched_at = ? "
        "ORDER BY volume_24hr DESC",
        (fetched_at,),
    ).fetchall()

    if not rows:
        return None

    return [json.loads(r[0]) for r in rows]


def store_events(
    conn: sqlite3.Connection,
    source_key: str,
    events_raw: list[dict],
    ttl: int,
) -> None:
    """Store event snapshots and update the fetch log."""
    now = int(time.time())

    for raw in events_raw:
        conn.execute(
            "INSERT OR IGNORE INTO event_snapshots "
            "(event_id, fetched_at, slug, title, volume, volume_24hr, "
            "liquidity, end_date, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                raw.get("id", ""),
                now,
                raw.get("slug", ""),
                raw.get("title", ""),
                float(raw.get("volume", 0) or 0),
                float(raw.get("volume24hr", 0) or 0),
                float(raw.get("liquidity", 0) or 0),
                raw.get("endDate", ""),
                json.dumps(raw),
            ),
        )

    mark_fetched(conn, source_key, ttl)


def get_cached_event_by_slug(
    conn: sqlite3.Connection,
    slug: str,
) -> dict | None:
    """Return the most recent cached snapshot for a slug, if fresh."""
    key = f"gamma:event:{slug}"
    if not is_fresh(conn, key):
        return None

    row = conn.execute(
        "SELECT raw_json FROM event_snapshots "
        "WHERE slug = ? ORDER BY fetched_at DESC LIMIT 1",
        (slug,),
    ).fetchone()

    if row is None:
        return None

    return json.loads(row[0])


def store_event(
    conn: sqlite3.Connection,
    slug: str,
    event_raw: dict,
    ttl: int,
) -> None:
    """Store a single event snapshot."""
    store_events(conn, f"gamma:event:{slug}", [event_raw], ttl)


# ---------------------------------------------------------------------------
# External data (weather, CPI, etc.)
# ---------------------------------------------------------------------------

def store_external(
    conn: sqlite3.Connection,
    source: str,
    series_id: str,
    points: list[dict],
) -> None:
    """Store external data points."""
    conn.executemany(
        "INSERT OR IGNORE INTO external_data "
        "(source, series_id, timestamp, value, metadata) VALUES (?, ?, ?, ?, ?)",
        [
            (
                source,
                series_id,
                p["timestamp"],
                p["value"],
                json.dumps(p.get("metadata", {})),
            )
            for p in points
        ],
    )
    conn.commit()


def get_external(
    conn: sqlite3.Connection,
    source: str,
    series_id: str,
    since: int | None = None,
    until: int | None = None,
) -> list[dict]:
    """Query external data by source, series, and time range."""
    query = "SELECT timestamp, value, metadata FROM external_data WHERE source = ? AND series_id = ?"
    params: list = [source, series_id]

    if since is not None:
        query += " AND timestamp >= ?"
        params.append(since)
    if until is not None:
        query += " AND timestamp <= ?"
        params.append(until)

    query += " ORDER BY timestamp ASC"

    rows = conn.execute(query, params).fetchall()
    return [
        {"timestamp": t, "value": v, "metadata": json.loads(m)}
        for t, v, m in rows
    ]


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def cache_stats(conn: sqlite3.Connection) -> dict:
    """Return cache statistics."""
    price_count = conn.execute("SELECT COUNT(*) FROM price_points").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM event_snapshots").fetchone()[0]
    external_count = conn.execute("SELECT COUNT(*) FROM external_data").fetchone()[0]

    # Count distinct tokens
    token_count = conn.execute(
        "SELECT COUNT(DISTINCT token_id) FROM price_points"
    ).fetchone()[0]

    # Oldest and newest price points
    oldest = conn.execute(
        "SELECT MIN(timestamp) FROM price_points"
    ).fetchone()[0]
    newest = conn.execute(
        "SELECT MAX(timestamp) FROM price_points"
    ).fetchone()[0]

    # DB file size
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    return {
        "db_path": str(DB_PATH),
        "db_size_bytes": db_size,
        "price_points": price_count,
        "tokens_tracked": token_count,
        "event_snapshots": event_count,
        "external_points": external_count,
        "oldest_timestamp": oldest,
        "newest_timestamp": newest,
    }


def clear_cache(
    conn: sqlite3.Connection,
    series: bool = False,
    events: bool = False,
    external: bool = False,
    all_data: bool = False,
) -> None:
    """Delete cached data."""
    if all_data:
        series = events = external = True

    if series:
        conn.execute("DELETE FROM price_points")
        conn.execute("DELETE FROM fetch_log WHERE source_key LIKE 'clob:%'")
    if events:
        conn.execute("DELETE FROM event_snapshots")
        conn.execute("DELETE FROM fetch_log WHERE source_key LIKE 'gamma:%'")
    if external:
        conn.execute("DELETE FROM external_data")

    conn.commit()
