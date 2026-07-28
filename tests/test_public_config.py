import os
import subprocess
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "release" / "app"


class PublicConfigTest(unittest.TestCase):
    def run_config_module(self, source, extra_env=None):
        env = os.environ.copy()
        env.pop("FINANCE_API_KEY", None)
        env.pop("FINANCE_DB_PATH", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=APP_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_application_requires_an_explicit_api_key(self):
        result = self.run_config_module("from config import Config; Config.validate()")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FINANCE_API_KEY", result.stderr)

    def test_default_database_path_is_not_a_private_container_path(self):
        result = self.run_config_module(
            "from config import Config; print(Config.DB_PATH)",
            {"FINANCE_API_KEY": "test-only-key"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("/app/config", result.stdout)
        self.assertIn("runtime", result.stdout)


if __name__ == "__main__":
    unittest.main()
