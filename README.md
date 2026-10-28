<div align="center">
<h1>Telegram channel duplicator</h1>
<h3>Multi-user Python/Telethon client-bot for mirroring Telegram channels, groups and private chats.</h3>
</div>

A real-only build: **no demo mode, no simulator, no sample configs, no auto-start and no
pre-approved actions.** Every account has a private workspace and must supply its own
configuration and explicitly confirm every state-changing action.

## Requirements

- Python >= 3.9
- A Telegram **user** API `api_id` / `api_hash` from https://my.telegram.org/apps
- Membership in the source chats and post permission in the destination chats

## Install / build

```bash
./build.sh                      # venv + deps + tests + wheel/sdist in artifacts/
.venv/bin/telegram-channel-duplicator --version
.venv/bin/telegram-channel-duplicator --check     # validate your config offline
.venv/bin/telegram-channel-duplicator             # run in the foreground (single local user)
.venv/bin/telegram-channel-duplicator --serve     # multi-user control panel on :8000
docker compose up --build -d                     # containerised, persistent volume
```

## Configuration

No `config.yaml`, `.env`, template or example file is shipped. Create your own
`config.yaml` (mode 0600) or paste it into the dashboard's private editor. Required keys:

```yaml
account_phone: "<your phone in international format>"
api_id: <your integer api_id>
api_hash: "<your api hash>"
delay: 5                        # poll interval in seconds
edit_message_checker_limit: 10  # 0 disables edit mirroring
groups:
  - name: <any label>
    sources: ["<exact source chat title>"]
    destinations: ["<exact destination chat title>"]
    whitelist: []               # optional keywords
```

`${VAR}` / `${VAR:-default}` placeholders are expanded from the environment or a local
`.env`. Chat titles must exactly match the titles visible in your Telegram dialog list.

## Multi-user isolation

Each account — and each anonymous browser — gets `data/workspaces/<id>/` containing a
private `config.yaml`, SQLite database, Telegram session and bot thread. Nobody can see
another user's config, counters, events or session. Salted PBKDF2 password hashes and
opaque bearer tokens protect accounts; `TCD_REQUIRE_LOGIN=1` enforces sign-in.

Nothing starts automatically. **Start bot**, **Stop**, **Save config** and **Reset state**
all require an explicit confirmation in the UI and an explicit `confirm: true` in the API.
A **dry run** option polls without sending anything, and must be checked deliberately.

## Interface

The control panel uses the **Relay** design: an inline SVG logo, a sidebar, metric cards and
a vertical activity timeline. Four palettes (**Paper**, **Ocean**, **Slate**, **Ember**) can
be switched instantly from the sidebar swatches or the top-bar theme button — the choice is
remembered per browser. No external fonts, images or scripts are loaded.

## Render deployment

A Render Blueprint is included in `render.yaml`. In Render, create a new Blueprint Instance from this repository and select that file. The service builds from the repository `Dockerfile`, uses Render's assigned `PORT`, and exposes `/api/health` for health checks. A persistent disk is mounted at `/app/data` for per-user workspaces, SQLite state, and Telegram session files; keep this disk attached to the service to preserve state across restarts. The Blueprint uses a paid Starter web service because persistent disks are not available on Render's free web services.

After the first successful deploy, open the service URL and create an account from the sign-in screen. Register a strong unique password. Configure Telegram API credentials and source/destination groups inside your own workspace, validate the configuration, and test with **Dry run** before enabling live forwarding. Keep login codes, two-step verification passwords, API hashes, and session files private. Do not enable live forwarding until you have verified source permissions, destination IDs, and the intended forwarding rules.

## TeleBridge integration

TeleBridge's useful features are integrated into this existing application rather than
running as a second web server. The unified dashboard keeps the existing per-user workspaces,
authentication, configuration editor, confirmed start/stop/reset actions, SQLite activity
history, and responsive Relay interface. Its **Recovery guide** section links directly to
Telegram's official FAQ and support form and includes account-safety guidance; it does not
claim to unlock accounts or bypass Telegram authentication.

Message retrieval uses Telethon's paginated iterator after a source checkpoint exists, so
messages newer than the saved position can be collected without the old fixed ten-message
history cap. On a source's first run, the client reads only the latest message and records
that position instead of downloading or forwarding the channel's old history. Offline tests
cover both behaviours.

The legacy standalone TeleBridge webapp dashboard is intentionally not added as a
second server: its useful control-room and recovery concepts live in the existing dashboard,
avoiding duplicate authentication and worker-management paths.

## Docs

- [BUILD.md](BUILD.md) — build, verification and configuration details
- [ENHANCEMENTS.md](ENHANCEMENTS.md) — architecture and change history
- [DOCKER.md](DOCKER.md) — deployment, HTTPS, backups and scaling

> Only mirror chats you own or have permission to copy, and respect Telegram's Terms of Service.
> Upstream declares no license; redistribution rights are unclear.
