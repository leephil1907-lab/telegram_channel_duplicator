"""Tests for the threaded lifecycle runner — no network, no Telegram, no demo mode."""

from __future__ import annotations

import asyncio
import time

from telegram_channel_duplicator.runner import BotRunner
from telegram_channel_duplicator.store import Store

VALID_CONFIG = {
    "account_phone": "+15551234567",
    "api_id": 123456,
    "api_hash": "abcdef1234567890",
    "delay": 0.02,
    "edit_message_checker_limit": 10,
    "groups": [{"name": "g", "sources": ["A"], "destinations": ["B"], "whitelist": []}],
}


class StubBot:
    """Minimal in-memory test double (lives only in tests, never shipped as a mode)."""

    def __init__(self, config, store, stop_event, dry_run, paths):
        self.config = config
        self.store = store
        self.stop_event = stop_event
        self.dry_run = dry_run

    async def start(self):
        self.store.event("system", "bot started")
        while not self.stop_event.is_set():
            self.store.increment("polls")
            await asyncio.sleep(0.01)
        self.store.event("system", "bot stopped")


def stub_factory(config, store, stop_event, dry_run, paths):
    return StubBot(config, store, stop_event, dry_run, paths)


def test_runner_start_status_stop(tmp_path):
    store = Store(str(tmp_path / "runner.db"))
    runner = BotRunner(
        str(tmp_path / "missing.yaml"), store, config=VALID_CONFIG, bot_factory=stub_factory
    )
    assert runner.status()["state"] == "stopped"  # nothing starts on its own

    ok, message = runner.start()
    assert ok and message == "started"

    deadline = time.time() + 5
    while time.time() < deadline and store.stats()["counters"]["polls"] == 0:
        time.sleep(0.02)
    assert store.stats()["counters"]["polls"] > 0

    status = runner.status()
    assert status["state"] == "running"
    assert status["mode"] == "live"
    assert status["dry_run"] is False
    assert status["thread_alive"] is True
    assert len(status["groups"]) == 1

    ok, _ = runner.stop()
    assert ok
    assert runner.status()["state"] == "stopped"
    assert runner.status()["thread_alive"] is False
    store.close()


def test_runner_supports_explicit_dry_run(tmp_path):
    store = Store(str(tmp_path / "runner.db"))
    runner = BotRunner(
        str(tmp_path / "missing.yaml"), store, config=VALID_CONFIG, dry_run=True, bot_factory=stub_factory
    )
    assert runner.start()[0] is True
    deadline = time.time() + 5
    while time.time() < deadline and runner.status()["state"] != "running":
        time.sleep(0.02)
    assert runner.status()["mode"] == "dry-run"
    runner.stop()
    store.close()


def test_runner_reports_config_errors_without_a_thread(tmp_path):
    store = Store(str(tmp_path / "runner.db"))
    runner = BotRunner(str(tmp_path / "does-not-exist.yaml"), store)

    ok, message = runner.start()
    assert not ok
    assert "not found" in message
    assert runner.status()["state"] == "error"
    assert runner.status()["error"]
    assert runner.status()["thread_alive"] is False
    store.close()


def test_immediate_stop_before_worker_initialization_does_not_leave_thread_running(tmp_path):
    import threading

    store = Store(str(tmp_path / "race.db"))
    runner = BotRunner(str(tmp_path / "unused.yaml"), store, config=VALID_CONFIG, bot_factory=stub_factory)
    gate = threading.Event()
    original_run = runner._run

    def delayed_run():
        gate.wait(timeout=3)
        original_run()

    runner._run = delayed_run
    assert runner.start()[0]
    result = []
    stopper = threading.Thread(target=lambda: result.append(runner.stop()))
    stopper.start()
    deadline = time.time() + 2
    while time.time() < deadline and runner.status()["state"] != "stopping":
        time.sleep(0.005)
    gate.set()
    stopper.join(timeout=4)
    assert result and result[0][0]
    assert runner.status()["state"] == "stopped"
    assert runner.status()["thread_alive"] is False
    store.close()
