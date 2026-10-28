"""Multi-user accounts and opaque session tokens (stdlib only).

Passwords are salted PBKDF2-HMAC-SHA256 hashes; only a SHA-256 digest of each
session token is stored, so a leaked database cannot be used to log in.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

PBKDF2_ROUNDS = 210_000
TOKEN_TTL_SECONDS = 30 * 24 * 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
GUEST_RE = re.compile(r"^g_[0-9a-f]{32}$")


@dataclass(frozen=True)
class Identity:
    """The calling principal, resolved once per HTTP request."""

    key: str          # workspace directory key
    username: Optional[str]
    is_guest: bool
    token: Optional[str] = None


class AuthError(Exception):
    pass


def new_guest_id() -> str:
    return "g_" + secrets.token_hex(16)


def valid_guest_id(value: Optional[str]) -> bool:
    return bool(value and GUEST_RE.match(value))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf8")).hexdigest()


class Auth:
    def __init__(self, db_path: str, *, allow_registration: bool = True, token_ttl: int = TOKEN_TTL_SECONDS):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.allow_registration = allow_registration
        self.token_ttl = token_ttl
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            TEXT PRIMARY KEY,
                    username      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    workspace     TEXT NOT NULL UNIQUE,
                    created_at    REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id    TEXT NOT NULL,
                    expires    REAL NOT NULL
                );
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------- accounts
    def register(self, username: str, password: str) -> Identity:
        if not self.allow_registration:
            raise AuthError("Registration is disabled on this server")
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise AuthError("Username must be 3-32 characters: letters, digits, . _ -")
        if not password or len(password) < 8:
            raise AuthError("Password must be at least 8 characters")

        user_id = "u_" + secrets.token_hex(16)
        salt = secrets.token_hex(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf8"), bytes.fromhex(salt), PBKDF2_ROUNDS
        ).hex()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO users(id, username, password_hash, salt, workspace, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, username, password_hash, salt, "w_" + user_id[2:], time.time()),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise AuthError("That username is already taken") from exc
        return Identity(key="w_" + user_id[2:], username=username, is_guest=False)

    def login(self, username: str, password: str) -> tuple[str, Identity]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ?", ((username or "").strip(),)
            ).fetchone()
        if row is None:
            raise AuthError("Invalid username or password")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf8"), bytes.fromhex(row["salt"]), PBKDF2_ROUNDS
        ).hex()
        if not hmac.compare_digest(candidate, row["password_hash"]):
            raise AuthError("Invalid username or password")

        token = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO tokens(token_hash, user_id, expires) VALUES (?, ?, ?)",
                (_digest(token), row["id"], time.time() + self.token_ttl),
            )
            self._conn.execute("DELETE FROM tokens WHERE expires < ?", (time.time(),))
            self._conn.commit()
        return token, Identity(key=row["workspace"], username=row["username"], is_guest=False, token=token)

    def identity_for_token(self, token: Optional[str]) -> Optional[Identity]:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT u.*, t.expires FROM tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = ?",
                (_digest(token),),
            ).fetchone()
            if row is None:
                return None
            if row["expires"] < time.time():
                self._conn.execute("DELETE FROM tokens WHERE token_hash = ?", (_digest(token),))
                self._conn.commit()
                return None
        return Identity(key=row["workspace"], username=row["username"], is_guest=False, token=token)

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute("DELETE FROM tokens WHERE token_hash = ?", (_digest(token),))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
