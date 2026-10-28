"""Backwards-compatible launcher: ``python main.py`` still works.

Prefer the installed console script ``telegram-channel-duplicator`` or
``python -m telegram_channel_duplicator``.
"""

from telegram_channel_duplicator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
