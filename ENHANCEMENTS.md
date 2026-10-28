# Architecture & change history (v0.6.0)

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
| `/api/start` | POST | `{"confirm":true,"dry_run":bool}` |
| `/api/stop`, `/api/reset` | POST | `{"confirm":true}` |

Optional shared secret `--token` / `TCD_TOKEN` (send `X-Token` header or `?token=`).

## Verification

```
./build.sh
==> 54 passed
==> BUILD OK
   artifacts/telegram_channel_duplicator-0.6.0-py3-none-any.whl
   artifacts/telegram_channel_duplicator-0.6.0.tar.gz
```

`tests/test_multitenancy.py::test_no_demo_simulation_or_sample_artifacts` asserts the
removed files are gone, `--demo` is rejected by the CLI, the dashboard HTML contains no demo
controls, and `telegram_channel_duplicator.simulator` no longer imports.
`tests/test_dashboard.py::test_actions_require_explicit_confirmation` proves no
state-changing endpoint acts without `confirm: true`.
