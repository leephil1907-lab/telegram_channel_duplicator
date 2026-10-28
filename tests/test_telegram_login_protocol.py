"""Exercise real Telethon auth methods with a test-only RPC boundary.

No live Telegram traffic or credentials are used. Unlike replacing the whole
client, this checks Telethon's real send_code_request, sign_in, code-hash
handling, start(), and the application's challenge transitions.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon import TelegramClient, errors, functions, types
from telethon.crypto import AuthKey
from telethon.client import auth as telethon_auth

import telegram_channel_duplicator.account_recovery as recovery_module
import telegram_channel_duplicator.client as mirroring_module
from telegram_channel_duplicator.account_recovery import RecoveryError, RecoverySession
from telegram_channel_duplicator.telegram_auth import TelegramChallenge

CREDS = {"phone": "+2348000000000", "api_id": 123456,
         "api_hash": "0123456789abcdef0123456789abcdef"}


def wait_until(condition, seconds=5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = condition()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("condition was not reached")


def protocol_client(*, two_step=True, sent_type="app", send_error=False, resend_type="sms"):
    """Actual Telethon methods; only connect/get_me and MTProto transport are replaced."""
    class ProtocolClient(TelegramClient):
        instances = []
        def __init__(self, session, api_id, api_hash, **kwargs):
            super().__init__(session, api_id, api_hash, **kwargs)
            self._test_connected = False
            self._test_user = types.User(id=123, first_name="Owner", phone="2348000000000", is_self=True)
            self.requests = []
            self.session.set_dc(2, "149.154.167.51", 443)
            self.session.auth_key = AuthKey(os.urandom(256))
            self.session.save()
            self.instances.append(self)
        async def connect(self):
            self._test_connected = True
            return True
        def is_connected(self):
            return self._test_connected
        async def disconnect(self):
            self._test_connected = False
            if self.session is not None:
                self.session.close()
        async def get_me(self, input_peer=False):
            return self._test_user if self._authorized is True else None
        async def __call__(self, request, *args, **kwargs):
            self.requests.append(request)
            if isinstance(request, functions.auth.SendCodeRequest):
                if send_error:
                    raise errors.PhoneNumberInvalidError(request=request)
                if sent_type == "payment-required":
                    return types.auth.SentCodePaymentRequired(store_product="premium", phone_code_hash="test-hash",
                        support_email_address="support@example.org", support_email_subject="test", premium_days=1,
                        currency="USD", amount=1)
                if sent_type == "email-setup":
                    kind = types.auth.SentCodeTypeSetUpEmailRequired()
                elif sent_type == "email":
                    kind = types.auth.SentCodeTypeEmailCode(email_pattern="a***@example.org", length=5)
                elif sent_type == "firebase":
                    kind = types.auth.SentCodeTypeFirebaseSms(length=5)
                else:
                    kind = types.auth.SentCodeTypeApp(length=5)
                return types.auth.SentCode(type=kind, phone_code_hash="first-hash", timeout=30)
            if isinstance(request, functions.auth.ResendCodeRequest):
                assert request.phone_code_hash == "first-hash"
                kind = (types.auth.SentCodeTypeFirebaseSms(length=5) if resend_type == "firebase"
                        else types.auth.SentCodeTypeEmailCode(email_pattern="a***@example.org", length=5) if resend_type == "email"
                        else types.auth.SentCodeTypeSms(length=5))
                return types.auth.SentCode(type=kind, phone_code_hash="second-hash", timeout=30)
            if isinstance(request, functions.auth.SignInRequest):
                if sent_type == "email" or resend_type == "email":
                    assert request.phone_code is None
                    assert isinstance(request.email_verification, types.EmailVerificationCode)
                    assert request.phone_code_hash == ("second-hash" if resend_type == "email" else "first-hash")
                    code = request.email_verification.code
                else:
                    code = request.phone_code
                if code == "expired":
                    raise errors.PhoneCodeExpiredError(request=request)
                if code != "12345":
                    raise errors.PhoneCodeInvalidError(request=request)
                if two_step:
                    raise errors.SessionPasswordNeededError(request=request)
                return types.auth.Authorization(user=self._test_user)
            if isinstance(request, functions.account.GetPasswordRequest):
                return SimpleNamespace()
            if isinstance(request, functions.auth.CheckPasswordRequest):
                if request.password.test_value != "correct two-step password":
                    raise errors.PasswordHashInvalidError(request=request)
                return types.auth.Authorization(user=self._test_user)
            if isinstance(request, functions.updates.GetStateRequest):
                return types.updates.State(pts=1, qts=0, date=datetime.now(timezone.utc), seq=1, unread_count=0)
            if isinstance(request, functions.updates.GetDifferenceRequest):
                return types.updates.DifferenceEmpty(date=datetime.now(timezone.utc), seq=1)
            raise AssertionError(f"Unexpected Telegram request: {type(request).__name__}")
    return ProtocolClient


def setup_protocol(monkeypatch, **kwargs):
    cls = protocol_client(**kwargs)
    # The SRP calculation itself belongs to Telethon. The test boundary checks
    # that the password branch performs GetPassword + CheckPassword without
    # requiring a real server-provided SRP challenge.
    monkeypatch.setattr(telethon_auth.pwd_mod, "compute_check",
                        lambda _pwd, value: SimpleNamespace(test_value=value))
    return cls


def test_recovery_real_telethon_methods_send_resend_code_then_require_two_step(tmp_path, monkeypatch):
    cls = setup_protocol(monkeypatch, two_step=True)
    monkeypatch.setattr(recovery_module, "TelegramClient", cls)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDS)
        code = wait_until(lambda: recovery.status()["challenge"])
        client = cls.instances[-1]
        sent = [r for r in client.requests if isinstance(r, functions.auth.SendCodeRequest)]
        assert len(sent) == 1 and sent[0].phone_number == "2348000000000"
        assert sent[0].api_id == CREDS["api_id"] and sent[0].api_hash == CREDS["api_hash"]
        assert code["kind"] == "code" and code["delivery"] == "app" and code["length"] == 5
        assert client._authorized is not True and recovery.status()["state"] == "connecting"
        with pytest.raises(RecoveryError, match="Wait"):
            recovery.resend_code(code["id"])
        with recovery._lock:
            recovery._resend_after = 0
        resent = recovery.resend_code(code["id"])
        assert resent["delivery"] == "sms"
        assert len([r for r in client.requests if isinstance(r, functions.auth.ResendCodeRequest)]) == 1
        recovery.submit_challenge("code", "00000", resent["id"])
        retry = wait_until(lambda: (c if (c := recovery.status()["challenge"]) and c["id"] != resent["id"] else None))
        assert retry["delivery"] == "sms" and "rejected" in retry["notice"]
        with pytest.raises(RecoveryError):
            recovery.submit_challenge("code", "12345", resent["id"])
        recovery.submit_challenge("code", "12345", retry["id"])
        password = wait_until(lambda: (c if (c := recovery.status()["challenge"]) and c["kind"] == "password" else None))
        signins = [r for r in client.requests if isinstance(r, functions.auth.SignInRequest)]
        assert signins[-1].phone_code_hash == "second-hash" and client._authorized is not True
        recovery.submit_challenge("password", "wrong two-step password", password["id"])
        retry_password = wait_until(lambda: (c if (c := recovery.status()["challenge"]) and c["id"] != password["id"] else None))
        assert retry_password["kind"] == "password" and "rejected" in retry_password["notice"]
        assert client._authorized is not True
        recovery.submit_challenge("password", "correct two-step password", retry_password["id"])
        wait_until(lambda: recovery.status()["state"] == "connected")
        assert client._authorized is True and recovery.status()["account"]["name"] == "Owner"
        assert len([r for r in client.requests if isinstance(r, functions.account.GetPasswordRequest)]) == 2
        assert len([r for r in client.requests if isinstance(r, functions.auth.CheckPasswordRequest)]) == 2
        assert "two-step password" not in str(recovery.status())
    finally:
        recovery.disconnect()


def test_recovery_no_two_step_logs_in_after_correct_code_only(tmp_path, monkeypatch):
    cls = setup_protocol(monkeypatch, two_step=False)
    monkeypatch.setattr(recovery_module, "TelegramClient", cls)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDS)
        code = wait_until(lambda: recovery.status()["challenge"])
        recovery.submit_challenge("code", "12345", code["id"])
        wait_until(lambda: recovery.status()["state"] == "connected")
        assert not any(isinstance(r, functions.account.GetPasswordRequest) for r in cls.instances[-1].requests)
    finally:
        recovery.disconnect()


@pytest.mark.parametrize("case", ["send-error", "email-setup", "firebase", "payment-required", "expired"])
def test_no_fabricated_code_prompt_or_login_when_telegram_rejects(tmp_path, monkeypatch, case):
    cls = setup_protocol(monkeypatch, two_step=False, sent_type=case if case in ("email-setup", "firebase", "payment-required") else "app",
                         send_error=case == "send-error")
    monkeypatch.setattr(recovery_module, "TelegramClient", cls)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDS)
        if case == "expired":
            code = wait_until(lambda: recovery.status()["challenge"])
            recovery.submit_challenge("code", "expired", code["id"])
        wait_until(lambda: recovery.status()["state"] == "error")
        status = recovery.status()
        assert status["challenge"] is None and cls.instances[-1]._authorized is not True
        assert ("phone number" in status["error"].lower() if case == "send-error" else
                "email setup" in status["error"].lower() if case == "email-setup" else
                "official mobile app" in status["error"].lower() if case == "firebase" else
                "paid or unsupported" in status["error"].lower() if case == "payment-required" else
                "expired" in status["error"].lower())
    finally:
        recovery.disconnect()


def test_mirroring_real_telethon_start_requests_code_and_two_step(tmp_path, monkeypatch):
    cls = setup_protocol(monkeypatch, two_step=True)
    monkeypatch.setattr(mirroring_module, "TelegramClient", cls)
    async def scenario():
        challenge = TelegramChallenge(timeout=2)
        config = {"account_phone": CREDS["phone"], "api_id": CREDS["api_id"], "api_hash": CREDS["api_hash"]}
        client = mirroring_module.Client(config, sessions_dir=str(tmp_path / "sessions"), auth_challenge=challenge)
        task = asyncio.create_task(client.start())
        for _ in range(100):
            await asyncio.sleep(0)
            if challenge.status(): break
        code = challenge.status()
        assert code and code["kind"] == "code"
        assert any(isinstance(r, functions.auth.SendCodeRequest) for r in client.client.requests)
        assert challenge.submit("code", "00000", challenge_id=code["id"])
        for _ in range(100):
            await asyncio.sleep(0)
            if challenge.status() and challenge.status()["id"] != code["id"]: break
        retry = challenge.status()
        assert retry and retry["kind"] == "code" and "rejected" in retry["notice"]
        assert challenge.submit("code", "12345", challenge_id=retry["id"])
        for _ in range(100):
            await asyncio.sleep(0)
            if challenge.status() and challenge.status()["kind"] == "password": break
        password = challenge.status()
        assert password and password["kind"] == "password" and client.client._authorized is not True
        assert challenge.submit("password", "incorrect password", challenge_id=password["id"])
        for _ in range(100):
            await asyncio.sleep(0)
            if challenge.status() and challenge.status()["id"] != password["id"]: break
        retry_password = challenge.status()
        assert retry_password and "rejected" in retry_password["notice"]
        assert challenge.submit("password", "correct two-step password", challenge_id=retry_password["id"])
        await task
        assert client.client._authorized is True
        await client.disconnect()
    asyncio.run(scenario())


def test_legacy_mirroring_session_for_other_number_is_not_silently_reused(tmp_path, monkeypatch):
    instances = []
    class SavedClient:
        def __init__(self, session, *_args):
            self.path = session
            self.authorized = session.endswith("account_session")
            self.phone = "15551234567" if self.authorized else "2348000000000"
            self.started = False
            instances.append(self)
        async def connect(self): pass
        async def disconnect(self): pass
        async def get_me(self):
            return SimpleNamespace(phone=self.phone, bot=False) if self.authorized else None
        async def start(self, **kwargs):
            self.started = True
            self.authorized = True
    monkeypatch.setattr(mirroring_module, "TelegramClient", SavedClient)
    sessions = tmp_path / "sessions"; sessions.mkdir()
    (sessions / "account_session.session").write_bytes(b"legacy")
    config = {"account_phone": CREDS["phone"], "api_id": CREDS["api_id"], "api_hash": CREDS["api_hash"]}
    client = mirroring_module.Client(config, sessions_dir=str(sessions))
    asyncio.run(client.start())
    assert len(instances) == 2 and not instances[0].started and instances[1].started
    assert client._session_path != client._legacy_session_path
    assert (sessions / "account_session.session").read_bytes() == b"legacy"
    if os.name != "nt":
        assert os.stat(client._session_path + ".session").st_mode & 0o777 == 0o600


def test_matching_legacy_session_is_reused_without_requesting_new_code(tmp_path, monkeypatch):
    class ExistingClient:
        def __init__(self, path, *_args):
            self.path = path
        async def connect(self): pass
        async def get_me(self):
            return SimpleNamespace(phone="2348000000000", bot=False)
        async def start(self, **_kwargs):
            raise AssertionError("A valid matching session should not request another login")
    monkeypatch.setattr(mirroring_module, "TelegramClient", ExistingClient)
    sessions = tmp_path / "sessions"; sessions.mkdir()
    (sessions / "account_session.session").write_bytes(b"legacy")
    config = {"account_phone": CREDS["phone"], "api_id": CREDS["api_id"], "api_hash": CREDS["api_hash"]}
    client = mirroring_module.Client(config, sessions_dir=str(sessions))
    asyncio.run(client.start())
    assert client._session_path == client._legacy_session_path


def test_mismatched_number_keyed_session_fails_closed(tmp_path, monkeypatch):
    import hashlib
    class WrongAccount:
        def __init__(self, path, *_args): self.path = path
        async def connect(self): pass
        async def get_me(self): return SimpleNamespace(phone="15551234567", bot=False)
    monkeypatch.setattr(mirroring_module, "TelegramClient", WrongAccount)
    sessions = tmp_path / "sessions"; sessions.mkdir()
    fingerprint = hashlib.sha256(b"2348000000000").hexdigest()[:24]
    (sessions / f"account_{fingerprint}.session").write_bytes(b"wrong account")
    config = {"account_phone": CREDS["phone"], "api_id": CREDS["api_id"], "api_hash": CREDS["api_hash"]}
    client = mirroring_module.Client(config, sessions_dir=str(sessions))
    with pytest.raises(RuntimeError, match="does not match"):
        asyncio.run(client.start())


def test_recovery_email_code_uses_telegram_email_verification_and_2fa(tmp_path, monkeypatch):
    cls = setup_protocol(monkeypatch, two_step=True, sent_type="email")
    monkeypatch.setattr(recovery_module, "TelegramClient", cls)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDS)
        code = wait_until(lambda: recovery.status()["challenge"])
        assert code["delivery"] == "email" and code["length"] == 5
        assert "example.org" not in str(code) and "first-hash" not in str(code)
        recovery.submit_challenge("code", "12345", code["id"])
        password = wait_until(lambda: (c if (c := recovery.status()["challenge"]) and c["kind"] == "password" else None))
        assert cls.instances[-1]._authorized is not True
        recovery.submit_challenge("password", "correct two-step password", password["id"])
        wait_until(lambda: recovery.status()["state"] == "connected")
        request = next(r for r in cls.instances[-1].requests if isinstance(r, functions.auth.SignInRequest))
        assert request.email_verification.code == "12345" and request.phone_code is None
    finally:
        recovery.disconnect()


def test_mirroring_email_code_and_expired_code_use_real_telethon_requests(tmp_path, monkeypatch):
    async def email_scenario():
        cls = setup_protocol(monkeypatch, two_step=False, sent_type="email")
        monkeypatch.setattr(mirroring_module, "TelegramClient", cls)
        challenge = TelegramChallenge(timeout=2)
        config = {"account_phone": CREDS["phone"], "api_id": CREDS["api_id"], "api_hash": CREDS["api_hash"]}
        client = mirroring_module.Client(config, sessions_dir=str(tmp_path / "email"), auth_challenge=challenge)
        task = asyncio.create_task(client.start())
        await asyncio.sleep(0)
        prompt = challenge.status()
        assert prompt and prompt["delivery"] == "email"
        assert challenge.submit("code", "12345", challenge_id=prompt["id"])
        await task
        req = next(r for r in client.client.requests if isinstance(r, functions.auth.SignInRequest))
        assert req.email_verification.code == "12345" and req.phone_code is None
        assert client.client._authorized is True
        await client.disconnect()

    async def expired_scenario():
        cls = setup_protocol(monkeypatch, two_step=False)
        monkeypatch.setattr(mirroring_module, "TelegramClient", cls)
        challenge = TelegramChallenge(timeout=2)
        config = {"account_phone": CREDS["phone"], "api_id": CREDS["api_id"], "api_hash": CREDS["api_hash"]}
        client = mirroring_module.Client(config, sessions_dir=str(tmp_path / "expired"), auth_challenge=challenge)
        task = asyncio.create_task(client.start())
        await asyncio.sleep(0)
        prompt = challenge.status()
        assert challenge.submit("code", "expired", challenge_id=prompt["id"])
        with pytest.raises(RuntimeError, match="expired.*restart mirroring"):
            await task
        assert challenge.status() is None and client.client._authorized is not True
        await client.disconnect()

    asyncio.run(email_scenario())
    asyncio.run(expired_scenario())


def test_resend_that_changes_to_unsupported_firebase_closes_stale_prompt(tmp_path, monkeypatch):
    cls = setup_protocol(monkeypatch, two_step=False, resend_type="firebase")
    monkeypatch.setattr(recovery_module, "TelegramClient", cls)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDS)
        code = wait_until(lambda: recovery.status()["challenge"])
        with recovery._lock:
            recovery._resend_after = 0
        with pytest.raises(recovery_module.RecoveryError, match="official mobile app"):
            recovery.resend_code(code["id"])
        wait_until(lambda: recovery.status()["state"] == "error")
        assert recovery.status()["challenge"] is None
        assert "official mobile app" in recovery.status()["error"]
        assert cls.instances[-1]._authorized is not True
        with pytest.raises(recovery_module.RecoveryError):
            recovery.submit_challenge("code", "12345", code["id"])
    finally:
        recovery.disconnect()


def test_recovery_resend_can_change_to_email_code_and_new_hash(tmp_path, monkeypatch):
    cls = setup_protocol(monkeypatch, two_step=False, sent_type="app", resend_type="email")
    monkeypatch.setattr(recovery_module, "TelegramClient", cls)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDS)
        code = wait_until(lambda: recovery.status()["challenge"])
        assert code["delivery"] == "app"
        with recovery._lock:
            recovery._resend_after = 0
        assert recovery.resend_code(code["id"])["delivery"] == "email"
        recovery.submit_challenge("code", "12345", code["id"])
        wait_until(lambda: recovery.status()["state"] == "connected")
        request = next(r for r in cls.instances[-1].requests if isinstance(r, functions.auth.SignInRequest))
        assert request.phone_code_hash == "second-hash" and request.email_verification.code == "12345"
    finally:
        recovery.disconnect()
