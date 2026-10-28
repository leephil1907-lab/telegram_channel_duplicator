"""Telethon client wrapper.

Upstream behaviour is preserved, with optional state restoration, retries on
dialog resolution and structured events for the dashboard.
"""

from __future__ import annotations

import copy
import hashlib
import os
from typing import Any, Dict, Optional

from loguru import logger
from telethon import TelegramClient, utils

from .destination_channel import DestinationChannel
from .resilience import RetryPolicy, call_with_retries
from .source_channel import SourceChannel
from .telegram_auth import code_delivery_details, complete_login_with_challenge, request_login_code

SESSIONS_DIR = "sessions"


class Client:
    def __init__(
        self,
        config: Dict[str, Any],
        store=None,
        retry_policy: Optional[RetryPolicy] = None,
        sessions_dir: Optional[str] = None,
        auth_challenge=None,
    ):
        self.config = config
        self.store = store
        self.retry_policy = retry_policy or RetryPolicy()
        self.auth_challenge = auth_challenge
        # Per-user session directory: two users must never share a Telegram
        # login or see each other's account.
        self.sessions_dir = sessions_dir or SESSIONS_DIR

        os.makedirs(self.sessions_dir, mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.sessions_dir, 0o700)
        self._expected_phone = utils.parse_phone(self.config.get("account_phone"))
        if not self._expected_phone:
            raise ValueError("The configured Telegram phone number is invalid. Use international format.")
        # Separate local authorizations by number. Continue to use the legacy
        # account_session if it belongs to this number, so upgrades do not
        # unnecessarily log existing users out.
        fingerprint = hashlib.sha256(self._expected_phone.encode("ascii")).hexdigest()[:24]
        self._number_session_path = os.path.join(self.sessions_dir, "account_" + fingerprint)
        self._legacy_session_path = os.path.join(self.sessions_dir, "account_session")
        if os.path.isfile(self._number_session_path + ".session"):
            self._session_path = self._number_session_path
        elif os.path.isfile(self._legacy_session_path + ".session"):
            self._session_path = self._legacy_session_path
        else:
            self._session_path = self._number_session_path
        self.client = self._new_client(self._session_path)
        logger.info("Account authorization")

    def _new_client(self, session_path):
        # SQLite must be private from the moment it is created, not only once
        # Telegram has written the authorization key.
        filename = session_path + ".session"
        fd = os.open(filename, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        if os.name != "nt":
            os.chmod(filename, 0o600)
        return TelegramClient(session_path, self.config["api_id"], self.config["api_hash"])

    def _matches_phone(self, me):
        return (not getattr(me, "bot", False)
                and utils.parse_phone(getattr(me, "phone", None)) == self._expected_phone)

    def _event(self, kind: str, message: str, level: str = "INFO") -> None:
        if self.store is not None:
            self.store.event(kind, message, level)

    async def start(self):
        """Client launch: interactive login (code / 2FA) on first run."""
        await self.client.connect()
        me = await self.client.get_me()
        if me and not self._matches_phone(me) and self._session_path == self._legacy_session_path:
            # A prior version kept every account in one filename. Never reuse
            # an old account just because a different number was configured.
            await self.client.disconnect()
            self._session_path = self._number_session_path
            self.client = self._new_client(self._session_path)
            await self.client.connect()
            me = await self.client.get_me()
        if me and not self._matches_phone(me):
            raise RuntimeError("The saved mirroring session does not match the configured Telegram number. Check the number and Telegram active sessions.")
        if not me:
            if self.auth_challenge:
                try:
                    sent = await request_login_code(self.client, self._get_phone())
                except RuntimeError as exc:
                    # Telethon can report immediate authorization rather than
                    # a SentCode; verify with Telegram before assuming success.
                    if "logged in right after" not in str(exc):
                        raise
                    me = await self.client.get_me()
                    if not me:
                        raise RuntimeError("Telegram did not complete authorization. Start mirroring again.") from None
                if not me:
                    details = code_delivery_details(sent)
                    private_hash = getattr(sent, "phone_code_hash", None)
                    await complete_login_with_challenge(
                        self.client, self._get_phone(), self.auth_challenge,
                        lambda: (details, private_hash),
                    )
            else:
                # Preserve Telethon's interactive terminal login for the CLI.
                await self.client.start(phone=self._get_phone, code_callback=self._enter_code,
                                        password=self._enter_password)
            me = await self.client.get_me()
        if not me or not self._matches_phone(me):
            raise RuntimeError("Telegram did not confirm the configured user account. Check the phone number and try again.")
        logger.info("Account authorization was successful")
        self._event("auth", "Telegram authorization successful", "SUCCESS")

    async def disconnect(self):
        await self.client.disconnect()

    async def get_last_messages(self, channel, min_id):
        """Return new messages oldest-to-newest, using Telethon pagination.

        On a source's first run, read only its latest message so existing
        history is not downloaded or forwarded. Once a checkpoint exists,
        iterate every message newer than the requested lower bound.
        """
        if channel.last_message_id() == 0:
            async for message in self.client.iter_messages(
                channel.channel_id(), limit=1
            ):
                return [message]
            return []

        messages = []
        async for message in self.client.iter_messages(
            channel.channel_id(), min_id=max(0, int(min_id))
        ):
            messages.append(message)
        messages.sort(key=lambda message: message.id)
        return messages

    async def send_message(self, *args, **kwargs):
        return await self.client.send_message(*args, **kwargs)

    async def edit_message(self, *args, **kwargs):
        return await self.client.edit_message(*args, **kwargs)

    async def get_groups(self):
        """Resolve chat titles to IDs, restoring each source's last seen ID."""
        groups_list = []
        for group in copy.deepcopy(self.config["groups"]):
            sources, destinations, unresolved = [], [], []
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
                else:
                    unresolved.append(f"source '{title}'")
            for title in group["destinations"]:
                chat_id = await call_with_retries(
                    self._get_chat_id,
                    title,
                    policy=self.retry_policy,
                )
                if chat_id is not None:
                    destinations.append(DestinationChannel(title, chat_id))
                else:
                    unresolved.append(f"destination '{title}'")

            if unresolved:
                # Do not silently advance a source checkpoint for a group whose
                # other source/destination is unavailable or ambiguous.
                details = ", ".join(unresolved)
                raise RuntimeError(
                    f"Group '{group['name']}' could not uniquely resolve {details}. "
                    "Use exact, unique Telegram chat titles and check membership."
                )
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
        match = None
        async for dialog in self.client.iter_dialogs():
            if dialog.name == chat_name:
                if match is not None and match != dialog.id:
                    logger.error(f"Chat title '{chat_name}' matches multiple conversations")
                    return None
                match = dialog.id
        if match is None:
            logger.error(f"Chat with the name '{chat_name}' was not found in the list of conversations")
        return match
