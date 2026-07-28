import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "runtime" / "agent-ledger.db"


class Config:
    API_PORT = int(os.environ.get("FINANCE_API_PORT", "5009"))
    DB_PATH = os.environ.get("FINANCE_DB_PATH", str(DEFAULT_DATABASE_PATH))
    API_KEY = os.environ.get("FINANCE_API_KEY", "").strip()

    @classmethod
    def validate(cls, values=None):
        api_key = (values or {"API_KEY": cls.API_KEY}).get("API_KEY", "")
        if not str(api_key).strip():
            raise RuntimeError("FINANCE_API_KEY must be set before starting the application")
