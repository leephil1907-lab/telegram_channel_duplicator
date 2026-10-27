# Initial audit and setup plan

This is a source review plus an initial reliability patch. The code has not yet been exercised against a real Telegram account. The original `main` branch remains unchanged.

## Completed on the integration branch

- [x] Correct malformed YAML in `config.yaml.example`.
- [x] Create the logs directory before configuring file logging.
- [x] Handle empty history responses.
- [x] Add SQLite-backed per-source checkpoints in `data/checkpoints.sqlite3`.
- [x] Record successful per-destination deliveries so normal retries do not resend already-recorded deliveries.
- [x] Replace the hard-coded ten-message history request with Telethon's paginated iterator and sort results oldest-to-newest.
- [x] Preserve first-run behavior: save the current latest message ID and do not bulk-copy old history by default.
- [x] Validate required configuration keys and values before Telegram authentication.
- [x] Add offline unit tests for checkpoint persistence, delivery tracking, pagination, empty history, and config validation.
- [x] Add a GitHub Actions workflow to run unit tests on pushes and pull requests.
- [x] Add Android Termux/Linux setup instructions and ignore local checkpoint database files.
- [x] Pin Tgback as an isolated Git submodule; the app does not install or launch it.

## Important limitations to address

1. **Runtime tests are not yet confirmed.** The test suite and dependency installation still need a successful CI run. An end-to-end test should use private test channels controlled by the operator.
2. **Telegram and SQLite cannot share one transaction.** If Telegram accepts a send and the process crashes before SQLite records it, that one message may be duplicated on retry. The implementation reduces duplicates but cannot guarantee exactly-once delivery.
3. **Edit tracking is currently in-memory.** The buffer used to update previously copied messages is not persisted. After a process restart, edits to older copied messages may not be propagated.
4. **Transient errors and flood waits.** Failed destination sends are retried on a later poll, but dedicated backoff and flood-wait handling should be added. The current implementation should not be treated as production-hardened.
5. **Media and message semantics.** Test photos, videos, albums, captions, service messages, protected content, and entities. The current send/edit path may not preserve every Telegram message type or formatting detail.
6. **Dependency maintenance.** Some dependency pins are old. Run a controlled compatibility and vulnerability review; do not merge Tgback's separate dependency pins into the main environment.
7. **Deployment durability.** Keep both `sessions/` and `data/checkpoints.sqlite3` on private persistent storage. Back up the checkpoint database carefully, but never commit the Telegram session or config secrets.

## Test command

```bash
python -m unittest discover -s tests -v
```

Run from the repository root in the virtual environment after installing `requirements.txt`. The tests should be offline and must not prompt for Telegram credentials.

## Termux/Linux setup

Follow [SETUP_TERMUX.md](SETUP_TERMUX.md). Python 3.10+ is recommended for the initial compatibility target. On Termux, Android battery and background-process management may interrupt long-running polling.

## Tgback setup status

- Imported as a pinned submodule at `third_party/tgback`.
- Not integrated into the channel duplicator runtime.
- Not installed or executed automatically.
- Upstream warns the legacy backup tool's cryptography has not been independently audited and that backup data can grant full account access. Review [third_party/TGBACK.md](third_party/TGBACK.md) before any use.
