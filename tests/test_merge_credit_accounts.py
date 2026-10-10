import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))
sys.path.insert(0, str(TEST_DIR.parent / "release" / "app"))

from db import get_db
from helpers import test_config
from main import create_app
from merge_credit_accounts import merge, preview


class CreditMergeTest(unittest.TestCase):
    def setUp(self):
        self.temp, config = test_config()
        self.app = create_app(config)

    def tearDown(self):
        self.temp.cleanup()

    def test_merge_preserves_total_history_repayments_and_subcard(self):
        with self.app.app_context():
            conn = get_db()
            conn.executemany(
                "INSERT INTO accounts (id, name, type, initial_balance, current_balance, is_active) "
                "VALUES (?, ?, 'credit', ?, ?, 1)",
                [("a", "工行信用卡", -20, -30), ("b", "建行信用卡", -40, -50),
                 ("sub", "示例副卡", -100, -100)],
            )
            conn.executemany(
                "INSERT INTO transactions (id, account_id, timestamp, amount, description, transaction_kind, "
                "repayment_group_id, balance_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [("t1", "a", "2026-01-01", -10, "消费", "expense", None, -30),
                 ("t2", "b", "2026-01-02", 20, "还款", "credit_repayment", "repay1", -50)],
            )
            conn.commit()
            self.assertEqual(preview(conn, {"示例副卡"})["balance"], -80)
            target_id, _ = merge(conn, {"示例副卡"})
            self.assertEqual(conn.execute("SELECT current_balance FROM accounts WHERE id = ?", (target_id,)).fetchone()[0], -80)
            self.assertEqual(conn.execute("SELECT current_balance FROM accounts WHERE id = 'sub'").fetchone()[0], -100)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 1 AND type = 'credit'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM credit_merge_origins").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT repayment_group_id FROM transactions WHERE id = 't2'").fetchone()[0], "repay1")
            self.assertEqual(conn.execute("SELECT balance_after FROM transactions WHERE id = 't2'").fetchone()[0], -80)

    def test_account_budget_blocks_merge_without_changes(self):
        with self.app.app_context():
            conn = get_db()
            conn.execute("INSERT INTO accounts (id, name, type, is_active) VALUES ('a', '工行信用卡', 'credit', 1)")
            conn.execute("INSERT INTO accounts (id, name, type, is_active) VALUES ('sub', '示例副卡', 'credit', 1)")
            conn.execute("INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) VALUES ('budget', 'account', 'a', 100, '2026-01-01')")
            conn.commit()
            with self.assertRaisesRegex(ValueError, "账户级预算"):
                merge(conn, {"示例副卡"})
            self.assertEqual(conn.execute("SELECT is_active FROM accounts WHERE id = 'a'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)

    def test_existing_credit_target_is_reused(self):
        with self.app.app_context():
            conn = get_db()
            conn.executemany(
                "INSERT INTO accounts (id, name, type, current_balance, is_active) VALUES (?, ?, 'credit', ?, 1)",
                [("target", "信用卡", -12), ("source", "招行信用卡", -3), ("sub", "示例副卡", 0)],
            )
            conn.commit()
            target_id, _ = merge(conn, {"示例副卡"})
            self.assertEqual(target_id, "target")
            self.assertEqual(conn.execute("SELECT current_balance FROM accounts WHERE id = 'target'").fetchone()[0], -15)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts WHERE name = '信用卡'").fetchone()[0], 1)

    def test_unknown_exclusion_cannot_merge_a_subcard_by_typo(self):
        with self.app.app_context():
            conn = get_db()
            conn.executemany(
                "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, 'credit', 1)",
                [("source", "示例主卡"), ("sub", "示例副卡")],
            )
            conn.commit()
            with self.assertRaisesRegex(ValueError, "排除账户不存在"):
                merge(conn, {"副卡"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 1").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
