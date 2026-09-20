import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from seed_catalog import load_category_color_seed, load_category_seed, load_tag_seed
from db import connect_database, initialize_database_path
from classifier import classify_transaction
from taxonomy import seed_taxonomy


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

    def test_existing_custom_person_name_does_not_block_taxonomy_seed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "wallet.db"
            initialize_database_path(db_path)
            conn = connect_database(db_path)
            try:
                conn.execute("UPDATE tags SET name = '示例成员A' WHERE id = 'family_member_a'")
                conn.execute("UPDATE tags SET name = '大宝' WHERE id = 'child'")
                conn.commit()

                seed_taxonomy(conn)
                conn.commit()

                self.assertEqual(conn.execute("SELECT name FROM tags WHERE id = 'child'").fetchone()["name"], "大宝")
                self.assertEqual(conn.execute("SELECT name FROM tags WHERE id = 'family_member_a'").fetchone()["name"], "示例成员A")
            finally:
                conn.close()

    def test_repeated_app_boot_preserves_existing_user_configuration(self):
        import tempfile
        from main import create_app

        for existing_release in (False, True):
            with self.subTest(existing_release=existing_release), tempfile.TemporaryDirectory() as directory:
                db_path = Path(directory) / "wallet.db"
                initialize_database_path(db_path)
                conn = connect_database(db_path)
                try:
                    # Include a historical name collision and inactive defaults.
                    conn.execute("UPDATE tags SET name='示例成员A', is_active=0 WHERE id='family_member_a'")
                    conn.execute("UPDATE tags SET name='大宝', group_name='自定义', keywords='保留', is_active=0 WHERE id='child'")
                    conn.execute("UPDATE categories SET name='我家的餐饮', sort_order=77, is_active=0 WHERE id='expense_dining'")
                    conn.execute("UPDATE categories SET parent_id='expense_daily_shopping', name='自定义餐费', sort_order=88 WHERE id='expense_dining_meal'")
                    conn.execute("UPDATE classification_rules SET pattern='用户规则', priority=7, is_active=0 WHERE id='tag-subscription'")
                    conn.execute("DELETE FROM classification_rules WHERE id='category-ai-subscription'")
                    conn.execute("DELETE FROM tags WHERE id='trip_example'")
                    conn.execute("INSERT INTO accounts (id, name, type, statement_day, due_day, due_month_offset, budget_period) VALUES ('card', '示例信用卡A', 'credit', NULL, NULL, 0, 'calendar_month')")
                    if existing_release:
                        conn.execute("DELETE FROM schema_meta WHERE key='taxonomy_initialized'")
                    conn.commit()
                    tables = ('categories', 'tags', 'classification_rules', 'accounts', 'transaction_tags')
                    before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in tables}
                finally:
                    conn.close()

                for _ in range(2):
                    app = create_app({'TESTING': True, 'DB_PATH': str(db_path), 'API_KEY': 'test-key'})
                    self.assertEqual(app.test_client().get('/health').status_code, 200)
                    conn = connect_database(db_path)
                    try:
                        after = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in tables}
                        self.assertEqual(after, before)
                        self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='taxonomy_initialized'").fetchone()[0], '1')
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
