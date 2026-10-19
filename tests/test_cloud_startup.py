"""Cloud startup rejects unsafe configurations without leaking connection details."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from scripts.start_cloud_api import configuration_errors, main


class CloudStartupTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "EXECUTION_MODE": "PAPER", "LIVE_TRADING_ENABLED": "false",
            "RUN_REFERENCE_GAP_BACKFILL": "0", "PMS_ALLOW_PRIVATE_HTTP": "0",
            "PMS_CLOUD_COLLECTION_REQUIRED": "1", "OPS_API_KEY": "x" * 40,
            "DATABASE_URL": "postgresql://user:private-password@pool.example.org:5432/postgres?sslmode=verify-full",
        }

    def test_deployment_configuration_accepted(self):
        self.assertEqual(configuration_errors(self.env), [])

    def test_each_missing_safety_setting_blocks(self):
        for name in self.env:
            with self.subTest(name=name):
                env = self.env.copy()
                env.pop(name)
                self.assertIn(name, configuration_errors(env))

    def test_unsafe_database_connections_rejected(self):
        for url in [
            self.env["DATABASE_URL"].replace("verify-full", "require"),
            self.env["DATABASE_URL"].replace("pool.example.org", "localhost"),
            self.env["DATABASE_URL"] + "&sslmode=disable",
            "postgresql://bad:[invalid/path",
        ]:
            with self.subTest(url=url):
                self.assertIn("DATABASE_URL", configuration_errors({**self.env, "DATABASE_URL": url}))

    def test_live_or_unguarded_entries_rejected(self):
        for name, value in [("EXECUTION_MODE", "LIVE"), ("LIVE_TRADING_ENABLED", "true"),
                            ("PMS_CLOUD_COLLECTION_REQUIRED", "0"), ("RUN_REFERENCE_GAP_BACKFILL", "1")]:
            self.assertIn(name, configuration_errors({**self.env, name: value}))

    def test_invalid_port_rejected(self):
        for value in ["0", "65536", "bad", "10000 --reload", "١٠٠٠٠"]:
            self.assertIn("PORT", configuration_errors({**self.env, "PORT": value}))

    def test_blocked_start_does_not_launch_or_expose_password(self):
        env = {**self.env, "LIVE_TRADING_ENABLED": "true"}
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch("os.execv") as launch, contextlib.redirect_stderr(output):
            self.assertEqual(main(), 2)
        launch.assert_not_called()
        self.assertNotIn("private-password", output.getvalue())
        self.assertIn("LIVE_TRADING_ENABLED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
