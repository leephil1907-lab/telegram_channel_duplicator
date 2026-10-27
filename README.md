<div align="center">
<h1>TeleBridge Toolkit</h1>
<img height="90" width="90" alt="Telegram logo" src="https://telegram.org/img/t_logo.svg?1" />
<h3>Telegram channel duplication tools with a separate, safety-first account recovery guide.</h3>
</div>

TeleBridge Toolkit keeps the existing channel duplicator and adds an independent recovery-resource hub. The recovery guide does not unlock accounts, bypass Telegram authentication, or collect account credentials.

## Modules

### Channel duplicator

The original Python client-bot copies content from Telegram channels, chats, and private messages to configured channels.

#### Installation

- Python 3.10+ recommended; see [Termux/Linux setup](SETUP_TERMUX.md).
- Install dependencies: `pip install -r requirements.txt`
- Copy `config.yaml.example` to `config.yaml` and configure your Telegram API credentials and source/destination chats.
- Run tests offline: `python -m unittest discover -s tests -v`.
- Run: `python main.py`

Keep API credentials and local session files private. Source message checkpoints and successful per-destination delivery records are persisted in `data/checkpoints.sqlite3`; do not delete it unless you intentionally want to reset tracking. History is paginated through Telethon's iterator rather than capped at ten messages. On the first run for a source, the current latest message ID is saved and old history is skipped, matching the original first-run behavior. The app retries incomplete deliveries, though a crash between Telegram accepting a message and the local SQLite write can still create a duplicate. Test with private channels before using production destinations.

### Account recovery guide

Open [recovery/index.html](recovery/index.html) in any modern browser. It is a self-contained, responsive page with a guided selector for:

- Lost phone or SIM
- Suspected account compromise
- Forgotten Two-Step Verification password
- Logged out of all devices
- Account restrictions
- Session backup and restore questions

It links to the official [Telegram FAQ](https://telegram.org/faq) and [Telegram Support](https://telegram.org/support), plus clearly labelled independent references.

### Tgback source import

The upstream [NotStatilko/tgback](https://github.com/NotStatilko/tgback) repository is pinned as a Git submodule at `third_party/tgback`. Clone this repository with submodules:

```bash
git clone --recurse-submodules https://github.com/leephil1907-lab/telegram_channel_duplicator.git
```

If you already cloned it without submodules:

```bash
git submodule update --init --recursive
```

Tgback is kept separate from the channel duplicator and is **not installed or automatically executed** by the main app. Its upstream README describes it as mostly legacy and warns that a usable session backup can grant full account access; it also says the cryptographic strength has not been independently audited. Read [third_party/TGBACK.md](third_party/TGBACK.md) before using it. Never upload session files, backup QR codes, login codes, or passwords to this app or an issue.

The newer [TGBACK-XQt](https://github.com/NotStatilko/tgback-x-qt) project is documented as an alternative reference, not a dependency of this repository.

## Security

Read [SETUP_TERMUX.md](SETUP_TERMUX.md), [AUDIT.md](AUDIT.md), [SECURITY.md](SECURITY.md), and [recovery/SECURITY.md](recovery/SECURITY.md). Never share Telegram login codes, Two-Step Verification passwords, API hashes, session files, or backup QR codes. Recovery outcomes depend on your access to the phone number, existing sessions, and Telegram's own security controls.

## Project identity

**TeleBridge Toolkit** is the proposed project name for the combined channel and recovery tools. This repository is an independent project and is not affiliated with Telegram.
