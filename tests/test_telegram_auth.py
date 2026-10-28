"""Telegram login prompt bridge: local async tests, no Telegram network."""
import asyncio
import threading
import time

import pytest

from telegram_channel_duplicator.client import Client
from telegram_channel_duplicator.runner import BotRunner
from telegram_channel_duplicator.store import Store
from telegram_channel_duplicator.telegram_auth import TelegramChallenge


def test_web_client_passes_code_and_two_step_password_without_logging_them():
    async def scenario():
        challenge = TelegramChallenge(timeout=2)
        received = {}

        class FakeTelethon:
            authorized = False
            async def connect(self):
                pass
            async def get_me(self):
                if not self.authorized:
                    return None
                from types import SimpleNamespace
                return SimpleNamespace(phone="15551234567", bot=False)
            async def send_code_request(self, phone):
                from telethon import types
                received["phone"] = phone
                return types.auth.SentCode(type=types.auth.SentCodeTypeApp(length=5), phone_code_hash="test-hash")
            async def sign_in(self, phone=None, code=None, password=None):
                from telethon import errors
                if code:
                    received["code"] = code
                    raise errors.SessionPasswordNeededError(request=None)
                received["password"] = password
                self.authorized = True

        client = object.__new__(Client)
        client.client = FakeTelethon()
        client.config = {"account_phone": "+15551234567"}
        client._expected_phone = "15551234567"
        client.auth_challenge = challenge
        client.store = None
        task = asyncio.create_task(client.start())
        await asyncio.sleep(0)
        assert challenge.status()["kind"] == "code"
        assert challenge.submit("code", "12345")
        for _ in range(20):
            await asyncio.sleep(0)
            if challenge.status() and challenge.status()["kind"] == "password":
                break
        assert challenge.status()["kind"] == "password"
        assert challenge.submit("password", "two-step secret")
        await task
        assert received == {"phone": "+15551234567", "code": "12345", "password": "two-step secret"}
        assert challenge.status() is None

    asyncio.run(scenario())


def test_challenge_timeout_and_cancel():
    async def scenario():
        challenge = TelegramChallenge(timeout=0.01)
        with pytest.raises(RuntimeError, match="timed out"):
            await challenge.request("code")
        task = asyncio.create_task(challenge.request("password"))
        await asyncio.sleep(0)
        challenge.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert challenge.status() is None
        assert challenge.submit("password", "anything") is False

    asyncio.run(scenario())


def test_stopping_worker_cancels_a_pending_verification(tmp_path):
    store = Store(str(tmp_path / "state.db"))
    runner = BotRunner(str(tmp_path / "unused"), store, config={"groups": [], "delay": 1})

    class WaitingBot:
        async def start(self):
            await runner.challenge.request("code")

    runner.bot_factory = lambda *_args: WaitingBot()
    assert runner.start()[0]
    deadline = time.time() + 3
    while time.time() < deadline and not runner.challenge.status():
        time.sleep(0.01)
    assert runner.challenge.status()["kind"] == "code"
    assert runner.stop()[0]
    assert runner.status()["state"] == "stopped"
    assert runner.challenge.status() is None
    store.close()


def test_chat_title_must_be_unique_to_avoid_wrong_destination():
    from types import SimpleNamespace

    class FakeTelethon:
        async def iter_dialogs(self):
            for name, chat_id in (("Unique", 1), ("Duplicate", 2), ("Duplicate", 3)):
                yield SimpleNamespace(name=name, id=chat_id)

    async def scenario():
        client = object.__new__(Client)
        client.client = FakeTelethon()
        assert await client._get_chat_id("Unique") == 1
        assert await client._get_chat_id("Duplicate") is None
        assert await client._get_chat_id("Missing") is None

    asyncio.run(scenario())


def test_unresolved_chat_stops_group_startup_instead_of_skipping_it():
    from types import SimpleNamespace
    from telegram_channel_duplicator.resilience import RetryPolicy

    class FakeTelethon:
        async def iter_dialogs(self):
            for name, chat_id in (("Source", 1), ("Existing destination", 2)):
                yield SimpleNamespace(name=name, id=chat_id)

    async def scenario():
        client = object.__new__(Client)
        client.client = FakeTelethon()
        client.config = {"groups": [{"name": "g", "sources": ["Source"],
                                      "destinations": ["Existing destination", "Missing"], "whitelist": []}]}
        client.store = None
        client.retry_policy = RetryPolicy(attempts=1)
        with pytest.raises(RuntimeError, match="could not uniquely resolve"):
            await client.get_groups()

    asyncio.run(scenario())


def test_resending_extends_one_use_challenge_and_never_exposes_secrets():
    async def scenario():
        challenge = TelegramChallenge(timeout=0.08)
        task = asyncio.create_task(challenge.request(
            "code", details={"delivery": "app", "length": 5, "phone": "+2348000000000", "code": "secret"}))
        await asyncio.sleep(0)
        first = challenge.status()
        assert first == {"kind": "code", "id": 1, "delivery": "app", "length": 5}
        await asyncio.sleep(0.05)
        challenge.timeout = 0.5
        assert challenge.update_details(first["id"], {"delivery": "sms", "phone_code_hash": "secret"}, extend=True)
        await asyncio.sleep(0.06)  # past the original deadline
        assert not task.done() and challenge.status()["delivery"] == "sms"
        assert not challenge.submit("code", "12345", challenge_id=first["id"] + 1)
        assert challenge.submit("code", "12345", challenge_id=first["id"])
        assert await task == "12345" and challenge.status() is None
    asyncio.run(scenario())


def test_real_worker_reports_starting_until_telegram_and_channels_are_ready(tmp_path, monkeypatch):
    from telegram_channel_duplicator import duplicator as module
    from telegram_channel_duplicator.source_channel import SourceChannel
    from telegram_channel_duplicator.destination_channel import DestinationChannel
    config = {"account_phone": "+15551234567", "api_id": 123456,
              "api_hash": "abcdef1234567890", "delay": 0.02,
              "edit_message_checker_limit": 10,
              "groups": [{"name": "g", "sources": ["A"], "destinations": ["B"], "whitelist": []}]}

    class WaitingClient:
        def __init__(self, _config, *, auth_challenge, **_kwargs):
            self.challenge = auth_challenge
        async def start(self):
            assert await self.challenge.request("code") == "12345"
        async def get_groups(self):
            return [{"name": "g", "sources": [SourceChannel("A", 1)],
                     "destinations": [DestinationChannel("B", 2)], "whitelist": []}]
        async def get_last_messages(self, *_args, **_kwargs):
            return []
        async def disconnect(self):
            pass

    monkeypatch.setattr(module, "Client", WaitingClient)
    store = Store(str(tmp_path / "state.db"))
    runner = BotRunner(str(tmp_path / "unused"), store, config=config)
    try:
        assert runner.start()[0]
        deadline = time.time() + 3
        while time.time() < deadline and not runner.challenge.status():
            time.sleep(.01)
        pending = runner.challenge.status()
        assert pending and runner.status()["state"] == "starting"
        assert runner.challenge.submit("code", "12345", challenge_id=pending["id"])
        deadline = time.time() + 3
        while time.time() < deadline and runner.status()["state"] != "running":
            time.sleep(.01)
        assert runner.status()["state"] == "running"
    finally:
        runner.stop()
        store.close()
