# Termux (Android) and Linux setup

This guide is for running TeleBridge locally. Do not upload your Telegram session, API hash, login code, or Two-Step Verification password to GitHub or to a website.

## Android with Termux

Install Termux from a trusted official distribution source, then run:

```bash
pkg update && pkg upgrade
pkg install python git
git clone --recurse-submodules https://github.com/leephil1907-lab/telegram_channel_duplicator.git
cd telegram_channel_duplicator
python --version
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
cp config.yaml.example config.yaml
nano config.yaml
```

Use a supported Python version available from your Termux package manager. If a package fails to build, capture the exact package name and full error before installing random system packages or switching to an untrusted wheel source.

## Linux

On Debian/Ubuntu, install Python, venv support, and Git first:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
git clone --recurse-submodules https://github.com/leephil1907-lab/telegram_channel_duplicator.git
cd telegram_channel_duplicator
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
cp config.yaml.example config.yaml
```

## Configure Telegram

1. Get your own API ID and API hash from https://my.telegram.org.
2. Edit `config.yaml`; quote the API hash and phone number as strings.
3. Replace every example source and destination chat name with the exact names visible to the authenticated account.
4. Ensure the account is allowed to read the source and post to each destination.
5. Run `python main.py` and complete Telegram's interactive login prompt.

The first time each source is seen, the app records its latest message ID and does not copy existing history. Messages posted after that checkpoint are eligible for copying. State is stored in `data/checkpoints.sqlite3`; keep the `data/` and `sessions/` directories persistent and private.

## Run tests

From the repository root, with the virtual environment active:

```bash
python -m unittest discover -s tests -v
```

These tests are offline and should not ask for a Telegram login. Run them before connecting a real account.

## Operational safety

- Start with a test source and destination that you control.
- Keep the phone session and checkpoint database on persistent local storage.
- Do not commit `config.yaml`, `sessions/`, `data/*.sqlite3`, or logs.
- Android may stop background processes to save battery. For reliable long-running service, use a trusted Linux host or configure Android battery/background settings carefully.
- A network failure after Telegram accepts a send but before the local delivery record is saved can still cause a duplicate on retry; Telegram sends and local SQLite writes cannot be one atomic transaction.
- This app is not an account recovery mechanism. Keep Tgback isolated and review its source before use.
