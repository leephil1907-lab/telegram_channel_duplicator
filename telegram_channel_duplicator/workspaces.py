"""Per-user workspaces: every account (and every anonymous browser) gets its
own directory, SQLite database, Telegram session, config and bot thread.

No sample config is provisioned.  A new workspace starts with an EMPTY
config.yaml that the owner must fill in before anything can run.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .auth import Auth
from .runner import BotRunner
from .store import Store

KEY_RE = re.compile(r"^[wg]_[0-9a-f]{32}$")


@dataclass
class Workspace:
    key: str
    path: str
    paths: Dict[str, str]
    store: Store
    runner: BotRunner
    created_at: float = field(default_factory=time.time)

    def close(self) -> None:
        self.runner.stop()
        self.store.close()


class WorkspaceManager:
    def __init__(
        self,
        data_dir: str,
        *,
        dry_run: bool = False,
        allow_registration: bool = True,
        bot_factory=None,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.dry_run = dry_run
        self.bot_factory = bot_factory
        os.makedirs(os.path.join(self.data_dir, "workspaces"), exist_ok=True)

        self.auth = Auth(
            os.path.join(self.data_dir, "users.db"), allow_registration=allow_registration
        )
        self._workspaces: Dict[str, Workspace] = {}
        self._lock = threading.RLock()

    def workspace(self, key: str) -> Workspace:
        if not KEY_RE.match(key):
            raise ValueError("invalid workspace key")
        with self._lock:
            existing = self._workspaces.get(key)
            if existing is not None:
                return existing

            root = os.path.join(self.data_dir, "workspaces", key)
            paths = {
                "root": root,
                "config": os.path.join(root, "config.yaml"),
                "state": os.path.join(root, "state.db"),
                "sessions": os.path.join(root, "sessions"),
                "logs": os.path.join(root, "logs"),
            }
            for sub in ("sessions", "logs"):
                os.makedirs(paths[sub], exist_ok=True)
            if not os.path.exists(paths["config"]):
                # Empty on purpose: nothing is pre-approved or pre-configured.
                with open(paths["config"], "w", encoding="utf8") as f:
                    f.write("")
                os.chmod(paths["config"], 0o600)

            store = Store(paths["state"])
            runner = BotRunner(
                paths["config"],
                store,
                dry_run=self.dry_run,
                paths=paths,
                bot_factory=self.bot_factory,
            )
            ws = Workspace(key=key, path=root, paths=paths, store=store, runner=runner)
            self._workspaces[key] = ws
            # Nothing starts automatically.  The owner must explicitly start it.
            return ws

    def workspace_count(self) -> int:
        with self._lock:
            return len(self._workspaces)

    def close(self) -> None:
        with self._lock:
            workspaces = list(self._workspaces.values())
            self._workspaces.clear()
        for ws in workspaces:
            ws.close()
        self.auth.close()
