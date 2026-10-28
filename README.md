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

## Docs

- [BUILD.md](BUILD.md) — build, verification and configuration details
- [ENHANCEMENTS.md](ENHANCEMENTS.md) — architecture and change history
- [DOCKER.md](DOCKER.md) — deployment, HTTPS, backups and scaling

> Only mirror chats you own or have permission to copy, and respect Telegram's Terms of Service.
> Upstream declares no license; redistribution rights are unclear.
