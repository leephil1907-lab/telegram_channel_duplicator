"""An isolated, explicitly connected Telegram recovery session per workspace.

This client is never the mirroring client's session. Telegram challenges,
phone-change hashes and operation results stay in memory and are never logged.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

from loguru import logger
from telethon import TelegramClient, errors, functions, types
from telethon.sessions import SQLiteSession, StringSession

from .recovery_backup import (BackupError, backup_qr, decrypt_backup,
                              encrypt_backup, read_upload, validate_credentials)
from .telegram_auth import (TelegramChallenge, TelegramLoginError,
                            code_delivery_details, complete_login_with_challenge,
                            request_login_code)


class RecoveryError(Exception):
    """An actionable error that does not include credentials or Telegram text."""


def _message(exc):
    if isinstance(exc, RecoveryError):
        return str(exc)
    if isinstance(exc, (BackupError, TelegramLoginError)):
        return str(exc)
    if isinstance(exc, errors.FloodWaitError):
        return f"Telegram requested a wait of {int(exc.seconds)} seconds. Try again later."
    if isinstance(exc, errors.RPCError):
        # Map common failures without reflecting request content or secrets.
        explanation = {
            "FreshChangePhoneForbiddenError": "Telegram requires this session to be older before changing the phone number. Wait and try again.",
            "PhoneNumberInvalidError": "Telegram rejected the phone number. Check its international format.",
            "PhoneNumberOccupiedError": "Telegram reports that the new phone number is already in use.",
            "PhoneNumberBannedError": "Telegram reports that this phone number is banned. Contact Telegram support.",
            "PhoneNumberUnoccupiedError": "Telegram reports no existing account for this number. Relay cannot create or restore that account automatically.",
            "PhoneCodeInvalidError": "Telegram rejected the verification code. Check it and try again.",
            "PhoneCodeExpiredError": "The Telegram verification code expired. Request a new code.",
            "AuthKeyUnregisteredError": "Telegram no longer recognizes this recovery session. Reconnect or create a new one.",
            "SessionRevokedError": "Telegram revoked this recovery session. Reconnect or create a new one.",
            "ApiIdInvalidError": "Telegram rejected the API ID or API hash. Check your own API credentials.",
            "ApiIdPublishedFloodError": "Telegram restricted this API ID. Use your own credentials and follow Telegram's guidance.",
            "PhonePasswordFloodError": "Telegram temporarily restricted password attempts. Wait before trying again.",
        }.get(type(exc).__name__)
        return explanation or f"Telegram rejected the request ({type(exc).__name__}). Check the account and try again."
    if isinstance(exc, RuntimeError) and str(exc).startswith("Telegram verification timed out"):
        return "Telegram verification timed out. Connect again to request a new code."
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        return "Telegram did not respond in time. The action may have completed; check Telegram before retrying."
    if isinstance(exc, (ConnectionError, OSError)):
        return "Could not reach Telegram or access the private recovery files."
    return "Recovery operation failed. Check the session and try again."


def _resend_delay(sent):
    timeout = getattr(sent, "timeout", None)
    return max(30, min(timeout, 300)) if type(timeout) is int and timeout > 0 else 30


def _account(me):
    phone = str(getattr(me, "phone", "") or "")
    return {
        "name": (str(getattr(me, "first_name", "") or "")[:80] or "Telegram account"),
        "username": str(getattr(me, "username", "") or "")[:64],
        "phone_masked": ("+•••" + phone[-4:]) if phone else "Not available",
    }


def _private_file(path):
    """Create before SQLite opens it, so even its first bytes are private."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    if os.name != "nt":
        os.chmod(path, 0o600)


