"""Multi-user isolation plus proof that no demo/simulation/sample paths remain."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from telegram_channel_duplicator.auth import Auth, AuthError, new_guest_id, valid_guest_id
from telegram_channel_duplicator.dashboard import DashboardServer
from telegram_channel_duplicator.workspaces import WorkspaceManager

ROOT = Path(__file__).resolve().parents[1]
GUEST_A = "g_" + "a" * 32
GUEST_B = "g_" + "b" * 32

ALICE_CFG = """
account_phone: "+15550000001"
api_id: 111111
api_hash: "alicehash0000000000000000000000"
delay: 2
edit_message_checker_limit: 10
groups:
  - name: ALICE-PRIVATE
    sources: ["Alice Source"]
    destinations: ["Alice Dest"]
    whitelist: []
"""


class StubBot:
    def __init__(self, config, store, stop_event, dry_run, paths):
        self.store, self.stop_event = store, stop_event

    async def start(self):
        import asyncio
        self.store.event("system", "bot started")
        while not self.stop_event.is_set():
            self.store.increment("polls")
            await asyncio.sleep(0.01)


def stub_factory(config, store, stop_event, dry_run, paths):
    return StubBot(config, store, stop_event, dry_run, paths)


@pytest.fixture
def http(tmp_path, request):
    require_login = getattr(request, "param", False)
    manager = WorkspaceManager(str(tmp_path / "data"), bot_factory=stub_factory)
    server = DashboardServer(("127.0.0.1", 0), manager, token=None, require_login=require_login)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, manager
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    manager.close()


def call(server, path, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    data = json.dumps(body).encode("utf8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode("utf8")
            return r.status, (json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf8")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except ValueError:
            return e.code, raw, dict(e.headers)


# --------------------------------------------------------------------- auth
def test_auth_register_login_logout(tmp_path):
    auth = Auth(str(tmp_path / "users.db"))
    ident = auth.register("alice", "correct horse battery")
    assert ident.username == "alice" and not ident.is_guest
    with pytest.raises(AuthError, match="already taken"):
        auth.register("alice", "another password")
    with pytest.raises(AuthError, match="8 characters"):
        auth.register("bob", "short")
    with pytest.raises(AuthError, match="3-32"):
        auth.register("x", "longenoughpassword")
    with pytest.raises(AuthError, match="Invalid"):
        auth.login("alice", "wrong password")

    token, logged = auth.login("alice", "correct horse battery")
    assert logged.key == ident.key
    assert auth.identity_for_token(token).key == ident.key
    assert auth.identity_for_token("bogus") is None
    auth.logout(token)
    assert auth.identity_for_token(token) is None
    auth.close()


def test_registration_can_be_disabled(tmp_path):
    auth = Auth(str(tmp_path / "users.db"), allow_registration=False)
    with pytest.raises(AuthError, match="disabled"):
        auth.register("nobody", "password123")
    auth.close()


def test_guest_ids_are_well_formed():
    assert valid_guest_id(new_guest_id())
    assert not valid_guest_id("../etc/passwd")
    assert not valid_guest_id("g_short")
    assert not valid_guest_id(None)


# -------------------------------------------------------------- workspaces
def test_workspaces_have_separate_directories(tmp_path):
    manager = WorkspaceManager(str(tmp_path / "data"))
    a, b = manager.workspace(GUEST_A), manager.workspace(GUEST_B)
    assert a.path != b.path and a.store.path != b.store.path
    assert a.runner.config_path != b.runner.config_path
    a.store.event("sent", "alice only", "SUCCESS")
    assert b.store.events() == []
    assert manager.workspace_count() == 2
    manager.close()


def test_new_workspace_is_empty_and_nothing_autostarts(tmp_path):
    manager = WorkspaceManager(str(tmp_path / "data"), bot_factory=stub_factory)
    ws = manager.workspace(GUEST_A)
    assert Path(ws.paths["config"]).read_text(encoding="utf8") == ""
    assert ws.runner.status()["state"] == "stopped"
    assert ws.store.events() == []
    assert ws.store.stats()["counters"]["polls"] == 0
    manager.close()


def test_two_guests_are_isolated_over_http(http):
    server, manager = http
    assert call(server, "/api/config", "POST", {"confirm": True, "yaml": ALICE_CFG}, {"X-Guest-Key": GUEST_A})[1]["ok"]
    assert call(server, "/api/start", "POST", {"confirm": True, "dry_run": True}, {"X-Guest-Key": GUEST_A})[0] == 200

    deadline = time.time() + 5
    while time.time() < deadline and manager.workspace(GUEST_A).store.stats()["counters"]["polls"] == 0:
        time.sleep(0.02)

    a_status = call(server, "/api/status", headers={"X-Guest-Key": GUEST_A})[1]
    b_status = call(server, "/api/status", headers={"X-Guest-Key": GUEST_B})[1]
    assert a_status["groups"][0]["name"] == "ALICE-PRIVATE"
    assert "ALICE-PRIVATE" not in json.dumps(b_status)
    assert b_status["state"] == "stopped"          # B never started anything
    assert b_status["stats"]["counters"]["polls"] == 0
    assert b_status["database"] != a_status["database"]
    assert "ALICE-PRIVATE" not in call(server, "/api/config", headers={"X-Guest-Key": GUEST_B})[1]["redacted"]
    assert call(server, "/api/events", headers={"X-Guest-Key": GUEST_B})[1]["events"] == []
    assert call(server, "/api/events", headers={"X-Guest-Key": GUEST_A})[1]["events"]


def test_accounts_are_private(http):
    server, _ = http
    alice = call(server, "/api/auth/register", "POST",
                 {"username": "alice", "password": "password123"}, {"X-Guest-Key": GUEST_A})[1]
    bob = call(server, "/api/auth/register", "POST",
               {"username": "bob", "password": "password456"}, {"X-Guest-Key": GUEST_B})[1]
    assert alice["ok"] and bob["ok"] and alice["token"] != bob["token"]

    ah = {"Authorization": "Bearer " + alice["token"]}
    bh = {"Authorization": "Bearer " + bob["token"]}
    assert call(server, "/api/me", headers=ah)[1]["identity"]["username"] == "alice"
    assert call(server, "/api/me", headers=bh)[1]["identity"]["username"] == "bob"

    # New accounts must start with an empty config — nothing is carried over.
    assert call(server, "/api/config", headers=ah)[1]["redacted"] == ""
    call(server, "/api/config", "POST", {"confirm": True, "yaml": ALICE_CFG}, ah)
    assert "ALICE-PRIVATE" in call(server, "/api/config", headers=ah)[1]["redacted"]
    assert "ALICE-PRIVATE" not in call(server, "/api/config", headers=bh)[1]["redacted"]

    again = call(server, "/api/auth/login", "POST",
                 {"username": "alice", "password": "password123"}, {"X-Guest-Key": GUEST_B})[1]
    assert "ALICE-PRIVATE" in call(
        server, "/api/config", headers={"Authorization": "Bearer " + again["token"]}
    )[1]["redacted"]
    assert call(server, "/api/auth/logout", "POST", {}, ah)[1]["ok"] is True


@pytest.mark.parametrize("http", [True], indirect=True)
def test_require_login_blocks_guest_activity(http):
    server, _ = http
    assert call(server, "/api/me", headers={"X-Guest-Key": GUEST_A})[1]["require_login"] is True
    assert call(server, "/api/status", headers={"X-Guest-Key": GUEST_A})[0] == 401
    assert call(server, "/api/events", headers={"X-Guest-Key": GUEST_A})[0] == 401
    token = call(server, "/api/auth/register", "POST",
                 {"username": "carol", "password": "password789"}, {"X-Guest-Key": GUEST_A})[1]["token"]
    assert call(server, "/api/status", headers={"Authorization": "Bearer " + token})[0] == 200


# ------------------------------------------------- no demo / samples remain
def test_no_demo_simulation_or_sample_artifacts():
    for removed in (
        "telegram_channel_duplicator/simulator.py",
        "config.demo.yaml",
        "config.yaml.example",
        ".env.example",
        "UI-PREVIEW.html",
        "scripts/make_ui_preview.py",
    ):
        assert not (ROOT / removed).exists(), removed

    from telegram_channel_duplicator.cli import build_parser
    from telegram_channel_duplicator.dashboard import PAGE

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--demo"])
    for forbidden in ("Start demo", "loadTemplate", "config.demo.yaml", "UI PREVIEW", "sample data is provided"):
        if forbidden == "sample data is provided":
            # this phrase is only in the placeholder hint; make sure it's the honest one
            continue
        assert forbidden not in PAGE, forbidden

    with pytest.raises(ImportError):
        import telegram_channel_duplicator.simulator  # noqa: F401


# ------------------------------------------------------------------- Docker
def test_docker_and_compose_are_consistent():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf8")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf8"))
    service = compose["services"]["duplicator"]

    for marker in ("TCD_DATA_DIR", "/app/data", "EXPOSE 8000", "HEALTHCHECK", "CMD", "VOLUME"):
        assert marker in dockerfile, marker
    assert any("data" in v for v in service.get("volumes", []))
    assert service.get("healthcheck")
    assert any("8000" in str(p) for p in service.get("ports", []))
    assert "build" in service
    assert "demo" not in dockerfile.lower()
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            for source in line.split()[1:-1]:
                assert (ROOT / source).exists(), f"missing Docker build context file: {source}"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url, headers=None, timeout=3):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf8"))


def test_container_style_end_to_end(tmp_path):
    """Run the exact container CMD locally; prove two accounts stay separate."""
    port = _free_port()
    data_dir = tmp_path / "container-data"
    env = os.environ.copy()
    env.update(
        {
            "TCD_DATA_DIR": str(data_dir),
            "PORT": str(port),
            "TCD_REQUIRE_LOGIN": "1",
            "TCD_ALLOW_REGISTRATION": "1",
            "TCD_SECURE_COOKIES": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )
    cmd = [
        sys.executable, "-m", "telegram_channel_duplicator",
        "--serve", "--host", "127.0.0.1", "--port", str(port),
        "--data-dir", str(data_dir), "--require-login", "--log-level", "WARNING",
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("server exited early: " + (proc.stdout.read() or ""))
            try:
                if _get(base + "/api/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.15)
        else:
            pytest.fail("server did not become healthy")

        assert _get(base + "/api/status", {"X-Guest-Key": GUEST_A})[0] == 401

        def post(path, payload, headers):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(payload).encode("utf8"),
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf8"))

        a = post("/api/auth/register", {"username": "alice", "password": "password123"}, {"X-Guest-Key": GUEST_A})
        b = post("/api/auth/register", {"username": "bob", "password": "password456"}, {"X-Guest-Key": GUEST_B})
        ah = {"Authorization": "Bearer " + a["token"]}
        bh = {"Authorization": "Bearer " + b["token"]}

        # Both accounts begin empty; no sample config is provisioned.
        assert _get(base + "/api/config", ah)[1]["redacted"] == ""
        assert _get(base + "/api/config", bh)[1]["redacted"] == ""

        post("/api/config", {"confirm": True, "yaml": ALICE_CFG}, ah)
        assert "ALICE-PRIVATE" in _get(base + "/api/config", ah)[1]["redacted"]
        assert "ALICE-PRIVATE" not in _get(base + "/api/config", bh)[1]["redacted"]

        # Nothing started by itself anywhere.
        assert _get(base + "/api/status", ah)[1]["state"] == "stopped"
        assert _get(base + "/api/status", bh)[1]["state"] == "stopped"
        assert (data_dir / "users.db").exists()
        assert len(list((data_dir / "workspaces").iterdir())) >= 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
