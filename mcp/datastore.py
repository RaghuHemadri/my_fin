"""SQLite store shared by the MCP cache proxy and the investment-debate scripts.

One database file holds:
- responses: cached MCP tool results with their fetch time and expiry
- calls: one row per proxied tool call (hit, miss, coalesced, bypass or error) with its latency
- inflight: short leases, so concurrent processes fetch the same result only once
- bars: daily and longer Yahoo bars parsed from history responses, kept as a queryable price history
- runs and stages: investment-debate runs and per-stage timings written by the runner

The default path is ~/.cache/my_fin/my_fin.sqlite3 (override with MY_FIN_DB). It stays off OneDrive and
off /mnt/c, because SQLite's WAL mode needs reliable file locking and shared memory. Standard library only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key TEXT PRIMARY KEY, server TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL, result TEXT NOT NULL,
    fetched_at REAL NOT NULL, expires_at REAL NOT NULL, bytes INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS responses_expiry ON responses(expires_at);
CREATE TABLE IF NOT EXISTS calls (
    ts REAL NOT NULL, server TEXT NOT NULL, tool TEXT NOT NULL, key TEXT, outcome TEXT NOT NULL,
    latency_ms REAL, bytes INTEGER, session TEXT);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE TABLE IF NOT EXISTS inflight (key TEXT PRIMARY KEY, owner TEXT NOT NULL, started_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, interval TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL,
    close REAL, volume REAL, source TEXT, fetched_at REAL NOT NULL, PRIMARY KEY (symbol, interval, date));
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, as_of TEXT NOT NULL, run_dir TEXT NOT NULL, mode TEXT,
    depth TEXT, settings TEXT, rating TEXT, status TEXT, runner TEXT, started_at REAL, finished_at REAL);
CREATE TABLE IF NOT EXISTS stages (
    run_id TEXT NOT NULL, stage TEXT NOT NULL, role TEXT, output TEXT, status TEXT, attempts INTEGER,
    model TEXT, effort TEXT, started_at REAL, finished_at REAL, PRIMARY KEY (run_id, stage));
"""

RUN_FIELDS = ("symbol", "as_of", "run_dir", "mode", "depth", "settings", "rating", "status", "runner",
              "started_at", "finished_at")
STAGE_FIELDS = ("role", "output", "status", "attempts", "model", "effort", "started_at", "finished_at")


def default_path() -> Path:
    if os.environ.get("MY_FIN_DB"):
        return Path(os.environ["MY_FIN_DB"]).expanduser()
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "my_fin" / "my_fin.sqlite3"


