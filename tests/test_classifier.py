import sqlite3
import sys
import tempfile
import unittest
import re
from contextlib import closing
from pathlib import Path

from flask import Flask


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from db import connect_database, init_database
from classifier import classify_transaction
from taxonomy import CARD_SETTINGS, seed_taxonomy
from seed_catalog import load_category_seed, load_tag_seed


class TaxonomySeedTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "wallet.db"
        self._create_legacy_database()
        self.app = Flask(__name__)
        self.app.config["DB_PATH"] = str(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_legacy_database(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT UNIQUE, type TEXT, "
                "initial_balance REAL DEFAULT 0, current_balance REAL DEFAULT 0, "
                "icon TEXT, notes TEXT, is_active INTEGER DEFAULT 1)"
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
            for index, account_name in enumerate(CARD_SETTINGS):
                conn.execute(
                    "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, 'credit', ?)",
                    ("legacy-card-%s" % index, account_name, 0 if account_name == "示例信用卡A" else 1),
                )
            conn.commit()
        finally:
            conn.close()

    def test_initialization_seeds_complete_taxonomy_tags_rules_and_card_cycles(self):
        init_database(self.app)

        with closing(connect_database(self.db_path)) as conn:
            categories = {
                row["id"]: row
                for row in conn.execute(
                    "SELECT id, name, type, parent_id FROM categories"
                )
            }
            seed = load_category_seed()
            for root in seed["roots"]:
                self.assertEqual(
                    (categories[root["id"]]["name"], categories[root["id"]]["type"],
                     categories[root["id"]]["parent_id"]),
                    (root["name"], root["type"], None),
                )
                for child in root["children"]:
                    self.assertEqual(
                        (categories[child["id"]]["name"], categories[child["id"]]["type"],
                         categories[child["id"]]["parent_id"]),
                        (child["name"], root["type"], root["id"]),
                    )
            self.assertEqual(categories["expense_digital_ai"]["name"], "AI工具")
            self.assertEqual(len(categories), sum(1 + len(root["children"]) for root in seed["roots"]))

            tags = [
                tuple(row)
                for row in conn.execute(
                    "SELECT id, name, group_name FROM tags ORDER BY rowid"
                )
            ]
            self.assertEqual(tags, [(tag["id"], tag["name"], tag["group"]) for tag in load_tag_seed()["tags"]])
            self.assertEqual(
                conn.execute("SELECT name FROM tags WHERE id = 'family_member_a'").fetchone()[0],
                "大宝",
            )

            active_rules = [
                tuple(row)
                for row in conn.execute(
                    "SELECT rule_type, pattern, category_id, tag_id, transaction_kind, "
                    "priority, is_active FROM classification_rules ORDER BY priority, id"
                )
            ]
            self.assertTrue(active_rules)
            self.assertTrue(all(rule[-1] == 1 for rule in active_rules))
            self.assertEqual([rule[-2] for rule in active_rules], sorted(rule[-2] for rule in active_rules))
            self.assertIn(("tag", "示例成员A|成员A", None, "family_member_a", None, 20, 1), active_rules)
            self.assertIn(("tag", "示例成员B", None, "family_member_b", None, 21, 1), active_rules)
            self.assertIn(
                ("category", "Gemini|GPT|OpenAI|Claude|硅基流动", "expense_digital_ai", None, None, 100, 1),
                active_rules,
            )
            self.assertIn(
                (
                    "category", "VPN|示例代理服务|[Pp]roxy|网络代理|代理服务器",
                    "expense_network_vpn", None, None, 101, 1,
                ),
                active_rules,
            )
            self.assertIn(
                ("category", "VPS|服务器", "expense_network_service", None, None, 102, 1), active_rules
            )
            self.assertIn(
                ("category", "域名|云服务", "expense_digital_cloud", None, None, 103, 1), active_rules
            )
            self.assertIn(("kind", "信用卡还款", None, None, "credit_repayment", 1, 1), active_rules)
            self.assertIn(("kind", "还款至", None, None, "credit_repayment", 2, 1), active_rules)
            self.assertIn(("kind", "平账调整", None, None, "balance_adjustment", 3, 1), active_rules)

            vpn_pattern = conn.execute(
                "SELECT pattern FROM classification_rules WHERE id = 'category-vpn-network'"
            ).fetchone()[0]
            for description in (
                "示例代理服务 Limited-VPN费用", "VPN 年费", "proxy service",
                "网络代理服务", "代理服务器费用",
            ):
                self.assertIsNotNone(re.search(vpn_pattern, description))
            for description in (
                "网络服务费", "家庭宽带", "中国电信宽带", "专利代理费", "商标代理服务",
            ):
                self.assertIsNone(re.search(vpn_pattern, description))

            for account_name, settings in CARD_SETTINGS.items():
                self.assertEqual(self._card_settings(conn, account_name), settings)
                self.assertEqual(
                    conn.execute(
                        "SELECT budget_period FROM accounts WHERE name = ?", (account_name,)
                    ).fetchone()[0],
                    "billing_cycle",
                )
            self.assertEqual(self._card_settings(conn, "示例信用卡E"), (25, 15, 0))
            self.assertEqual(
                conn.execute(
                    "SELECT is_active FROM accounts WHERE name = '示例信用卡A'"
                ).fetchone()[0],
                0,
            )

    def test_repeated_seeding_is_idempotent_and_preserves_user_card_values(self):
        init_database(self.app)
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "UPDATE accounts SET statement_day = 28, due_day = 8, "
                "due_month_offset = 1, budget_period = 'calendar_month' "
                "WHERE name = '示例信用卡B'"
            )
            conn.commit()
            first_snapshot = self._seed_snapshot(conn)

            seed_taxonomy(conn)
            conn.commit()
            second_snapshot = self._seed_snapshot(conn)

        init_database(self.app)
        with closing(connect_database(self.db_path)) as conn:
            self.assertEqual(first_snapshot, second_snapshot)
            self.assertEqual(second_snapshot, self._seed_snapshot(conn))
            self.assertEqual(self._card_settings(conn, "示例信用卡B"), (28, 8, 1))
            self.assertEqual(
                conn.execute(
                    "SELECT budget_period FROM accounts WHERE name = '示例信用卡B'"
                ).fetchone()[0],
                "calendar_month",
            )

    def test_reseeding_fills_only_missing_card_values_for_inactive_and_partial_accounts(self):
        init_database(self.app)
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "UPDATE accounts SET statement_day = 29, due_day = NULL, "
                "due_month_offset = 9, budget_period = 'calendar_month' "
                "WHERE name = '示例信用卡D'"
            )
            conn.execute(
                "UPDATE accounts SET statement_day = NULL, due_day = 28, "
                "due_month_offset = 7, budget_period = 'billing_cycle' "
                "WHERE name = '示例信用卡B'"
            )
            seed_taxonomy(conn)
            conn.commit()

            self.assertEqual(self._card_settings(conn, "示例信用卡A"), (5, 20, 1))
            self.assertEqual(self._card_settings(conn, "示例信用卡D"), (29, 10, 9))
            self.assertEqual(self._card_settings(conn, "示例信用卡B"), (10, 28, 7))
            self.assertEqual(
                conn.execute(
                    "SELECT budget_period FROM accounts WHERE name = '示例信用卡D'"
                ).fetchone()[0],
                "calendar_month",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT budget_period FROM accounts WHERE name = '示例信用卡B'"
                ).fetchone()[0],
                "billing_cycle",
            )

    def test_reseeding_preserves_edited_or_deactivated_taxonomy_rows_and_restores_missing_rows(self):
        init_database(self.app)
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "UPDATE categories SET name = '自定义正餐', is_active = 0 WHERE id = 'expense_dining_meal'"
            )
            conn.execute(
                "UPDATE tags SET name = '自定义订阅', group_name = 'custom', "
                "keywords = '手动维护', is_active = 0 WHERE id = 'subscription'"
            )
            conn.execute(
                "UPDATE classification_rules SET pattern = '手动规则', priority = 999, "
                "is_active = 0 WHERE id = 'tag-family_member_a'"
            )
            conn.execute("DELETE FROM categories WHERE id = 'expense_dining_delivery'")
            conn.execute("DELETE FROM tags WHERE id = 'temporary'")
            conn.execute("DELETE FROM classification_rules WHERE id = 'tag-family_member_b'")
            seed_taxonomy(conn)
            conn.commit()

            self.assertEqual(
                tuple(conn.execute(
                    "SELECT name, is_active FROM categories WHERE id = 'expense_dining_meal'"
                ).fetchone()),
                ("正餐", 1),
            )
            self.assertEqual(
                tuple(conn.execute(
                    "SELECT name, group_name, keywords, is_active FROM tags "
                    "WHERE id = 'subscription'"
                ).fetchone()),
                ("订阅", "既有标签", "手动维护", 1),
            )
            self.assertEqual(
                tuple(conn.execute(
                    "SELECT pattern, priority, is_active FROM classification_rules "
                    "WHERE id = 'tag-family_member_a'"
                ).fetchone()),
                ("手动规则", 999, 0),
            )
            self.assertEqual(
                conn.execute("SELECT name FROM categories WHERE id = 'expense_dining_delivery'").fetchone()[0],
                "外卖",
            )
            self.assertEqual(
                conn.execute("SELECT name FROM tags WHERE id = 'temporary'").fetchone()[0],
                "临时支出",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT pattern FROM classification_rules WHERE id = 'tag-family_member_b'"
                ).fetchone()[0],
                "示例成员B",
            )

    @staticmethod
    def _card_settings(conn, account_name):
        row = conn.execute(
            "SELECT statement_day, due_day, due_month_offset FROM accounts WHERE name = ?",
            (account_name,),
        ).fetchone()
        return tuple(row) if row else None

    @staticmethod
    def _seed_snapshot(conn):
        return {
            "categories": [tuple(row) for row in conn.execute(
                "SELECT id, name, type, parent_id FROM categories ORDER BY id"
            )],
            "tags": [tuple(row) for row in conn.execute(
                "SELECT id, name, group_name, keywords, is_active FROM tags ORDER BY id"
            )],
            "rules": [tuple(row) for row in conn.execute(
                "SELECT id, rule_type, pattern, category_id, tag_id, transaction_kind, "
                "priority, is_active FROM classification_rules ORDER BY id"
            )],
            "cards": [tuple(row) for row in conn.execute(
                "SELECT name, statement_day, due_day, due_month_offset, budget_period "
                "FROM accounts ORDER BY id"
            )],
        }


