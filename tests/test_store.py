"""Tests for the SQLite state/event store and config redaction."""

from __future__ import annotations

import threading

import pytest

from telegram_channel_duplicator.dashboard import redact_config
from telegram_channel_duplicator.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "nested" / "state.db"))
    yield s
    s.close()


def test_counters_events_and_stats(store):
    for _ in range(3):
        store.increment("polls")
    store.increment("mirrored", 2)
    store.event("sent", "hello", "SUCCESS")
    store.event("error", "boom", "ERROR")

    stats = store.stats()
    assert stats["counters"]["polls"] == 3
    assert stats["counters"]["mirrored"] == 2
    assert stats["last_event_at"] is not None

    events = store.events()
    assert [e["message"] for e in events] == ["hello", "boom"]
    assert events[1]["level"] == "ERROR"
    assert store.events(since_id=events[0]["id"])[0]["message"] == "boom"
    with pytest.raises(ValueError):
        store.increment("not-a-counter")


def test_source_state_is_monotonic_and_listed(store):
    assert store.get_last_id("chat-1") == 0
    store.set_last_id("chat-1", 42)
    store.set_last_id("chat-1", 10)  # out-of-order update must not rewind
    assert store.get_last_id("chat-1") == 42
    store.set_last_id("chat-1", 55)
    assert store.get_last_id("chat-1") == 55
    assert store.stats()["sources"][0]["key"] == "chat-1"


def test_message_map_and_edit_dedupe(store):
    store.record_send("chat-1", 100, -200, 900)
    store.record_send("chat-1", 100, -300, 901)
    assert len(store.destinations_for("chat-1", 100)) == 2
    assert len(store.pending_edits("chat-1", 100)) == 2
    store.mark_edited("chat-1", 100, -200, 900)
    assert store.pending_edits("chat-1", 100) == [{"dest_chat_id": -300, "dest_message_id": 901}]
    assert store.stats()["message_map_size"] == 2

    assert store.is_edit_applied("chat-1", 100, "t1") is False
    store.mark_edit_applied("chat-1", 100, "t1")
    store.mark_edit_applied("chat-1", 100, "t1")  # idempotent
    assert store.is_edit_applied("chat-1", 100, "t1") is True


def test_reset_keeps_event_history(store):
    store.increment("polls", 5)
    store.set_last_id("chat-1", 10)
    store.record_send("chat-1", 10, -2, 20)
    store.reset()
    stats = store.stats()
    assert stats["counters"]["polls"] == 0
    assert stats["sources"] == []
    assert stats["message_map_size"] == 0
    assert any("State reset" in e["message"] for e in store.events())


def test_concurrent_increments_are_safe(store):
    threads = [
        threading.Thread(target=lambda: [store.increment("polls") for _ in range(25)])
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.stats()["counters"]["polls"] == 200


def test_redact_config_hides_secrets():
    text = (
        'account_phone: "+15551234567"\n'
        "api_id: 12345678\n"
        'api_hash: "supersecret"\n'
        "delay: 5\n"
        "groups: []\n"
    )
    redacted = redact_config(text)
    assert "supersecret" not in redacted
    assert "+15551234567" not in redacted
    assert "12345678" not in redacted
    assert "delay: 5" in redacted
