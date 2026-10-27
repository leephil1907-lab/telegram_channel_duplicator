# TeleBridge Toolkit — Account Recovery

This folder adds a safety-first account recovery guide alongside the existing Telegram channel duplicator.

## What this module does

- Provides a self-contained mobile-friendly guide at recovery/index.html.
- Routes common scenarios to Telegram's official FAQ/support first.
- Links to the independent TgHacker guide and upstream Tgback repositories with clear third-party warnings.
- Explains that account recovery depends on phone-number access, active sessions, and Telegram's own authentication controls.

## Upstream projects

- Official Telegram FAQ: https://telegram.org/faq
- Official support: https://telegram.org/support
- Independent guide: https://tghacker.com/en
- Tgback (legacy): https://github.com/NotStatilko/tgback
- TgbackXQt (upstream author's newer version): https://github.com/NotStatilko/tgback-x-qt

## Integration boundary

Tgback is not embedded or automatically executed by this project. Its own documentation warns that a usable backup session can grant full account access and notes that the project has not received a competent security audit. For that reason, this repository links to upstream source and documents the risks rather than bundling session-management code into the channel duplicator.

The existing channel-copying workflow remains separate from recovery guidance. This page does not request or transmit Telegram credentials, login codes, session files, QR backups, API hashes, or Two-Step Verification passwords.

## Run locally

Open recovery/index.html in a browser. No build step, server, or external JavaScript package is required.
