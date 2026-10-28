"""Telethon client wrapper.

Upstream behaviour is preserved, with optional state restoration, retries on
dialog resolution and structured events for the dashboard.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional

from loguru import logger
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest

from .destination_channel import DestinationChannel
from .resilience import RetryPolicy, call_with_retries
from .source_channel import SourceChannel

SESSIONS_DIR = "sessions"


class Client:
    def __init__(
        self,
        config: Dict[str, Any],
        store=None,
        retry_policy: Optional[RetryPolicy] = None,
        sessions_dir: Optional[str] = None,
    ):
        self.config = config
        self.store = store
        self.retry_policy = retry_policy or RetryPolicy()
        # Per-user session directory: two users must never share a Telegram
        # login or see each other's account.
        self.sessions_dir = sessions_dir or SESSIONS_DIR

        os.makedirs(self.sessions_dir, exist_ok=True)
        self.client = TelegramClient(
            os.path.join(self.sessions_dir, "account_session"),
            self.config["api_id"],
            self.config["api_hash"],
        )
        logger.info("Account authorization")

    def _event(self, kind: str, message: str, level: str = "INFO") -> None:
        if self.store is not None:
            self.store.event(kind, message, level)

    async def start(self):
        """Client launch: interactive login (code / 2FA) on first run."""
        await self.client.start(
            phone=self._get_phone,
            code_callback=self._enter_code,
            password=self._enter_password,
        )
        logger.info("Account authorization was successful")
        self._event("auth", "Telegram authorization successful", "SUCCESS")

    async def disconnect(self):
        await self.client.disconnect()

    async def get_last_messages(self, channel, min_id):
        history = await self.client(
            GetHistoryRequest(
                peer=channel.channel_id(),
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=10,
                max_id=0,
                min_id=min_id,
                hash=0,
            )
        )
        return history.messages[::-1]

    async def send_message(self, *args, **kwargs):
        return await self.client.send_message(*args, **kwargs)

    async def edit_message(self, *args, **kwargs):
        return await self.client.edit_message(*args, **kwargs)

    async def get_groups(self):
        """Resolve chat titles to IDs, restoring each source's last seen ID."""
        groups_list = []
        for group in copy.deepcopy(self.config["groups"]):
            sources, destinations = [], []
            for title in group["sources"]:
                chat_id = await call_with_retries(
                    self._get_chat_id,
                    title,
                    policy=self.retry_policy,
                    on_retry=lambda a, e, d: self._event(
                        "retry", f"Resolving '{title}' attempt {a} failed, retry in {d:.1f}s", "WARNING"
                    ),
                )
                if chat_id is not None:
                    last_id = self.store.get_last_id(str(chat_id)) if self.store else 0
                    sources.append(SourceChannel(title, chat_id, last_id))
                    if last_id:
                        self._event("resume", f"'{title}' resumes after message {last_id}")
            for title in group["destinations"]:
                chat_id = await call_with_retries(
                    self._get_chat_id,
                    title,
                    policy=self.retry_policy,
                )
                if chat_id is not None:
                    destinations.append(DestinationChannel(title, chat_id))

            group["sources"] = sources
            group["destinations"] = destinations
            if sources and destinations:
                groups_list.append(group)
            else:
                logger.warning(f"Group '{group['name']}' has no usable sources/destinations, skipping")
                self._event("warning", f"Group '{group['name']}' skipped (unresolved chats)", "WARNING")

        return groups_list

    def _get_phone(self):
        return self.config["account_phone"]

    @staticmethod
    def _enter_code():
        return input("Enter the code from Telegram: ")

    @staticmethod
    def _enter_password():
        return input("Enter your two-factor authentication password: ")

    async def _get_chat_id(self, chat_name):
        async for dialog in self.client.iter_dialogs():
            if dialog.name == chat_name:
                return dialog.id

        logger.error(
            f"Chat with the name '{chat_name}' was not found in the list of "
            "conversations, it will be skipped"
        )
        return None
