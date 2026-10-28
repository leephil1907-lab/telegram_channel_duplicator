"""Pagination and first-run safety tests for the Telegram client wrapper."""

import asyncio

from telegram_channel_duplicator.client import Client
from telegram_channel_duplicator.source_channel import SourceChannel


class Message:
    def __init__(self, message_id):
        self.id = message_id


class FakeTelegram:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def iter_messages(self, channel_id, **kwargs):
        self.calls.append((channel_id, kwargs))

        async def generate():
            selected = self.messages
            if "limit" in kwargs:
                selected = selected[:kwargs["limit"]]
            elif "min_id" in kwargs:
                selected = [m for m in selected if m.id > kwargs["min_id"]]
            for message in selected:
                yield message

        return generate()


def test_first_run_reads_only_latest_message():
    client = object.__new__(Client)
    client.client = FakeTelegram([Message(11), Message(12), Message(13)])
    source = SourceChannel("source", 123, last_message_id=0)

    messages = asyncio.run(client.get_last_messages(source, min_id=0))

    assert [message.id for message in messages] == [11]
    assert client.client.calls == [(123, {"limit": 1})]


def test_existing_checkpoint_uses_pagination_and_orders_oldest_first():
    client = object.__new__(Client)
    client.client = FakeTelegram([Message(14), Message(12), Message(13), Message(11)])
    source = SourceChannel("source", 123, last_message_id=10)

    messages = asyncio.run(client.get_last_messages(source, min_id=10))

    assert [message.id for message in messages] == [11, 12, 13, 14]
    assert client.client.calls == [(123, {"min_id": 10})]