class RecoverySession:
    def __init__(self, root, sessions):
        self.root = os.path.join(root, "recovery")
        self.sessions = os.path.join(sessions, "recovery")
        for directory in (self.root, self.sessions):
            os.makedirs(directory, mode=0o700, exist_ok=True)
            if os.name != "nt":
                os.chmod(directory, 0o700)
        self.credentials_path = os.path.join(self.root, "credentials.json")
        self.session_path = os.path.join(self.sessions, "recovery.session")
        self.challenge = TelegramChallenge(timeout=600)
        self._lock = threading.RLock()
        self._operation = threading.RLock()
        self._thread = None
        self._loop = None
        self._task = None
        self._stop_event = None
        self._stop_requested = threading.Event()
        self._client = None
        self._state = "disconnected"
        self._account = None
        self._error = None
        self._hint = None
        self._pending_phone = None
        self._login_phone = None
        self._delivery_details = {}
        self._login_code_hash = None
        self._resend_after = 0.0

    def _load_credentials(self):
        try:
            with open(self.credentials_path, "r", encoding="utf8") as f:
                data = json.load(f)
            return validate_credentials(data.get("phone"), data.get("api_id"), data.get("api_hash"))
        except (OSError, ValueError, TypeError, AttributeError, BackupError):
            raise RecoveryError("No valid saved recovery credentials. Enter your Telegram API details to connect.") from None

    def _save_credentials(self, credentials):
        credentials = validate_credentials(**credentials)
        fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf8") as f:
                os.chmod(tmp, 0o600)
                json.dump(credentials, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.credentials_path)
            tmp = None
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def status(self):
        with self._lock:
            if self._pending_phone and time.monotonic() > self._pending_phone["expires"]:
                self._pending_phone = None
            return {
                "state": self._state,
                "account": dict(self._account) if self._account else None,
                "error": self._error,
                "hint": self._hint,
                "has_credentials": os.path.isfile(self.credentials_path),
                "has_session_file": os.path.isfile(self.session_path),
                "challenge": self.challenge.status(),
                "phone_change_pending": bool(self._pending_phone),
            }

    def connect(self, credentials=None):
        with self._operation:
            with self._lock:
                if self._thread and self._thread.is_alive():
                    raise RecoveryError("Recovery is already connected or connecting")
            explicit = bool(credentials)
            try:
                creds = validate_credentials(**credentials) if explicit else self._load_credentials()
            except (BackupError, TypeError) as exc:
                raise RecoveryError(_message(exc)) from None
            self.challenge.reset()
            self._stop_requested = threading.Event()
            with self._lock:
                self._state, self._error, self._hint, self._account = "connecting", None, None, None
                self._thread = threading.Thread(target=self._worker, args=(creds, explicit), daemon=True,
                                                name="telegram-recovery")
                self._thread.start()
        return "Connecting to Telegram. Complete any verification request shown on this page."

    def _worker(self, credentials, explicit):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._stop_event = asyncio.Event()
            self._task = loop.create_task(self._serve(credentials, explicit))
            task = self._task
        try:
            if self._stop_requested.is_set():
                task.cancel()
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Only the exception type is logged, never credentials, code, or payload.
            logger.warning("Telegram recovery connection failed ({})", type(exc).__name__)
            with self._lock:
                self._error = _message(exc)
                self._state = "error"
        finally:
            pending = asyncio.all_tasks(loop)
            for item in pending:
                item.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            with self._lock:
                if self._state != "error":
                    self._state = "disconnected"
                self._loop = self._task = self._client = self._stop_event = None
                self._account = None
                self._hint = None
                self._pending_phone = None
                self._login_phone = None
                self._delivery_details = {}
                self._login_code_hash = None
                self._resend_after = 0.0

    async def _serve(self, credentials, explicit):
        _private_file(self.session_path)
        client = TelegramClient(self.session_path, credentials["api_id"], credentials["api_hash"],
                                connection_retries=2, timeout=15, flood_sleep_threshold=0)
        # Avoid persisting chat/contact entities; only the authorization session is needed.
        client.session.save_entities = False
        with self._lock:
            self._client = client
        try:
            await client.connect()
            if self._stop_requested.is_set():
                return
            me = await client.get_me()
            if not me:
                # This is a real Telegram request. Do not show a code prompt
                # until Telegram/Telethon has accepted the code request.
                try:
                    sent = await request_login_code(client, credentials["phone"])
                except RuntimeError as exc:
                    # Telethon can report an already-authorized response instead
                    # of returning SentCode. Never assume success: ask Telegram.
                    if "logged in right after" not in str(exc):
                        raise
                    me = await client.get_me()
                    if not me:
                        raise RecoveryError("Telegram did not complete authorization. Try connecting again.") from None
                if not me:
                    details = code_delivery_details(sent)
                    with self._lock:
                        self._login_phone = credentials["phone"]
                        self._delivery_details = details
                        self._login_code_hash = getattr(sent, "phone_code_hash", None)
                        self._resend_after = time.monotonic() + _resend_delay(sent)
                    me = await self._sign_in(client, credentials["phone"])
            if not me or getattr(me, "bot", False):
                raise RecoveryError("A Telegram user account is required for recovery")
            actual_phone = str(getattr(me, "phone", "") or "")
            if explicit and not actual_phone:
                raise RecoveryError("Telegram did not confirm this recovery session's phone number. Check the account and try again.")
            if actual_phone:
                actual_phone = "+" + actual_phone.lstrip("+")
                if explicit and actual_phone != credentials["phone"]:
                    raise RecoveryError("This recovery session belongs to a different phone number. Reconnect using its saved credentials, or use a separate Relay account for the other Telegram account.")
                credentials = {**credentials, "phone": actual_phone}
            self._save_credentials(credentials)
            with self._lock:
                self._account = _account(me)
                self._state = "connected"
                self._hint = None
            if self._stop_requested.is_set():
                return
            await self._stop_event.wait()
        finally:
            await client.disconnect()

    def _code_state(self):
        with self._lock:
            return dict(self._delivery_details), self._login_code_hash

    async def _sign_in(self, client, phone):
        return await complete_login_with_challenge(client, phone, self.challenge, self._code_state)

    def disconnect(self):
        with self._operation:
            self._stop_requested.set()
            self.challenge.cancel()
            with self._lock:
                loop, task, event, thread, state = self._loop, self._task, self._stop_event, self._thread, self._state
            if loop:
                try:
                    if state == "connecting" and task:
                        loop.call_soon_threadsafe(task.cancel)
                    elif event:
                        loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    pass
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=12)
            if thread and thread.is_alive():
                raise RecoveryError("Recovery is still disconnecting. Wait and try again.")
            with self._lock:
                self._state, self._error, self._hint, self._account = "disconnected", None, None, None
                self._pending_phone = None
        return "Recovery session disconnected. Your saved session was not revoked."

    def submit_challenge(self, kind, value, challenge_id):
        with self._operation:
            active = self.challenge.status()
            if type(challenge_id) is not int or not active or active["id"] != challenge_id or active["kind"] != kind:
                raise RecoveryError("This Telegram verification is no longer waiting. Refresh and try again.")
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise RecoveryError("Enter the requested verification value (up to 256 characters)")
            if not self.challenge.submit(kind, value, challenge_id=challenge_id):
                raise RecoveryError("This Telegram verification is no longer waiting. Refresh and try again.")
            return "Verification submitted"

    def resend_code(self, challenge_id):
        """Ask Telegram for another code, only while this owner's login waits."""
        with self._operation:
            active = self.challenge.status()
            with self._lock:
                state, loop, client, phone, next_allowed = (self._state, self._loop, self._client,
                                                              self._login_phone, self._resend_after)
            if (type(challenge_id) is not int or not active or active["id"] != challenge_id
                    or active["kind"] != "code" or state != "connecting" or not loop or not client or not phone):
                raise RecoveryError("No matching Telegram code request is waiting. Reconnect to start again.")
            wait = math.ceil(next_allowed - time.monotonic())
            if wait > 0:
                raise RecoveryError(f"Wait {wait} seconds before requesting another code. Telegram may apply additional limits.")
            # Reserve a cooldown even if the network times out; Telegram may
            # already have accepted a resend despite a lost response.
            with self._lock:
                self._resend_after = time.monotonic() + 30
            async def request_again():
                return await request_login_code(client, phone)
            future = asyncio.run_coroutine_threadsafe(request_again(), loop)
            try:
                sent = future.result(timeout=45)
                details = code_delivery_details(sent)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise RecoveryError("Telegram did not respond. A code may still have been sent; check Telegram before retrying.") from None
            except TelegramLoginError as exc:
                # A resend can change delivery type/hash. Do not let the owner
                # submit an obsolete code when the new method is unsupported.
                self.challenge.fail(challenge_id, exc)
                raise RecoveryError(_message(exc)) from None
            except Exception as exc:
                raise RecoveryError(_message(exc)) from None
            with self._lock:
                self._resend_after = time.monotonic() + _resend_delay(sent)
                self._delivery_details = details
                self._login_code_hash = getattr(sent, "phone_code_hash", None)
                self._hint = None
            if not self.challenge.update_details(challenge_id, details, extend=True):
                raise RecoveryError("The verification request is no longer waiting. Check Telegram before retrying.")
            return self.challenge.status()

    def _call(self, callback, timeout=45):
        with self._lock:
            loop, client = self._loop, self._client
            if self._state != "connected" or not loop or not client:
                raise RecoveryError("Connect the recovery session first")
        future = asyncio.run_coroutine_threadsafe(callback(client), loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RecoveryError("Telegram did not respond in time. The action may have completed; check Telegram before retrying.") from None
        except Exception as exc:
            logger.warning("Telegram recovery operation failed ({})", type(exc).__name__)
            raise RecoveryError(_message(exc)) from None

    def verify(self):
        async def operation(client):
            me = await client.get_me()  # A fresh Telegram request, not a local authorization flag.
            if not me or getattr(me, "bot", False):
                raise RecoveryError("This recovery session is no longer authorized by Telegram")
            return _account(me)
        with self._operation:
            account = self._call(operation)
            with self._lock:
                self._account = account
            return account

    def export(self, password, as_qr=False):
        async def operation(client):
            me = await client.get_me()
            if not me:
                raise RecoveryError("This recovery session is no longer authorized by Telegram")
            return StringSession.save(client.session)
        with self._operation:
            session = self._call(operation)
            credentials = self._load_credentials()
            try:
                encrypted = encrypt_backup(session, credentials, password)
                return backup_qr(encrypted) if as_qr else encrypted
            except BackupError as exc:
                raise RecoveryError(str(exc)) from None

    def restore(self, upload, password):
        """Authenticate/decrypt offline, then verify with Telegram before replacing.

        Neither imported data nor its password is stored until Telegram confirms
        that the recovery session is still a live, user-owned authorization.
        """
        with self._operation:
            with self._lock:
                if self._thread and self._thread.is_alive():
                    raise RecoveryError("Disconnect the current recovery session before restoring a backup")
            try:
                payload = decrypt_backup(read_upload(upload), password)
            except BackupError as exc:
                raise RecoveryError(str(exc)) from None

            async def check():
                # The address embedded in an uploaded StringSession is untrusted:
                # connecting to it directly would permit server-side port scans.
                # Bootstrap only against Telethon's built-in Telegram endpoint,
                # fetch Telegram's current DC options, and replace the uploaded
                # address with a real endpoint for its DC before using its key.
                bootstrap = TelegramClient(StringSession(), payload["api_id"], payload["api_hash"],
                                           connection_retries=2, timeout=15, flood_sleep_threshold=0)
                try:
                    await bootstrap.connect()
                    config = await bootstrap(functions.help.GetConfigRequest())
                finally:
                    await bootstrap.disconnect()
                session = StringSession(payload["session"])
                choices = [dc for dc in config.dc_options
                           if dc.id == session.dc_id and not dc.media_only and not dc.cdn and not dc.tcpo_only]
                if not choices:
                    raise RecoveryError("Telegram did not provide a usable data center for this backup")
                choices.sort(key=lambda dc: (bool(dc.ipv6), dc.ip_address != session.server_address))
                endpoint = choices[0]
                session.set_dc(session.dc_id, endpoint.ip_address, endpoint.port)
                client = TelegramClient(session, payload["api_id"], payload["api_hash"],
                                        connection_retries=2, timeout=15, flood_sleep_threshold=0)
                try:
                    await client.connect()
                    me = await client.get_me()
                    if not me or getattr(me, "bot", False):
                        raise RecoveryError("Telegram reports that this backup session is no longer authorized")
                    return StringSession.save(client.session), me
                finally:
                    await client.disconnect()
            try:
                session_string, me = asyncio.run(asyncio.wait_for(check(), timeout=60))
            except Exception as exc:
                logger.warning("Telegram recovery restore verification failed ({})", type(exc).__name__)
                raise RecoveryError(_message(exc)) from None
            credentials = {k: payload[k] for k in ("phone", "api_id", "api_hash")}
            if getattr(me, "phone", None):
                credentials["phone"] = "+" + str(me.phone).lstrip("+")
            # Build a complete SQLite session in a private temporary file. On a
            # failed verification or build, the previous session stays intact.
            fd, tmp = tempfile.mkstemp(prefix=".restore-", suffix=".session", dir=self.sessions)
            os.close(fd)
            try:
                os.chmod(tmp, 0o600)
                source = StringSession(session_string)
                destination = SQLiteSession(tmp)
                try:
                    destination.set_dc(source.dc_id, source.server_address, source.port)
                    destination.auth_key = source.auth_key
                    destination.save()
                finally:
                    destination.close()
                # Preserve the old credential bytes so an ordinary file
                # replacement failure does not leave the prior session paired
                # with the imported account's API credentials.
                try:
                    with open(self.credentials_path, "rb") as previous:
                        old_credentials = previous.read(8193)
                    if len(old_credentials) > 8192:
                        raise RecoveryError("The existing recovery credentials file is too large")
                except FileNotFoundError:
                    old_credentials = None
                self._save_credentials(credentials)
                try:
                    os.replace(tmp, self.session_path)
                    tmp = None
                except OSError:
                    if old_credentials is None:
                        try:
                            os.unlink(self.credentials_path)
                        except FileNotFoundError:
                            pass
                    else:
                        fd, rollback = tempfile.mkstemp(prefix=".credentials-rollback-", dir=self.root)
                        try:
                            with os.fdopen(fd, "wb") as previous:
                                os.chmod(rollback, 0o600)
                                previous.write(old_credentials)
                                previous.flush()
                                os.fsync(previous.fileno())
                            os.replace(rollback, self.credentials_path)
                            rollback = None
                        finally:
                            if rollback:
                                try:
                                    os.unlink(rollback)
                                except OSError:
                                    pass
                    raise
            except Exception as exc:
                raise RecoveryError("Telegram verified the backup, but Relay could not save the recovery session on the server.") from exc
            finally:
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            return _account(me)

    def login_messages(self):
        async def operation(client):
            peer = None
            try:
                peer = await client.get_entity(777000)
            except ValueError:
                # Entity IDs need an access hash; find the official dialog
                # rather than accepting user-supplied peers or usernames.
                async for dialog in client.iter_dialogs(limit=1000):
                    if dialog.id == 777000:
                        peer = dialog.entity
                        break
            if not peer or getattr(peer, "id", None) != 777000:
                raise RecoveryError("Telegram's official service chat was not found. Open it in Telegram and try again.")
            messages = []
            async for message in client.iter_messages(peer, limit=3):
                if message.sender_id != 777000 or not message.raw_text:
                    continue
                messages.append({"text": message.raw_text[:4096],
                                 "date": message.date.isoformat() if message.date else None})
            return messages
        with self._operation:
            return self._call(operation, timeout=60)

    def request_phone_change(self, phone):
        # Reuse strict phone validation without requiring another set of API credentials.
        if not isinstance(phone, str) or not phone.startswith("+"):
            raise RecoveryError("Enter the new phone number in international format")
        try:
            phone = validate_credentials(phone, 1, "0" * 32)["phone"]
        except BackupError as exc:
            raise RecoveryError(str(exc)) from None
        async def operation(client):
            result = await client(functions.account.SendChangePhoneCodeRequest(phone, types.CodeSettings()))
            return result.phone_code_hash
        with self._operation:
            phone_hash = self._call(operation)
            with self._lock:
                self._pending_phone = {"phone": phone, "hash": phone_hash, "expires": time.monotonic() + 600}
            return "Telegram sent or requested a verification code for the new number."

    def confirm_phone_change(self, code):
        if not isinstance(code, str) or not code.strip() or len(code) > 64:
            raise RecoveryError("Enter the verification code sent by Telegram")
        with self._operation:
            with self._lock:
                pending = dict(self._pending_phone) if self._pending_phone else None
            if not pending or pending["expires"] < time.monotonic():
                raise RecoveryError("The phone-change request expired. Request a new Telegram code.")
            async def operation(client):
                return await client(functions.account.ChangePhoneRequest(pending["phone"], pending["hash"], code.strip()))
            me = self._call(operation)
            with self._lock:
                self._pending_phone = None
                self._account = _account(me)
            warning = None
            try:
                credentials = self._load_credentials()
                credentials["phone"] = pending["phone"]
                self._save_credentials(credentials)
            except (RecoveryError, OSError, BackupError):
                warning = "Telegram changed the phone number, but Relay could not update its saved credentials. Enter the new number when reconnecting."
            return {"account": _account(me), "warning": warning}

    def schedule_reminder(self, days):
        if isinstance(days, bool):
            raise RecoveryError("Choose a whole number of days between 1 and 365")
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise RecoveryError("Choose a whole number of days between 1 and 365") from None
        if not 1 <= days <= 365:
            raise RecoveryError("Choose a whole number of days between 1 and 365")
        when = datetime.now(timezone.utc) + timedelta(days=days)
        async def operation(client):
            await client.send_message("me", "Relay reminder: verify your Telegram recovery session and refresh your encrypted backup. Never share a session backup or its password.",
                                      schedule=when, parse_mode=None, link_preview=False)
        with self._operation:
            self._call(operation)
            return when.isoformat(timespec="seconds")

    def revoke(self):
        async def operation(client):
            if not await client.log_out():
                raise RecoveryError("Telegram did not confirm the recovery session was revoked")
            return True
        with self._operation:
            self._call(operation)
            # Disconnect waits for the worker to exit and clears in-memory state.
            self.disconnect()
            try:
                os.unlink(self.credentials_path)
            except FileNotFoundError:
                pass
            except OSError:
                # The authorization is already revoked. Do not misreport that.
                return "Telegram revoked the recovery session. Saved API credentials could not be deleted from the server."
            return "Telegram revoked the recovery session. Backups of this session will no longer work."
