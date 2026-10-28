"""Loading and validation of the YAML configuration.

Compared with upstream this adds:

* ``.env`` support and ``${VAR}`` / ``${VAR:-default}`` expansion, so API
  credentials never have to be committed to a config file;
* structural/type validation with actionable error messages;
* ``validate_text`` for validating YAML from the dashboard without writing it.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:  # python-dotenv is a declared dependency, but keep imports resilient
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover
    find_dotenv = load_dotenv = None  # type: ignore[assignment]

CONFIG_FILE_PATH = "config.yaml"

_REQUIRED_KEYS = ("account_phone", "api_id", "api_hash")
_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """Raised when the configuration is missing, unreadable or invalid."""


class ConfigController:
    """Reads ``config.yaml`` and returns a validated, normalised dict."""

    @staticmethod
    def get_config() -> Dict[str, Any]:
        """Backwards-compatible entry point used by :class:`Duplicator`."""
        return ConfigController.load()

    @staticmethod
    def load(path: Optional[str] = None, *, use_dotenv: bool = True) -> Dict[str, Any]:
        """Load, expand and validate a config file from disk."""
        path = path or CONFIG_FILE_PATH
        if use_dotenv:
            _load_dotenv()
        return ConfigController.validate_text(_read(path), source=path)

    @staticmethod
    def validate_text(
        text: str,
        *,
        use_dotenv: bool = False,
        source: str = "<text>",
        allow_unresolved_env: bool = False,
    ) -> Dict[str, Any]:
        """Expand env placeholders and validate YAML text without saving it.

        ``allow_unresolved_env`` substitutes safe dummy values for unset
        variables so a config can be *saved* before its secrets are exported
        into the environment.  Startup (``load``) always stays strict.
        """
        if use_dotenv:
            _load_dotenv()
        expanded, missing = _expand_env(text, dummies={} if allow_unresolved_env else None)
        if missing and not allow_unresolved_env:
            raise ConfigError(
                "Environment variable(s) not set: "
                + ", ".join(sorted(set(missing)))
                + ". Set them in your shell or in a .env file (see .env.example)."
            )
        try:
            data = yaml.safe_load(expanded)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in '{source}': {exc}") from exc
        return validate_config(data)

    @staticmethod
    def missing_env(text: str, *, use_dotenv: bool = True):
        """Names of ``${VAR}`` placeholders that currently have no value."""
        if use_dotenv:
            _load_dotenv()
        return sorted(set(_expand_env(text)[1]))


def _load_dotenv() -> None:
    if load_dotenv is not None and find_dotenv is not None:
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            load_dotenv(dotenv_path, override=False)


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf8") as f:
            return f.read()
    except FileNotFoundError as exc:
        raise ConfigError(
            f"'{path}' not found. Create your own config.yaml "
            "and fill in your Telegram API credentials and channel groups."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read '{path}': {exc}") from exc


def _expand_env(raw: str, *, dummies=None) -> Tuple[str, List[str]]:
    """Substitute ``${VAR}`` / ``${VAR:-default}``; return (text, missing).

    When ``dummies`` is not None, unresolved names are replaced with
    structurally valid placeholder values (used when saving a config whose
    secrets will be supplied by the environment at runtime).
    """
    missing: List[str] = []

    def replace(match: "re.Match[str]") -> str:
        name, default = match.group(1), match.group(2)
        value = os.environ.get(name)
        if value is None or value == "":
            if default is not None:
                return default
            missing.append(name)
            if dummies is None:
                return ""
            upper = name.upper()
            if "PHONE" in upper:
                return "+10000000000"
            if "API_ID" in upper or upper.endswith("_ID"):
                return "123456"
            if "HASH" in upper or "SECRET" in upper or "TOKEN" in upper:
                return "placeholder0000000000000000000000000"
            return "placeholder"
        return value

    return _ENV_VAR.sub(replace, raw), missing


def validate_config(config: Any) -> Dict[str, Any]:
    """Validate a parsed config mapping and return a normalised copy."""
    if not isinstance(config, dict):
        raise ConfigError("Config root must be a YAML mapping (key: value pairs).")

    for key in _REQUIRED_KEYS:
        if config.get(key) in (None, ""):
            raise ConfigError(
                f"Missing or empty required setting '{key}'. "
                "See https://core.telegram.org/api/obtaining_api_id for api_id/api_hash."
            )

    try:
        api_id = int(config["api_id"])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'api_id' must be an integer, got {config['api_id']!r}") from exc
    if api_id <= 0:
        raise ConfigError("'api_id' must be a positive integer.")

    api_hash = str(config["api_hash"]).strip()
    phone = str(config["account_phone"]).strip()
    if not api_hash or set(api_hash) == {"0"}:
        raise ConfigError("'api_hash' is a placeholder; put your real API hash in the config.")
    if not phone.startswith("+") or len(phone) < 5:
        raise ConfigError("'account_phone' must be in international format, e.g. +15551234567.")

    delay = _number(config, "delay", 5.0, float)
    if delay <= 0:
        raise ConfigError("'delay' must be greater than 0 seconds.")
    limit = _number(config, "edit_message_checker_limit", 10, int)
    if limit < 0:
        raise ConfigError("'edit_message_checker_limit' cannot be negative.")

    groups = config.get("groups") or []
    if not isinstance(groups, list):
        raise ConfigError("'groups' must be a YAML list of group objects.")
    if not groups:
        raise ConfigError("'groups' is empty: define at least one source/destination group.")

    normalised = []
    for i, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise ConfigError(f"Group #{i} must be a mapping with name/sources/destinations.")
        name = group.get("name") or f"group-{i}"
        sources = _string_list(group.get("sources"), f"Group '{name}' sources")
        destinations = _string_list(group.get("destinations"), f"Group '{name}' destinations")
        if not sources:
            raise ConfigError(f"Group '{name}' has no sources.")
        if not destinations:
            raise ConfigError(f"Group '{name}' has no destinations.")
        whitelist = group.get("whitelist") or []
        if not isinstance(whitelist, list):
            raise ConfigError(f"Group '{name}' whitelist must be a list of words.")
        normalised.append(
            {
                "name": str(name),
                "sources": sources,
                "destinations": destinations,
                "whitelist": [str(word) for word in whitelist],
            }
        )

    return {
        "account_phone": phone,
        "api_id": api_id,
        "api_hash": api_hash,
        "delay": delay,
        "edit_message_checker_limit": limit,
        "groups": normalised,
    }


def _number(config: Dict[str, Any], key: str, default: Any, caster: Any) -> Any:
    value = config.get(key, default)
    try:
        return caster(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'{key}' must be a number, got {value!r}") from exc


def _string_list(value: Any, label: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be a list of chat titles.")
    return [str(item).strip() for item in value if str(item).strip()]
