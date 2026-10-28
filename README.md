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
`.env`. Chat titles must exactly and uniquely match the titles visible in your Telegram dialog list.
If a title is missing or matches more than one chat, the worker stops rather than choosing
an unintended destination.

## Multi-user isolation

Each account — and each anonymous browser — gets `data/workspaces/<id>/` containing a
private `config.yaml`, SQLite database, Telegram session and bot thread. Nobody can see
another user's config, counters, events or session through the workspace APIs. Server
administrators can access private files and running processes. Salted PBKDF2 password
hashes and opaque bearer tokens protect accounts; `TCD_REQUIRE_LOGIN=1` enforces sign-in.

Nothing starts automatically. **Start bot**, **Stop**, **Save config** and **Reset state**
all require an explicit confirmation in the UI and an explicit `confirm: true` in the API.
The dashboard starts with **Dry run** selected, but the bot never starts automatically.
A dry run polls without sending or editing messages and does not advance saved live
checkpoints. It can still add activity events and increment counters. The API requires
an explicit boolean `dry_run` choice when starting.

## Dashboard

Relay is a routed web application rather than a single scrolling page. Its distinct,
reloadable URLs are **Overview** (`/overview`), **Channels** (`/channels`),
**Configuration** (`/configuration`), **Activity** (`/activity`), **Recovery center**
(`/recovery`) and **Settings** (`/settings`). Navigation uses the browser history and
smooth, reduced-motion-aware transitions. Old hash links such as `/#config` continue to
work. The UI is responsive and uses no external fonts, scripts or images.

- Use the configuration editor to validate and save YAML. Saved secrets are redacted;
  validating or saving unchanged redacted values preserves the real credentials.
- Overview shows live worker status, counters, recent events and confirmed start/stop
  controls. Counters include dry-run activity, not just successful real sends.
- Channels shows configured groups and supports search. Activity supports search and
  severity filters. Settings provides four browser-local color themes and a state reset.
- Reset requires a stopped worker and clears positions, counters and message/edit maps;
  it retains configuration, Telegram session files and event history.
- Guest and registered workspaces are separate. Creating or signing into an account
  **does not copy** the guest workspace.

### Recovery center

`/recovery` provides a **separate Telegram user session** for signed-in Relay accounts;
it does not reuse or revoke the mirroring session. Each Relay workspace holds one recovery
session; use a separate Relay account/workspace for another Telegram account. Nothing
connects or starts implicitly. Use only your own account and API credentials on a trusted
HTTPS server.

