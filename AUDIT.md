# Initial audit and setup plan

This is a source review, not a completed runtime or security test. The original main branch has not been changed.

## Fixed on the integration branch

- [x] Malformed `config.yaml.example`: one destination had an unclosed quote, making the sample invalid YAML.
- [x] Fresh-checkout logging: `main.py` attempted to open `logs/debug.log` without creating the ignored `logs/` directory first.
- [x] Empty history edge case: the first-cycle message filter indexed `messages[-1]` even when Telegram returned no messages.
- [x] Tgback source is pinned as a Git submodule rather than copied into the main app or automatically executed.

## High-priority next fixes

1. **Config validation.** `ConfigController.get_config()` currently returns parsed YAML without validating required keys, list types, chat names, or numeric settings. Add friendly startup errors before authentication.
2. **Durable message checkpoints.** Source message IDs are stored only in memory on `SourceChannel`; a restart resets them to zero and the first cycle intentionally skips existing history. Persist checkpoints if restart-safe forwarding is required.
3. **Burst handling.** `Client.get_last_messages()` hard-codes a history limit of 10. A burst larger than the limit between polling cycles may be missed unless pagination/checkpoint logic is improved.
4. **Delivery and edit reliability.** Add retry/backoff for transient Telegram errors, handling for flood waits, and tests for edits, media, empty messages, deleted/unsupported messages, and multiple destinations.
5. **Tests and CI.** No test or CI configuration was found at the repository root. Add automated tests for configuration parsing and message/checkpoint behavior, then run lint/type checks.
6. **Dependency maintenance.** Several requirements are old pinned versions; audit current supported versions and update in a controlled compatibility pass. Do not combine Tgback's Telethon pin with the app's dependency environment.
7. **Operational setup.** Decide the intended runtime (local/Termux vs hosted Linux), Python version, process restart policy, log retention, and persistent storage for session/checkpoint data. Do not expose the authenticated session to web clients or commit it.
8. **Compliance and permissions.** Only duplicate content you own or are authorized to redistribute. Respect channel access, privacy, copyright, Telegram limits, and destination permissions.

## Tgback setup status

- Imported as a pinned submodule at `third_party/tgback`.
- Not integrated into the channel duplicator runtime.
- Not installed or executed automatically.
- Upstream warns the legacy backup tool's cryptography has not been independently audited and that backup data can grant full account access. Review [third_party/TGBACK.md](third_party/TGBACK.md) before any use.

## Environment checklist

- [ ] Select supported Python version and deployment target.
- [ ] Obtain Telegram API ID/hash from the official Telegram developer portal.
- [ ] Configure `config.yaml` locally; never commit it.
- [ ] Confirm `sessions/` and logs are excluded from version control.
- [ ] Use a test source and destination first.
- [ ] Add durable state storage and a documented backup/revocation process.
- [ ] Add tests and a CI workflow before merging.
