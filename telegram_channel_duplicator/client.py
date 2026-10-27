import os

from loguru import logger
from telethon import TelegramClient

from .destination_channel import DestinationChannel
from .source_channel import SourceChannel

SESSIONS_DIR = "sessions"


class Client:
    def __init__(self, config):
        self.config = config
        os.makedirs(SESSIONS_DIR, exist_ok=True)

        self.client = TelegramClient(
            os.path.join(SESSIONS_DIR, "account_session"),
            self.config["api_id"],
            self.config["api_hash"],
        )
        logger.info("Account authorization")

    async def start(self):
        """Start the authenticated Telegram client."""
        await self.client.start(
            phone=self._get_phone,
            code_callback=self._enter_code,
            password=self._enter_password,
        )
        logger.info("Account authorization was successful")

    async def get_last_messages(self, channel, min_id):
        """Return every message newer than min_id, in ascending ID order.

        Telethon's iterator handles Telegram history pagination; unlike the old
        GetHistoryRequest(limit=10), this does not silently cap each poll at
        ten messages. min_id is exclusive.
        """
        messages = []
        async for message in self.client.iter_messages(
            channel.channel_id(),
            min_id=max(0, int(min_id)),
            reverse=True,
        ):
            messages.append(message)
        return messages

    async def send_message(self, *args, **kwargs):
        return await self.client.send_message(*args, **kwargs)

    async def get_groups(self):
        """Resolve configured chat names to IDs without mutating the config."""
        groups_list = []
        for configured_group in self.config["groups"]:
            group = dict(configured_group)
            group["sources"] = [
                SourceChannel(name, await self._get_chat_id(name))
                for name in configured_group["sources"]
            ]
            group["destinations"] = [
                DestinationChannel(name, await self._get_chat_id(name))
                for name in configured_group["destinations"]
            ]
            groups_list.append(group)
        return groups_list

    def _get_phone(self):
        return self.config["account_phone"]

    @staticmethod
    def _enter_code():
        return input("Enter the code from the Telegram message: ")

    @staticmethod
    def _enter_password():
        return input("Enter your two-factor authentication password: ")

    async def _get_chat_id(self, chat_name):
        async for dialog in self.client.iter_dialogs():
            if dialog.name == chat_name:
                return dialog.id

        logger.error(
            "Chat '{}' was not found in the account's conversations; skipping it",
            chat_name,
        )
        return None