class TransactionClassifierTest(unittest.TestCase):
    CASES = (
        ("还款示例信用卡C", "其他支出", -22674.51, "credit_repayment", True, None, ()),
        ("日常储蓄账户还款", "其他", 172.41, "credit_repayment", True, None, ()),
        ("平账调整", "其他", -514.34, "balance_adjustment", True, None, ()),
        ("Gemini订阅", "其他", -200.00, "expense", False, "expense_digital_ai", ("subscription",)),
        ("示例成员A房租", "购物", -2322.57, "expense", False, "expense_home_rent", ("family_member_a",)),
        ("示例成员A示例交通卡", "交通", -44.09, "expense", False, "expense_transport_public", ("family_member_a",)),
        ("示例成员B交通卡", "交通", -99.47, "expense", False, "expense_transport_public", ("family_member_b",)),
        ("GPT订阅", "其他", -136.37, "expense", False, "expense_digital_ai", ("subscription",)),
        ("示例代理服务 Limited-VPN费用", "其他", -99.00, "expense", False, "expense_network_vpn", ()),
    )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "wallet.db"
        self.app = Flask(__name__)
        self.app.config["DB_PATH"] = str(self.db_path)
        init_database(self.app)
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, ?, 1)",
                ("own-salary-card", "日常储蓄账户", "debit"),
            )
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_classifies_supplied_ledger_cases_without_counting_exclusions(self):
        with closing(connect_database(self.db_path)) as conn:
            for (
                description, legacy_category, amount, expected_kind, expected_excluded,
                expected_category, expected_tags,
            ) in self.CASES:
                with self.subTest(description=description):
                    result = classify_transaction(conn, description, legacy_category, amount)
                    self.assertEqual(result.transaction_kind, expected_kind)
                    self.assertEqual(result.excluded_from_stats, expected_excluded)
                    self.assertEqual(result.category_id, expected_category)
                    self.assertEqual(result.tag_ids, expected_tags)
                    self.assertEqual(result.legacy_category, legacy_category)
                    self.assertEqual(result.classification_status, "classified")

    def test_keeps_ambiguous_blank_other_expense_for_review(self):
        with closing(connect_database(self.db_path)) as conn:
            result = classify_transaction(conn, "", "其他", -18.00)

        self.assertEqual(result.transaction_kind, "expense")
        self.assertFalse(result.excluded_from_stats)
        self.assertIsNone(result.category_id)
        self.assertEqual(result.legacy_category, "其他")
        self.assertEqual(result.classification_status, "needs_review")

    def test_does_not_treat_generic_person_transfer_as_internal_transfer(self):
        with closing(connect_database(self.db_path)) as conn:
            result = classify_transaction(
                conn, "转账-转给示例成员B", "其他", -88.00, source_account_name="日常储蓄账户"
            )

        self.assertEqual(result.transaction_kind, "expense")
        self.assertFalse(result.excluded_from_stats)
        self.assertEqual(result.tag_ids, ("family_member_b",))

    def test_recognizes_transfer_to_a_known_own_account(self):
        with closing(connect_database(self.db_path)) as conn:
            result = classify_transaction(
                conn, "转账到日常储蓄账户", "其他", -88.00, source_account_name="示例电子钱包"
            )

        self.assertEqual(result.transaction_kind, "transfer")
        self.assertTrue(result.excluded_from_stats)
        self.assertIsNone(result.category_id)

    def test_does_not_mistake_transit_card_recharge_for_wallet_topup(self):
        with closing(connect_database(self.db_path)) as conn:
            result = classify_transaction(conn, "示例交通卡交通卡充值", "交通出行", -50.00)

        self.assertEqual(result.transaction_kind, "expense")
        self.assertFalse(result.excluded_from_stats)
        self.assertEqual(result.category_id, "expense_transport_public")

    def test_uses_active_rules_case_insensitively_and_skips_invalid_regex(self):
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO classification_rules "
                "(id, rule_type, pattern, category_id, tag_id, transaction_kind, priority, is_active) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, 1)",
                ("invalid-regex", "category", "[", "expense_daily_shopping_general", 999),
            )
            conn.execute(
                "INSERT INTO classification_rules "
                "(id, rule_type, pattern, category_id, tag_id, transaction_kind, priority, is_active) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, 1)",
                ("unsafe-regex", "category", "(a+)+$", "expense_dining_meal", 998),
            )
            conn.execute(
                "INSERT INTO classification_rules "
                "(id, rule_type, pattern, category_id, tag_id, transaction_kind, priority, is_active) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, 1)",
                ("case-insensitive", "category", "cloudflare", "expense_digital_cloud", 500),
            )
            conn.commit()
            result = classify_transaction(conn, "CLOUDFLARE 月费", "其他", -30.00)
            unsafe = classify_transaction(conn, "a" * 200 + "!", "其他", -30.00)

        self.assertEqual(result.category_id, "expense_digital_cloud")
        self.assertNotEqual(unsafe.category_id, "expense_dining_meal")

    def test_uses_stable_rule_tie_breaking_and_legacy_aliases(self):
        with closing(connect_database(self.db_path)) as conn:
            conn.executemany(
                "INSERT INTO classification_rules "
                "(id, rule_type, pattern, category_id, tag_id, transaction_kind, priority, is_active) "
                "VALUES (?, 'category', '同优先级', ?, NULL, NULL, 800, 1)",
                (("tie-a", "expense_dining_meal"), ("tie-b", "expense_daily_shopping_general")),
            )
            conn.commit()
            tied = classify_transaction(conn, "同优先级", "其他", -10.00)
            food = classify_transaction(conn, "", "餐饮美食", -10.00)
            transport = classify_transaction(conn, "", "交通出行", -10.00)
            shopping = classify_transaction(conn, "", "购物百货", -10.00)
            refund = classify_transaction(conn, "退款", "退款", 10.00)

        self.assertEqual(tied.category_id, "expense_dining_meal")
        self.assertEqual(food.category_id, "expense_dining_meal")
        self.assertEqual(transport.category_id, "expense_transport_other")
        self.assertEqual(shopping.category_id, "expense_daily_shopping_general")
        self.assertEqual(refund.transaction_kind, "refund")
        self.assertIsNone(refund.category_id)
        self.assertNotEqual(refund.transaction_kind, "income")
        self.assertEqual(refund.classification_status, "needs_review")

    def test_does_not_classify_patent_agency_fee_as_vpn(self):
        with closing(connect_database(self.db_path)) as conn:
            result = classify_transaction(conn, "专利代理费", "其他", -100.00)

        self.assertNotEqual(result.category_id, "expense_network_vpn")

    def test_accepts_english_legacy_aliases_without_losing_refund_kind(self):
        with closing(connect_database(self.db_path)) as conn:
            food = classify_transaction(conn, "", "food", -20.00)
            transport = classify_transaction(conn, "", "transport", -20.00)
            shopping = classify_transaction(conn, "", "shopping", -20.00)
            refund = classify_transaction(conn, "refund", "refund", 20.00)

        self.assertEqual(food.category_id, "expense_dining_meal")
        self.assertEqual(transport.category_id, "expense_transport_other")
        self.assertEqual(shopping.category_id, "expense_daily_shopping_general")
        self.assertIsNone(refund.category_id)
        self.assertEqual(refund.transaction_kind, "refund")

    def test_keeps_credit_card_repayment_fee_as_an_expense(self):
        with closing(connect_database(self.db_path)) as conn:
            result = classify_transaction(conn, "信用卡还款手续费", "其他支出", -12.00)

        self.assertEqual(result.transaction_kind, "expense")
        self.assertFalse(result.excluded_from_stats)

    def test_recognizes_clear_balance_adjustment_contexts(self):
        with closing(connect_database(self.db_path)) as conn:
            balance_rows = (
                classify_transaction(conn, "余额调整(平账)", "其他", -1.00),
                classify_transaction(conn, "刷卡（平账）", "其他", -1.00),
            )

        for result in balance_rows:
            self.assertEqual(result.transaction_kind, "balance_adjustment")
            self.assertTrue(result.excluded_from_stats)

    def test_recognizes_clear_wallet_and_stored_value_topups_but_not_transit_cards(self):
        with closing(connect_database(self.db_path)) as conn:
            topups = (
                classify_transaction(conn, "示例电子钱包充值", "其他", -50.00),
                classify_transaction(conn, "电子钱包充值-来自日常储蓄账户", "其他", -50.00),
                classify_transaction(conn, "充值示例储值卡", "其他", -50.00),
            )
            transit = classify_transaction(conn, "示例交通卡/交通卡充值", "交通", -50.00)

        for result in topups:
            self.assertEqual(result.transaction_kind, "topup_withdrawal")
            self.assertTrue(result.excluded_from_stats)
        self.assertEqual(transit.transaction_kind, "expense")
        self.assertEqual(transit.category_id, "expense_transport_public")

    def test_excludes_only_directional_transfer_to_a_different_owned_account(self):
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, ?, 0)",
                ("inactive-own-card", "备用储蓄卡", "debit"),
            )
            conn.execute(
                "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, ?, 1)",
                ("generic-short-name", "甲乙", "debit"),
            )
            conn.commit()
            to_other_account = classify_transaction(
                conn, "转账到日常储蓄账户", "其他", -50.00, source_account_name="示例电子钱包"
            )
            same_source = classify_transaction(
                conn, "日常储蓄账户转账给甲乙", "其他", -50.00, source_account_name="日常储蓄账户"
            )
            inactive_destination = classify_transaction(
                conn, "转账到备用储蓄卡", "其他", -50.00, source_account_name="示例电子钱包"
            )
            short_name = classify_transaction(
                conn, "转账到甲乙", "其他", -50.00, source_account_name="示例电子钱包"
            )

        self.assertEqual(to_other_account.transaction_kind, "transfer")
        self.assertTrue(to_other_account.excluded_from_stats)
        self.assertEqual(same_source.transaction_kind, "expense")
        self.assertFalse(same_source.excluded_from_stats)
        self.assertEqual(inactive_destination.transaction_kind, "transfer")
        self.assertTrue(inactive_destination.excluded_from_stats)
        self.assertEqual(short_name.transaction_kind, "expense")
        self.assertFalse(short_name.excluded_from_stats)

    def test_recognizes_owned_account_prefix_before_inbound_transfer_direction(self):
        with closing(connect_database(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, ?, 1)",
                ("wallet", "示例电子钱包", "wallet"),
            )
            conn.commit()
            from_wallet = classify_transaction(
                conn, "示例电子钱包转入", "其他", 50.00, source_account_name="日常储蓄账户"
            )
            from_salary_card = classify_transaction(
                conn, "日常储蓄账户转入", "其他", 50.00, source_account_name="示例电子钱包"
            )

        for result in (from_wallet, from_salary_card):
            self.assertEqual(result.transaction_kind, "transfer")
            self.assertTrue(result.excluded_from_stats)

    def test_recognizes_both_sides_of_owned_stored_value_recharge(self):
        with closing(connect_database(self.db_path)) as conn:
            conn.executemany(
                "INSERT INTO accounts (id, name, type, is_active) VALUES (?, ?, ?, 1)",
                (
                    ("zheshang-card", "示例信用卡C", "credit"),
                    ("moda-card", "示例储值卡", "stored_value"),
                    ("stored-value-backup", "备用储值卡", "debit"),
                ),
            )
            conn.commit()
            outgoing = classify_transaction(
                conn, "充值示例储值卡", "其他", -100.00, source_account_name="示例信用卡C"
            )
            inbound_moda = classify_transaction(
                conn, "示例信用卡C充值", "其他", 100.00, source_account_name="示例储值卡"
            )
            inbound_hair = classify_transaction(
                conn, "示例信用卡C充值", "其他", 100.00, source_account_name="备用储值卡"
            )
            merchant_income = classify_transaction(
                conn, "顾客充值", "其他", 100.00, source_account_name="示例储值卡"
            )

        for result in (outgoing, inbound_moda, inbound_hair):
            self.assertEqual(result.transaction_kind, "topup_withdrawal")
            self.assertTrue(result.excluded_from_stats)
        self.assertEqual(merchant_income.transaction_kind, "income")
        self.assertFalse(merchant_income.excluded_from_stats)


if __name__ == "__main__":
    unittest.main()
