"""Command line entry point.

Modes
-----
``--check``   offline config validation (no network, no credentials needed)
``--serve``   multi-user dashboard at http://HOST:PORT (default 8000)
``--dry-run`` poll for real but do not send anything
(no flags)    run the live mirror loop in the foreground (single local user)

Nothing starts automatically in server mode: each user must explicitly start
their own bot after supplying their own configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import List, Optional

from loguru import logger

from . import __version__
from .config_controller import CONFIG_FILE_PATH, ConfigController, ConfigError
from .store import Store


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-channel-duplicator",
        description="Multi-user Telegram channel mirroring with a local control panel.",
    )
    parser.add_argument("-c", "--config", default=None, help="config path for --check / standalone run")
    parser.add_argument("--check", action="store_true", help="validate config and print a summary, then exit (no network)")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--log-level", default="INFO", help="console log level (DEBUG, INFO, ...)")

    parser.add_argument("--serve", action="store_true", help="start the multi-user control-panel dashboard")
    parser.add_argument("--dry-run", action="store_true", help="poll but never send or edit messages")
    parser.add_argument("--host", default="0.0.0.0", help="dashboard bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="dashboard port")
    parser.add_argument("--token", default=os.environ.get("TCD_TOKEN"), help="shared secret required on every API call")
    parser.add_argument("--data-dir", default=os.environ.get("TCD_DATA_DIR", "data"), help="root for per-user workspaces")
    parser.add_argument("--require-login", action="store_true", default=_env_flag("TCD_REQUIRE_LOGIN"),
                        help="guests must sign in before using the dashboard")
    parser.add_argument("--no-registration", action="store_true", default=not _env_flag("TCD_ALLOW_REGISTRATION", True),
                        help="disable new account creation")
    parser.add_argument("--secure-cookies", action="store_true", default=_env_flag("TCD_SECURE_COOKIES"),
                        help="set Secure; SameSite=None cookies (use behind HTTPS)")
    parser.add_argument("--db", default=os.environ.get("TCD_DB", "state/duplicator.db"), help="SQLite state file (standalone mode)")
    parser.add_argument("--reset-state", action="store_true", help="clear saved positions/counters (standalone mode)")
    return parser


def _configure_logging(level: str, *, file_logging: bool = True) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format="<cyan>{time:HH:mm:ss}</cyan> | <lvl>{level: <8}</lvl> | <lvl>{message}</lvl>",
        colorize=True,
        level=level.upper(),
    )
    if not file_logging:
        return
    try:
        os.makedirs("logs", exist_ok=True)
        logger.add(
            os.path.join("logs", "debug.log"),
            format="{time} {level} {message}",
            level="DEBUG",
            rotation="3 MB",
            compression="zip",
        )
    except OSError as exc:
        logger.warning(f"File logging disabled: {exc}")


def _summary(config, path: str) -> str:
    lines = [
        "Configuration OK",
        f"  file:                 {os.path.abspath(path)}",
        f"  account:              {config['account_phone'][:3]}***",
        f"  api_id:               {config['api_id']}",
        f"  poll delay:           {config['delay']}s",
        f"  edit tracker limit:   {config['edit_message_checker_limit']}",
        f"  groups:               {len(config['groups'])}",
    ]
    for group in config["groups"]:
        lines.append(f"    - {group['name']}")
        lines.append(f"        sources:      {', '.join(group['sources'])}")
        lines.append(f"        destinations: {', '.join(group['destinations'])}")
        if group["whitelist"]:
            lines.append(f"        whitelist:    {', '.join(group['whitelist'])}")
    lines.append("No Telegram connection was made. Run without --check to start mirroring.")
    return "\n".join(lines)


def _serve_mode(args) -> int:
    from .dashboard import serve

    serve(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        token=args.token,
        require_login=args.require_login,
        allow_registration=not args.no_registration,
        secure_cookies=args.secure_cookies,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(f"telegram-channel-duplicator {__version__}")
        return 0

    server_mode = args.serve
    _configure_logging(args.log_level, file_logging=not server_mode)

    if server_mode:
        return _serve_mode(args)

    try:
        config = ConfigController.load(args.config)
    except ConfigError as exc:
        logger.error(str(exc))
        return 2

    if args.check:
        print(_summary(config, args.config or CONFIG_FILE_PATH))
        return 0

    from .duplicator import Duplicator

    store = Store(args.db)
    if args.reset_state:
        store.reset()
    duplicator = Duplicator(config=config, store=store, dry_run=args.dry_run)
    logger.info("Created by https://github.com/deFiss - starting duplicator (Ctrl+C to stop)")
    try:
        asyncio.run(duplicator.start())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        store.close()
    return 0
