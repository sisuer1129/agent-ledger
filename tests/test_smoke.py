import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(TEST_DIR))
sys.path.insert(0, str(APP_DIR))

from main import create_app
from helpers import test_config


class SmokeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir, config = test_config()
        self.app = create_app(config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_frontend_root(self):
        response = self.client.get("/")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertIn("日用账本", response.get_data(as_text=True))
