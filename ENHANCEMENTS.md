# Architecture & change history

## v0.8.1 — Telegram phone/code and conditional two-step login

- Both hosted mirroring and recovery request a code through the real Telethon client,
  show Telegram's returned delivery type and length when available, and request the
  Telegram two-step password only after Telegram requires it. A failed or unsupported
  send does not open a fabricated code prompt. Recovery resends are confirmed, serialized,
  rate-limited and update both the code hash and challenge deadline.
- Email login uses Telegram's `auth.signIn.email_verification` field; regular phone/app
  codes use Telethon's normal `sign_in`. Unsupported email setup, Firebase SMS and paid
  verification explain the official-app/support limitation. Codes, private hashes, API
  secrets and passwords are never included in challenge status.
- Per-phone mirroring session files prevent cross-number reuse while preserving a matching
  legacy session. A server-confirmed account mismatch fails closed. Worker readiness is
  reported only after successful authentication and chat-title resolution.
- Actual Telethon 1.34 and 1.45 authentication methods were exercised with simulated RPC
  boundaries, including retries, resend, expiry, email and two-step; desktop/mobile
  browser workflows were also tested. No live Telegram account was available.

## v0.8.0 — separate Telegram recovery workspace

- A dedicated per-account Telethon session supports user-authorized login and two-step
  challenges, encrypted file/QR session backups, Telegram-verified restoration, live
  session verification, official service messages, verified phone changes, a scheduled
  Saved Messages reminder, disconnect and explicit revocation. The mirroring session
  remains separate. No recovery activity starts automatically or uses guest accounts.
- Recovery operations require explicit confirmation and workspace-password reauthentication
  where sensitive. Attempt limiting is process-local; use proxy-level controls as well.
  Codes, passwords and service-message bodies are not written to activity logs. Recovery
  credentials and the active Telegram session remain sensitive **unencrypted server files**.
- Portable Relay backups use AES-256-GCM and scrypt, with bounded QR decoding and no
  pickle. Unsafe legacy `.tgback` imports are deliberately unsupported. Restore fetches
  Telegram's current data-center options before using an uploaded session key rather
  than connecting to an untrusted address embedded in a backup.
- The recovery center has responsive UI, prominent risk warnings, one-minute message
  clearing, short-lived QR display and no fabricated account data. Offline tests use
  test clients; actual Telegram and phone changes still need an owned account to verify.

## v0.7.0 — routed workspace and hosted-login update

- Six distinct, reloadable application routes share the existing authenticated backend;
  browser history, old hash links, responsive navigation and reduced-motion preferences
  are supported. The HTML, styles and scripts remain self-contained and are included in
  the Python package.
- First-time hosted Telegram sign-in uses per-workspace, short-lived in-memory code/2FA
  challenges. The standalone CLI retains terminal input; secrets are not returned by
  the challenge status endpoint or written to activity events.
- Dry runs cannot send or edit messages or advance the saved forwarding checkpoint.
  Failed sends retain the checkpoint and retry; confirmed destination copies are skipped.
  Sources shared by several groups are polled together. Failed edits are retried per
  destination. Missing or ambiguous chat titles stop startup instead of selecting or
  silently skipping a chat. These changes reduce duplicates but cannot guarantee
  exactly-once Telegram delivery.
- Stopping is safe even if requested during worker startup; state reset is rejected until
  the worker stops. Reset retains event history. Config validation understands redacted
  credentials, and config writes are atomic.
- The UI defaults to a dry run but never starts automatically. The API requires callers
  to explicitly choose a boolean `dry_run` value when requesting a confirmed start.

The sections below describe earlier releases and their design at the time.

## v0.6.0 — visual redesign

The dark/purple gradient dashboard was replaced with a completely different
**"Relay"** interface. Nothing about the API or the isolation model changed.

- **New logo**: an inline SVG "copy" mark (two overlapping cards + an arrow),
  drawn in the current accent colour. No image files, no fonts, no CDN.
- **Four switchable palettes** — Paper (warm light, terracotta), Ocean (cool light, blue),
  Slate (dark neutral, mint), Ember (dark warm, amber). Chosen from four swatches in the
  sidebar or cycled from the top bar; saved in `localStorage`. All colour is driven by CSS
  custom properties, so the transition is animated and `prefers-reduced-motion` is honoured.
- **No gradients, no glass blur, no purple.** Flat surfaces, hairline borders, sharp-ish
  8–10px radii, editorial serif headline, monospace numerals.
- **New components**: sticky sidebar navigation (Overview / Channels / Configuration /
  Activity), breadcrumb top bar, editorial hero with status chips and a dry-run toggle,
  six metric cards with animated counters and proportional bars, a bordered groups table,
  a two-pane config editor, and a vertical activity timeline with severity-coloured tags.
- Responsive: sidebar collapses to a horizontal nav on tablets; metrics reflow 6→3→2.
- `test_four_switchable_themes_are_wired_to_the_ui` and the extended front-end test assert
  the themes are real CSS variables, the SVG logo is inline, no external assets are used
  and no legacy gradient/purple colour remains.

The upstream repo was a single-purpose polling script with all state in memory. The current
build is a multi-user, restart-safe application with **no demo mode, no simulator, no sample
configs and no pre-approved actions**.

