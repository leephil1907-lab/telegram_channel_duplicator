"""Authenticated, portable backups for a Telegram *recovery* session.

Relay's format is intentionally not compatible with legacy .tgback files:
that format is unauthenticated and deserializes pickle.  Here a versioned
header is authenticated by AES-256-GCM, and the only payload is bounded JSON.
The backup password and plaintext session are never written by this module.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import threading
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from PIL import Image
from telethon.sessions import StringSession
import qrcode
import zxingcpp

MAGIC = b"RELAY-SESSION\x00\x01"
QR_PREFIX = "RELAY-SESSION-1:"
MAX_BACKUP_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
_KDF_SLOTS = threading.BoundedSemaphore(2)
PHONE_RE = re.compile(r"^\+[1-9][0-9]{5,14}$")
HASH_RE = re.compile(r"^[a-fA-F0-9]{32}$")


class BackupError(ValueError):
    """A safe, user-facing backup format or password error."""


def validate_credentials(phone, api_id, api_hash):
    phone = str(phone or "").strip()
    api_hash = str(api_hash or "").strip()
    if not PHONE_RE.fullmatch(phone):
        raise BackupError("Enter the Telegram phone number in international format, such as +234...")
    try:
        if isinstance(api_id, bool):
            raise ValueError
        api_id = int(api_id)
    except (TypeError, ValueError):
        raise BackupError("Telegram API ID must be a positive number") from None
    if api_id <= 0 or api_id > 2**31 - 1:
        raise BackupError("Telegram API ID must be a positive number")
    if not HASH_RE.fullmatch(api_hash):
        raise BackupError("Telegram API hash must contain 32 hexadecimal characters")
    return {"phone": phone, "api_id": api_id, "api_hash": api_hash.lower()}


def _validate_payload(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise BackupError("Unsupported recovery backup")
    creds = validate_credentials(payload.get("phone"), payload.get("api_id"), payload.get("api_hash"))
    session = payload.get("session")
    if not isinstance(session, str) or not (100 <= len(session) <= 1024):
        raise BackupError("The backup does not contain a valid Telegram session")
    try:
        parsed = StringSession(session)
        if parsed.auth_key is None or not parsed.server_address or not parsed.port:
            raise ValueError
    except Exception as exc:
        # StringSession can raise struct.error or ipaddress errors; none should
        # be surfaced with payload contents.
        raise BackupError("The backup does not contain a valid Telegram session") from exc
    created = payload.get("created_at", "")
    if not isinstance(created, str) or len(created) > 64:
        raise BackupError("The backup has invalid metadata")
    return {"version": 1, **creds, "session": session, "created_at": created}


def _password(password):
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise BackupError("Use a backup password between 12 and 256 characters")
    return password.encode("utf8")


def _key(password, salt):
    secret = _password(password)
    if not _KDF_SLOTS.acquire(timeout=3):
        raise BackupError("Backup encryption is busy; please try again")
    try:
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(secret)
    finally:
        _KDF_SLOTS.release()


def encrypt_backup(session, credentials, password):
    payload = _validate_payload({
        "version": 1, **validate_credentials(**credentials), "session": session,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf8")
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_key(password, salt)).encrypt(nonce, encoded, MAGIC)
    return MAGIC + salt + nonce + ciphertext


def decrypt_backup(backup, password):
    if not isinstance(backup, bytes) or not backup.startswith(MAGIC):
        raise BackupError("Unsupported file. Legacy .tgback files are not compatible with Relay backups.")
    if not len(MAGIC) + 16 + 12 + 16 <= len(backup) <= MAX_BACKUP_BYTES:
        raise BackupError("The recovery backup is invalid or too large")
    offset = len(MAGIC)
    salt, nonce, ciphertext = backup[offset:offset + 16], backup[offset + 16:offset + 28], backup[offset + 28:]
    try:
        plaintext = AESGCM(_key(password, salt)).decrypt(nonce, ciphertext, MAGIC)
        payload = json.loads(plaintext.decode("utf8"))
    except (InvalidTag, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise BackupError("Incorrect backup password or corrupted backup") from None
    return _validate_payload(payload)


def backup_qr(backup):
    if not isinstance(backup, bytes) or not backup.startswith(MAGIC) or len(backup) > MAX_BACKUP_BYTES:
        raise BackupError("Invalid recovery backup")
    text = QR_PREFIX + base64.urlsafe_b64encode(backup).decode("ascii")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
    qr.add_data(text)
    try:
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
    except (ValueError, qrcode.exceptions.DataOverflowError) as exc:
        raise BackupError("Backup is too large for a QR image; download the file instead") from exc


def read_upload(upload):
    if not isinstance(upload, bytes) or len(upload) > MAX_UPLOAD_BYTES:
        raise BackupError("Backup file is too large")
    if upload.startswith(MAGIC):
        return upload
    if not upload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BackupError("Choose a Relay backup file or Relay backup QR image; legacy .tgback is not supported")
    try:
        with Image.open(io.BytesIO(upload)) as image:
            if image.format != "PNG" or image.width * image.height > MAX_IMAGE_PIXELS:
                raise BackupError("QR image is too large")
            code = zxingcpp.read_barcode(image.convert("RGB"), formats=zxingcpp.BarcodeFormat.QRCode)
        if not code or not code.text.startswith(QR_PREFIX):
            raise BackupError("No Relay recovery QR code was found")
        raw = base64.b64decode(code.text[len(QR_PREFIX):].encode("ascii"), altchars=b"-_", validate=True)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Could not read the recovery QR image") from exc
    if len(raw) > MAX_BACKUP_BYTES or not raw.startswith(MAGIC):
        raise BackupError("The QR image does not contain a supported Relay backup")
    return raw
