"""HTTP-level tests for the multi-user dashboard (localhost sockets only)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from telegram_channel_duplicator.dashboard import DashboardServer, merge_config_text, redact_config
from telegram_channel_duplicator.workspaces import WorkspaceManager


class StubBot:
    """In-memory test double; lives only in tests, never shipped as a mode."""

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

GUEST_A = "g_" + "a" * 32

VALID_YAML = """
account_phone: "+15551234567"
api_id: 123456
api_hash: "abcdef1234567890"
delay: 2
groups:
  - name: my-mirror
    sources: ["Source A"]
    destinations: ["Dest B"]
    whitelist: []
"""


@pytest.fixture
def dash(tmp_path):
    manager = WorkspaceManager(str(tmp_path / "data"), bot_factory=stub_factory)
    server = DashboardServer(("127.0.0.1", 0), manager, token=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, manager
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    manager.close()


def request(server, path, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    data = json.dumps(body).encode("utf8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf8")
            payload = json.loads(raw) if "json" in resp.headers.get("Content-Type", "") else raw
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf8")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw
        return exc.code, payload, dict(exc.headers)


def gh(key=GUEST_A):
    return {"X-Guest-Key": key}


def test_health_html_and_guest_creation(dash):
    server, manager = dash
    assert request(server, "/api/health")[1] == {"ok": True}

    code, html, _ = request(server, "/")
    assert code == 200
    for marker in (
        "Control Panel", "openAuth", "toggleTheme", "cubic-bezier", "prefers-reduced-motion",
        "X-Guest-Key", "confirm:true", 'data-theme="paper"', 'class="swatches"',
        'id="groupsBody"', 'class="timeline"', '<svg viewBox="0 0 40 40"', "brand-name",
    ):
        assert marker in html
    for forbidden in (
        "Start demo", "loadTemplate", "config.demo", "UI PREVIEW",
        "linear-gradient", "radial-gradient", "backdrop-filter",
    ):
        assert forbidden not in html

    code, me, headers = request(server, "/api/me", headers=gh("invalid"))
    assert code == 200 and me["identity"]["is_guest"] is True
    assert "demo" not in me
    assert headers.get("X-New-Guest-Key", "").startswith("g_")
    assert manager.workspace_count() == 0


def test_new_workspace_starts_empty_with_no_bot_running(dash):
    server, manager = dash
    code, cfg, _ = request(server, "/api/config", headers=gh())
    assert code == 200
    assert cfg["redacted"] == ""
    assert cfg["validation_error"]  # empty config is invalid, as intended
    assert manager.workspace(GUEST_A).runner.status()["state"] == "stopped"
    assert manager.workspace(GUEST_A).store.events() == []


def test_config_save_redaction_merge_and_validation(dash):
    server, manager = dash
    code, result, _ = request(server, "/api/config", "POST", {"confirm": True, "yaml": VALID_YAML}, gh())
    assert code == 200 and result["ok"] is True

    ws = manager.workspace(GUEST_A)
    with open(ws.paths["config"], encoding="utf8") as f:
        saved = f.read()
    assert "my-mirror" in saved

    code, cfg, _ = request(server, "/api/config", headers=gh())
    assert "abcdef1234567890" not in cfg["redacted"]
    assert "*** redacted ***" in cfg["redacted"]
    assert cfg["validation_error"] is None

    edited = cfg["redacted"].replace("delay: 2", "delay: 9")
    code, result, _ = request(server, "/api/config", "POST", {"confirm": True, "yaml": edited}, gh())
    assert code == 200 and result["ok"] is True
    with open(ws.paths["config"], encoding="utf8") as f:
        saved2 = f.read()
    assert "delay: 9" in saved2 and "abcdef1234567890" in saved2  # hidden secret preserved

    code, bad, _ = request(server, "/api/config/validate", "POST", {"yaml": "a: [oops"}, gh())
    assert code == 200 and bad["ok"] is False and "Invalid YAML" in bad["error"]
    code, bad2, _ = request(server, "/api/config/validate", "POST", {"yaml": "groups: []"}, gh())
    assert bad2["ok"] is False

    # No template/sample endpoint exists any more.
    assert request(server, "/api/config/template", headers=gh())[0] == 404


def test_actions_require_explicit_confirmation(dash):
    server, _ = dash
    for path, payload in (
        ("/api/start", {"dry_run": True}),
        ("/api/stop", {}),
        ("/api/reset", {}),
        ("/api/config", {"yaml": VALID_YAML}),
    ):
        code, result, _ = request(server, path, "POST", payload, gh())
        assert code == 400, path
        assert "confirmation" in result["error"]


def test_start_stop_reset_and_events(dash):
    server, manager = dash
    request(server, "/api/config", "POST", {"confirm": True, "yaml": VALID_YAML}, gh())
    code, result, _ = request(server, "/api/start", "POST", {"confirm": True, "dry_run": True}, gh())
    assert code == 200 and result["ok"] is True

    store = manager.workspace(GUEST_A).store
    deadline = time.time() + 5
    while time.time() < deadline and store.stats()["counters"]["polls"] == 0:
        time.sleep(0.02)
    assert store.stats()["counters"]["polls"] > 0

    code, events, _ = request(server, "/api/events?limit=20", headers=gh())
    assert code == 200 and len(events["events"]) > 0
    assert request(server, "/api/stop", "POST", {"confirm": True}, gh())[1]["ok"] is True
    assert request(server, "/api/reset", "POST", {"confirm": True}, gh())[1]["ok"] is True
    assert store.stats()["counters"]["polls"] == 0
    assert request(server, "/api/nope", headers=gh())[0] == 404


def test_shared_token_protects_all_endpoints(dash):
    server, _ = dash
    server.token = "s3cret"
    assert request(server, "/api/status", headers=gh())[0] == 401
    assert request(server, "/api/me", headers=gh())[0] == 401
    assert request(server, "/api/status?token=s3cret", headers=gh())[0] == 200
    assert request(server, "/api/stop", "POST", {"confirm": True}, {**gh(), "X-Token": "s3cret"})[0] == 200
    server.token = None


def test_redaction_helpers():
    text = 'account_phone: "+1555"\napi_id: 42\napi_hash: "secret"\ndelay: 5\n'
    red = redact_config(text)
    assert "secret" not in red and "+1555" not in red and "42" not in red
    assert "secret" in merge_config_text(red, text)
    changed = merge_config_text(red.replace("delay: 5", "delay: 7"), text)
    assert "delay: 7" in changed and 'api_hash: "secret"' in changed


def test_frontend_is_self_contained_and_valid():
    """Catch broken CSS/JS, external assets and dead API calls in the page."""
    import re
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from telegram_channel_duplicator.dashboard import PAGE

    css = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)
    js = re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)

    assert css.count("{") == css.count("}"), "unbalanced CSS"
    assert "<script src=" not in PAGE and "<link " not in PAGE
    assert "https://" not in css and "http://" not in css
    for marker in ("cubic-bezier", "transition:", "body.light", "prefers-reduced-motion", "focus-visible", "@keyframes"):
        assert marker in css, marker
    for theme in ("ocean", "slate", "ember"):
        assert "body[data-theme='%s']" % theme in css, theme
    for legacy in ("linear-gradient", "radial-gradient", "backdrop-filter", "#7c3aed", "#8b5cf6", "#a855f7", "#6366f1"):
        assert legacy not in css, legacy
    assert '<svg viewBox="0 0 40 40"' in PAGE
    assert PAGE.count('data-theme-choice=') == 4
    for marker in ("setInterval(refresh", "toggleTheme", "openAuth", "saveConfig", "X-Guest-Key", "Authorization", "confirm:true"):
        assert marker in js, marker
    assert "Start demo" not in PAGE and "btnDemo" not in PAGE and "loadTemplate" not in js

    if shutil.which("node"):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "dashboard.js"
            script.write_text(js, encoding="utf8")
            result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr

    handler = Path(__file__).resolve().parents[1] / "telegram_channel_duplicator" / "dashboard.py"
    source = handler.read_text(encoding="utf8")
    routes = set(re.findall(r'path == "(/api/[^"]+)"', source)) | {"/api/auth/login", "/api/auth/register"}
    for endpoint in re.findall(r"api\('(/api/[^']+)'", js):
        if endpoint.startswith("/api/auth/") and endpoint.rstrip("/") == "/api/auth":
            continue
        assert endpoint.split("?")[0] in routes, endpoint


def test_four_switchable_themes_are_wired_to_the_ui():
    """The palette picker must actually set CSS variables, not just look pretty."""
    import re
    from telegram_channel_duplicator.dashboard import PAGE

    js = re.search(r"<script>(.*?)</script>", PAGE, re.S).group(1)
    css = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)
    for theme in ("paper", "ocean", "slate", "ember"):
        assert "'%s'" % theme in js or '"%s"' % theme in js, theme
        assert 'data-theme-choice="%s"' % theme in PAGE, theme
    for theme in ("paper", "ocean", "slate", "ember"):
        assert css.count("data-theme='%s'" % theme) >= 1 or theme == "paper"
    assert "setTheme(" in js and "toggleTheme()" in js
    assert "document.body.dataset.theme" in js
    assert 'class="nav on"' in PAGE and 'class="metrics"' in PAGE
