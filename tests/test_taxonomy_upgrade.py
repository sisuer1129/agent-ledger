import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from db import connect_database, initialize_database_path
from taxonomy_upgrade import upgrade_taxonomy


class TaxonomyUpgradeTest(unittest.TestCase):
    def test_upgrade_activates_confirmed_categories_tags_and_deactivates_legacy_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.db"
            initialize_database_path(db_path)

            preview = upgrade_taxonomy(db_path)
            self.assertFalse(preview["applied"])

            result = upgrade_taxonomy(db_path, apply=True)
            self.assertTrue(result["applied"])
            conn = connect_database(db_path)
            try:
                category = conn.execute(
                    "SELECT id, parent_id, is_active FROM categories WHERE name = '食品杂货'"
                ).fetchone()
                self.assertEqual(tuple(category), ("expense_daily_shopping_groceries", "expense_daily_shopping", 1))
                self.assertEqual(
                    conn.execute("SELECT is_active FROM tags WHERE id = 'family_internal'").fetchone()[0], 1
                )
                flow = conn.execute(
                    "SELECT name, type, parent_id, is_active FROM categories WHERE id = 'nonexpense_flow_credit_repayment'"
                ).fetchone()
                self.assertEqual(tuple(flow), ("信用卡还款", "transaction", "nonexpense_flow", 1))
                parking_rows = [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT c.name, p.name FROM categories c "
                        "JOIN categories p ON p.id = c.parent_id "
                        "WHERE c.name IN ('临时停车', '固定停车') AND c.is_active = 1 ORDER BY p.name"
                    )
                ]
                self.assertEqual(parking_rows, [("临时停车", "交通出行"), ("固定停车", "车辆")])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
