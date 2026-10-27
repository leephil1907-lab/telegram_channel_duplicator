import asyncio
import datetime

from loguru import logger

from telegram_channel_duplicator.checkpoint_store import CheckpointStore
from telegram_channel_duplicator.client import Client
from telegram_channel_duplicator.config_controller import ConfigController
from telegram_channel_duplicator.message_preparer import MessagePreparer
from telegram_channel_duplicator.sending_message_buffer import SendingMessageBuffer


class Duplicator:
    def __init__(self):
        self.config = ConfigController.get_config()
        self.groups = None
        self.client = Client(self.config)
        self.checkpoints = CheckpointStore(self.config.get("checkpoint_db"))
        self.message_preparer = MessagePreparer(self.config)
        self.sending_message_buffer = SendingMessageBuffer(
            max(
                1,
                self.config["edit_message_checker_limit"]
                * max(1, sum(len(g["sources"]) for g in self.config["groups"]))
                * 5,
            )
        )

    async def start(self):
        await self.client.start()
        await self.duplicate()

    async def duplicate(self):
        logger.info("Resolving configured source and destination chats")
        self.groups = await self.client.get_groups()

        while True:
            for group in self.groups:
                logger.debug("Processing group '{}'", group["name"])
                for source_channel in group["sources"]:
                    if source_channel.channel_id() is None:
                        continue

                    checkpoint = self.checkpoints.get(source_channel.channel_id())
                    # Re-read a small trailing window to notice recent edits, while
                    # the persistent checkpoint identifies genuinely new messages.
                    min_id = max(
                        0, checkpoint - int(self.config["edit_message_checker_limit"])
                    )
                    history = await self.client.get_last_messages(source_channel, min_id)

                    for msg in history:
                        if msg.id <= checkpoint:
                            continue

                        delivered = await self._process_one_message(
                            group, source_channel, msg
                        )
                        if not delivered:
                            logger.warning(
                                "Checkpoint for '{}' held at {} because message {} "
                                "was not delivered to every destination",
                                source_channel,
                                checkpoint,
                                msg.id,
                            )
                            break

                        # Advance only after every destination succeeded (or the
                        # message was deliberately filtered by the whitelist).
                        self.checkpoints.set(source_channel.channel_id(), msg.id)
                        checkpoint = msg.id

                    await self._process_edited_messages(history)

            await asyncio.sleep(float(self.config["delay"]))

    async def _process_one_message(self, group, source_channel, msg):
        if not self.message_preparer.check_whitelist(msg, group["whitelist"]):
            logger.debug(
                "Message {} from {} did not match the whitelist; skipping",
                msg.id,
                source_channel,
            )
            return True

        for destination in group["destinations"]:
            destination_id = destination.channel_id()
            if destination_id is None:
                logger.error("Destination '{}' could not be resolved", destination)
                return False

            if self.checkpoints.was_delivered(
                source_channel.channel_id(), msg.id, destination_id
            ):
                continue

            try:
                sent = await self.client.send_message(destination_id, msg)
            except Exception:
                logger.exception(
                    "Failed to deliver source message {} to '{}'; will retry",
                    msg.id,
                    destination,
                )
                return False

            self.checkpoints.mark_delivered(
                source_channel.channel_id(),
                msg.id,
                destination_id,
                getattr(sent, "id", None),
            )
            self.sending_message_buffer.put(msg, sent)

        return True

    async def _process_edited_messages(self, messages):
        for msg in messages:
            if msg.edit_date is None:
                continue

            destination_messages = self.sending_message_buffer.get_unedited_destination_messages(
                msg,
                datetime.timedelta(seconds=float(self.config["delay"])),
            )
            if not destination_messages:
                continue

            logger.info(
                "Detected edit to source message {} in chat {}; updating known copies",
                msg.id,
                msg.chat_id,
            )
            for dest_msg in destination_messages:
                try:
                    new_msg = await self.client.client.edit_message(
                        dest_msg.chat_id, dest_msg.id, text=msg.message
                    )
                except Exception:
                    logger.exception(
                        "Failed to update copied message {} after source edit",
                        dest_msg.id,
                    )
                    continue

                self.sending_message_buffer.remove_by_destination_message(dest_msg)
                self.sending_message_buffer.put(msg, new_msg)
