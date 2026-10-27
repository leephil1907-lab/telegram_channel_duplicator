# TeleBridge web dashboard

The web dashboard adds a browser-based control room for the existing Python forwarding worker. It is server-side: Telegram credentials, the SQLite checkpoint database, and Telethon session files remain on the host.

## Requirements

- Python 3.10+
- A private server or trusted host
- A strong unique `TELEBRIDGE_WEB_PASSWORD`
- A valid `config.yaml`
- A Telegram Telethon session created through the CLI first

## Run locally

Install dependencies, configure the existing app as described in [SETUP_TERMUX.md](SETUP_TERMUX.md), then set a dashboard password:

```bash
pip install -r requirements.txt
export TELEBRIDGE_WEB_PASSWORD='use-a-long-unique-password'
export TELEBRIDGE_WEB_HOST='127.0.0.1'
export TELEBRIDGE_WEB_PORT='8080'
python webapp.py
```

Open `http://127.0.0.1:8080`. Your browser asks for the username/password; the username can be any non-empty value and the password is the value of `TELEBRIDGE_WEB_PASSWORD`.

## Hosting

The included Procfile uses the host-provided `PORT`. Set `TELEBRIDGE_WEB_PASSWORD` in the hosting provider's secret/environment settings. If you want remote access, deploy behind HTTPS and a trusted reverse proxy; do not expose the built-in HTTP server directly to the public internet. The default bind address is loopback for local use. Set `TELEBRIDGE_WEB_HOST=0.0.0.0` only behind that protected HTTPS layer.

Persist the entire application data area, including `config.yaml`, `sessions/`, `data/checkpoints.sqlite3`, and `logs/`. Treat session files as full account-access secrets. Never upload them to GitHub or the browser.

## First-time Telegram login

The web panel deliberately does not collect Telegram login codes or two-step verification passwords. Complete first-time Telethon session setup using the CLI in a trusted terminal before using the web panel to start the worker. The worker start button will refuse to run if it cannot find a session file.

## Current limits

- The dashboard reads route counts and the polling delay from the server configuration; it does not expose the API hash or channel names.
- It starts/stops a worker process and shows worker status. It is not a live delivery log and does not yet display per-message history.
- HTTP Basic authentication is not a substitute for HTTPS. Use a reverse proxy for public deployments.
- The dashboard process tracks the worker it starts. If the host restarts, the worker will need to be started again unless the host's process supervisor is configured to restart it.

## Deploy on Railway

1. Push this branch and open the pull request.
2. In Railway, create a service from this GitHub repository and select the dashboard branch after reviewing/merging it.
3. Add variables: `TELEBRIDGE_WEB_PASSWORD` (a long, unique secret) and `TELEBRIDGE_WEB_HOST=0.0.0.0`. Railway provides `PORT` automatically.
4. Attach a persistent volume and mount it at the application root (or configure a persistent project data path and adjust the app paths before deployment). The service needs `config.yaml`, `sessions/`, `data/`, and `logs/` to survive redeploys.
5. Add a Railway public domain, then test login, status, start, and stop with a private/test channel first.

**Important:** Do not deploy a fresh service without securely provisioning the configuration and Telethon session. The repository does not contain these private files by design. A dashboard deployment without them will load but cannot start forwarding. The start/stop controller is intentionally simple and should be upgraded to a proper process supervisor before handling production-critical workloads.
