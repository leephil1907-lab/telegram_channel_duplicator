"""Offline build/smoke tests - no Telegram credentials or network required."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]

MODULES = [
    "telegram_channel_duplicator",
    "telegram_channel_duplicator.cli",
    "telegram_channel_duplicator.client",
    "telegram_channel_duplicator.config_controller",
    "telegram_channel_duplicator.destination_channel",
    "telegram_channel_duplicator.duplicator",
    "telegram_channel_duplicator.message_preparer",
    "telegram_channel_duplicator.sending_message_buffer",
    "telegram_channel_duplicator.source_channel",
    "telegram_channel_duplicator.store",
    "telegram_channel_duplicator.auth",
    "telegram_channel_duplicator.workspaces",
    "telegram_channel_duplicator.timing",
    "telegram_channel_duplicator.resilience",
    "telegram_channel_duplicator.runner",
    "telegram_channel_duplicator.dashboard",
    "telegram_channel_duplicator.account_recovery",
    "telegram_channel_duplicator.recovery_backup",
]


@pytest.fixture(autouse=True)
def _clean_sinks():
    yield
    logger.remove()


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("TG_PHONE", "+15551234567")
    monkeypatch.setenv("TG_API_ID", "12345678")
    monkeypatch.setenv("TG_API_HASH", "0123456789abcdef0123456789abcdef")


def msg(mid, text="hello", chat_id=-1001, edited=None):
    return SimpleNamespace(
        id=mid,
        message=text,
        chat_id=chat_id,
        edit_date=edited,
        date=dt.datetime.now(dt.timezone.utc),
    )


def test_every_module_imports():
    for name in MODULES:
        assert importlib.import_module(name) is not None


def test_empty_config_is_rejected(credentials):
    """New workspaces start empty — no premade sample is shipped or accepted."""
    from telegram_channel_duplicator.config_controller import ConfigController, ConfigError

    with pytest.raises(ConfigError, match="YAML mapping"):
        ConfigController.validate_text("", use_dotenv=False)


def test_no_sample_files_remain():
    for name in ("config.yaml.example", "config.demo.yaml", ".env.example", "UI-PREVIEW.html"):
        assert not (ROOT / name).exists(), name


def test_missing_config_file_has_actionable_error(tmp_path, monkeypatch):
    from telegram_channel_duplicator.config_controller import ConfigController, ConfigError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="not found"):
        ConfigController.load(use_dotenv=False)


def test_invalid_yaml_reports_error(tmp_path):
    from telegram_channel_duplicator.config_controller import ConfigController, ConfigError

    path = tmp_path / "bad.yaml"
    path.write_text("account_phone: [unclosed\n", encoding="utf8")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        ConfigController.load(str(path), use_dotenv=False)


def test_unset_environment_variables_are_reported(tmp_path, monkeypatch):
    from telegram_channel_duplicator.config_controller import ConfigController, ConfigError

    monkeypatch.delenv("TG_MISSING_TOKEN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text('account_phone: "+1"\napi_id: "1"\napi_hash: "${TG_MISSING_TOKEN}"\n', encoding="utf8")
    with pytest.raises(ConfigError, match="TG_MISSING_TOKEN"):
        ConfigController.load(str(path), use_dotenv=False)


@pytest.mark.parametrize(
    "yaml_text, message",
    [
        ('account_phone: "+1555"\napi_id: "abc"\napi_hash: "xyz"\n', "api_id"),
        ('account_phone: "555"\napi_id: 1\napi_hash: "xyz"\n', "account_phone"),
        ('account_phone: "+1555"\napi_id: 1\napi_hash: "000000"\n', "api_hash"),
        ('account_phone: "+1555"\napi_id: 1\napi_hash: "xyz"\ngroups: []\n', "groups"),
        ('account_phone: "+1555"\napi_id: 1\napi_hash: "xyz"\ndelay: 0\n', "delay"),
    ],
)
def test_validation_rejects_bad_values(yaml_text, message, tmp_path):
    from telegram_channel_duplicator.config_controller import ConfigController, ConfigError

    path = tmp_path / "config.yaml"
    path.write_text(yaml_text, encoding="utf8")
    with pytest.raises(ConfigError, match=message):
        ConfigController.load(str(path), use_dotenv=False)


def test_validation_applies_defaults(tmp_path):
    from telegram_channel_duplicator.config_controller import ConfigController

    path = tmp_path / "config.yaml"
    path.write_text(
        'account_phone: "+15551234567"\napi_id: 42\napi_hash: "deadbeef"\n'
        'groups:\n  - name: g\n    sources: ["A"]\n    destinations: ["B"]\n',
        encoding="utf8",
    )
    config = ConfigController.load(str(path), use_dotenv=False)
    assert config["delay"] == 5.0
    assert config["edit_message_checker_limit"] == 10
    assert config["groups"][0]["whitelist"] == []


def test_whitelist_handles_media_only_messages():
    from telegram_channel_duplicator.message_preparer import MessagePreparer

    preparer = MessagePreparer({})
    assert preparer.check_whitelist(msg(1, "Telegram news"), []) is True
    assert preparer.check_whitelist(msg(1, "Telegram news"), ["telegram"]) is True
    assert preparer.check_whitelist(msg(1, "crypto news"), ["telegram"]) is False
    assert preparer.check_whitelist(msg(1, None), ["telegram"]) is False


def test_sending_message_buffer_tracks_edits():
    from telegram_channel_duplicator.sending_message_buffer import SendingMessageBuffer

    buf = SendingMessageBuffer(2)
    src = msg(1, "original")
    dest = SimpleNamespace(id=99, chat_id=-1002, edit_date=None)
    buf.put(src, dest)

    edited = msg(1, "edited", edited=dt.datetime.now(dt.timezone.utc))
    assert buf.get_unedited_destination_messages(edited, dt.timedelta(seconds=5)) == [dest]
    buf.remove_by_destination_message(dest)
    assert buf.get_unedited_destination_messages(edited, dt.timedelta(seconds=5)) == []

    # bounded buffer and disabled (max_len=0) mode must not raise
    for i in range(5):
        buf.put(msg(i), dest)
    assert len(buf._buf) <= 2
    assert SendingMessageBuffer(0).get_unedited_destination_messages(edited, dt.timedelta(0)) == []


def test_channel_wrappers():
    from telegram_channel_duplicator.destination_channel import DestinationChannel
    from telegram_channel_duplicator.source_channel import SourceChannel

    src = SourceChannel("News", -100)
    assert src.last_message_id() == 0
    src.set_last_message_id(12)
    assert src.last_message_id() == 12 and src.name() == "News"
    assert "News" in repr(src)
    assert DestinationChannel("Mirror", -200).channel_id() == -200


def test_empty_history_does_not_crash():
    from telegram_channel_duplicator.duplicator import Duplicator
    from telegram_channel_duplicator.source_channel import SourceChannel

    dup = object.__new__(Duplicator)
    assert dup._filter_old_messages(SourceChannel("A", -1), []) == []


def test_new_messages_go_to_every_destination_in_chronological_order():
    from telegram_channel_duplicator.duplicator import Duplicator
    from telegram_channel_duplicator.message_preparer import MessagePreparer
    from telegram_channel_duplicator.sending_message_buffer import SendingMessageBuffer
    from telegram_channel_duplicator.destination_channel import DestinationChannel
    from telegram_channel_duplicator.source_channel import SourceChannel

    class FakeClient:
        def __init__(self):
            self.sent = []

        async def send_message(self, entity, message):
            self.sent.append((entity, message.id))
            return SimpleNamespace(id=1000 + message.id, chat_id=entity, edit_date=None)

    dup = object.__new__(Duplicator)
    dup.config = {"delay": 5}
    dup.client = FakeClient()
    dup.message_preparer = MessagePreparer({})
    dup.sending_message_buffer = SendingMessageBuffer(50)

    group = {
        "whitelist": [],
        "destinations": [DestinationChannel("B", -200), DestinationChannel("C", -300)],
    }
    asyncio.run(
        dup._process_new_messages(group, SourceChannel("A", -100), [msg(3), msg(1), msg(2)])
    )
    assert dup.client.sent == [(-200, 1), (-200, 2), (-200, 3), (-300, 1), (-300, 2), (-300, 3)]


def test_full_polling_cycle_runs_offline(monkeypatch):
    """One start() -> duplicate() cycle with a fake Telethon client."""
    from telegram_channel_duplicator import duplicator as dm
    from telegram_channel_duplicator.destination_channel import DestinationChannel
    from telegram_channel_duplicator.message_preparer import MessagePreparer
    from telegram_channel_duplicator.sending_message_buffer import SendingMessageBuffer
    from telegram_channel_duplicator.source_channel import SourceChannel

    class StopLoop(Exception):
        pass

    class FakeClient:
        def __init__(self):
            self.sent = []
            self.polls = 0

        async def start(self):
            return None

        async def get_groups(self):
            return [{
                "name": "g",
                "sources": [SourceChannel("A", -100)],
                "destinations": [DestinationChannel("B", -200)],
                "whitelist": [],
            }]

        async def get_last_messages(self, channel, min_id):
            self.polls += 1
            ids = [1, 2, 3] if self.polls < 3 else [1, 2, 3, 4]
            return [msg(i) for i in ids]  # third poll adds a new message

        async def send_message(self, entity, message):
            self.sent.append(message.id)
            return SimpleNamespace(id=1000 + message.id, chat_id=entity, edit_date=None)

        async def edit_message(self, *args, **kwargs):
            raise AssertionError("no edits expected")

    real_sleep = asyncio.sleep

    async def fake_sleep(_delay):
        # let three poll cycles run, then break the infinite loop
        if client.polls >= 3:
            raise StopLoop
        await real_sleep(0)

    client = FakeClient()
    # The loop sleeps through timing.wait_or_stop -> asyncio.sleep.
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    dup = object.__new__(dm.Duplicator)
    dup.config = {"delay": 0.01, "edit_message_checker_limit": 10}
    dup.client = client
    dup.message_preparer = MessagePreparer({})
    dup.sending_message_buffer = SendingMessageBuffer(50)

    with pytest.raises(StopLoop):
        asyncio.run(dup.start())
    assert client.polls >= 3
    assert client.sent == [4]  # only the message added on poll 3 is new


def test_cli_version(capsys):
    from telegram_channel_duplicator.cli import main

    assert main(["--version"]) == 0
    assert "telegram-channel-duplicator" in capsys.readouterr().out


def test_cli_check_is_offline(tmp_path, monkeypatch, capsys):
    """A user-supplied config validates without any network access."""
    from telegram_channel_duplicator.cli import main

    (tmp_path / "my.yaml").write_text(
        'account_phone: "+15551234567"\n'
        "api_id: 12345678\n"
        'api_hash: "0123456789abcdef0123456789abcdef"\n'
        "delay: 5\n"
        "edit_message_checker_limit: 10\n"
        "groups:\n  - name: mine\n    sources: [A]\n    destinations: [B]\n    whitelist: []\n",
        encoding="utf8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["--check", "--config", "my.yaml"]) == 0
    out = capsys.readouterr().out
    assert "Configuration OK" in out
    assert "mine" in out
    assert "No Telegram connection" in out
    assert not (tmp_path / "sessions").exists()


def test_cli_bad_config_exit_code(tmp_path, monkeypatch):
    from telegram_channel_duplicator.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["--check"]) == 2
