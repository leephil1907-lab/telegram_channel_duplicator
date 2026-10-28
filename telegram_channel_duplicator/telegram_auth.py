"""A short-lived, per-workspace bridge for Telegram login challenges.

A hosted process cannot read from a terminal.  The worker waits on a future
while the authenticated workspace owner submits a code/password through the
same-origin dashboard. Values are never written to disk, returned in status,
or logged. Standalone CLI mode still uses the terminal callbacks.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from telethon import errors, functions, types, utils


class TelegramLoginError(RuntimeError):
    """A safe, actionable Telegram sign-in error for the workspace owner."""


class TelegramLoginUnsupported(TelegramLoginError):
    """Telegram selected a login method that this client cannot complete."""


async def request_login_code(client, phone):
    """Request a code; translate a known Telethon 1.x unsupported result."""
    try:
        return await client.send_code_request(phone)
    except AttributeError as exc:
        # Telethon 1.x can access .type before returning this newer result.
        # No payment is initiated and no code prompt is shown.
        if "SentCodePaymentRequired" in str(exc):
            raise TelegramLoginUnsupported("Telegram requires a paid or unsupported verification step. Use an official Telegram app or contact Telegram support.") from None
        raise


def code_delivery_details(sent):
    """Map Telegram's code type to a safe UI description; never expose its hash."""
    if sent is None:
        raise TelegramLoginUnsupported("Telegram did not confirm that a login code was issued. Try connecting again.")
    kind = getattr(sent, "type", None)
    if kind is None:
        raise TelegramLoginUnsupported("Telegram requires an unsupported sign-in step. Use the official Telegram app or contact Telegram support.")
    name = type(kind).__name__
    if name == "SentCodeTypeSetUpEmailRequired":
        raise TelegramLoginUnsupported("Telegram requires email setup before issuing a code. Complete this step in the official Telegram app and try again.")
    if name == "SentCodeTypeFirebaseSms":
        raise TelegramLoginUnsupported("Telegram requires Firebase SMS verification, which this third-party client cannot complete. Use Telegram's official mobile app.")
    delivery = {
        "SentCodeTypeApp": "app",
        "SentCodeTypeSms": "sms", "SentCodeTypeSmsPhrase": "sms", "SentCodeTypeSmsWord": "sms",
        "SentCodeTypeCall": "call", "SentCodeTypeFlashCall": "flashcall", "SentCodeTypeMissedCall": "missedcall",
        "SentCodeTypeEmailCode": "email", "SentCodeTypeFragmentSms": "fragment",
    }.get(name, "unknown")
    details = {"delivery": delivery}
    if name in ("SentCodeTypeSmsPhrase", "SentCodeTypeSmsWord"):
        details["code_format"] = "text"
    elif delivery != "unknown":
        details["code_format"] = "numeric"
    length = getattr(kind, "length", None)
    if type(length) is int and 1 <= length <= 20:
        details["length"] = length
    return details


async def sign_in_with_code(client, phone, code, details, phone_code_hash=None):
    """Use Telethon's regular flow, with its raw API for Telegram email codes.

    Telethon 1.x's high-level sign_in sends every code as phone_code; current
    Telegram email login instead requires auth.signIn.email_verification. The
    authorization result still goes through Telethon's normal _on_login hook.
    """
    if details.get("delivery") != "email":
        return await client.sign_in(phone=phone, code=code)
    if not isinstance(phone_code_hash, str) or not phone_code_hash:
        raise TelegramLoginUnsupported("Telegram did not provide an email verification hash. Reconnect to request another code.")
    existing = await client.get_me()
    if existing:
        return existing
    request = functions.auth.SignInRequest(
        phone_number=utils.parse_phone(phone) or phone,
        phone_code_hash=phone_code_hash,
        email_verification=types.EmailVerificationCode(code=code),
    )
    result = await client(request)
    if isinstance(result, types.auth.AuthorizationSignUpRequired):
        raise errors.PhoneNumberUnoccupiedError(request=request)
    return await client._on_login(result.user)