## v0.5.0 — production-only cleanup

Removed in this release:

- `simulator.py` / `DemoDuplicator` and the `--demo` CLI flag.
- `config.demo.yaml`, `config.yaml.example`, `.env.example`, `UI-PREVIEW.html` and the
  `scripts/make_ui_preview.py` mock-data generator.
- Every demo/sample string in the UI (Start demo, Load template, sample events, sample
  counters) and in the docs.
- `DEMO_TEMPLATE` / `DEFAULT_TEMPLATE`: new workspaces now start with an **empty**
  `config.yaml` (mode 0600) that the owner must fill in.
- Auto-start behaviour, `autostart_demo`, `--no-autostart`, and the guest-config "claim"
  on registration (an unrequested automatic action).
- The `/api/config/template` endpoint.

Added in this release:

- **Explicit approval**: `POST /api/start`, `/api/stop`, `/api/reset` and `/api/config`
  reject the request with HTTP 400 unless the body contains `confirm: true`. The UI shows a
  native confirmation dialog before each of those calls.
- **Dry run is opt-in**: a checkbox (and `--dry-run`) must be selected deliberately; it polls
  but never sends.
- `bot_factory` dependency injection in `BotRunner`/`WorkspaceManager`, used **only by unit
  tests** with in-memory doubles. No simulated mode is exposed to users.
- `timing.wait_or_stop` extracted from the removed simulator module.
- Automated tests that fail if any demo/simulation/sample artifact reappears.

## v0.4.0 — multi-user isolation

- **`auth.py`**: PBKDF2-HMAC-SHA256 salted password hashes (210k rounds), opaque bearer
  tokens stored only as SHA-256 digests, 30-day expiry, `HttpOnly` cookies. Registration
  can be disabled and login enforced.
- **`workspaces.py`**: each account (and each anonymous browser via `X-Guest-Key`) gets a
  private `config.yaml`, SQLite DB, Telegram session dir, log routing and bot thread.
- **`dashboard.py`**: polished UI — dark/light theme with smooth color transitions,
  animated stat counters, slide-in auth modal, fade-in events, toasts, responsive layout,
  focus rings and `prefers-reduced-motion`. One self-contained HTML file, zero CDN assets.
- Private config editor with redacted secrets merged back on save; env placeholders can be
  saved before secrets exist (`allow_unresolved_env`), while startup validation stays strict.
- Docker: named volume, healthcheck, env-driven settings, CI image build.

## v0.3.0 — reliability

- SQLite state: source positions, source→destination message map, edit idempotency keys,
  counters and event log. Restarts resume without re-sending.
- Retries with exponential backoff/jitter and Telegram `FloodWaitError` handling on dialog
  resolution, polling, sends and edits.
- Threaded lifecycle with cooperative stop; structured events instead of silent crashes.
- Packaging, tests, CI and Docker scaffolding.

## v0.2.0 — import and build

Backwards-compatible clone of `leephil1907-lab/telegram_channel_duplicator`, modern
dependency ranges (original pins in `requirements.legacy.txt`), packaging, CLI with offline
`--check`, and fixes for upstream crashes (invalid example YAML, `get_event_loop()`, empty
history, reversed send order, media-only posts, config mutation, empty buffer).

## API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | liveness probe |
| `/api/me` | GET | identity + server policy |
| `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | POST | accounts |
| `/api/status` | GET | runner state + counters + groups |
| `/api/events?limit=&since_id=` | GET | private event log |
| `/api/config` | GET | redacted config + validation result |
| `/api/config` | POST | `{"confirm":true,"yaml":"…"}` — save private config |
| `/api/config/validate` | POST | `{"yaml":"…"}` — validate without saving |
| `/api/start` | POST | `{"confirm":true,"dry_run":bool}` (both values required) |
| `/api/telegram/challenge` | POST | Confirm and submit the matching pending mirroring challenge ID and code/password |
| `/api/recovery/status` | GET | Signed-in account's recovery state and non-secret challenge metadata |
| `/api/recovery/connect`, `/disconnect` | POST | Confirmed recovery connect/disconnect; connect requires Relay reauthentication |
| `/api/recovery/challenge`, `/resend` | POST | Confirm and submit or resend the matching recovery challenge ID |
| `/api/recovery/verify`, `/export`, `/import`, `/codes` | POST | Reauthenticated session verification, backups and official messages |
| `/api/recovery/phone/request`, `/phone/confirm`, `/reminder`, `/revoke` | POST | Reauthenticated sensitive account operations |
| `/api/stop`, `/api/reset` | POST | `{"confirm":true}` |

Optional shared secret `--token` / `TCD_TOKEN` (send `X-Token` header or `?token=`).

## Verification

```sh
python -m pytest
python -m build --outdir artifacts
```

The test suite includes private workspaces, confirmed actions, routed HTML, browser-facing
Telegram challenges, dry-run safety, partial send/edit retries, redacted YAML and worker
start/stop behavior. The live Telegram service is not exercised by the automated tests;
see [REVIEW_AND_DEPLOY.md](REVIEW_AND_DEPLOY.md) for deployment checks and limitations.
