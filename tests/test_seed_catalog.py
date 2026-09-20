import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from seed_catalog import load_category_color_seed, load_category_seed, load_tag_seed
from db import connect_database, initialize_database_path
from classifier import classify_transaction


class SeedCatalogTest(unittest.TestCase):
    def test_category_color_seed_resolves_configured_root_and_child_colours(self):
        colors = load_category_color_seed()
        self.assertEqual(colors["expense_dining"]["color"], "#F28E6B")
        self.assertEqual(colors["expense_dining_meal"]["color"], "#F28E6B")
        self.assertEqual(colors["expense_leisure_fitness"]["color"], "#ED9FC0")
        self.assertEqual(colors["income_salary_salary"]["color"], "#34A853")
        self.assertEqual(colors["nonexpense_flow_credit_repayment"]["color"], "#B0BEC5")

    def test_category_seed_has_unique_stable_leaf_ids_and_all_confirmed_roots(self):
        seed = load_category_seed()
        self.assertEqual(seed["version"], "1.0")
        self.assertTrue({"餐饮", "工资薪酬", "资金往来"} <= {root["name"] for root in seed["roots"]})
        leaf_ids = [child["id"] for root in seed["roots"] for child in root["children"]]
        self.assertEqual(len(leaf_ids), len(set(leaf_ids)))
        self.assertTrue(all(child_id.startswith(("expense_", "income_", "nonexpense_")) for child_id in leaf_ids))

    def test_tag_seed_preserves_personal_tags_and_adds_confirmed_groups(self):
        seed = load_tag_seed()
        names = {tag["name"] for tag in seed["tags"]}
        self.assertTrue({"大宝", "二宝", "本人", "配偶", "家庭公共", "示例旅行项目", "必要消费", "车辆"} <= names)

    def test_new_database_initializes_categories_and_tags_from_seed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.db"
            initialize_database_path(db_path)
            conn = connect_database(db_path)
            try:
                category = conn.execute(
                    "SELECT child.id, parent.name FROM categories child "
                    "JOIN categories parent ON parent.id = child.parent_id "
                    "WHERE child.id = 'income_salary_bonus'"
                ).fetchone()
                self.assertEqual(tuple(category), ("income_salary_bonus", "工资薪酬"))
                tag = conn.execute("SELECT name FROM tags WHERE id = 'trip_example'").fetchone()
                self.assertEqual(tag["name"], "示例旅行项目")
            finally:
                conn.close()

    def test_automatic_classification_returns_seed_category_ids(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.db"
            initialize_database_path(db_path)
            conn = connect_database(db_path)
            try:
                dining = classify_transaction(conn, legacy_category="餐饮", amount=-32)
                self.assertEqual(dining.category_id, "expense_dining_meal")
                ai_tool = classify_transaction(conn, description="OpenAI API", amount=-20)
                self.assertEqual(ai_tool.category_id, "expense_digital_ai")
            finally:
                conn.close()
