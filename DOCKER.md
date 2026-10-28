# Docker & multi-user deployment

The container runs the **multi-user control panel**. Every account (and every anonymous
browser) gets its own directory under `/app/data/workspaces/`:

```
/app/data/
├── users.db                  # accounts + hashed session tokens
└── workspaces/
    ├── w_<id>/               # one registered user
    │   ├── config.yaml       # EMPTY until the owner fills it in (mode 600)
    │   ├── state.db          # private SQLite counters/events/positions
    │   ├── recovery/         # private recovery API credentials (unencrypted at rest)
    │   └── sessions/         # private, separate mirroring and recovery sessions
    └── g_<id>/               # one anonymous browser
        └── ...
```

Nothing is shared and nothing starts automatically. Each person must add their own
configuration and explicitly start their own bot.

## Quick start

```bash
docker compose up --build -d
docker compose ps             # wait for "healthy"
docker compose logs -f
```

Open **http://localhost:8000**. Create an account, paste your own `config.yaml` into the
private editor, click **Save config** (you'll be asked to confirm), then **Start bot**
(another confirmation). No sample config or `.env` file is provided by the project — supply
credentials yourself.

## Telegram login on a hosted instance

On a trusted HTTPS deployment, provide your own Telegram phone number and API credentials
in the mirroring configuration or recovery sign-in form. The server requests a real
Telegram code, shows the returned delivery type when available and asks for a Telegram
two-step password only if required. Telegram chooses delivery and can impose restrictions;
SMS and account recovery are not guaranteed. Recovery supports a cooldown-limited resend.
Saved authorized sessions can reconnect without a code. The mirroring worker is not shown
as running until it has authenticated and resolved its chat titles. See the README for
unsupported verification steps and security warnings. Never enter credentials into a
preview server or other server that you do not control and trust.

## Config schema

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

`${TG_API_ID}` / `${TG_API_HASH}` / `${TG_PHONE}` placeholders resolve from the container
environment, or you can paste literal values into your private config. Set them in the
shell before `docker compose up`:

```bash
export TG_API_ID=... TG_API_HASH=... TG_PHONE=...
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8000` | Host port mapped to container port 8000 |
| `TCD_REQUIRE_LOGIN` | `1` | Guests must sign in before using the dashboard |
| `TCD_ALLOW_REGISTRATION` | `1` | Set `0` to close sign-ups (create accounts first!) |
| `TCD_SECURE_COOKIES` | `0` | Set `1` behind HTTPS → `Secure; SameSite=Lax` cookies |
| `TCD_TOKEN` | *(empty)* | Extra shared secret; open `/?token=...` |
| `TG_PHONE`, `TG_API_ID`, `TG_API_HASH` | *(empty)* | Optional server-wide credentials for `${VAR}` placeholders |
| `TCD_DATA_DIR` | `/app/data` | Root of all per-user data (inside the container) |

Run without Compose:

```bash
docker build -t telegram-channel-duplicator:0.8.1 .
docker run -d --name tcd -p 8000:8000 \
  -v tcd-data:/app/data \
  -e TCD_REQUIRE_LOGIN=1 -e TCD_SECURE_COOKIES=0 \
  telegram-channel-duplicator:0.8.1
```

## HTTPS / reverse proxy

The recovery UI requires HTTPS except on a local loopback address. The HTTP server does not
terminate TLS itself, so API clients must also use a trusted TLS reverse proxy. Put the
container behind nginx/Caddy/Traefik, restrict access, configure proxy-level rate limits, and set
`TCD_SECURE_COOKIES=1`:

```nginx
location / {
  proxy_pass http://127.0.0.1:8000;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto https;
  proxy_http_version 1.1;
}
```

If cookies are blocked entirely (e.g. a strict sandboxed iframe), the UI also sends
`X-Guest-Key` / `Authorization` headers, so isolation still holds.

## Backups & upgrades

```bash
docker run --rm -v tcd-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/tcd-backup.tgz -C /data .
```

The named volume survives `docker compose down` (but not `down -v`). Its configuration,
recovery credentials and Telegram sessions are sensitive and unencrypted at rest. Encrypt
and restrict disk backups; a server administrator can access the active session. Downloaded
Relay recovery backups are separately encrypted with the user's backup password.

## Scaling note

Run **one server process/instance per data directory**. Recovery workers, one-use challenges
and rate limits are held in process memory, and Telethon SQLite sessions must not be opened
concurrently by multiple workers. Sharing the volume or adding sticky sessions alone does
not make horizontal scaling safe.

## Verification

`docker` is not available inside the Arena sandbox, so the image cannot be built here.
Instead:

- `tests/test_multitenancy.py::test_container_style_end_to_end` starts the real CLI command
  with the same env vars, registers two users over HTTP, and asserts each starts with an
  empty config, neither can see the other's config, and **nothing started by itself**.
- `tests/test_multitenancy.py::test_docker_and_compose_are_consistent` parses Compose,
  checks `HEALTHCHECK`/`VOLUME`/`EXPOSE`, and confirms every `COPY` source exists.
- `.github/workflows/ci.yml` builds the image and smoke-tests it with real Docker.
