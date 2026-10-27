# Tgback upstream import and review

## Imported source

The upstream repository is included as a pinned Git submodule at `third_party/tgback`:

- Repository: https://github.com/NotStatilko/tgback
- Pinned upstream commit: `34344c70efbb2562baa578121c3f1319cc521956`
- Upstream default branch: `master`
- License declared by upstream `setup.py`: MIT

Initialize it after cloning with:

```bash
git submodule update --init --recursive
```

The submodule is source-only. The main application does not install, import, or launch it. Keep it isolated in its own environment if you decide to evaluate it.

## Important caveats

The upstream README describes Tgback as mostly legacy and points to [TGBACK-XQt](https://github.com/NotStatilko/tgback-x-qt) as a newer project. Tgback creates backups of authenticated Telegram sessions; anyone obtaining a usable backup may gain full account control. Upstream also says its cryptographic strength has not been independently audited.

The upstream `setup.py` pins Telethon 1.36.0 and PyAES 1.6.1, with optional QR dependencies. Do not merge those requirements into the channel duplicator's environment without a compatibility review. Prefer a separate virtual environment and install only from the official upstream repository after reviewing the code.

## Review and setup checklist

- [ ] Review upstream source and release history before execution.
- [ ] Use a dedicated Python virtual environment; do not install Tgback dependencies into the main app environment.
- [ ] Never commit `.tgback` files, QR backups, Telegram session files, API hashes, login codes, or Two-Step Verification passwords.
- [ ] Test backup creation and restore only with an account you control and a trusted device.
- [ ] Confirm how to terminate the backup session in Telegram Settings → Devices.
- [ ] Do not treat the tool as a way to recover an account when you cannot sign in in the first place.

This repository is not affiliated with Telegram or the Tgback author.