class Store:
    """Thread-safe wrapper around one SQLite connection in autocommit mode."""

    def __init__(self, path: str | Path | None = None, timeout_s: float = 10.0):
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=timeout_s, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self.db.execute(sql, tuple(params)).fetchall()]

    # Cached responses

    def get(self, key: str, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        rows = self._rows("SELECT result, fetched_at, expires_at FROM responses WHERE key = ? AND expires_at > ?",
                          (key, now))
        if not rows:
            return None
        row = rows[0]
        return {"result": json.loads(row["result"]), "fetched_at": row["fetched_at"], "expires_at": row["expires_at"]}

    def put(self, key: str, server: str, tool: str, args: str, result: dict, ttl_s: float,
            now: float | None = None) -> None:
        now = time.time() if now is None else now
        payload = json.dumps(result, ensure_ascii=False)
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (key, server, tool, args, payload, now, now + ttl_s, len(payload.encode())))

    def clear(self, server: str | None = None, tool: str | None = None) -> int:
        sql, params = "DELETE FROM responses WHERE 1 = 1", []
        if server:
            sql, params = sql + " AND server = ?", params + [server]
        if tool:
            sql, params = sql + " AND tool = ?", params + [tool]
        with self._lock:
            return self.db.execute(sql, params).rowcount

    def prune(self, now: float | None = None, keep_calls_days: float = 30) -> dict:
        """Delete expired responses, stale leases and call logs older than keep_calls_days."""
        now = time.time() if now is None else now
        with self._lock:
            expired = self.db.execute("DELETE FROM responses WHERE expires_at <= ?", (now,)).rowcount
            leases = self.db.execute("DELETE FROM inflight WHERE started_at < ?", (now - 3600,)).rowcount
            calls = self.db.execute("DELETE FROM calls WHERE ts < ?", (now - keep_calls_days * 86400,)).rowcount
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"expired_responses": expired, "stale_leases": leases, "old_calls": calls}

    # Cross-process single-flight

    def acquire(self, key: str, owner: str, lease_s: float = 60, now: float | None = None) -> bool:
        """Take the fetch lease for key. False while another owner holds a lease younger than lease_s."""
        now = time.time() if now is None else now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT owner, started_at FROM inflight WHERE key = ?", (key,)).fetchone()
                if row and row["owner"] != owner and row["started_at"] > now - lease_s:
                    return False
                self.db.execute("INSERT OR REPLACE INTO inflight VALUES (?, ?, ?)", (key, owner, now))
                return True
            finally:
                self.db.execute("COMMIT")

    def release(self, key: str, owner: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM inflight WHERE key = ? AND owner = ?", (key, owner))

    # Call log

    def log_call(self, server: str, tool: str, key: str | None, outcome: str, latency_ms: float | None = None,
                 size: int | None = None, session: str | None = None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self.db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (now, server, tool, key, outcome, latency_ms, size, session))

    def stats(self, since: float | None = None) -> list[dict]:
        since = 0 if since is None else since
        return self._rows(
            "SELECT server, tool, outcome, COUNT(*) AS calls, ROUND(AVG(latency_ms), 1) AS avg_ms, "
            "SUM(COALESCE(bytes, 0)) AS bytes FROM calls WHERE ts >= ? "
            "GROUP BY server, tool, outcome ORDER BY server, tool, outcome", (since,))

    # Bars

    def upsert_bars(self, symbol: str, interval: str, rows: list[dict], source: str,
                    now: float | None = None) -> int:
        now = time.time() if now is None else now
        values = [(symbol, interval, r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                   r.get("volume"), source, now) for r in rows if r.get("date")]
        with self._lock:
            self.db.executemany("INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        return len(values)

    def get_bars(self, symbol: str, interval: str = "1d", start: str | None = None,
                 end: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM bars WHERE symbol = ? AND interval = ?", [symbol, interval]
        if start:
            sql, params = sql + " AND date >= ?", params + [start]
        if end:
            sql, params = sql + " AND date <= ?", params + [end]
        return self._rows(sql + " ORDER BY date", params)

    # Runs and stages

    def record_run(self, run_id: str, **fields: Any) -> None:
        self._upsert("runs", {"run_id": run_id}, fields, RUN_FIELDS)

    def record_stage(self, run_id: str, stage: str, **fields: Any) -> None:
        self._upsert("stages", {"run_id": run_id, "stage": stage}, fields, STAGE_FIELDS)

    def _upsert(self, table: str, keys: dict, fields: dict, allowed: tuple[str, ...]) -> None:
        """Update the given fields of an existing row, or insert the row. SQLite checks NOT NULL columns
        before an ON CONFLICT clause applies, so a partial update cannot be written as one UPSERT."""
        unknown = set(fields) - set(allowed)
        if unknown:
            raise ValueError(f"unknown {table} fields: {sorted(unknown)}")
        values = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in fields.items()}
        where = " AND ".join(f"{k} = ?" for k in keys)
        with self._lock:
            if values:
                sets = ", ".join(f"{k} = ?" for k in values)
                updated = self.db.execute(f"UPDATE {table} SET {sets} WHERE {where}",
                                          [*values.values(), *keys.values()]).rowcount
            else:
                updated = len(self.db.execute(f"SELECT 1 FROM {table} WHERE {where}", list(keys.values())).fetchall())
            if not updated:
                columns = [*keys, *values]
                self.db.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                                [*keys.values(), *values.values()])

    def runs(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        if symbol:
            return self._rows("SELECT * FROM runs WHERE symbol = ? ORDER BY started_at DESC LIMIT ?", (symbol, limit))
        return self._rows("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))

    def stages(self, run_id: str) -> list[dict]:
        return self._rows("SELECT * FROM stages WHERE run_id = ? ORDER BY started_at", (run_id,))
