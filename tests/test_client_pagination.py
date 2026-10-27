import asyncio
import unittest
from types import SimpleNamespace

from telegram_channel_duplicator.client import Client


class FakeTelegramClient:
    def __init__(self, messages):
        self.messages = messages

    async def iter_messages(self, channel_id, min_id=0, limit=None):
        candidates = [m for m in self.messages if m.id > min_id]
        candidates.sort(key=lambda m: m.id, reverse=True)
        if limit is not None:
            candidates = candidates[:limit]
        for message in candidates:
            yield message


class ClientPaginationTests(unittest.TestCase):
    def test_fetches_more_than_ten_messages_in_ascending_order(self):
        client = Client.__new__(Client)
        client.client = FakeTelegramClient(
            [SimpleNamespace(id=n) for n in range(1, 36)]
        )
        channel = SimpleNamespace(channel_id=lambda: -100123)

        messages = asyncio.run(client.get_last_messages(channel, 7))

        self.assertEqual(len(messages), 28)
        self.assertEqual([m.id for m in messages], list(range(8, 36)))

    def test_latest_message_id_does_not_fetch_full_history(self):
        client = Client.__new__(Client)
        client.client = FakeTelegramClient(
            [SimpleNamespace(id=n) for n in range(1, 36)]
        )
        channel = SimpleNamespace(channel_id=lambda: -100123)

        latest_id = asyncio.run(client.get_latest_message_id(channel))

        self.assertEqual(latest_id, 35)

    def test_empty_history_returns_empty_list_and_zero_latest_id(self):
        client = Client.__new__(Client)
        client.client = FakeTelegramClient([])
        channel = SimpleNamespace(channel_id=lambda: -100123)

        self.assertEqual(asyncio.run(client.get_last_messages(channel, 0)), [])
        self.assertEqual(asyncio.run(client.get_latest_message_id(channel)), 0)


if __name__ == "__main__":
    unittest.main()
