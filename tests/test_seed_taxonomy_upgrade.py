import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from db import connect_database, initialize_database_path
from seed_taxonomy_upgrade import resolve_legacy_target, upgrade_seed_taxonomy


class SeedTaxonomyUpgradeTest(unittest.TestCase):
    def test_history_mapping_prefers_person_and_description_context(self):
        self.assertEqual(
            resolve_legacy_target("教育成长", "子女教育", "示例成员A学费", {"family_member_a"}),
            "expense_family_child_education",
        )
        self.assertEqual(
            resolve_legacy_target("车辆", "停车", "小区固定车位", set()),
            "expense_vehicle_fixed_parking",
        )
        self.assertEqual(
            resolve_legacy_target("交通出行", "停车", "商场临停", set()),
            "expense_transport_temporary_parking",
        )
        self.assertEqual(
            resolve_legacy_target("旅行度假", "酒店住宿", "东京酒店", set()),
            "expense_travel_hotel",
        )
        self.assertEqual(
            resolve_legacy_target("车辆", "其他车辆费用", "车辆购置分期", set()),
            "expense_vehicle_loan",
        )
        self.assertEqual(
            resolve_legacy_target("人情与家庭", "子女支出", "示例大学", set()),
            "expense_family_child_education",
        )

    def test_unmatched_legacy_category_is_preserved_and_remains_active_while_referenced(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.db"
            backup_dir = Path(directory) / "backups"
            initialize_database_path(db_path)
            conn = connect_database(db_path)
            try:
                conn.execute(
                    "INSERT INTO accounts (id, name, type, current_balance) VALUES ('a1', '测试账户', 'cash', 88)"
                )
                conn.execute(
                    "INSERT INTO categories (id, name, type, icon, keywords, parent_id, sort_order, is_active) "
                    "VALUES ('legacy_food', '旧餐饮', 'expense', '', '', NULL, 99, 1)"
                )
                conn.execute(
                    "INSERT INTO transactions (id, account_id, timestamp, amount, description, category_id, "
                    "transaction_kind, excluded_from_stats, classification_status) "
                    "VALUES ('t1', 'a1', '2026-07-26T00:00:00+00:00', -12, '测试消费', 'legacy_food', "
                    "'expense', 0, 'manual')"
                )
                conn.commit()
                before = _financial_snapshot(conn)
            finally:
                conn.close()

            preview = upgrade_seed_taxonomy(db_path, apply=False)
            self.assertFalse(preview["applied"])
            self.assertEqual(preview["categories_to_deactivate"], 0)
            self.assertEqual(preview["preserved_existing"], 1)
            self.assertEqual(preview["unmatched"], 1)
            self.assertEqual(preview["review_examples"][0]["record"], "record-1")
            self.assertNotIn("description", preview["review_items"][0])
            self.assertEqual(_snapshot_path(db_path), before)

            result = upgrade_seed_taxonomy(db_path, apply=True, backup_dir=backup_dir)
            self.assertTrue(result["applied"])
            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertEqual(_snapshot_path(db_path), before)
            conn = connect_database(db_path)
            try:
                self.assertEqual(
                    conn.execute("SELECT is_active FROM categories WHERE id = 'legacy_food'").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute("SELECT category_id FROM transactions WHERE id = 't1'").fetchone()[0], "legacy_food"
                )
                self.assertEqual(
                    conn.execute("SELECT classification_status FROM transactions WHERE id = 't1'").fetchone()[0], "needs_review"
                )
            finally:
                conn.close()

    def test_verified_legacy_mapping_updates_and_then_does_not_deactivate_referenced_target(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.db"
            initialize_database_path(db_path)
            conn = connect_database(db_path)
            try:
                conn.execute("INSERT INTO accounts (id, name, type, current_balance) VALUES ('a1', '测试账户', 'cash', 88)")
                conn.executemany(
                    "INSERT INTO categories (id, name, type, icon, keywords, parent_id, sort_order, is_active) "
                    "VALUES (?, ?, 'expense', '', '', ?, 99, 1)",
                    (("legacy_food_root", "餐饮", None), ("legacy_food_child", "正餐", "legacy_food_root")),
                )
                conn.execute(
                    "INSERT INTO transactions (id, account_id, timestamp, amount, description, category_id, "
                    "transaction_kind, excluded_from_stats, classification_status) "
                    "VALUES ('t1', 'a1', '2026-07-26T00:00:00+00:00', -12, '测试消费', 'legacy_food_child', "
                    "'expense', 0, 'manual')"
                )
                conn.commit()
            finally:
                conn.close()

            first = upgrade_seed_taxonomy(db_path, apply=True)
            second = upgrade_seed_taxonomy(db_path, apply=True)
            self.assertEqual(first["updated"], 1)
            self.assertEqual(second["updated"], 0)
            conn = connect_database(db_path)
            try:
                self.assertEqual(conn.execute("SELECT category_id FROM transactions WHERE id = 't1'").fetchone()[0], "expense_dining_meal")
            finally:
                conn.close()


def _financial_snapshot(conn):
    return {
        "transactions": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        "amount": conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions").fetchone()[0],
        "balances": [tuple(row) for row in conn.execute("SELECT id, current_balance FROM accounts ORDER BY id")],
    }


def _snapshot_path(db_path):
    conn = connect_database(db_path)
    try:
        return _financial_snapshot(conn)
    finally:
        conn.close()
