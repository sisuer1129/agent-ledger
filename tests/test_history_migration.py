import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from db import connect_database, initialize_database_path
from main import create_app
from migrate_history import HistoryMigrationError, analyze_csv, normalize_history
from tests.helpers import test_config


class HistoryMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir, config = test_config()
        self.app = create_app(config)
        self.db_path = Path(config["DB_PATH"])
        conn = connect_database(self.db_path)
        conn.execute("INSERT INTO accounts (id, name, type, current_balance, is_active) VALUES ('cash', '现金', 'debit', 100, 1)")
        cases = (
            ("repay", -100, "还款至示例信用卡B", "其他", 0),
            ("adjust", 5, "平账调整", "其他", 5),
            ("ai", -20, "OpenAI 订阅", "其他", -15),
            ("member-a-lunch-1", -20, "示例成员A午餐", "food", -35),
            ("member-a-lunch-2", -20, "示例成员A午餐", "food", -55),
            ("member-b-tuition", -20, "示例成员B学费", "其他", -75),
            ("ambiguous", -10, "不明消费", "其他", -85),
        )
        conn.executemany(
            "INSERT INTO transactions (id, account_id, timestamp, amount, description, category, balance_after) "
            "VALUES (?, 'cash', '2026-07-01T00:00:00+00:00', ?, ?, ?, ?)", cases,
        )
        conn.commit(); conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def legacy_snapshot(self):
        conn = connect_database(self.db_path)
        rows = [tuple(row) for row in conn.execute(
            "SELECT id, account_id, timestamp, amount, description, category, balance_after FROM transactions ORDER BY id"
        )]
        balances = [tuple(row) for row in conn.execute("SELECT id, current_balance FROM accounts ORDER BY id")]
        conn.close()
        return rows, balances

    def test_seven_case_dry_run_is_read_only_and_apply_preserves_financial_values(self):
        before = self.legacy_snapshot()
        database_bytes = self.db_path.read_bytes()
        dry = normalize_history(self.db_path)
        self.assertEqual(dry["total_transactions"], 7)
        self.assertEqual(dry["auto_classified"], 5)
        self.assertEqual(dry["needs_review"], 2)
        self.assertEqual(dry["amount_checksum_before"], "-185.00")
        self.assertEqual(dry["amount_checksum_after"], "-185.00")
        self.assertTrue(dry["balances_unchanged"])
        self.assertFalse(dry["applied"])
        self.assertEqual(before, self.legacy_snapshot())
        self.assertEqual(database_bytes, self.db_path.read_bytes())

        applied = normalize_history(self.db_path, apply=True)
        self.assertTrue(applied["applied"])
        self.assertTrue(Path(applied["backup_path"]).is_file())
        self.assertEqual(before, self.legacy_snapshot())
        conn = connect_database(self.db_path)
        self.assertEqual(conn.execute("SELECT transaction_kind FROM transactions WHERE id='repay'").fetchone()[0], "credit_repayment")
        self.assertEqual(conn.execute("SELECT transaction_kind FROM transactions WHERE id='adjust'").fetchone()[0], "balance_adjustment")
        self.assertEqual(conn.execute("SELECT category_id FROM transactions WHERE id='ai'").fetchone()[0], "expense_digital_ai")
        self.assertEqual([row[0] for row in conn.execute("SELECT tag_id FROM transaction_tags WHERE transaction_id='member-a-lunch-1'")], ["family_member_a"])
        self.assertEqual([row[0] for row in conn.execute("SELECT tag_id FROM transaction_tags WHERE transaction_id='member-b-tuition'")], ["family_member_b"])
        conn.close()

    def test_apply_creates_non_overwriting_backups_and_rolls_back_reconciliation_failure(self):
        first = normalize_history(self.db_path, apply=True)
        second = normalize_history(self.db_path, apply=True)
        self.assertNotEqual(first["backup_path"], second["backup_path"])
        before = self.legacy_snapshot()
        conn = connect_database(self.db_path)
        conn.execute("CREATE TRIGGER corrupt_balance AFTER UPDATE ON transactions BEGIN UPDATE accounts SET current_balance = current_balance + 1; END")
        conn.commit(); conn.close()
        with self.assertRaises(HistoryMigrationError):
            normalize_history(self.db_path, apply=True)
        self.assertEqual(before, self.legacy_snapshot())

    def test_csv_mode_reads_a_synthetic_ledger_without_creating_or_modifying_a_database(self):
        csv_path = Path(self.temp_dir.name) / "sample-ledger.csv"
        csv_path.write_text(
            "时间,账户,金额,分类,备注\n"
            "2026-07-01,日常储蓄账户,-20,餐饮,示例成员A午餐\n"
            "2026-07-02,日常储蓄账户,-10,其他,未分类交易\n"
            "2026-07-03,日常储蓄账户,-50,其他,还款至示例信用卡B\n",
            encoding="utf-8",
        )
        directory_before = {path.name for path in csv_path.parent.iterdir()}
        report = analyze_csv(csv_path)
        self.assertEqual(report["total_transactions"], 3)
        self.assertEqual(report["auto_classified"], 2)
        self.assertFalse(report["applied"])
        self.assertEqual(directory_before, {path.name for path in csv_path.parent.iterdir()})

    def test_initialize_database_path_upgrades_an_exact_legacy_schema(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT UNIQUE, type TEXT, initial_balance REAL DEFAULT 0, current_balance REAL DEFAULT 0, icon TEXT, notes TEXT, is_active INTEGER DEFAULT 1)")
        conn.execute("CREATE TABLE transactions (id TEXT PRIMARY KEY, account_id TEXT, timestamp TIMESTAMP, amount REAL, description TEXT, category TEXT, balance_after REAL)")
        conn.execute("CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT UNIQUE, type TEXT, icon TEXT, keywords TEXT)")
        conn.commit(); conn.close()
        initialize_database_path(legacy_path)
        conn = connect_database(legacy_path)
        self.assertIn("classification_status", [row["name"] for row in conn.execute("PRAGMA table_info(transactions)")])
        self.assertIsNotNone(conn.execute("SELECT id FROM tags WHERE id='family_member_a'").fetchone())
        conn.close()

    def test_apply_can_store_backups_in_an_explicit_directory(self):
        backup_dir = Path(self.temp_dir.name) / "backups"
        result = normalize_history(self.db_path, apply=True, backup_dir=backup_dir)
        self.assertTrue(Path(result["backup_path"]).is_file())
        self.assertEqual(Path(result["backup_path"]).parent, backup_dir)
