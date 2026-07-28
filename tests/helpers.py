import tempfile
from pathlib import Path


def test_config():
    temp_dir = tempfile.TemporaryDirectory()
    return temp_dir, {
        "TESTING": True,
        "DB_PATH": str(Path(temp_dir.name) / "wallet.db"),
        "API_KEY": "test-key",
    }


def auth_headers():
    return {"X-API-Key": "test-key"}
