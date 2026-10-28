"""Lifecycle manager: runs one bot in a background thread per workspace.

Nothing is started automatically.  ``start()`` is called only after an
explicit, confirmed user action from the dashboard/API.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from .config_controller import ConfigError, ConfigController
from .resilience import describe_error
from .store import Store, _utc
from .telegram_auth import TelegramChallenge

_current_store: contextvars.ContextVar[Optional[Store]] = contextvars.ContextVar(
    "tcd_current_store", default=None
)


class BotRunner:
    def __init__(
        self,
        config_path: str,
        store: Store,
        *,
        dry_run: bool = False,
        config: Optional[Dict[str, Any]] = None,
        paths: Optional[Dict[str, str]] = None,
        bot_factory=None,
    ):
        self.config_path = config_path
        self.store = store
        self.dry_run = dry_run
        self.paths = paths or {}
        self.bot_factory = bot_factory
        self._config = config
        self._snapshot: Optional[Dict[str, Any]] = None

        self._lock = threading.Lock()
        self._state = "stopped"
        self._error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._stop_requested = threading.Event()
        self.challenge = TelegramChallenge()

    # -------------------------------------------------------------- public API
    def start(self, *, dry_run: Optional[bool] = None) -> Tuple[bool, str]:
        with self._lock:
            if self._state in ("starting", "running", "stopping") or (self._thread and self._thread.is_alive()):
                return False, "Bot is already running"
            if dry_run is not None:
                self.dry_run = dry_run

            try:
                config = self._config or self._load_config()
            except ConfigError as exc:
                self._state = "error"
                self._error = str(exc)
                self.store.event("error", str(exc), "ERROR")
                return False, str(exc)

            self._snapshot = config
            self._stop_requested.clear()
            self.challenge.reset()
            self._error = None
            self._state = "starting"
            self._started_at = time.time()
            self._thread = threading.Thread(target=self._run, daemon=True, name="duplicator-bot")
            self._thread.start()
            return True, "started"

    def stop(self) -> Tuple[bool, str]:
        with self._lock:
            if self._state in ("stopped", "error") and not (self._thread and self._thread.is_alive()):
                return True, "already stopped"
            self._stop_requested.set()
            loop, stop_event, thread = self._loop, self._stop_event, self._thread
            if self._state in ("starting", "running"):
                self._state = "stopping"

        self.challenge.cancel()
        if loop is not None and stop_event is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass
        if thread is not None:
            thread.join(timeout=15)

        with self._lock:
            alive = bool(self._thread and self._thread.is_alive())
            if not alive and self._state == "stopping":
                self._state = "stopped"
        return not alive, "stopped" if not alive else "still stopping"

    def reset_state(self) -> Tuple[bool, str]:
        """Reset only while stopped, atomically with respect to start()."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False, "Stop the bot before resetting state"
            self.store.reset()
            return True, "state reset"

    def status(self) -> Dict[str, Any]:
        with self._lock:
            state, error = self._state, self._error
            started = self._started_at
            dry_run = self.dry_run
            snapshot = self._snapshot
            alive = bool(self._thread and self._thread.is_alive())

        # After the worker stops, display the currently saved config rather
        # than the stale snapshot from its previous run.
        if snapshot is None or state not in ("starting", "running", "stopping"):
            try:
                snapshot = self._config or ConfigController.load(self.config_path)
            except ConfigError:
                snapshot = None

        groups = []
        if snapshot:
            groups = [
                {
                    "name": g["name"],
                    "sources": list(g["sources"]),
                    "destinations": list(g["destinations"]),
                    "whitelist": list(g.get("whitelist") or []),
                }
                for g in snapshot.get("groups", [])
            ]
        return {
            "state": state,
            "mode": "dry-run" if dry_run else "live",
            "dry_run": dry_run,
            "error": error,
            "started_at": _utc(started) if started else None,
            "uptime_seconds": round(time.time() - started, 1) if started and state in ("starting", "running") else 0,
            "thread_alive": alive,
            "config_path": self.config_path,
            "database": self.store.path,
            "groups": groups,
            "poll_delay": snapshot.get("delay") if snapshot else None,
            "telegram_challenge": self.challenge.status(),
        }

    # --------------------------------------------------------------- internals
    def _load_config(self) -> Dict[str, Any]:
        if self._config is not None:
            return self._config
        return ConfigController.load(self.config_path)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop_event = asyncio.Event()
        with self._lock:
            self._loop, self._stop_event = loop, stop_event
            if self._stop_requested.is_set():
                stop_event.set()
            elif self.bot_factory is not None:
                # Test-injected workers do not have Duplicator's ready callback.
                self._state = "running"

        token = _current_store.set(self.store)
        my_store = self.store

        def sink(message):
            if _current_store.get() is my_store:
                my_store.event("log", str(message.record["message"]).strip(), message.record["level"].name)

        sink_id = logger.add(sink, level="WARNING", format="{message}")
        try:
            if self._stop_requested.is_set():
                self._finish("stopped")
                return
            config = self._snapshot or self._load_config()
            if self.bot_factory is not None:
                bot = self.bot_factory(config, self.store, stop_event, self.dry_run, self.paths)
            else:
                from .duplicator import Duplicator

                bot = Duplicator(
                    config=config,
                    store=self.store,
                    dry_run=self.dry_run,
                    stop_event=stop_event,
                    paths=self.paths,
                    auth_challenge=self.challenge,
                    on_ready=self._mark_running,
                )
            loop.run_until_complete(bot.start())
            self._finish("stopped")
        except asyncio.CancelledError:
            # Stopping while a Telegram code/password is pending is normal.
            self._finish("stopped")
        except Exception as exc:  # noqa: BLE001 - surface every failure in the UI
            detail = describe_error(exc)
            self.store.event("error", f"Bot stopped: {detail}", "ERROR")
            logger.exception("Bot crashed")
            self._finish("error", detail)
        finally:
            try:
                logger.remove(sink_id)
            except (ValueError, KeyError):
                pass
            _current_store.reset(token)
            with self._lock:
                self._loop = self._stop_event = None
            loop.close()

    def _mark_running(self) -> None:
        # Do not report a live worker until Telegram authentication and chat
        # resolution have both succeeded. A stop request wins any startup race.
        with self._lock:
            if self._state == "starting" and not self._stop_requested.is_set():
                self._state = "running"

    def _finish(self, state: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._state = state
            self._error = error
