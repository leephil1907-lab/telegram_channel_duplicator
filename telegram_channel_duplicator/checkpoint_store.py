"""Durable message checkpoints for the Telegram channel duplicator."""
import os
import sqlite3


class CheckpointStore:
    """Persist the last fully processed source-message ID per source chat."""

    def __init__(self, path=None):
        self.path = path or os.path.join("data", "checkpoints.sqlite3")
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
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

    def close(self):
        self._connection.close()
