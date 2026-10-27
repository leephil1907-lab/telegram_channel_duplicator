import os

import yaml


CONFIG_FILE_PATH = os.environ.get("TELEBRIDGE_CONFIG", "config.yaml")


class ConfigController:
    @staticmethod
    def get_config():
        if not os.path.isfile(CONFIG_FILE_PATH):
            raise FileNotFoundError(
                f"Configuration file '{CONFIG_FILE_PATH}' was not found. "
                "Copy config.yaml.example to config.yaml and edit it."
            )

        with open(CONFIG_FILE_PATH, "r", encoding="utf8") as config_file:
            config = yaml.safe_load(config_file)

        ConfigController.validate(config)
        return config

    @staticmethod
    def validate(config):
        if not isinstance(config, dict):
            raise ValueError("Configuration must be a YAML mapping.")

        required = ("account_phone", "api_id", "api_hash", "delay",
                    "edit_message_checker_limit", "groups")
        missing = [key for key in required if key not in config]
        if missing:
            raise ValueError("Missing configuration keys: " + ", ".join(missing))

        if not isinstance(config["account_phone"], str) or not config["account_phone"].strip():
            raise ValueError("account_phone must be a non-empty string.")
        if not isinstance(config["api_id"], int) or isinstance(config["api_id"], bool) or config["api_id"] <= 0:
            raise ValueError("api_id must be the positive integer from my.telegram.org.")
        if not isinstance(config["api_hash"], str) or len(config["api_hash"].strip()) < 10:
            raise ValueError("api_hash must be the API hash string from my.telegram.org.")
        if not isinstance(config["delay"], (int, float)) or isinstance(config["delay"], bool) or config["delay"] <= 0:
            raise ValueError("delay must be a number greater than zero.")
        if not isinstance(config["edit_message_checker_limit"], int) or isinstance(config["edit_message_checker_limit"], bool) or config["edit_message_checker_limit"] < 0:
            raise ValueError("edit_message_checker_limit must be a non-negative integer.")
        if not isinstance(config["groups"], list) or not config["groups"]:
            raise ValueError("groups must be a non-empty list.")

        for index, group in enumerate(config["groups"]):
            prefix = f"groups[{index}]"
            if not isinstance(group, dict):
                raise ValueError(f"{prefix} must be a mapping.")
            if not isinstance(group.get("name"), str) or not group["name"].strip():
                raise ValueError(f"{prefix}.name must be a non-empty string.")
            for key in ("sources", "destinations", "whitelist"):
                if not isinstance(group.get(key), list):
                    raise ValueError(f"{prefix}.{key} must be a list.")
            if not group["sources"] or not all(isinstance(v, str) and v.strip() for v in group["sources"]):
                raise ValueError(f"{prefix}.sources must contain chat names.")
            if not group["destinations"] or not all(isinstance(v, str) and v.strip() for v in group["destinations"]):
                raise ValueError(f"{prefix}.destinations must contain chat names.")
            if not all(isinstance(v, str) for v in group["whitelist"]):
                raise ValueError(f"{prefix}.whitelist must contain only strings.")
