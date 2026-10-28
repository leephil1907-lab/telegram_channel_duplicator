"""Network-free regression tests for safe dry runs and partial retries."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_channel_duplicator.destination_channel import DestinationChannel
from telegram_channel_duplicator.duplicator import Duplicator
from telegram_channel_duplicator.message_preparer import MessagePreparer
from telegram_channel_duplicator.resilience import RetryPolicy
from telegram_channel_duplicator.sending_message_buffer import SendingMessageBuffer
from telegram_channel_duplicator.source_channel import SourceChannel
from telegram_channel_duplicator.store import Store


def message(message_id, edit_date=None):
    return SimpleNamespace(id=message_id, chat_id=-100, message="hello", edit_date=edit_date, date=datetime.now(timezone.utc))


def bare_duplicator(client, store, stop_event, dry_run=False):
    dup = object.__new__(Duplicator)
    dup.config = {"delay": 0.01, "edit_message_checker_limit": 10}
    dup.client = client
    dup.store = store
    dup.stop_event = stop_event
    dup.dry_run = dry_run
    dup.retry_policy = RetryPolicy(attempts=1)
    dup.message_preparer = MessagePreparer({})
    dup.sending_message_buffer = SendingMessageBuffer(50)
    return dup


def test_dry_run_never_sends_edits_or_advances_saved_checkpoint(tmp_path, monkeypatch):
    from telegram_channel_duplicator import duplicator as module

    store = Store(str(tmp_path / "state.db"))
    store.set_last_id("-100", 10)
    store.record_send("-100", 10, -200, 50)

    async def scenario():
        stop = asyncio.Event()

        class Client:
            async def get_groups(self):
                return [{"sources": [SourceChannel("A", -100, 10)],
                         "destinations": [DestinationChannel("B", -200)], "whitelist": []}]

            async def get_last_messages(self, *_args, **_kwargs):
                return [message(10, datetime.now(timezone.utc)), message(11)]

            async def send_message(self, *_args, **_kwargs):
                raise AssertionError("dry run attempted to send")

            async def edit_message(self, *_args, **_kwargs):
                raise AssertionError("dry run attempted to edit")

        async def stop_after_cycle(event, _delay):
            event.set()

        monkeypatch.setattr(module, "wait_or_stop", stop_after_cycle)
        dup = bare_duplicator(Client(), store, stop, dry_run=True)
        await dup.duplicate()

    asyncio.run(scenario())
    assert store.get_last_id("-100") == 10
    assert store.stats()["message_map_size"] == 1
    assert any(event["kind"] == "dry-run" for event in store.events())
    store.close()


def test_partial_send_retries_without_resending_successes_across_groups(tmp_path):
    store = Store(str(tmp_path / "state.db"))
    store.set_last_id("-100", 10)

    async def scenario():
        stop = asyncio.Event()

        class Client:
            def __init__(self):
                self.polls = 0
                self.attempts = []
                self.checkpoints = []
                self.failed_once = False

            async def get_groups(self):
                return [
                    {"sources": [SourceChannel("A", -100, 10)], "destinations": [DestinationChannel("B", -200)], "whitelist": []},
                    {"sources": [SourceChannel("A", -100, 10)], "destinations": [DestinationChannel("C", -300)], "whitelist": []},
                ]

            async def get_last_messages(self, *_args, **_kwargs):
                self.polls += 1
                self.checkpoints.append(store.get_last_id("-100"))
                return [message(11)]

            async def send_message(self, dest, msg):
                self.attempts.append((dest, msg.id))
                if dest == -300 and not self.failed_once:
                    self.failed_once = True
                    raise RuntimeError("temporary failure")
                if dest == -300:
                    stop.set()
                return SimpleNamespace(id=1000 + msg.id, chat_id=dest, edit_date=None)

        client = Client()
        dup = bare_duplicator(client, store, stop)
        await dup.duplicate()
        assert client.polls == 2  # one poll per source per cycle, not per group
        assert client.checkpoints == [10, 10]  # don't skip failed deliveries
        assert client.attempts == [(-200, 11), (-300, 11), (-300, 11)]

    asyncio.run(scenario())
    assert store.get_last_id("-100") == 11
    assert len(store.destinations_for("-100", 11)) == 2
    store.close()


def test_failed_edit_is_retried_without_repeating_confirmed_destination(tmp_path):
    store = Store(str(tmp_path / "state.db"))
    store.record_send("-100", 10, -200, 50)
    store.record_send("-100", 10, -300, 60)
    edited = message(10, datetime.now(timezone.utc))
    edit_ts = str(edited.edit_date)

    async def scenario():
        class Client:
            def __init__(self):
                self.calls = []
                self.failed = False

            async def edit_message(self, chat_id, message_id, text):
                self.calls.append(chat_id)
                if chat_id == -300 and not self.failed:
                    self.failed = True
                    raise RuntimeError("temporary edit failure")
                return SimpleNamespace(id=message_id, chat_id=chat_id, edit_date=edited.edit_date)

        client = Client()
        dup = bare_duplicator(client, store, asyncio.Event())
        await dup._process_edited_messages([edited], "-100")
        assert not store.is_edit_applied("-100", 10, edit_ts)
        await dup._process_edited_messages([edited], "-100")
        assert store.is_edit_applied("-100", 10, edit_ts)
        assert client.calls.count(-200) == 1
        assert client.calls.count(-300) == 2

    asyncio.run(scenario())
    store.close()


def test_no_resolvable_groups_surfaces_an_error_instead_of_silent_stop():
    import pytest

    class Client:
        async def get_groups(self):
            return []

    dup = object.__new__(Duplicator)
    dup.client = Client()
    with pytest.raises(RuntimeError, match="No resolvable groups"):
        asyncio.run(dup.duplicate())
