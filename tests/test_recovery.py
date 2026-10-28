"""Offline cryptography, recovery lifecycle and security tests.

Telegram is replaced only inside these tests. No test double is exposed as a
user-facing feature; live Telegram requires a separately owned account.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon import errors, functions
from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession, StringSession

from telegram_channel_duplicator.account_recovery import RecoveryError, RecoverySession
from telegram_channel_duplicator.auth import Auth, AuthError
from telegram_channel_duplicator.dashboard import DashboardServer
from telegram_channel_duplicator.recovery_backup import (
    BackupError, MAGIC, backup_qr, decrypt_backup, encrypt_backup, read_upload,
)
from telegram_channel_duplicator.workspaces import WorkspaceManager


CREDENTIALS = {"phone": "+2348000000000", "api_id": 123456,
               "api_hash": "0123456789abcdef0123456789abcdef"}
BACKUP_PASSWORD = "unique backup password with length"


def make_session(address="149.154.167.51"):
    session = StringSession()
    session.set_dc(2, address, 443)
    session.auth_key = AuthKey(os.urandom(256))
    return session.save()


def wait_until(condition, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("condition not reached before timeout")


def test_authenticated_file_and_qr_roundtrip_and_randomization():
    session = make_session()
    first = encrypt_backup(session, CREDENTIALS, BACKUP_PASSWORD)
    second = encrypt_backup(session, CREDENTIALS, BACKUP_PASSWORD)
    assert first.startswith(MAGIC) and first != second
    assert session.encode() not in first and CREDENTIALS["api_hash"].encode() not in first
    result = decrypt_backup(read_upload(first), BACKUP_PASSWORD)
    assert result["session"] == session and result["api_hash"] == CREDENTIALS["api_hash"]
    image = backup_qr(first)
    assert image.startswith(b"\x89PNG\r\n\x1a\n") and read_upload(image) == first
    assert decrypt_backup(read_upload(image), BACKUP_PASSWORD)["phone"] == CREDENTIALS["phone"]


def test_wrong_password_tampering_legacy_and_unsafe_input_are_rejected():
    data = encrypt_backup(make_session(), CREDENTIALS, BACKUP_PASSWORD)
    for corrupt in (data[:-1] + bytes([data[-1] ^ 1]), data[:len(MAGIC) + 3] + bytes([data[len(MAGIC) + 3] ^ 1]) + data[len(MAGIC) + 4:]):
        with pytest.raises(BackupError, match="password or corrupted"):
            decrypt_backup(corrupt, BACKUP_PASSWORD)
    with pytest.raises(BackupError, match="password or corrupted"):
        decrypt_backup(data, "not the right password")
    with pytest.raises(BackupError, match="Legacy"):
        decrypt_backup(b"\x80\x04potentially-dangerous-pickle", BACKUP_PASSWORD)
    with pytest.raises(BackupError, match="legacy"):
        read_upload(b"not a Relay archive")
    with pytest.raises(BackupError, match="12 and 256"):
        encrypt_backup(make_session(), CREDENTIALS, "short")
    with pytest.raises(BackupError, match="too large"):
        read_upload(b"x" * (1024 * 1024 + 1))


def test_qr_decoder_rejects_non_relay_and_oversized_images():
    import io
    import qrcode
    from PIL import Image
    wrong = io.BytesIO()
    qrcode.make("not a Relay backup").save(wrong, format="PNG")
    with pytest.raises(BackupError, match="No Relay"):
        read_upload(wrong.getvalue())
    oversized = io.BytesIO()
    Image.new("RGB", (2001, 2000), "white").save(oversized, format="PNG")
    with pytest.raises(BackupError, match="too large"):
        read_upload(oversized.getvalue())


def test_failed_restore_leaves_existing_session_untouched(tmp_path):
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    recovery._save_credentials(CREDENTIALS)
    with open(recovery.session_path, "wb") as f:
        f.write(b"prior session file")
    old_creds = open(recovery.credentials_path, "rb").read()
    archive = encrypt_backup(make_session(), CREDENTIALS, BACKUP_PASSWORD)
    with pytest.raises(RecoveryError, match="password or corrupted"):
        recovery.restore(archive, "incorrect password")
    assert open(recovery.session_path, "rb").read() == b"prior session file"
    assert open(recovery.credentials_path, "rb").read() == old_creds


def test_restore_uses_telegram_provided_dc_not_untrusted_archive_address(tmp_path, monkeypatch):
    # The uploaded session contains a private endpoint. The bootstrap client's
    # authoritative DC options must replace it before any session connects.
    import telegram_channel_duplicator.account_recovery as module
    observed = []
    fake_user = SimpleNamespace(first_name="Owner", username="owner", phone="2348000000000", bot=False)
    class Client:
        def __init__(self, session, *args, **kwargs):
            self.session = session
            self.bootstrap = session.auth_key is None
        async def connect(self):
            observed.append((self.bootstrap, self.session.server_address))
        async def disconnect(self):
            pass
        async def __call__(self, request):
            assert isinstance(request, functions.help.GetConfigRequest)
            return SimpleNamespace(dc_options=[SimpleNamespace(id=2, media_only=False, cdn=False, tcpo_only=False,
                                                                ipv6=False, ip_address="149.154.167.51", port=443)])
        async def get_me(self):
            assert self.session.server_address == "149.154.167.51"
            return fake_user
    monkeypatch.setattr(module, "TelegramClient", Client)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    archive = encrypt_backup(make_session("127.0.0.1"), CREDENTIALS, BACKUP_PASSWORD)
    account = recovery.restore(archive, BACKUP_PASSWORD)
    assert account["phone_masked"].endswith("0000")
    assert observed == [(True, None), (False, "149.154.167.51")]
    saved = SQLiteSession(recovery.session_path)
    try:
        assert saved.server_address == "149.154.167.51" and saved.auth_key
    finally:
        saved.close()
    if os.name != "nt":
        assert os.stat(recovery.root).st_mode & 0o777 == 0o700
        assert os.stat(recovery.session_path).st_mode & 0o777 == 0o600
        assert os.stat(recovery.credentials_path).st_mode & 0o777 == 0o600
    old_session = open(recovery.session_path, "rb").read()
    old_credentials = open(recovery.credentials_path, "rb").read()
    replacement_archive = encrypt_backup(make_session(), {**CREDENTIALS, "api_id": 98765}, BACKUP_PASSWORD)
    original_replace = module.os.replace
    def fail_session_replace(source, destination):
        if destination == recovery.session_path:
            raise OSError("simulated replacement failure")
        return original_replace(source, destination)
    monkeypatch.setattr(module.os, "replace", fail_session_replace)
    with pytest.raises(RecoveryError, match="could not save"):
        recovery.restore(replacement_archive, BACKUP_PASSWORD)
    assert open(recovery.session_path, "rb").read() == old_session
    assert open(recovery.credentials_path, "rb").read() == old_credentials


def test_connection_challenge_operations_and_revocation(tmp_path, monkeypatch):
    import telegram_channel_duplicator.account_recovery as module
    sent, changed = [], []
    me = SimpleNamespace(first_name="Owner", username="owner", phone="2348000000000", bot=False)
    class Client:
        def __init__(self, session, *args, **kwargs):
            self.session = SQLiteSession(session)
            self.session.set_dc(2, "149.154.167.51", 443)
            self.session.auth_key = AuthKey(os.urandom(256))
            self.session.save()
            self.authorized = False
        async def connect(self): pass
        async def disconnect(self):
            if self.session: self.session.close()
        async def get_me(self): return me if self.authorized else None
        async def send_code_request(self, phone):
            from telethon import types
            assert phone == CREDENTIALS["phone"]
            return types.auth.SentCode(type=types.auth.SentCodeTypeApp(length=5), phone_code_hash="test-hash")
        async def sign_in(self, phone=None, code=None, password=None):
            if code:
                assert code == "12345"
                raise errors.SessionPasswordNeededError(request=None)
            assert password == "two-step password"
            self.authorized = True
            return me
        async def get_entity(self, uid):
            assert uid == 777000
            return SimpleNamespace(id=777000)
        async def iter_messages(self, peer, limit):
            assert peer.id == 777000 and limit == 3
            yield SimpleNamespace(sender_id=777000, raw_text="Telegram login code: 98765", date=datetime.now(timezone.utc))
            yield SimpleNamespace(sender_id=42, raw_text="not official", date=datetime.now(timezone.utc))
        async def __call__(self, request):
            if isinstance(request, functions.account.SendChangePhoneCodeRequest):
                changed.append(request.phone_number)
                return SimpleNamespace(phone_code_hash="private-hash")
            assert isinstance(request, functions.account.ChangePhoneRequest)
            assert request.phone_code_hash == "private-hash" and request.phone_code == "67890"
            me.phone = request.phone_number.lstrip("+")
            return me
        async def send_message(self, entity, text, **kwargs):
            sent.append((entity, text, kwargs))
        async def log_out(self):
            self.session.delete(); self.session = None; self.authorized = False
            return True
    monkeypatch.setattr(module, "TelegramClient", Client)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    recovery.connect(CREDENTIALS)
    challenge = wait_until(lambda: recovery.status()["challenge"])
    assert challenge["kind"] == "code"
    with pytest.raises(RecoveryError): recovery.submit_challenge("code", "12345", challenge["id"] + 1)
    recovery.submit_challenge("code", "12345", challenge["id"])
    next_challenge = wait_until(lambda: (c if (c := recovery.status()["challenge"]) and c["kind"] == "password" else None))
    recovery.submit_challenge("password", "two-step password", next_challenge["id"])
    wait_until(lambda: recovery.status()["state"] == "connected")
    status = recovery.status()
    assert CREDENTIALS["api_hash"] not in json.dumps(status) and "2348000000000" not in json.dumps(status)
    assert recovery.verify()["name"] == "Owner"
    archive = recovery.export(BACKUP_PASSWORD)
    assert decrypt_backup(archive, BACKUP_PASSWORD)["api_id"] == CREDENTIALS["api_id"]
    assert read_upload(recovery.export(BACKUP_PASSWORD, as_qr=True)).startswith(MAGIC)
    messages = recovery.login_messages()
    assert len(messages) == 1 and "98765" in messages[0]["text"]
    recovery.request_phone_change("+2348111111111")
    assert recovery.status()["phone_change_pending"]
    result = recovery.confirm_phone_change("67890")
    assert result["account"]["phone_masked"].endswith("1111") and not recovery.status()["phone_change_pending"]
    assert recovery._load_credentials()["phone"] == "+2348111111111"
    scheduled = recovery.schedule_reminder(30)
    assert sent[0][0] == "me" and "backup" in sent[0][1] and sent[0][2]["schedule"].isoformat(timespec="seconds") == scheduled
    assert "revoked" in recovery.revoke().lower()
    assert not os.path.exists(recovery.credentials_path)
    assert recovery.status()["state"] == "disconnected"


def test_password_reauthentication_is_rate_limited_and_recovers(tmp_path, monkeypatch):
    import telegram_channel_duplicator.auth as module
    clock = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    auth = Auth(str(tmp_path / "users.db"))
    try:
        auth.register("alice", "correct password")
        for _ in range(5):
            with pytest.raises(AuthError, match="Invalid"):
                auth.verify_password("alice", "wrong password")
        with pytest.raises(AuthError, match="Too many"):
            auth.verify_password("alice", "correct password")
        clock[0] += 901
        auth.verify_password("alice", "correct password")
        assert len(auth._attempts) == 0
        with pytest.raises(AuthError):
            auth.verify_password("x" * 100000, "wrong password")
        assert len(auth._attempts) == 0
    finally:
        auth.close()


def http(server, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read()), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), exc.headers


def test_recovery_http_requires_account_confirmation_and_password_and_is_workspace_isolated(tmp_path, monkeypatch):
    manager = WorkspaceManager(str(tmp_path / "data"))
    server = DashboardServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        manager.auth.register("alice", "correct password")
        manager.auth.register("bob", "another password")
        token_a, identity_a = manager.auth.login("alice", "correct password")
        token_b, identity_b = manager.auth.login("bob", "another password")
        assert http(server, "/api/recovery/status")[0] == 403
        assert http(server, "/api/recovery/codes", {"confirm": True})[0] == 403
        assert http(server, "/api/recovery/resend", {"confirm": True, "id": 1})[0] == 403
        assert http(server, "/api/recovery/resend", {"id": 1}, token_a)[0] == 400
        assert http(server, "/api/recovery/resend", {"confirm": True, "id": 1}, token_a)[0] == 400
        assert http(server, "/api/recovery/codes", {}, token_a)[0] == 400
        assert http(server, "/api/recovery/codes", {"confirm": True}, token_a)[0] == 403
        assert http(server, "/api/recovery/codes", {"confirm": True, "relay_password": "wrong"}, token_a)[0] == 403
        a, b = manager.workspace(identity_a.key), manager.workspace(identity_b.key)
        monkeypatch.setattr(a.recovery, "login_messages", lambda: [{"text": "private service message", "date": None}])
        code, result, headers = http(server, "/api/recovery/codes", {"confirm": True, "relay_password": "correct password"}, token_a)
        assert code == 200 and result["messages"][0]["text"] == "private service message"
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "script-src 'sha256-" in headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in headers
        code, result, _ = http(server, "/api/recovery/codes", {"confirm": True, "relay_password": "another password"}, token_b)
        assert code == 400 and "Connect" in result["error"]
        assert "private service message" not in json.dumps(b.store.events())
        assert http(server, "/api/recovery/revoke", {"confirm": True, "relay_password": "correct password"}, token_a)[0] == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5); manager.close()


@pytest.mark.parametrize("existing_phone", ["15551234567", None])
def test_recovery_never_reuses_different_or_unverifiable_saved_account(tmp_path, monkeypatch, existing_phone):
    import telegram_channel_duplicator.account_recovery as module
    class ExistingClient:
        def __init__(self, path, *_args, **_kwargs):
            self.session = SQLiteSession(path)
        async def connect(self):
            pass
        async def get_me(self):
            return SimpleNamespace(first_name="Other", phone=existing_phone, bot=False)
        async def send_code_request(self, _phone):
            raise AssertionError("A saved authorization must not silently switch Telegram accounts")
        async def disconnect(self):
            self.session.close()
    monkeypatch.setattr(module, "TelegramClient", ExistingClient)
    recovery = RecoverySession(str(tmp_path), str(tmp_path / "sessions"))
    try:
        recovery.connect(CREDENTIALS)
        wait_until(lambda: recovery.status()["state"] == "error")
        result = recovery.status()
        assert result["challenge"] is None and not result["has_credentials"]
        assert ("separate Relay account" if existing_phone else "did not confirm") in result["error"]
    finally:
        recovery.disconnect()
