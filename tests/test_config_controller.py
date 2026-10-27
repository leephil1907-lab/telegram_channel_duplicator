import unittest

from telegram_channel_duplicator.config_controller import ConfigController


def valid_config():
    return {
        "account_phone": "+10000000000",
        "api_id": 12345,
        "api_hash": "0123456789abcdef0123456789abcdef",
        "delay": 5,
        "edit_message_checker_limit": 10,
        "groups": [
            {
                "name": "test",
                "sources": ["source"],
                "destinations": ["destination"],
                "whitelist": [],
            }
        ],
    }


class ConfigValidationTests(unittest.TestCase):
    def test_valid_configuration_is_accepted(self):
        ConfigController.validate(valid_config())

    def test_missing_required_key_is_rejected(self):
        config = valid_config()
        del config["api_hash"]
        with self.assertRaisesRegex(ValueError, "api_hash"):
            ConfigController.validate(config)

    def test_zero_delay_is_rejected(self):
        config = valid_config()
        config["delay"] = 0
        with self.assertRaisesRegex(ValueError, "delay"):
            ConfigController.validate(config)

    def test_empty_destination_list_is_rejected(self):
        config = valid_config()
        config["groups"][0]["destinations"] = []
        with self.assertRaisesRegex(ValueError, "destinations"):
            ConfigController.validate(config)


if __name__ == "__main__":
    unittest.main()
