"""SQLite-backed state, counters and event log.

Upstream kept everything in memory, so a restart re-sent messages and lost all
visibility.  This module is deliberately dependency-free (stdlib sqlite3) and
safe to share between the bot thread and the dashboard's HTTP threads.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state (
    source_key      TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_map (
    source_key        TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    dest_chat_id      INTEGER NOT NULL,
    dest_message_id   INTEGER NOT NULL,
    edited            INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (source_key, source_message_id, dest_chat_id, dest_message_id)
);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    level  TEXT NOT NULL,
    kind   TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id DESC);
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS applied_edits (
    source_key        TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    edit_ts           TEXT NOT NULL,
    PRIMARY KEY (source_key, source_message_id, edit_ts)
);
CREATE TABLE IF NOT EXISTS applied_edit_targets (
    source_key        TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    edit_ts           TEXT NOT NULL,
    dest_chat_id      INTEGER NOT NULL,
    dest_message_id   INTEGER NOT NULL,
    PRIMARY KEY (source_key, source_message_id, edit_ts, dest_chat_id, dest_message_id)
);
"""

COUNTERS = ("polls", "mirrored", "filtered", "edited", "failed", "retries")


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Store:
    """Small persistent store; every public method is thread-safe."""

    def __init__(self, path: str = "state/duplicator.db"):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            for name in COUNTERS:
                self._conn.execute("INSERT OR IGNORE INTO counters(name, value) VALUES (?, 0)", (name,))
            self._conn.commit()
        self._closed = False

    # ------------------------------------------------------------------ events
    def event(self, kind: str, message: str, level: str = "INFO") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(ts, level, kind, message) VALUES (?, ?, ?, ?)",
                (time.time(), level, kind, str(message)[:1000]),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def events(self, limit: int = 100, since_id: int = 0) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id, limit)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "time": _utc(r["ts"]),
                "level": r["level"],
                "kind": r["kind"],
                "message": r["message"],
            }
            for r in reversed(rows)
        ]

    # ---------------------------------------------------------------- counters
    def increment(self, name: str, by: int = 1) -> int:
        if name not in COUNTERS:
            raise ValueError(f"unknown counter: {name}")
        with self._lock:
            self._conn.execute("UPDATE counters SET value = value + ? WHERE name = ?", (by, name))
            self._conn.commit()
            return self._conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            counters = {r["name"]: r["value"] for r in self._conn.execute("SELECT * FROM counters")}
            mapped = self._conn.execute("SELECT COUNT(*) FROM message_map").fetchone()[0]
            state_rows = self._conn.execute(
                "SELECT source_key, last_message_id, updated_at FROM source_state ORDER BY source_key"
            ).fetchall()
            last = self._conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]
        return {
            "counters": counters,
            "message_map_size": mapped,
            "sources": [
                {
                    "key": r["source_key"],
                    "last_message_id": r["last_message_id"],
                    "updated_at": r["updated_at"],
                }
                for r in state_rows
            ],
            "last_event_at": _utc(last) if last else None,
        }

    # ----------------------------------------------------------- source state
    def get_last_id(self, source_key: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_message_id FROM source_state WHERE source_key = ?", (source_key,)
            ).fetchone()
        return int(row[0]) if row else 0

    def set_last_id(self, source_key: str, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO source_state(source_key, last_message_id, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(source_key) DO UPDATE SET "
                "last_message_id = MAX(last_message_id, excluded.last_message_id), updated_at = excluded.updated_at",
                (source_key, int(message_id), _utc(time.time())),
            )
            self._conn.commit()

    # ------------------------------------------------------------ message map
    def record_send(
        self, source_key: str, source_message_id: int, dest_chat_id: int, dest_message_id: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO message_map"
                "(source_key, source_message_id, dest_chat_id, dest_message_id, edited, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (source_key, source_message_id, dest_chat_id, dest_message_id, _utc(time.time())),
            )
            self._conn.commit()

    def destinations_for(self, source_key: str, source_message_id: int) -> List[Dict[str, int]]:
        """All known copies of a source message (for edit mirroring)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT dest_chat_id, dest_message_id FROM message_map "
                "WHERE source_key = ? AND source_message_id = ?",
                (source_key, source_message_id),
            ).fetchall()
        return [{"dest_chat_id": r[0], "dest_message_id": r[1]} for r in rows]

    def pending_edits(self, source_key: str, source_message_id: int) -> List[Dict[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT dest_chat_id, dest_message_id FROM message_map "
                "WHERE source_key = ? AND source_message_id = ? AND edited = 0",
                (source_key, source_message_id),
            ).fetchall()
        return [{"dest_chat_id": r[0], "dest_message_id": r[1]} for r in rows]

    def mark_edited(
        self, source_key: str, source_message_id: int, dest_chat_id: int, dest_message_id: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE message_map SET edited = 1 WHERE source_key = ? AND source_message_id = ? "
                "AND dest_chat_id = ? AND dest_message_id = ?",
                (source_key, source_message_id, dest_chat_id, dest_message_id),
            )
            self._conn.commit()

    # ---------------------------------------------------------------- edits
    def is_edit_applied(self, source_key: str, source_message_id: int, edit_ts: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM applied_edits WHERE source_key = ? AND source_message_id = ? AND edit_ts = ?",
                (source_key, source_message_id, edit_ts),
            ).fetchone()
        return row is not None

    def mark_edit_applied(self, source_key: str, source_message_id: int, edit_ts: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO applied_edits(source_key, source_message_id, edit_ts) VALUES (?, ?, ?)",
                (source_key, source_message_id, edit_ts),
            )
            self._conn.commit()

    def is_target_edit_applied(self, source_key, source_message_id, edit_ts, dest_chat_id, dest_message_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM applied_edit_targets WHERE source_key = ? AND source_message_id = ? "
                "AND edit_ts = ? AND dest_chat_id = ? AND dest_message_id = ?",
                (source_key, source_message_id, edit_ts, dest_chat_id, dest_message_id),
            ).fetchone()
        return row is not None

    def mark_target_edit_applied(self, source_key, source_message_id, edit_ts, dest_chat_id, dest_message_id):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO applied_edit_targets "
                "(source_key, source_message_id, edit_ts, dest_chat_id, dest_message_id) VALUES (?, ?, ?, ?, ?)",
                (source_key, source_message_id, edit_ts, dest_chat_id, dest_message_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ admin
    def reset(self) -> None:
        """Forget positions/counters/mappings but keep the event history."""
        with self._lock:
            self._conn.execute("DELETE FROM source_state")
            self._conn.execute("DELETE FROM message_map")
            self._conn.execute("DELETE FROM applied_edits")
            self._conn.execute("DELETE FROM applied_edit_targets")
            self._conn.execute("UPDATE counters SET value = 0")
            self._conn.commit()
        self.event("system", "State reset: positions, counters and message map cleared", "WARNING")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()
