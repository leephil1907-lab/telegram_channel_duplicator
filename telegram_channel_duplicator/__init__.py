"""Telegram channel duplicator - see https://github.com/deFiss/telegram_channel_duplicator"""

__version__ = "0.8.1"

__all__ = ["Duplicator", "Client", "ConfigController", "ConfigError"]


def __getattr__(name):
    # Lazy imports keep ``import telegram_channel_duplicator`` cheap and let
    # ``--version``/``--check`` work without constructing a Telethon client.
    if name in __all__:
        from importlib import import_module

        module = {
            "Duplicator": "duplicator",
            "Client": "client",
            "ConfigController": "config_controller",
            "ConfigError": "config_controller",
        }[name]
        return getattr(import_module(f"{__name__}.{module}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
