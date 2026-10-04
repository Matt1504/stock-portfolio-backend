import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class DatabaseConfigurationTests(unittest.TestCase):
    def load(self):
        path = Path(__file__).resolve().parents[1] / "src/database/database.py"
        spec = importlib.util.spec_from_file_location("database_config_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_environment_uri_does_not_need_a_password_file(self):
        with patch.dict(os.environ, {"MONGODB_URI": "mongodb://example.invalid", "MONGODB_DATABASE": "other_portfolio"}), patch("mongoengine.connect") as connect:
            module = self.load()
        self.assertEqual(module.DATABASE, "other_portfolio")
        connect.assert_called_once_with("other_portfolio", host="mongodb://example.invalid", serverSelectionTimeoutMS=5000, alias="default")

    def test_legacy_credentials_are_uri_encoded(self):
        passwords = types.ModuleType("database.passwords")
        passwords.USER = "example@user"
        passwords.PASSWORD = "test/p:a+ss"
        passwords.CLUSTER = "example-cluster"
        with patch.dict(os.environ, {"MONGODB_URI": "", "MONGODB_DATABASE": "stock_portfolio"}), patch.dict(sys.modules, {"database.passwords": passwords}), patch("mongoengine.connect") as connect:
            self.load()
        self.assertEqual(connect.call_args.kwargs["host"], "mongodb+srv://example%40user:test%2Fp%3Aa%2Bss@example-cluster.mongodb.net/?ssl=true")

    def test_missing_configuration_has_a_clear_error(self):
        with patch.dict(os.environ, {"MONGODB_URI": ""}), patch.dict(sys.modules, {"database.passwords": None}), patch("mongoengine.connect") as connect:
            with self.assertRaisesRegex(RuntimeError, "Set MONGODB_URI"):
                self.load()
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
