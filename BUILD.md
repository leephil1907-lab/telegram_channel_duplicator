# Build notes — telegram_channel_duplicator (v0.6.0)

Imported from **https://github.com/leephil1907-lab/telegram_channel_duplicator**
(a fork of [deFiss/telegram_channel_duplicator](https://github.com/deFiss/telegram_channel_duplicator)),
then rebuilt as a reproducible, multi-user, real-only application.

## 1. What it is

A Python/Telethon **user-client bot** that logs in as your own Telegram account, polls source
chats and re-sends new/edited messages into destination chats. There is no demo mode, no
simulator, no sample data and no automatic start. It does nothing until a user supplies their
own credentials and explicitly starts it.

> Only mirror chats you own or have permission to copy.

## 2. Quick start

```bash
./build.sh                                        # full build + verification
# create your own config.yaml (see README.md / section 5)
.venv/bin/telegram-channel-duplicator --check    # offline validation, no network
.venv/bin/telegram-channel-duplicator            # foreground, single local user
.venv/bin/telegram-channel-duplicator --serve --require-login --data-dir data --port 8000
docker compose up --build -d                     # containerised
```

Entry points: `python main.py`, `python -m telegram_channel_duplicator`, or the installed
`telegram-channel-duplicator` console script. `make help` lists shortcuts.

## 3. `./build.sh`

| Step | Action |
|------|--------|
| 1 | Create `.venv` (`PYTHON=python3.11 ./build.sh` to override) |
| 2 | `pip install -e ".[dev]"`, `pip check`, `compileall` |
| 3 | Run the offline pytest suite (config validation, whitelist, buffer, fake-client polling cycle, SQLite store, retries, threaded runner, multi-user HTTP API, Docker config consistency, container-style end-to-end, front-end asset checks) |
| 4 | `python -m build --outdir artifacts` → sdist + wheel |
| 5 | Install the wheel into a clean temp target and verify imports, `--version` and `--check` using an **ephemeral** config fixture (not shipped) |

Artifacts: `artifacts/telegram_channel_duplicator-0.6.0-py3-none-any.whl` and `.tar.gz`.
`.venv`, `build/`, `*.egg-info`, `data/`, `state/`, `sessions/` and `logs/` are regenerable
and git-ignored.

Verified here: **Python 3.13.16, 54 offline tests passing**, wheel installed and CLI verified.
CI also covers Python 3.9/3.11/3.13 and builds the Docker image.

## 4. Dependencies

Upstream pinned 2020-era versions; `PyYAML==6.0` cannot build on Python 3.13
(`'build_ext' object has no attribute 'cython_sources'`). `requirements.legacy.txt` keeps
those pins for reference; `requirements.txt` / `pyproject.toml` use compatible lower bounds.
Telethon brings `pyaes`, `rsa`, `pyasn1`; loguru brings `win32-setctime` on Windows only.

## 5. Configuration

**No config template, example or `.env` file is shipped.** Create `config.yaml` yourself
(mode 0600) or paste it into your private dashboard editor. Required keys:

```yaml
account_phone: "<international phone>"
api_id: <integer>
api_hash: "<string>"
delay: 5
edit_message_checker_limit: 10
groups:
  - name: <label>
    sources: ["<exact chat title>"]
    destinations: ["<exact chat title>"]
    whitelist: []
```

- `${VAR}` / `${VAR:-default}` expand from the environment or a local `.env`.
- `--check` validates offline: missing env vars, invalid YAML, placeholder credentials,
  empty groups and bad types are reported with exit code 2.
- Secret lines are redacted in the dashboard; saving keeps existing hidden values unless you
  replace them. A config can be saved with unresolved env placeholders (validated
  structurally) but startup validation stays strict.
- First real run prompts for the login code / 2FA and writes a session file inside that
  user's private `sessions/` directory.

## 6. Multi-user and explicit approval

`auth.py` + `workspaces.py` + `dashboard.py` implement:

- Salted PBKDF2 password hashes, opaque tokens stored as SHA-256 digests, 30-day expiry,
  `HttpOnly` cookies; optional `Secure; SameSite=None` for HTTPS.
- Per-account **and** per-anonymous-browser workspaces (`X-Guest-Key` header works even when
  third-party cookies are blocked). Each has its own empty config, SQLite store, Telegram
  session directory, log routing and bot thread.
- `TCD_REQUIRE_LOGIN` / `TCD_ALLOW_REGISTRATION` / `TCD_TOKEN` / `TCD_SECURE_COOKIES`.
- **No pre-approved actions**: no auto-start, no auto-claim, no template provisioning.
  `POST /api/start`, `/api/stop`, `/api/reset` and `/api/config` return 400 unless the body
  contains `confirm: true`; the UI also shows a native confirmation dialog.
- `--dry-run` (or the dry-run checkbox) polls without sending — an explicit user choice.

See [ENHANCEMENTS.md](ENHANCEMENTS.md) for the API table and
[DOCKER.md](DOCKER.md) for deployment.

## 7. Files

| File | Purpose |
|------|---------|
| `build.sh`, `Makefile`, `.github/workflows/ci.yml` | Reproducible build, shortcuts, CI + Docker smoke test |
| `pyproject.toml`, `MANIFEST.in`, `requirements*.txt` | Packaging and deps |
| `cli.py`, `__main__.py`, `main.py` | CLI: check / serve / dry-run / standalone |
| `config_controller.py` | Env expansion, validation, structural validation for saves |
| `store.py` | SQLite positions, message map, edit dedupe, counters, events |
| `resilience.py`, `timing.py` | Retry/backoff + FloodWait, cooperative sleep |
| `auth.py`, `workspaces.py`, `runner.py` | Accounts, isolation, one bot thread per user |
| `dashboard.py` | Stdlib HTTP server + self-contained themed control panel |
| `duplicator.py`, `client.py`, `message_preparer.py`, `sending_message_buffer.py`, `source_channel.py`, `destination_channel.py` | Mirroring engine |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `DOCKER.md` | Multi-user container deployment |
| `tests/test_*.py` | Offline tests incl. two-user isolation and "no demo" checks |

## 8. Upstream fixes retained

Invalid YAML in the old example, `asyncio.get_event_loop()` removal on 3.12+, empty-history
crash, reversed message order with multiple destinations, media-only post crash, in-place
config mutation, empty-deque buffer crash, per-user session/log routing, safe log previews.

## 9. Limitations

- Polling uses `GetHistoryRequest(limit=10)`; large bursts between polls can be missed.
- Chats are matched by exact display title; renames break them.
- A fresh state establishes a baseline on the first cycle; old messages aren't backfilled.
- Single-container SQLite design; see DOCKER.md for scaling notes.
