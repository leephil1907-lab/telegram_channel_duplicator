"""Core polling/duplication loop.

v0.3 additions: SQLite state persistence, retry/backoff, dry-run mode, a
cooperative stop event, structured events for the dashboard, and restart-safe
edit mirroring.  All new behaviour is optional so the class can still be used
exactly like upstream.
"""

from __future__ import annotations

import datetime

from loguru import logger

from .client import Client
from .config_controller import ConfigController
from .message_preparer import MessagePreparer
from .resilience import RetryPolicy, call_with_retries, describe_error
from .sending_message_buffer import SendingMessageBuffer
from .timing import wait_or_stop


class Duplicator:
    def __init__(
        self,
        config=None,
        *,
        store=None,
        dry_run: bool = False,
        stop_event=None,
        retry_policy: RetryPolicy = None,
        client=None,
        paths=None,
    ):
        self.config = config if config is not None else ConfigController.get_config()
        self.store = store
        self.dry_run = dry_run
        self.stop_event = stop_event
        self.retry_policy = retry_policy or RetryPolicy()
        self.paths = paths or {}

        self.groups = None
        self.client = client or Client(
            self.config,
            store=store,
            retry_policy=self._policy(),
            sessions_dir=self.paths.get("sessions"),
        )
        self.message_preparer = MessagePreparer(self.config)
        self.sending_message_buffer = SendingMessageBuffer(
            max(
                1,
                self.config["edit_message_checker_limit"]
                * sum(len(g["sources"]) for g in self.config["groups"])
                * 5,
            )
        )

    # ------------------------------------------------------- optional helpers
    def _opt(self, name, default=None):
        """Allows tests to construct the class via ``object.__new__``."""
        return getattr(self, name, default)

    def _bump(self, name: str, by: int = 1) -> None:
        store = self._opt("store")
        if store is not None:
            store.increment(name, by)

    def _event(self, kind: str, message: str, level: str = "INFO") -> None:
        store = self._opt("store")
        if store is not None:
            store.event(kind, message, level)

    def _stopping(self) -> bool:
        event = self._opt("stop_event")
        return bool(event and event.is_set())

    def _policy(self) -> RetryPolicy:
        return self._opt("retry_policy") or RetryPolicy()

    def _on_retry(self, attempt, exc, delay):
        self._bump("retries")
        self._event("retry", f"attempt {attempt} failed ({describe_error(exc)}), retry in {delay:.1f}s", "WARNING")

    # -------------------------------------------------------------- lifecycle
    async def start(self):
        try:
            await self.client.start()
            await self.duplicate()
        finally:
            disconnect = getattr(self.client, "disconnect", None)
            if callable(disconnect):
                await disconnect()

    async def duplicate(self):
        logger.info("parse conversation account list")
        self.groups = await self.client.get_groups()
        if not self.groups:
            logger.error("No resolvable groups found - check the chat titles in config.yaml")
            self._event("error", "No resolvable groups found", "ERROR")
            return

        self._event("start", f"Mirroring started: {len(self.groups)} group(s)", "SUCCESS")
        while not self._stopping():
            logger.debug("run cycle")
            for group in self.groups:
                if self._stopping():
                    break
                logger.debug(f"process '{group['name']}' group")
                for source_channel in group["sources"]:
                    if self._stopping():
                        break
                    key = str(source_channel.channel_id())
                    try:
                        messages_history = await call_with_retries(
                            self.client.get_last_messages,
                            source_channel,
                            min_id=self._calc_channel_min_id(source_channel),
                            policy=self._policy(),
                            on_retry=self._on_retry,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._bump("failed")
                        self._event("error", f"Poll failed for {source_channel}: {describe_error(exc)}", "ERROR")
                        continue

                    self._bump("polls")
                    new_messages = self._filter_old_messages(source_channel, messages_history)
                    if new_messages:
                        await self._process_new_messages(group, source_channel, new_messages)
                    else:
                        logger.debug(f"new messages in '{source_channel}' not found")

                    await self._process_edited_messages(messages_history, key)

                    store = self._opt("store")
                    if store is not None:
                        store.set_last_id(key, source_channel.last_message_id())

            await wait_or_stop(self._opt("stop_event"), float(self.config.get("delay", 5)))
        self._event("stop", "Mirroring stopped")

    # ------------------------------------------------------------- processing
    async def _process_new_messages(self, group, source_channel, new_messages):
        if not new_messages:
            return

        # Upstream reversed this list inside the destination loop, so with two
        # or more destinations every other channel received messages backwards.
        new_messages = sorted(new_messages, key=lambda m: m.id)
        whitelist = group.get("whitelist") or []
        store = self._opt("store")
        dry_run = self._opt("dry_run", False)
        key = str(source_channel.channel_id())

        for destination_channel in group["destinations"]:
            if not destination_channel:
                continue
            for msg in new_messages:
                if not self.message_preparer.check_whitelist(msg, whitelist):
                    self._bump("filtered")
                    self._event("filtered", f"{source_channel} #{msg.id}: {_preview(msg)} skipped", "DEBUG")
                    continue

                if dry_run:
                    self._bump("mirrored")
                    self._event("dry-run", f"would send #{msg.id} to {destination_channel}")
                    continue

                try:
                    destination_message = await call_with_retries(
                        self.client.send_message,
                        destination_channel.channel_id(),
                        msg,
                        policy=self._policy(),
                        on_retry=self._on_retry,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._bump("failed")
                    self._event("error", f"Send failed for #{msg.id}: {describe_error(exc)}", "ERROR")
                    continue

                self.sending_message_buffer.put(msg, destination_message)
                if store is not None and destination_message is not None:
                    store.record_send(key, msg.id, destination_channel.channel_id(), destination_message.id)
                self._bump("mirrored")
                self._event("sent", f"{source_channel} #{msg.id} → {destination_channel}: {_preview(msg)}", "SUCCESS")

    async def _process_edited_messages(self, messages, source_key=None):
        store = self._opt("store")
        for msg in messages or []:
            edit_date = getattr(msg, "edit_date", None)
            if edit_date is None:
                continue

            key = source_key or str(getattr(msg, "chat_id", "unknown"))
            edit_ts = str(edit_date)
            if store is not None and store.is_edit_applied(key, msg.id, edit_ts):
                continue

            targets = {}
            for dest in self.sending_message_buffer.get_unedited_destination_messages(
                msg, datetime.timedelta(seconds=int(self.config.get("delay", 5)))
            ):
                targets[(dest.chat_id, dest.id)] = dest
            if store is not None:
                for row in store.destinations_for(key, msg.id):
                    targets.setdefault((row["dest_chat_id"], row["dest_message_id"]), None)

            if not targets:
                continue

            logger.info(f"detected message editing in {msg.chat_id}, text: {_preview(msg)}, copy editing")
            self._event("edited", f"#{msg.id} edited, mirroring to {len(targets)} destination(s)")
            for (chat_id, message_id), buffered in targets.items():
                new_msg = await self._safe_edit(msg, chat_id, message_id)
                if buffered is not None:
                    self.sending_message_buffer.remove_by_destination_message(buffered)
                if store is not None:
                    store.mark_edited(key, msg.id, chat_id, message_id)
                if new_msg is not None:
                    self.sending_message_buffer.put(msg, new_msg)
                    if store is not None:
                        store.record_send(key, msg.id, chat_id, new_msg.id)
                        store.mark_edited(key, msg.id, chat_id, new_msg.id)
                self._bump("edited")
            if store is not None:
                store.mark_edit_applied(key, msg.id, edit_ts)

    async def _safe_edit(self, msg, chat_id, message_id):
        if not hasattr(self.client, "edit_message"):
            return None
        try:
            return await call_with_retries(
                self.client.edit_message,
                chat_id,
                message_id,
                text=msg.message,
                policy=self._policy(),
                on_retry=self._on_retry,
            )
        except Exception as exc:  # noqa: BLE001
            self._bump("failed")
            self._event("error", f"Edit failed for #{msg.id}: {describe_error(exc)}", "ERROR")
            return None

    # --------------------------------------------------------------- history
    def _calc_channel_min_id(self, source_channel):
        channel_last_id = source_channel.last_message_id() or 0
        return max(0, channel_last_id - self.config["edit_message_checker_limit"])

    async def _wait(self, delay: float) -> None:
        await wait_or_stop(self._opt("stop_event"), delay)

    @staticmethod
    def _filter_old_messages(source_channel, messages):
        if not messages:
            return []

        newest_id = messages[-1].id
        if source_channel.last_message_id() == 0:
            source_channel.set_last_message_id(newest_id)
            logger.debug("skip first cycle")
            return []

        new_messages = [m for m in messages if m.id > source_channel.last_message_id()]
        for m in new_messages:
            logger.debug(f"parse message with id: {m.id}, text: {_preview(m)}, date: {m.date}")

        if new_messages:
            logger.debug(f"find new message with ids: {', '.join(str(m.id) for m in new_messages)}")
            logger.debug(f"last cycle id for '{source_channel}': {newest_id}")
            source_channel.set_last_message_id(newest_id)
        else:
            logger.debug("new message not found")

        return new_messages


def _preview(message):
    """Short, safe log representation (message.message may be None for media)."""
    text = getattr(message, "message", None) or "<media-only>"
    text = str(text).replace("\n", " ")
    return repr(text[:80] + ("..." if len(text) > 80 else ""))