1. Sign in to Relay, open **Recovery center**, and select **Sign in to Telegram**.
   Enter your Telegram phone number in international format and your own API ID and
   API hash from [my.telegram.org/apps](https://my.telegram.org/apps), then re-enter
   your Relay password. Select **Request sign-in code**. Enter the code delivered by
   Telegram; enter your Telegram two-step password only if Telegram requests it.
2. Download an encrypted `.rlybackup` file or create an encrypted QR PNG. Choose a
   unique backup password of at least 12 characters, store it separately, and keep both
   private. These files contain a Telegram session and API credentials, **not message
   history**. Anyone with the file and password can take over the Telegram account.
3. Verify the session with Telegram periodically, create a fresh backup when needed,
   or schedule a reminder in your Telegram Saved Messages. A Telegram session may
   expire or be revoked; a backup cannot revive a revoked session.
4. Restore by disconnecting the recovery session, choosing a Relay backup or QR image,
   and entering its backup password. Relay verifies the imported session against
   Telegram before replacing the local one. Then reconnect using saved credentials.
   Avoid using copies of the same Telegram session concurrently on different servers:
   Telegram may invalidate duplicated authorization keys.
5. The recovery tools can fetch up to three recent messages from Telegram's official
   service account (`777000`), request and confirm a Telegram phone-number change, or
   revoke the dedicated recovery session. Telegram can impose restrictions or waiting
   periods. After a phone change, update your mirroring configuration as needed. Code
   results clear from the page after one minute (or when the tab is hidden) and are not
   stored by Relay. Revocation makes **all** backups of that recovery session unusable.

A network timeout after a phone change, reminder or revocation may occur after Telegram has
accepted the request. Check the account in Telegram before retrying any such action.

Recovery actions require explicit confirmation and most require re-entering the Relay
workspace password. Failed login/reauthentication attempts are limited per account in
memory (five attempts per 15 minutes); this resets on server restart and is not a
substitute for proxy-level, distributed rate limiting. Signing out disconnects recovery
but leaves a running mirroring bot alone.

Relay's new portable format uses **AES-256-GCM** with a random salt and nonce and
**scrypt** (`N=32768, r=8, p=1`). The encrypted archive is not retained on the server
after download. The active Telegram session and API credentials **are stored unencrypted
on the server** in private workspace files (POSIX directories `0700`, files `0600`),
and are accessible to server administrators; protect the server and persistent disk.
This application has not been independently security-audited.

**Legacy `.tgback` backups cannot be imported.** The upstream format uses unauthenticated
AES-CBC followed by Python pickle deserialization; importing an untrusted file that way
is unsafe for a hosted server. Relay deliberately does not run or unpickle that format.
Telegram still controls all authentication and can reject a recovery attempt; these tools
are not a way to bypass its security checks.

### Telegram sign-in and code delivery

A hosted server has no interactive terminal. In the recovery center, **Request sign-in
code** contacts Telegram through Telethon after you provide your own number, API ID and
API hash. The mirroring worker does the same when its configured number has no matching
authorized session. A code prompt appears only after the Telegram request succeeds. If
Telegram returns an app, SMS, voice/missed/flash-call, email or Fragment delivery type,
the prompt describes it when supported. Telegram chooses the method: **SMS is not
promised**. An existing valid session may connect without a new code.

Enter the most recent Telegram code. If Telegram reports that two-step verification is
enabled, Relay then asks for your **Telegram two-step password**, which is different from
your Relay workspace password. The account is not marked connected until Telegram
confirms it. For mirroring, status stays **Starting** until authentication and channel
resolution both succeed. Invalid codes and passwords can be retried; when a code expires,
reconnect recovery or restart mirroring. Recovery also offers **Request another code**
while a code challenge is active, subject to a local cooldown and Telegram's own limits.
Resending can change the delivery method and invalidates older codes.

Some Telegram-required steps, including setting up a login email, Firebase SMS and paid
verification, cannot be completed by this client. Use an official Telegram app or contact
Telegram support if Telegram requires them. The software cannot bypass Telegram's code,
two-step verification or account-recovery rules. The recovery feature needs an authorized
session or access to Telegram's normal sign-in requirements; it cannot guarantee access to
an account whose phone, password and valid sessions are all lost.

Code/password prompts use short-lived, one-use memory challenges (five minutes for
mirroring; ten minutes for recovery, extended after a successful resend). They are not
stored in the event log or returned in status. Stopping or disconnecting cancels the prompt.
The standalone CLI continues to prompt in the terminal. Mirroring sessions are isolated
by phone number; a compatible legacy session is kept only if Telegram confirms that it
belongs to the configured number.

The web UI disables Telegram verification and recovery controls over non-local HTTP;
API clients must also use trusted HTTPS. **Only enter a Telegram code or two-step
password on a Relay instance you control and trust.** Never send them in chat or to
support staff; anyone who controls
the server can access its running process and unencrypted session files. Keep API hashes,
session files and backups private as well.

## Render deployment

A Render Blueprint is included in `render.yaml`. In Render, create a new Blueprint Instance from this repository and select that file. The service builds from the repository `Dockerfile`, uses Render's assigned `PORT`, and exposes `/api/health` for health checks. A persistent disk is mounted at `/app/data` for per-user workspaces, SQLite state, and Telegram session files; keep this disk attached to the service to preserve state across restarts. The Blueprint uses a paid Starter web service because persistent disks are not available on Render's free web services.

After the first successful deploy, open the service URL and create an account from the sign-in screen. Register a strong unique password. Configure Telegram API credentials and source/destination groups inside your own workspace, validate the configuration, and test with **Dry run** before enabling live forwarding. Only enter Telegram sign-in codes and two-step passwords on your own trusted HTTPS instance; never share them with another person. Keep API hashes and session files private. Do not enable live forwarding until you have verified source permissions, exact destination titles, and the intended forwarding rules.

## TeleBridge integration

TeleBridge's useful features are integrated into this existing application rather than
running as a second web server. The unified dashboard keeps the existing per-user workspaces,
authentication, configuration editor, confirmed start/stop/reset actions, SQLite activity
history, and responsive Relay interface. Its **Recovery center** provides the separate
Telegram session tools described above and links to official Telegram support. It does not
claim to unlock accounts or bypass Telegram authentication.

Message retrieval uses Telethon's paginated iterator after a source checkpoint exists, so
messages newer than the saved position can be collected without the old fixed ten-message
history cap. On a source's first run, the client reads only the latest message and records
that position instead of downloading or forwarding the channel's old history. Offline tests
cover both behaviours.

The legacy standalone TeleBridge webapp dashboard is intentionally not added as a
second server: its useful control-room and recovery concepts live in the existing dashboard,
avoiding duplicate authentication and worker-management paths.

### Operational limitations

- On a source's first run, Relay establishes a checkpoint at its latest message; it does
  not copy older channel history. It is not a historical bulk-import tool.
- Workers do not auto-start after a server restart. Start them explicitly from the
  workspace again; keep the Render persistent disk attached.
- Confirmed copies are recorded and failed destinations retried before advancing a
  source checkpoint. Delivery cannot be guaranteed exactly once: for example, a network
  failure after Telegram accepts a send but before the server receives the response can
  still produce a duplicate. Review the activity log and test with your own account.
- There is no built-in Relay password recovery. The per-account in-memory attempt limit
  is not distributed and does not survive restarts; add reverse-proxy rate limiting and
  restrict registration and access. Use strong passwords and HTTPS, protect the data
  disk and its backups, and run one server instance per data directory. The software has
  not been independently security-audited.

## Docs

- [BUILD.md](BUILD.md) — build, verification and configuration details
- [ENHANCEMENTS.md](ENHANCEMENTS.md) — architecture and change history
- [DOCKER.md](DOCKER.md) — deployment, HTTPS, backups and scaling

> Only mirror chats you own or have permission to copy, and respect Telegram's Terms of Service.
> Upstream declares no license; redistribution rights are unclear.
