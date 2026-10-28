"""Multi-user control panel (stdlib HTTP server, zero front-end dependencies).

Every signed-in account AND every anonymous browser gets an isolated workspace
(config, SQLite state, Telegram session, bot thread).  The page is fully
self-contained: inline CSS/JS, no CDN, works in a sandboxed iframe.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from loguru import logger
import yaml

from .auth import AuthError, Identity, new_guest_id, valid_guest_id
from .account_recovery import RecoveryError
from .recovery_backup import MAX_UPLOAD_BYTES
from .config_controller import ConfigController, ConfigError
from .workspaces import WorkspaceManager

# The UI is kept as a packaged, self-contained HTML document.  Each app path is
# served by the same shell and has a distinct, reloadable URL.
PAGE = (Path(__file__).resolve().parent / "dashboard.html").read_text(encoding="utf8")
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(PAGE.split("<script>", 1)[1].split("</script>", 1)[0].encode("utf8")).digest()).decode("ascii")
APP_PATHS = {"/", "/index.html", "/overview", "/channels", "/configuration", "/activity", "/recovery", "/settings"}


_SECRET_LINE = re.compile(r"^\s*(account_phone|api_id|api_hash)\s*:", re.I)
_SECRET_NAMES = {"account_phone", "api_id", "api_hash"}
REDACTED = '"*** redacted ***"'


def _secret_spans(text: str):
    """Find whole YAML values, including multiline scalars, without resolving envs.

    An alias referring to an earlier value can put its node before the key. In
    that ambiguous case fail closed rather than accidentally rendering a secret.
    """
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None
    if root is None:
        return []
    if not isinstance(root, yaml.MappingNode):
        return []
    result = []
    for key, value in root.value:
        if isinstance(key, yaml.ScalarNode) and key.value.lower() in _SECRET_NAMES:
            if value.start_mark.index < key.end_mark.index:
                return None
            result.append((key.value.lower(), value.start_mark.index, value.end_mark.index, value))
    return result


def redact_config(text: str) -> str:
    spans = _secret_spans(text)
    if spans is None:
        # Never expose potentially multiline/quoted secrets from malformed YAML.
        return "# This configuration cannot be safely displayed. Replace it here or repair it on the server.\n"
    result = text
    for _key, start, end, _node in reversed(spans):
        original = text[start:end]
        line_end = "\n" if original.endswith("\n") else ""
        result = result[:start] + REDACTED + line_end + result[end:]
    return result


def merge_config_text(new_text: str, old_text: str) -> str:
    """Preserve unchanged redacted YAML values without exposing their contents."""
    old_spans = _secret_spans(old_text)
    secrets = {key: old_text[start:end] for key, start, end, _node in (old_spans or [])}
    new_spans = _secret_spans(new_text)
    if new_spans is not None:
        result = new_text
        for key, start, end, node in reversed(new_spans):
            if isinstance(node, yaml.ScalarNode) and node.value == "*** redacted ***" and key in secrets:
                replacement = secrets[key]
                # A block scalar's span already includes its trailing newline;
                # the editor's redaction line has its own newline.
                if replacement.endswith("\n") and new_text[end:end + 1] == "\n":
                    replacement = replacement[:-1]
                result = result[:start] + replacement + result[end:]
        return result

    # If the edited YAML is temporarily invalid, merge simple marker lines so
    # validation can report the actual syntax issue without discarding secrets.
    out = []
    for line in new_text.splitlines(keepends=True):
        match = _SECRET_LINE.match(line)
        ending = "\n" if line.endswith("\n") else ""
        if match and line[match.end():].strip() == REDACTED and match.group(1).lower() in secrets:
            value = secrets[match.group(1).lower()].rstrip("\n")
            out.append(line[:match.end()] + " " + value + ending)
        else:
            out.append(line)
    return "".join(out)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        manager: WorkspaceManager,
        *,
        token: Optional[str] = None,
        require_login: bool = False,
        secure_cookies: bool = False,
    ):
        super().__init__(address, DashboardHandler)
        self.manager = manager
        self.token = token
        self.require_login = require_login
        self.secure_cookies = secure_cookies


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DuplicatorDashboard/2.0"

    def log_message(self, *_args):
        pass

    # ------------------------------------------------------------- plumbing
    def _begin(self):
        self._set_cookies = []
        self._new_guest_key = None
        self._identity_cache = None

    def _cookie(self, name: str, value: str, max_age: int) -> str:
        same_site = "Lax"
        parts = [f"{name}={value}", "Path=/", "HttpOnly", f"SameSite={same_site}", f"Max-Age={max_age}"]
        if self.server.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def _headers(self, content_type="application/json; charset=utf-8"):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # The app and API are same-origin. Do not allow arbitrary sites to
        # read or issue credentialed cross-origin requests to recovery APIs.
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'sha256-{_SCRIPT_HASH}'; style-src 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; form-action 'self'; base-uri 'none'")
        for cookie in getattr(self, "_set_cookies", []):
            self.send_header("Set-Cookie", cookie)
        if getattr(self, "_new_guest_key", None):
            self.send_header("X-New-Guest-Key", self._new_guest_key)

    def _json(self, payload: Any, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self._headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download(self, data: bytes, content_type: str, filename: str):
        self.send_response(200)
        self._headers(content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self, max_bytes=1024 * 1024) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length > max_bytes:
            raise OverflowError("request body is too large")
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("request body must contain valid JSON") from exc

    def _cookies(self) -> SimpleCookie:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except Exception:  # malformed cookie header
            pass
        return jar

    def _shared_token_ok(self) -> bool:
        token = self.server.token
        if not token:
            return True
        supplied = self.headers.get("X-Token") or parse_qs(urlparse(self.path).query).get("token", [None])[0]
        return supplied == token

    def _identity(self) -> Identity:
        if self._identity_cache is not None:
            return self._identity_cache
        jar = self._cookies()
        auth_header = self.headers.get("Authorization") or ""
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else None
        if not token and "tcd_token" in jar:
            token = jar["tcd_token"].value

        identity = self.server.manager.auth.identity_for_token(token)
        if identity is not None:
            self._identity_cache = identity
            return identity

        guest = self.headers.get("X-Guest-Key")
        if not valid_guest_id(guest) and "tcd_guest" in jar:
            guest = jar["tcd_guest"].value
        if not valid_guest_id(guest):
            guest = new_guest_id()
            self._new_guest_key = guest
            self._set_cookies.append(self._cookie("tcd_guest", guest, 60 * 60 * 24 * 365))
        identity = Identity(key=guest, username=None, is_guest=True)
        self._identity_cache = identity
        return identity

    def _workspace(self, identity: Identity):
        return self.server.manager.workspace(identity.key)

    def _guard(self):
        """Return an error payload when the request may not proceed."""
        if not self._shared_token_ok():
            return {"error": "unauthorized"}, 401
        identity = self._identity()
        if self.server.require_login and identity.is_guest:
            return {"error": "login required"}, 401
        return None, None

    # -------------------------------------------------------------- routes
    def do_OPTIONS(self):
        self._begin()
        self.send_response(204)
        self._headers()
        self.end_headers()

    def do_HEAD(self):
        self._begin()
        path = urlparse(self.path).path
        if path in APP_PATHS or (path != "/" and path.rstrip("/") in APP_PATHS):
            body = PAGE.encode("utf8")
            self.send_response(200)
            self._headers("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        elif path == "/api/health":
            body = json.dumps({"ok": True}).encode("utf8")
            self.send_response(200)
            self._headers()
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_response(404)
            self._headers()
            self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._begin()
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if path in APP_PATHS or (path != "/" and path.rstrip("/") in APP_PATHS):
            body = PAGE.encode("utf8")
            self.send_response(200)
            self._headers("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            return self._json({"ok": True})

        # /api/me must stay reachable for guests so the UI can show the login gate.
        if path == "/api/me":
            if not self._shared_token_ok():
                return self._json({"error": "unauthorized"}, 401)
            identity = self._identity()
            return self._json(
                {
                    "identity": {
                        "username": identity.username,
                        "is_guest": identity.is_guest,
                        "short": identity.key[:9] if identity.is_guest else None,
                    },
                    "require_login": self.server.require_login,
                    "allow_registration": self.server.manager.auth.allow_registration,
                }
            )

        if path not in {"/api/status", "/api/events", "/api/config", "/api/recovery/status"}:
            return self._json({"error": "not found"}, 404)
        error, status = self._guard()
        if error:
            return self._json(error, status)
        identity = self._identity()
        if path == "/api/recovery/status" and identity.is_guest:
            return self._json({"error": "Sign in to use account recovery"}, 403)
        ws = self._workspace(identity)

        if path == "/api/recovery/status":
            return self._json(ws.recovery.status())
        if path == "/api/status":
            data = ws.runner.status()
            data["stats"] = ws.store.stats()
            data["identity"] = {"username": identity.username, "is_guest": identity.is_guest}
            return self._json(data)
        if path == "/api/events":
            try:
                limit = int(query.get("limit", [100])[0])
                since = int(query.get("since_id", [0])[0])
                if since < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return self._json({"error": "limit and since_id must be valid integers"}, 400)
            return self._json({"events": ws.store.events(limit=limit, since_id=since)})
        if path == "/api/config":
            raw = self._read_config(ws)
            try:
                ConfigController.validate_text(raw, use_dotenv=True, allow_unresolved_env=True)
                error = None
            except ConfigError as exc:
                error = str(exc)
            return self._json(
                {
                    "redacted": redact_config(raw),
                    "validation_error": error,
                    "env_missing": ConfigController.missing_env(raw),
                }
            )
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        self._begin()
        path = urlparse(self.path).path
        if path not in {"/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/start",
                        "/api/stop", "/api/reset", "/api/config/validate", "/api/config", "/api/telegram/challenge",
                        "/api/recovery/connect", "/api/recovery/disconnect", "/api/recovery/challenge", "/api/recovery/resend",
                        "/api/recovery/verify", "/api/recovery/export", "/api/recovery/import",
                        "/api/recovery/codes", "/api/recovery/phone/request", "/api/recovery/phone/confirm",
                        "/api/recovery/reminder", "/api/recovery/revoke"}:
            return self._json({"error": "not found"}, 404)
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._json({"error": "Content-Type must be application/json"}, 415)
        try:
            body = self._body(max_bytes=1_500_000 if path == "/api/recovery/import" else 1024 * 1024)
        except OverflowError as exc:
            return self._json({"error": str(exc)}, 413)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "request body must be a JSON object"}, 400)
        manager = self.server.manager

        # Auth endpoints must work even when login is required.
        if path in ("/api/auth/register", "/api/auth/login"):
            if not self._shared_token_ok():
                return self._json({"error": "unauthorized"}, 401)
            try:
                if path.endswith("register"):
                    manager.auth.register(str(body.get("username", "")), str(body.get("password", "")))
                token, identity = manager.auth.login(str(body.get("username", "")), str(body.get("password", "")))
            except AuthError as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            self._set_cookies.append(self._cookie("tcd_token", token, 60 * 60 * 24 * 30))
            self._set_cookies.append(self._cookie("tcd_guest", "", 0))
            self._identity_cache = identity
            return self._json(
                {"ok": True, "token": token, "identity": {"username": identity.username, "is_guest": False}}
            )
        if path == "/api/auth/logout":
            if not self._shared_token_ok():
                return self._json({"error": "unauthorized"}, 401)
            identity = self._identity()
            if not identity.is_guest:
                try:
                    manager.workspace(identity.key).recovery.disconnect()
                except RecoveryError:
                    pass  # Still invalidate the website token even if shutdown is slow.
            manager.auth.logout(identity.token)
            self._set_cookies.append(self._cookie("tcd_token", "", 0))
            return self._json({"ok": True})

        error, status = self._guard()
        if error:
            return self._json(error, status)
        identity = self._identity()
        if path.startswith("/api/recovery/") and identity.is_guest:
            return self._json({"error": "Sign in to use account recovery"}, 403)
        ws = self._workspace(identity)

        if path.startswith("/api/recovery/"):
            return self._recovery_post(path, body, identity, ws)

        # No pre-approved actions: state-changing calls require an explicit flag.
        if path in ("/api/start", "/api/stop", "/api/reset", "/api/config"):
            if body.get("confirm") is not True:
                return self._json({"error": "explicit confirmation required"}, 400)

        if path == "/api/start":
            dry_run = body.get("dry_run")
            if not isinstance(dry_run, bool):
                return self._json({"ok": False, "error": "dry_run must be a boolean"}, 400)
            ok, message = ws.runner.start(dry_run=dry_run)
            return self._json({"ok": ok, "message": message, "status": ws.runner.status()})
        if path == "/api/stop":
            ok, message = ws.runner.stop()
            return self._json({"ok": ok, "message": message})
        if path == "/api/reset":
            ok, message = ws.runner.reset_state()
            return self._json({"ok": ok, "message": message} if ok else {"ok": False, "error": message}, 200 if ok else 409)
        if path == "/api/config/validate":
            try:
                # Validation must use the same redacted-secret merge as saving.
                # Otherwise the redacted api_id in an already-saved file fails
                # validation even though saving exactly that text works.
                candidate = merge_config_text(str(body.get("yaml", "")), self._read_config(ws))
                config = ConfigController.validate_text(
                    candidate, use_dotenv=True, allow_unresolved_env=True
                )
                return self._json({"ok": True, "groups": len(config["groups"]), "delay": config["delay"]})
            except ConfigError as exc:
                return self._json({"ok": False, "error": str(exc)})
        if path == "/api/telegram/challenge":
            kind = str(body.get("kind", ""))
            value, challenge_id = body.get("value", ""), body.get("id")
            if body.get("confirm") is not True:
                return self._json({"error": "explicit confirmation required"}, 400)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                return self._json({"ok": False, "error": "Enter the requested value (up to 256 characters)"}, 400)
            if type(challenge_id) is not int or not ws.runner.challenge.submit(kind, value, challenge_id=challenge_id):
                return self._json({"ok": False, "error": "No matching Telegram verification is waiting"}, 409)
            return self._json({"ok": True, "message": "Verification submitted"})
        if path == "/api/config":
            old = self._read_config(ws)
            merged = merge_config_text(str(body.get("yaml", "")), old)
            try:
                config = ConfigController.validate_text(merged, use_dotenv=True, allow_unresolved_env=True)
            except ConfigError as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            # Replace atomically so concurrent status reads never see a partly
            # written file. The temporary file is private even before replace.
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix=".config-", dir=ws.path)
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    os.chmod(temporary, 0o600)
                    f.write(merged)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, ws.paths["config"])
                temporary = None
            except OSError:
                logger.exception("Could not save workspace configuration")
                return self._json({"ok": False, "error": "Could not save configuration on the server"}, 500)
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
            ws.store.event("config", f"Configuration updated: {len(config['groups'])} group(s)", "SUCCESS")
            return self._json({"ok": True, "message": f"{len(config['groups'])} group(s) validated"})
        return self._json({"error": "not found"}, 404)

    def _recovery_post(self, path, body, identity, ws):
        # All operations require confirmation. The one-use challenge is tied
        # to an authenticated connection that was already reauthenticated.
        if body.get("confirm") is not True:
            return self._json({"error": "explicit confirmation required"}, 400)
        if path not in ("/api/recovery/challenge", "/api/recovery/resend", "/api/recovery/disconnect"):
            password = body.get("relay_password")
            if not isinstance(password, str):
                return self._json({"error": "Re-enter your Relay password to continue"}, 403)
            try:
                self.server.manager.auth.verify_password(identity.username, password)
            except AuthError as exc:
                return self._json({"error": str(exc)}, 403)
        recovery = ws.recovery
        try:
            if path == "/api/recovery/connect":
                credentials = body.get("credentials")
                if credentials is not None and not isinstance(credentials, dict):
                    raise RecoveryError("Enter valid Telegram API credentials")
                return self._json({"ok": True, "message": recovery.connect(credentials)})
            if path == "/api/recovery/disconnect":
                return self._json({"ok": True, "message": recovery.disconnect()})
            if path == "/api/recovery/challenge":
                message = recovery.submit_challenge(body.get("kind"), body.get("value"), body.get("id"))
                return self._json({"ok": True, "message": message})
            if path == "/api/recovery/resend":
                challenge = recovery.resend_code(body.get("id"))
                return self._json({"ok": True, "challenge": challenge,
                                   "message": "Telegram accepted the request. Check the indicated delivery method."})
            if path == "/api/recovery/verify":
                return self._json({"ok": True, "account": recovery.verify()})
            if path == "/api/recovery/export":
                kind = body.get("format", "file")
                if kind not in ("file", "qr"):
                    raise RecoveryError("Choose a recovery backup file or QR image")
                data = recovery.export(body.get("backup_password"), as_qr=(kind == "qr"))
                return self._download(data, "image/png" if kind == "qr" else "application/octet-stream",
                                      "relay-recovery-qr.png" if kind == "qr" else "relay-recovery.rlybackup")
            if path == "/api/recovery/import":
                encoded = body.get("file")
                if not isinstance(encoded, str) or len(encoded) > 1_400_000:
                    raise RecoveryError("Choose a Relay backup file or QR image under 1 MB")
                try:
                    upload = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    raise RecoveryError("The uploaded recovery file is invalid") from None
                if len(upload) > MAX_UPLOAD_BYTES:
                    raise RecoveryError("Choose a Relay backup file or QR image under 1 MB")
                account = recovery.restore(upload, body.get("backup_password"))
                return self._json({"ok": True, "account": account,
                                   "message": "Backup verified with Telegram and restored. Connect the recovery session to use it."})
            if path == "/api/recovery/codes":
                return self._json({"ok": True, "messages": recovery.login_messages()})
            if path == "/api/recovery/phone/request":
                return self._json({"ok": True, "message": recovery.request_phone_change(body.get("phone"))})
            if path == "/api/recovery/phone/confirm":
                if body.get("acknowledgement") != "CHANGE":
                    raise RecoveryError("Type CHANGE to confirm the Telegram phone-number change")
                return self._json({"ok": True, **recovery.confirm_phone_change(body.get("code"))})
            if path == "/api/recovery/reminder":
                return self._json({"ok": True, "scheduled_for": recovery.schedule_reminder(body.get("days"))})
            if path == "/api/recovery/revoke":
                if body.get("acknowledgement") != "REVOKE":
                    raise RecoveryError("Type REVOKE to confirm revocation")
                return self._json({"ok": True, "message": recovery.revoke()})
        except RecoveryError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            # Do not log request bodies, user secrets or exception messages.
            logger.warning("Recovery API failed ({})", type(exc).__name__)
            return self._json({"ok": False, "error": "Recovery operation failed on the server"}, 500)
        return self._json({"error": "not found"}, 404)

    @staticmethod
    def _read_config(ws) -> str:
        try:
            with open(ws.paths["config"], "r", encoding="utf8") as f:
                return f.read()
        except OSError:
            return ""


def serve(
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    data_dir: str = "data",
    dry_run: bool = False,
    token: Optional[str] = None,
    require_login: bool = False,
    allow_registration: bool = True,
    secure_cookies: bool = False,
    bot_factory=None,
) -> None:
    """Blocking entry point used by the CLI."""
    manager = WorkspaceManager(
        data_dir,
        dry_run=dry_run,
        allow_registration=allow_registration,
        bot_factory=bot_factory,
    )
    server = DashboardServer(
        (host, port),
        manager,
        token=token,
        require_login=require_login,
        secure_cookies=secure_cookies,
    )
    logger.info(
        f"Dashboard http://{host}:{port} | data={manager.data_dir} | "
        f"login_required={require_login} | autostart=off | token={'yes' if token else 'no'}"
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        manager.close()
        server.server_close()
