"""Unit tests for the SQLite cache module.

Uses in-memory databases — no disk I/O, no network calls.
"""

import json
import sqlite3
import time

import pytest

from polymarket_cli.cache import (
    ensure_schema,
    is_fresh,
    mark_fetched,
    store_series,
    get_cached_series,
    get_cached_batch_series,
    get_full_history,
    store_events,
    get_cached_events,
    store_event,
    get_cached_event_by_slug,
    store_external,
    get_external,
    cache_stats,
    clear_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory SQLite connection with schema applied."""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode=WAL")
    ensure_schema(c)
    yield c
    c.close()


def _make_points(n: int = 24, start_t: int = 1_700_000_000, start_p: float = 0.50, slope: float = 0.01) -> list[dict]:
    return [{"t": start_t + i * 3600, "p": round(start_p + i * slope, 4)} for i in range(n)]


def _make_event_raw(event_id: str = "evt-1", slug: str = "test-event", title: str = "Test Event") -> dict:
    return {
        "id": event_id,
        "slug": slug,
        "title": title,
        "volume": 10_000_000,
        "volume24hr": 500_000,
        "liquidity": 1_000_000,
        "endDate": "2027-01-01T00:00:00Z",
        "markets": [
            {
                "id": "mkt-1",
                "question": "Will it happen?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.60", "0.40"]',
                "clobTokenIds": '["tok-1", "tok-2"]',
            }
        ],
    }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_ensure_schema_idempotent(self, conn):
        ensure_schema(conn)
        ensure_schema(conn)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 1

    def test_tables_exist(self, conn):
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "price_points" in tables
        assert "event_snapshots" in tables
        assert "fetch_log" in tables
        assert "external_data" in tables
        assert "schema_version" in tables


# ---------------------------------------------------------------------------
# Fetch log
# ---------------------------------------------------------------------------

class TestFetchLog:
    def test_not_fresh_when_missing(self, conn):
        assert not is_fresh(conn, "nonexistent:key")

    def test_fresh_within_ttl(self, conn):
        mark_fetched(conn, "test:key", ttl=300)
        assert is_fresh(conn, "test:key")

    def test_stale_after_ttl(self, conn):
        conn.execute(
            "INSERT OR REPLACE INTO fetch_log (source_key, fetched_at, ttl_seconds) "
            "VALUES (?, ?, ?)",
            ("test:old", int(time.time()) - 600, 300),
        )
        conn.commit()
        assert not is_fresh(conn, "test:old")


# ---------------------------------------------------------------------------
# Price series
# ---------------------------------------------------------------------------

class TestPriceSeries:
    def test_store_and_retrieve(self, conn):
        points = _make_points(24)
        store_series(conn, "tok-1", "1w", points)
        cached = get_cached_series(conn, "tok-1", "1w")
        assert cached is not None
        assert len(cached) == 24
        assert cached[0]["t"] == points[0]["t"]
        assert cached[-1]["p"] == points[-1]["p"]

    def test_returns_none_when_stale(self, conn):
        points = _make_points(24)
        store_series(conn, "tok-1", "1w", points)
        # Manually expire the entry
        conn.execute(
            "UPDATE fetch_log SET fetched_at = fetched_at - 9999 WHERE source_key LIKE '%tok-1%'"
        )
        conn.commit()
        assert get_cached_series(conn, "tok-1", "1w") is None

    def test_returns_none_when_missing(self, conn):
        assert get_cached_series(conn, "nonexistent", "1w") is None

    def test_deduplication(self, conn):
        points = _make_points(24)
        store_series(conn, "tok-1", "1w", points)
        # Store overlapping data — should not duplicate
        store_series(conn, "tok-1", "1w", points)
        count = conn.execute(
            "SELECT COUNT(*) FROM price_points WHERE token_id = 'tok-1'"
        ).fetchone()[0]
        assert count == 24

    def test_accumulation(self, conn):
        """New points merge with existing, extending history."""
        batch1 = _make_points(24, start_t=1_700_000_000)
        store_series(conn, "tok-1", "max", batch1)

        # Second batch overlaps by 12 hours and extends by 12 hours
        batch2 = _make_points(24, start_t=1_700_000_000 + 12 * 3600)
        store_series(conn, "tok-1", "max", batch2)

        count = conn.execute(
            "SELECT COUNT(*) FROM price_points WHERE token_id = 'tok-1'"
        ).fetchone()[0]
        # 24 original + 12 new = 36
        assert count == 36

    def test_empty_points_ignored(self, conn):
        store_series(conn, "tok-1", "1w", [])
        count = conn.execute(
            "SELECT COUNT(*) FROM price_points WHERE token_id = 'tok-1'"
        ).fetchone()[0]
        assert count == 0


class TestBatchSeries:
    def test_all_cached(self, conn):
        for i in range(3):
            store_series(conn, f"tok-{i}", "1w", _make_points(24))

        cached, missing = get_cached_batch_series(conn, ["tok-0", "tok-1", "tok-2"], "1w")
        assert len(cached) == 3
        assert len(missing) == 0

    def test_partial_cache(self, conn):
        store_series(conn, "tok-0", "1w", _make_points(24))

        cached, missing = get_cached_batch_series(conn, ["tok-0", "tok-1"], "1w")
        assert len(cached) == 1
        assert "tok-0" in cached
        assert missing == ["tok-1"]

    def test_none_cached(self, conn):
        cached, missing = get_cached_batch_series(conn, ["tok-0", "tok-1"], "1w")
        assert len(cached) == 0
        assert len(missing) == 2


class TestFullHistory:
    def test_returns_all_points(self, conn):
        points = _make_points(48)
        store_series(conn, "tok-1", "max", points)
        history = get_full_history(conn, "tok-1")
        assert len(history) == 48

    def test_since_filter(self, conn):
        points = _make_points(48, start_t=1_700_000_000)
        store_series(conn, "tok-1", "max", points)
        midpoint = 1_700_000_000 + 24 * 3600
        history = get_full_history(conn, "tok-1", since=midpoint)
        assert len(history) == 24
        assert history[0]["t"] >= midpoint

    def test_empty_for_unknown_token(self, conn):
        assert get_full_history(conn, "nonexistent") == []


# ---------------------------------------------------------------------------
# Event metadata
# ---------------------------------------------------------------------------

class TestEventCache:
    def test_store_and_retrieve_list(self, conn):
        events = [_make_event_raw("e1", "slug-1"), _make_event_raw("e2", "slug-2")]
        store_events(conn, "gamma:top_events:volume_24hr:10", events, ttl=300)
        cached = get_cached_events(conn, "gamma:top_events:volume_24hr:10")
        assert cached is not None
        assert len(cached) == 2

    def test_returns_none_when_stale(self, conn):
        events = [_make_event_raw()]
        store_events(conn, "gamma:top:test", events, ttl=300)
        conn.execute(
            "UPDATE fetch_log SET fetched_at = fetched_at - 9999 WHERE source_key = 'gamma:top:test'"
        )
        conn.commit()
        assert get_cached_events(conn, "gamma:top:test") is None

    def test_returns_none_when_missing(self, conn):
        assert get_cached_events(conn, "gamma:nonexistent") is None


class TestEventBySlug:
    def test_store_and_retrieve(self, conn):
        raw = _make_event_raw(slug="test-slug")
        store_event(conn, "test-slug", raw, ttl=300)
        cached = get_cached_event_by_slug(conn, "test-slug")
        assert cached is not None
        assert cached["slug"] == "test-slug"

    def test_returns_none_when_missing(self, conn):
        assert get_cached_event_by_slug(conn, "nonexistent") is None

    def test_returns_latest_snapshot(self, conn):
        """Newer snapshots (different timestamps) should win."""
        raw1 = _make_event_raw(slug="evolving", title="Version 1")
        now = int(time.time())

        # Insert first snapshot directly with explicit timestamp
        conn.execute(
            "INSERT INTO event_snapshots "
            "(event_id, fetched_at, slug, title, volume, volume_24hr, liquidity, end_date, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("evt-1", now - 10, "evolving", "Version 1", 0, 0, 0, "", json.dumps(raw1)),
        )
        # Insert second snapshot 10 seconds later
        raw2 = _make_event_raw(slug="evolving", title="Version 2")
        conn.execute(
            "INSERT INTO event_snapshots "
            "(event_id, fetched_at, slug, title, volume, volume_24hr, liquidity, end_date, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("evt-1", now, "evolving", "Version 2", 0, 0, 0, "", json.dumps(raw2)),
        )
        mark_fetched(conn, "gamma:event:evolving", 300)

        cached = get_cached_event_by_slug(conn, "evolving")
        assert cached is not None
        assert cached["title"] == "Version 2"


# ---------------------------------------------------------------------------
# External data
# ---------------------------------------------------------------------------

class TestExternalData:
    def test_store_and_retrieve(self, conn):
        points = [
            {"timestamp": 1700000000, "value": 72.5, "metadata": {"unit": "F"}},
            {"timestamp": 1700003600, "value": 74.1, "metadata": {"unit": "F"}},
        ]
        store_external(conn, "noaa_weather", "NYC_temp_max", points)
        result = get_external(conn, "noaa_weather", "NYC_temp_max")
        assert len(result) == 2
        assert result[0]["value"] == 72.5
        assert result[0]["metadata"]["unit"] == "F"

    def test_time_range_filter(self, conn):
        points = [{"timestamp": t, "value": float(t)} for t in range(100, 200)]
        store_external(conn, "test", "series-1", points)
        result = get_external(conn, "test", "series-1", since=150, until=160)
        assert len(result) == 11  # 150..160 inclusive
        assert result[0]["timestamp"] == 150

    def test_dedup(self, conn):
        points = [{"timestamp": 100, "value": 1.0}]
        store_external(conn, "test", "s", points)
        store_external(conn, "test", "s", points)
        count = conn.execute(
            "SELECT COUNT(*) FROM external_data WHERE source='test'"
        ).fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

class TestCacheManagement:
    def test_stats_empty(self, conn):
        # Override DB_PATH check since we're in-memory
        s = cache_stats(conn)
        assert s["price_points"] == 0
        assert s["tokens_tracked"] == 0

    def test_stats_with_data(self, conn):
        store_series(conn, "tok-1", "1w", _make_points(24))
        store_series(conn, "tok-2", "1w", _make_points(12))
        s = cache_stats(conn)
        assert s["price_points"] == 36
        assert s["tokens_tracked"] == 2

    def test_clear_series(self, conn):
        store_series(conn, "tok-1", "1w", _make_points(24))
        store_events(conn, "gamma:top", [_make_event_raw()], 300)
        clear_cache(conn, series=True)
        assert conn.execute("SELECT COUNT(*) FROM price_points").fetchone()[0] == 0
        # Events should still be there
        assert conn.execute("SELECT COUNT(*) FROM event_snapshots").fetchone()[0] > 0

    def test_clear_events(self, conn):
        store_series(conn, "tok-1", "1w", _make_points(24))
        store_events(conn, "gamma:top", [_make_event_raw()], 300)
        clear_cache(conn, events=True)
        assert conn.execute("SELECT COUNT(*) FROM event_snapshots").fetchone()[0] == 0
        # Price points should still be there
        assert conn.execute("SELECT COUNT(*) FROM price_points").fetchone()[0] > 0

    def test_clear_all(self, conn):
        store_series(conn, "tok-1", "1w", _make_points(24))
        store_events(conn, "gamma:top", [_make_event_raw()], 300)
        store_external(conn, "test", "s", [{"timestamp": 1, "value": 1.0}])
        clear_cache(conn, all_data=True)
        assert conn.execute("SELECT COUNT(*) FROM price_points").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM event_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM external_data").fetchone()[0] == 0