async def complete_login_with_challenge(client, phone, challenge, code_state):
    """Validate the Telegram code, then request two-step password only if required.

    code_state returns the latest (safe details, private hash); recovery may
    update it on resend while the one-use challenge is pending.
    """
    code_notice = None
    for _ in range(3):
        details, _private_hash = code_state()
        details = dict(details)
        if code_notice:
            details["notice"] = code_notice
        value = await challenge.request("code", details=details)
        details, private_hash = code_state()
        try:
            return await sign_in_with_code(client, phone, value, details, private_hash)
        except errors.SessionPasswordNeededError:
            password_notice = None
            for _ in range(3):
                prompt = {"notice": password_notice} if password_notice else None
                password = await challenge.request("password", details=prompt)
                try:
                    return await client.sign_in(password=password)
                except errors.PasswordHashInvalidError:
                    password_notice = "Telegram rejected the two-step password. Check it and try again."
            raise TelegramLoginError("Telegram rejected several two-step passwords. Reconnect or restart mirroring to try again.") from None
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            code_notice = "Telegram rejected the code. Check the latest code and try again."
        except (errors.PhoneCodeExpiredError, errors.PhoneCodeHashEmptyError):
            raise TelegramLoginError("Telegram code expired. Reconnect or restart mirroring to request a new code.") from None
    raise TelegramLoginError("Telegram rejected several codes. Reconnect or restart mirroring to request a new code.")


class TelegramChallenge:
    def __init__(self, timeout: float = 300):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._future: Optional[asyncio.Future] = None
        self._kind: Optional[str] = None
        self._details = {}
        self._deadline = 0.0
        self._sequence = 0
        self._claimed = False
        self._cancelled = False

    def reset(self) -> None:
        with self._lock:
            self._cancelled = False

    @staticmethod
    def _safe_details(details):
        # No code, phone number, hashes, email address or API credentials in status.
        result = {}
        if not isinstance(details, dict):
            return result
        if details.get("delivery") in {"app", "sms", "call", "flashcall", "missedcall", "email", "fragment", "unknown"}:
            result["delivery"] = details["delivery"]
        if details.get("code_format") in {"numeric", "text"}:
            result["code_format"] = details["code_format"]
        length = details.get("length")
        if type(length) is int and 1 <= length <= 20:
            result["length"] = length
        notice = details.get("notice")
        if isinstance(notice, str) and len(notice) <= 180:
            result["notice"] = notice
        return result

    async def request(self, kind: str, *, details=None) -> str:
        if kind not in ("code", "password"):
            raise ValueError("unknown Telegram challenge")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._lock:
            if self._cancelled:
                raise asyncio.CancelledError
            self._loop, self._future, self._kind = loop, future, kind
            self._details = self._safe_details(details)
            self._deadline = loop.time() + self.timeout
            self._sequence += 1
            self._claimed = False
        try:
            while True:
                with self._lock:
                    remaining = max(0.0, self._deadline - loop.time())
                try:
                    # Shield keeps the one-use future alive if a successful
                    # resend extended the deadline while this timer was running.
                    return await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    with self._lock:
                        extended = self._deadline > loop.time() and not self._cancelled
                    if not extended:
                        raise RuntimeError("Telegram verification timed out. Start again.") from exc
        finally:
            if not future.done():
                future.cancel()
            with self._lock:
                if self._future is future:
                    self._loop = self._future = self._kind = None
                    self._details = {}
                    self._deadline = 0.0
                    self._claimed = False

    def status(self):
        with self._lock:
            if self._future is None or self._claimed or self._cancelled:
                return None
            return {"kind": self._kind, "id": self._sequence, **self._details}

    def update_details(self, challenge_id, details, *, extend=False) -> bool:
        with self._lock:
            if (self._future is None or self._claimed or self._cancelled
                    or self._sequence != challenge_id):
                return False
            self._details = self._safe_details(details)
            if extend and self._loop is not None:
                self._deadline = self._loop.time() + self.timeout
            return True

    def submit(self, kind: str, value: str, *, challenge_id=None) -> bool:
        with self._lock:
            if (self._future is None or self._loop is None or self._claimed
                    or self._cancelled or kind != self._kind
                    or (challenge_id is not None and challenge_id != self._sequence)):
                return False
            loop, future = self._loop, self._future
            self._claimed = True
        def complete():
            if not future.done():
                future.set_result(value.strip() if kind == "code" else value)
        try:
            loop.call_soon_threadsafe(complete)
        except RuntimeError:
            return False
        return True

    def fail(self, challenge_id, error) -> bool:
        """Fail a pending challenge when a resend changes to an unsupported step."""
        with self._lock:
            if (self._future is None or self._loop is None or self._claimed or self._cancelled
                    or self._sequence != challenge_id):
                return False
            loop, future = self._loop, self._future
            self._claimed = True
        def complete():
            if not future.done():
                future.set_exception(error)
        try:
            loop.call_soon_threadsafe(complete)
        except RuntimeError:
            return False
        return True

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            loop, future = self._loop, self._future
        if loop is not None and future is not None:
            try:
                loop.call_soon_threadsafe(future.cancel)
            except RuntimeError:
                pass
