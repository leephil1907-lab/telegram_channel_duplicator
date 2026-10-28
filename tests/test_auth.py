"""Signup, sign-in and session-token regression tests."""

import pytest

from telegram_channel_duplicator.auth import Auth, AuthError


def test_registration_rejects_password_shorter_than_eight_characters(tmp_path):
    auth = Auth(str(tmp_path / "users.db"))
    try:
        with pytest.raises(AuthError, match="at least 8 characters"):
            auth.register("test_user", "short7")
    finally:
        auth.close()


def test_register_login_and_session_persistence(tmp_path):
    db_path = str(tmp_path / "users.db")
    auth = Auth(db_path)
    try:
        created = auth.register("test_user", "correct-horse-8")
        assert created.username == "test_user"
        assert created.is_guest is False

        token, identity = auth.login("test_user", "correct-horse-8")
        assert identity.username == "test_user"
        assert identity.key == created.key
        assert identity.is_guest is False

        resolved = auth.identity_for_token(token)
        assert resolved is not None
        assert resolved.username == "test_user"
        assert resolved.key == created.key

        auth.close()
        auth = Auth(db_path)
        restored = auth.identity_for_token(token)
        assert restored is not None
        assert restored.username == "test_user"
        assert restored.key == created.key
    finally:
        auth.close()


def test_login_rejects_wrong_password(tmp_path):
    auth = Auth(str(tmp_path / "users.db"))
    try:
        auth.register("test_user", "correct-horse-8")
        with pytest.raises(AuthError, match="Invalid username or password"):
            auth.login("test_user", "wrong-password")
    finally:
        auth.close()


def test_logout_invalidates_session_token(tmp_path):
    auth = Auth(str(tmp_path / "users.db"))
    try:
        auth.register("test_user", "correct-horse-8")
        token, _ = auth.login("test_user", "correct-horse-8")
        assert auth.identity_for_token(token) is not None
        auth.logout(token)
        assert auth.identity_for_token(token) is None
    finally:
        auth.close()
