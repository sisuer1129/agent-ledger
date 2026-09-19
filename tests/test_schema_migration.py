import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from flask import Flask


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

import db
from db import column_exists, connect_database, init_database, schema_version


ACCOUNT_V2_COLUMNS = (
    "monthly_budget",
    "budget_period",
    "credit_limit",
    "statement_day",
    "due_day",
    "due_month_offset",
)
TRANSACTION_V2_COLUMNS = (
    "category_id",
    "transaction_kind",
    "excluded_from_stats",
    "classification_status",
)
TRANSACTION_V5_COLUMNS = ("repayment_group_id",)
CATEGORY_V2_COLUMNS = ("parent_id", "sort_order", "is_active")


class SchemaMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "legacy-wallet.db"
        self._create_legacy_database()
        self.app = self._make_app()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_app(self):
        app = Flask(__name__)
        app.config["DB_PATH"] = str(self.db_path)
        return app

    def _create_legacy_database(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
                "type TEXT, initial_balance REAL DEFAULT 0, current_balance REAL "
                "DEFAULT 0, icon TEXT, notes TEXT, is_active INTEGER DEFAULT 1)"
            )
            conn.execute(
                "CREATE TABLE transactions (id TEXT PRIMARY KEY, account_id TEXT, "
                "timestamp TIMESTAMP, amount REAL, description TEXT, category TEXT, "
                "balance_after REAL)"
            )
            conn.execute(
                "CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
                "type TEXT, icon TEXT, keywords TEXT)"
            )
            conn.execute(
                "INSERT INTO accounts "
                "(id, name, type, initial_balance, current_balance) "
                "VALUES (?, ?, ?, ?, ?)",
                ("acc-1", "示例银行", "debit", 10000.00, 9873.90),
            )
            conn.execute(
                "INSERT INTO transactions "
                "(id, account_id, timestamp, amount, description, category, balance_after) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "tx-1",
                    "acc-1",
                    "2026-07-12T12:00:00",
                    -126.10,
                    "示例出行",
                    "交通",
                    9873.90,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_migrates_legacy_database_without_changing_existing_values(self):
        with closing(connect_database(self.db_path)) as conn:
            before_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            before_balance = conn.execute(
                "SELECT current_balance FROM accounts WHERE id = ?", ("acc-1",)
            ).fetchone()[0]

            init_database(self.app)

            after_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            after_balance = conn.execute(
                "SELECT current_balance FROM accounts WHERE id = ?", ("acc-1",)
            ).fetchone()[0]
            self.assertEqual(after_count, before_count)
            self.assertEqual(after_balance, before_balance)
            for column in ACCOUNT_V2_COLUMNS:
                self.assertTrue(column_exists(conn, "accounts", column))
            for column in TRANSACTION_V2_COLUMNS:
                self.assertTrue(column_exists(conn, "transactions", column))
            for column in CATEGORY_V2_COLUMNS:
                self.assertTrue(column_exists(conn, "categories", column))
            for column in TRANSACTION_V5_COLUMNS:
                self.assertTrue(column_exists(conn, "transactions", column))
            self.assertEqual(schema_version(conn), 6)
            init_database(self.app)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
                before_count,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT current_balance FROM accounts WHERE id = ?", ("acc-1",)
                ).fetchone()[0],
                before_balance,
            )
            self.assertEqual(schema_version(conn), 6)

    def test_v4_upgrade_preserves_existing_budgets_and_history_and_accepts_total_budget(self):
        with closing(connect_database(self.db_path)) as conn:
            db._create_base_schema(conn)
            db._migrate_to_version_2(conn)
            db._migrate_to_version_3(conn)
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '3')"
            )
            conn.execute(
                "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) VALUES (?, ?, ?, ?, ?)",
                ("account-budget", "account", "acc-1", 3000, "2026-07-01"),
            )
            conn.execute(
                "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) VALUES (?, ?, ?, ?, ?)",
                ("category-budget", "category", "legacy-category", 600, "2026-07-01"),
            )
            conn.commit()
            history_before = [tuple(row) for row in conn.execute(
                "SELECT id, account_id, timestamp, amount, description FROM transactions ORDER BY id"
            )]

        init_database(self.app)

        with closing(connect_database(self.db_path)) as conn:
            self.assertEqual(schema_version(conn), 6)
            self.assertEqual(
                [tuple(row) for row in conn.execute(
                    "SELECT id, scope_type, scope_id, amount, effective_from FROM budgets "
                    "WHERE id IN ('account-budget', 'category-budget') ORDER BY id"
                )],
                [
                    ("account-budget", "account", "acc-1", 3000.0, "2026-07-01"),
                    ("category-budget", "category", "legacy-category", 600.0, "2026-07-01"),
                ],
            )
            self.assertEqual(
                [tuple(row) for row in conn.execute(
                    "SELECT id, account_id, timestamp, amount, description FROM transactions ORDER BY id"
                )],
                history_before,
            )
            conn.execute(
                "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) VALUES (?, ?, ?, ?, ?)",
                ("total-budget", "total", "total", 9000, "2026-07-01"),
            )
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT amount FROM budgets WHERE id = 'total-budget'").fetchone()[0],
                9000.0,
            )

    def test_migration_creates_v2_schema_with_defaults_constraints_and_foreign_keys(self):
        init_database(self.app)

        with closing(connect_database(self.db_path)) as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertTrue(
                {"tags", "transaction_tags", "budgets", "classification_rules"} <= tables
            )

            account_info = {
                row["name"]: row for row in conn.execute("PRAGMA table_info(accounts)")
            }
            transaction_info = {
                row["name"]: row for row in conn.execute("PRAGMA table_info(transactions)")
            }
            category_info = {
                row["name"]: row for row in conn.execute("PRAGMA table_info(categories)")
            }
            tag_info = {
                row["name"]: row for row in conn.execute("PRAGMA table_info(tags)")
            }
            classification_rule_info = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(classification_rules)")
            }
            self.assertEqual(account_info["budget_period"]["dflt_value"], "'calendar_month'")
            self.assertEqual(account_info["due_month_offset"]["dflt_value"], "1")
            self.assertEqual(transaction_info["excluded_from_stats"]["dflt_value"], "0")
            self.assertEqual(
                transaction_info["classification_status"]["dflt_value"], "'unreviewed'"
            )
            self.assertEqual(category_info["sort_order"]["dflt_value"], "0")
            self.assertEqual(category_info["is_active"]["dflt_value"], "1")
            self.assertEqual(tag_info["keywords"]["dflt_value"], "''")
            self.assertEqual(tag_info["is_active"]["dflt_value"], "1")
            self.assertEqual(classification_rule_info["priority"]["dflt_value"], "100")

            foreign_keys = {
                row["from"]: (row["table"], row["on_delete"])
                for row in conn.execute("PRAGMA foreign_key_list(transaction_tags)")
            }
            self.assertEqual(foreign_keys["transaction_id"], ("transactions", "CASCADE"))
            self.assertEqual(foreign_keys["tag_id"], ("tags", "NO ACTION"))

            conn.execute(
                "INSERT INTO tags (id, name, group_name) VALUES (?, ?, ?)",
                ("tag-1", "出行", "场景"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tags (id, name, group_name) VALUES (?, ?, ?)",
                    ("tag-duplicate", "出行", "场景"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    ("missing-tx", "tag-1"),
                )
            conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                ("tx-1", "tag-1"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    ("tx-1", "tag-1"),
                )
            conn.execute("DELETE FROM transactions WHERE id = ?", ("tx-1",))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM transaction_tags").fetchone()[0], 0
            )

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("budget-invalid", "account", "acc-1", -1, "2026-07-01"),
                )
            conn.execute(
                "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) "
                "VALUES (?, ?, ?, ?, ?)",
                ("budget-1", "account", "acc-1", 100, "2026-07-01"),
            )
            conn.execute(
                "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) "
                "VALUES (?, ?, ?, ?, ?)",
                ("budget-total", "total", "total", 1000, "2026-07-01"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("budget-duplicate", "account", "acc-1", 200, "2026-07-01"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO classification_rules (id, rule_type, pattern) VALUES (?, ?, ?)",
                    ("rule-invalid", "invalid", "示例规则"),
                )

    def test_concurrent_initialization_succeeds_with_one_versioned_migration(self):
        self.assertTrue(
            hasattr(db, "_migration_lock_acquired"),
            "migration lock synchronization hook is missing",
        )
        start = threading.Barrier(2)
        lock_acquired = threading.Event()
        release_migration = threading.Event()
        original_hook = db._migration_lock_acquired
        original_migration = db._migrate_to_version_2
        migration_calls = 0
        migration_call_lock = threading.Lock()

        def hold_first_lock(conn):
            if not lock_acquired.is_set():
                lock_acquired.set()
                release_migration.wait(timeout=5)
            return original_hook(conn)

        def initialize():
            start.wait(timeout=5)
            init_database(self._make_app())

        def count_migration(conn):
            nonlocal migration_calls
            with migration_call_lock:
                migration_calls += 1
            return original_migration(conn)

        with patch("db._migration_lock_acquired", side_effect=hold_first_lock), patch(
            "db._migrate_to_version_2", side_effect=count_migration
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(initialize)
                second = executor.submit(initialize)
                self.assertTrue(lock_acquired.wait(timeout=5))
                release_migration.set()
                first.result(timeout=5)
                second.result(timeout=5)

        with closing(connect_database(self.db_path)) as conn:
            self.assertEqual(migration_calls, 1)
            self.assertEqual(schema_version(conn), 6)

    def test_late_migration_failure_rolls_back_schema_and_version(self):
        self.assertTrue(
            hasattr(db, "_after_schema_version_update"),
            "late-migration failure injection hook is missing",
        )
        with patch(
            "db._after_schema_version_update",
            side_effect=RuntimeError("injected migration failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                init_database(self.app)

        with closing(connect_database(self.db_path)) as conn:
            self.assertFalse(column_exists(conn, "accounts", "monthly_budget"))
            self.assertFalse(column_exists(conn, "transactions", "category_id"))
            self.assertNotIn(
                "tags",
                {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                },
            )
            self.assertEqual(schema_version(conn), 0)


if __name__ == "__main__":
    unittest.main()
