"""Durable message checkpoints and delivery records."""
import os
import sqlite3


class CheckpointStore:
    """Persist processed source IDs and successful per-destination deliveries."""

    def __init__(self, path=None):
        self.path = path or os.path.join("data", "checkpoints.sqlite3")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_checkpoints (
                source_id TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL DEFAULT 0
                    CHECK (last_message_id >= 0)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deliveries (
                source_id TEXT NOT NULL,
                source_message_id INTEGER NOT NULL,
                destination_id TEXT NOT NULL,
                destination_message_id INTEGER,
                PRIMARY KEY (source_id, source_message_id, destination_id)
            )
            """
        )
        self._connection.commit()

    def get(self, source_id):
        row = self._connection.execute(
            "SELECT last_message_id FROM source_checkpoints WHERE source_id = ?",
            (str(source_id),),
        ).fetchone()
        return int(row[0]) if row else 0

    def set(self, source_id, message_id):
        message_id = int(message_id)
        if message_id < 0:
            raise ValueError("message_id must be non-negative")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_checkpoints (source_id, last_message_id)
                VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_message_id = MAX(last_message_id, excluded.last_message_id)
                """,
                (str(source_id), message_id),
            )

    def was_delivered(self, source_id, message_id, destination_id):
        row = self._connection.execute(
            """
            SELECT 1 FROM deliveries
            WHERE source_id = ? AND source_message_id = ? AND destination_id = ?
            """,
            (str(source_id), int(message_id), str(destination_id)),
        ).fetchone()
        return row is not None

    def mark_delivered(self, source_id, message_id, destination_id, destination_message_id=None):
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO deliveries
                    (source_id, source_message_id, destination_id, destination_message_id)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(source_id),
                    int(message_id),
                    str(destination_id),
                    int(destination_message_id) if destination_message_id is not None else None,
                ),
            )

    def close(self):
        self._connection.close()
