import csv
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from flask import Flask


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from db import connect_database, init_database
from repositories import (
    ConflictError,
    NotFoundError,
    ValidationError,
    _atomic,
    create_credit_card_repayment,
    delete_credit_card_repayment,
    create_transaction,
    deactivate_account,
    delete_transaction,
    list_transactions,
    replace_transaction_tags,
    update_account,
    update_transaction,
)
from tests.helpers import auth_headers, test_config
from main import create_app


class TransactionRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "wallet.db"
        app = Flask(__name__)
        app.config["DB_PATH"] = str(self.db_path)
        init_database(app)
        self.conn = connect_database(self.db_path)
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.conn.executemany(
            "INSERT INTO accounts (id, name, type, initial_balance, current_balance, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                ("cash", "现金账户", "debit", 1000, 1000, 1),
                ("card", "信用卡", "credit", -200, -200, 1),
                ("inactive", "已停用", "debit", 30, 30, 0),
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def payload(self, **overrides):
        data = {
            "id": "tx-new",
            "account_id": "cash",
            "timestamp": "2026-07-22T10:30:00+08:00",
            "amount": -120.5,
            "description": "午饭",
            "category": "餐饮美食",
            "category_id": "expense_dining_meal",
            "transaction_kind": "expense",
            "excluded_from_stats": False,
            "classification_status": "classified",
            "tag_ids": ["family_member_a", "travel"],
        }
        data.update(overrides)
        return data

    def balance(self, account_id):
        return self.conn.execute(
            "SELECT current_balance FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()[0]

    def transaction(self, transaction_id):
        return self.conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()

    def tags(self, transaction_id):
        return {
            row[0]
            for row in self.conn.execute(
                "SELECT tag_id FROM transaction_tags WHERE transaction_id = ?", (transaction_id,)
            )
        }

    def test_create_updates_active_account_and_preserves_legacy_category(self):
        created = create_transaction(self.conn, self.payload())

        self.assertEqual(created["id"], "tx-new")
        self.assertEqual(self.balance("cash"), 879.5)
        saved = self.transaction("tx-new")
        self.assertEqual(saved["category"], "餐饮美食")
        self.assertEqual(saved["category_id"], "expense_dining_meal")
        self.assertEqual(saved["balance_after"], 879.5)
        self.assertEqual(self.tags("tx-new"), {"family_member_a", "travel"})

    def test_update_moves_transaction_atomically_between_accounts_and_replaces_tags(self):
        create_transaction(self.conn, self.payload(id="tx-edit", amount=-120.5))
        old_balance = self.balance("cash")
        new_balance = self.balance("card")

        updated = update_transaction(
            self.conn,
            "tx-edit",
            {
                "account_id": "card",
                "amount": -40,
                "description": "改为信用卡支付",
                "tag_ids": ["travel"],
            },
        )

        self.assertEqual(self.balance("cash"), old_balance - (-120.5))
        self.assertEqual(self.balance("card"), new_balance + (-40))
        self.assertEqual(updated["account_id"], "card")
        self.assertEqual(updated["balance_after"], -240)
        self.assertEqual(self.tags("tx-edit"), {"travel"})

    def test_update_same_account_recalculates_balance_and_preserves_unspecified_fields(self):
        create_transaction(self.conn, self.payload(id="tx-same", amount=-120.5))
        old_balance = self.balance("cash")

        updated = update_transaction(self.conn, "tx-same", {"amount": -20})

        self.assertEqual(self.balance("cash"), old_balance - (-120.5) + (-20))
        self.assertEqual(updated["description"], "午饭")
        self.assertEqual(updated["category"], "餐饮美食")
        self.assertEqual(self.tags("tx-same"), {"family_member_a", "travel"})

    def test_update_normalizes_legacy_auto_classified_status(self):
        create_transaction(self.conn, self.payload(id="tx-legacy-status"))
        self.conn.execute(
            "UPDATE transactions SET classification_status = 'auto_classified' WHERE id = 'tx-legacy-status'"
        )
        self.conn.commit()

        updated = update_transaction(self.conn, "tx-legacy-status", {"description": "已编辑的历史交易"})

        self.assertEqual(updated["description"], "已编辑的历史交易")
        self.assertEqual(updated["classification_status"], "classified")

    def test_invalid_category_tag_or_destination_leaves_everything_unchanged(self):
        create_transaction(self.conn, self.payload(id="tx-safe"))
        original_balances = (self.balance("cash"), self.balance("card"))
        original = dict(self.transaction("tx-safe"))
        original_tags = self.tags("tx-safe")

        for invalid in (
            {"category_id": "missing-category"},
            {"tag_ids": ["missing-tag"]},
            {"account_id": "inactive"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    update_transaction(self.conn, "tx-safe", invalid)
                self.assertEqual((self.balance("cash"), self.balance("card")), original_balances)
                self.assertEqual(dict(self.transaction("tx-safe")), original)
                self.assertEqual(self.tags("tx-safe"), original_tags)

    def test_sql_failure_rolls_back_new_balance_transaction_and_tags(self):
        self.conn.execute(
            "CREATE TRIGGER fail_transaction_insert BEFORE INSERT ON transactions "
            "BEGIN SELECT RAISE(ABORT, 'injected SQL failure'); END"
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.DatabaseError):
            create_transaction(self.conn, self.payload(id="tx-failure"))

        self.assertEqual(self.balance("cash"), 1000)
        self.assertIsNone(self.transaction("tx-failure"))
        self.assertEqual(self.tags("tx-failure"), set())

    def test_sql_failure_during_update_restores_both_balances_transaction_and_tags(self):
        create_transaction(self.conn, self.payload(id="tx-update-failure"))
        original_balances = (self.balance("cash"), self.balance("card"))
        original_transaction = dict(self.transaction("tx-update-failure"))
        original_tags = self.tags("tx-update-failure")
        self.conn.execute(
            "CREATE TRIGGER fail_transaction_update BEFORE UPDATE ON transactions "
            "BEGIN SELECT RAISE(ABORT, 'injected SQL failure'); END"
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.DatabaseError):
            update_transaction(
                self.conn,
                "tx-update-failure",
                {"account_id": "card", "amount": -40, "tag_ids": ["travel"]},
            )

        self.assertEqual((self.balance("cash"), self.balance("card")), original_balances)
        self.assertEqual(dict(self.transaction("tx-update-failure")), original_transaction)
        self.assertEqual(self.tags("tx-update-failure"), original_tags)

    def test_delete_reverses_amount_and_removes_tags_in_one_operation(self):
        create_transaction(self.conn, self.payload(id="tx-delete", amount=-40))
        balance_after_create = self.balance("cash")

        delete_transaction(self.conn, "tx-delete")

        self.assertEqual(self.balance("cash"), balance_after_create - (-40))
        self.assertIsNone(self.transaction("tx-delete"))
        self.assertEqual(self.tags("tx-delete"), set())
        with self.assertRaises(NotFoundError):
            delete_transaction(self.conn, "missing")

    def test_credit_card_repayment_can_only_be_reversed_as_a_pair(self):
        repayment = create_credit_card_repayment(
            self.conn,
            {
                "source_account_id": "cash", "credit_account_id": "card", "amount": 50,
                "timestamp": "2026-07-25T12:00:00+00:00",
            },
        )
        group_id = repayment["source_transaction"]["repayment_group_id"]
        self.assertEqual(repayment["source_transaction"]["repayment_group_id"], group_id)
        self.assertEqual(repayment["credit_transaction"]["repayment_group_id"], group_id)
        with self.assertRaisesRegex(ValidationError, "reversed as a group"):
            delete_transaction(self.conn, repayment["source_transaction"]["id"])
        with self.assertRaisesRegex(ValidationError, "reversed as a group"):
            update_transaction(self.conn, repayment["credit_transaction"]["id"], {"description": "错误修改"})
        self.assertEqual((self.balance("cash"), self.balance("card")), (950.0, -150.0))

        delete_credit_card_repayment(self.conn, group_id)

        self.assertEqual((self.balance("cash"), self.balance("card")), (1000.0, -200.0))
        self.assertIsNone(self.transaction(repayment["source_transaction"]["id"]))
        self.assertIsNone(self.transaction(repayment["credit_transaction"]["id"]))

    def test_sql_failure_during_delete_restores_balance_transaction_and_tags(self):
        create_transaction(self.conn, self.payload(id="tx-delete-failure"))
        original_balance = self.balance("cash")
        original_transaction = dict(self.transaction("tx-delete-failure"))
        original_tags = self.tags("tx-delete-failure")
        self.conn.execute(
            "CREATE TRIGGER fail_transaction_delete BEFORE DELETE ON transactions "
            "BEGIN SELECT RAISE(ABORT, 'injected SQL failure'); END"
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.DatabaseError):
            delete_transaction(self.conn, "tx-delete-failure")

        self.assertEqual(self.balance("cash"), original_balance)
        self.assertEqual(dict(self.transaction("tx-delete-failure")), original_transaction)
        self.assertEqual(self.tags("tx-delete-failure"), original_tags)

    def test_replace_transaction_tags_validates_all_tags_before_replacing(self):
        create_transaction(self.conn, self.payload(id="tx-tags"))

        with self.assertRaises(ValidationError):
            replace_transaction_tags(self.conn, "tx-tags", ["travel", "missing-tag"])

        self.assertEqual(self.tags("tx-tags"), {"family_member_a", "travel"})
        replace_transaction_tags(self.conn, "tx-tags", ["travel", "subscription"])
        self.assertEqual(self.tags("tx-tags"), {"travel", "subscription"})

    def test_create_rejects_invalid_amount_timestamp_description_and_inactive_account(self):
        invalid_payloads = (
            self.payload(id="zero", amount=0),
            self.payload(id="nan", amount=float("nan")),
            self.payload(id="bad-time", timestamp="not-an-iso-time"),
            self.payload(id="blank", description="  "),
            self.payload(id="inactive", account_id="inactive"),
        )

        for payload in invalid_payloads:
            with self.subTest(payload_id=payload["id"]):
                with self.assertRaises(ValidationError):
                    create_transaction(self.conn, payload)
                self.assertEqual(self.balance("cash"), 1000)
                self.assertIsNone(self.transaction(payload["id"]))

    def test_create_rejects_overflowed_balance_without_mutating_account_or_transaction(self):
        self.conn.execute("UPDATE accounts SET current_balance = ? WHERE id = 'cash'", (1e308,))
        self.conn.commit()

        with self.assertRaises(ValidationError):
            create_transaction(self.conn, self.payload(id="tx-create-overflow", amount=1e308))
        with self.assertRaises(ValidationError):
            create_transaction(self.conn, self.payload(id="tx-huge-amount", amount=10 ** 1000))

        self.assertEqual(self.balance("cash"), 1e308)
        self.assertIsNone(self.transaction("tx-create-overflow"))
        self.assertIsNone(self.transaction("tx-huge-amount"))

    def test_update_rejects_overflowed_balance_without_mutating_transaction_or_account(self):
        self.conn.execute("UPDATE accounts SET current_balance = ? WHERE id = 'cash'", (1e308,))
        self.conn.execute(
            "INSERT INTO transactions "
            "(id, account_id, timestamp, amount, description, category, balance_after, "
            "category_id, transaction_kind, excluded_from_stats, classification_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("tx-update-overflow", "cash", "2026-07-22T00:00:00+00:00", -1, "旧记录", "其他", 1e308,
             "expense_other_pending", "expense", 0, "classified"),
        )
        self.conn.commit()
        original = dict(self.transaction("tx-update-overflow"))

        with self.assertRaises(ValidationError):
            update_transaction(self.conn, "tx-update-overflow", {"amount": 1e308})

        self.assertEqual(self.balance("cash"), 1e308)
        self.assertEqual(dict(self.transaction("tx-update-overflow")), original)

    def test_delete_rejects_overflowed_restored_balance_without_deleting_history(self):
        self.conn.execute("UPDATE accounts SET current_balance = ? WHERE id = 'cash'", (1e308,))
        self.conn.execute(
            "INSERT INTO transactions "
            "(id, account_id, timestamp, amount, description, category, balance_after, "
            "category_id, transaction_kind, excluded_from_stats, classification_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("tx-delete-overflow", "cash", "2026-07-22T00:00:00+00:00", -1e308, "旧记录", "其他", 1e308,
             "expense_other_pending", "expense", 0, "classified"),
        )
        self.conn.execute(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            ("tx-delete-overflow", "travel"),
        )
        self.conn.commit()
        original = dict(self.transaction("tx-delete-overflow"))

        with self.assertRaises(ValidationError):
            delete_transaction(self.conn, "tx-delete-overflow")

        self.assertEqual(self.balance("cash"), 1e308)
        self.assertEqual(dict(self.transaction("tx-delete-overflow")), original)
        self.assertEqual(self.tags("tx-delete-overflow"), {"travel"})

    def test_atomic_rolls_back_and_releases_for_base_exception_at_top_level_and_savepoint(self):
        class InjectedInterrupt(KeyboardInterrupt):
            pass

        with self.assertRaises(InjectedInterrupt):
            with _atomic(self.conn):
                self.conn.execute("UPDATE accounts SET current_balance = 1 WHERE id = 'cash'")
                raise InjectedInterrupt("stop")
        self.assertEqual(self.balance("cash"), 1000)
        self.assertFalse(self.conn.in_transaction)

        self.conn.execute("BEGIN")
        try:
            with self.assertRaises(InjectedInterrupt):
                with _atomic(self.conn):
                    self.conn.execute("UPDATE accounts SET current_balance = 2 WHERE id = 'cash'")
                    raise InjectedInterrupt("stop")
            self.assertEqual(self.balance("cash"), 1000)
            self.assertTrue(self.conn.in_transaction)
        finally:
            self.conn.rollback()

    def test_account_updates_are_whitelisted_and_validated_and_deactivation_is_soft(self):
        updated = update_account(
            self.conn,
            "cash",
            {"name": "现金新名称", "monthly_budget": 500, "notes": "备用"},
        )
        self.assertEqual(updated["name"], "现金新名称")
        self.assertEqual(updated["current_balance"], 1000)
        with self.assertRaises(ValidationError):
            update_account(self.conn, "cash", {"current_balance": 1})
        with self.assertRaises(ValidationError):
            update_account(self.conn, "cash", {"credit_limit": -1})
        with self.assertRaises(ConflictError):
            update_account(self.conn, "cash", {"name": "信用卡"})

        create_transaction(self.conn, self.payload(id="tx-keep"))
        deactivated = deactivate_account(self.conn, "cash")
        self.assertEqual(deactivated["is_active"], 0)
        self.assertIsNotNone(self.transaction("tx-keep"))
        with self.assertRaises(ValidationError):
            create_transaction(self.conn, self.payload(id="tx-after-deactivate"))

    def test_missing_accounts_raise_not_found_but_transaction_account_validation_is_explicit(self):
        with self.assertRaises(NotFoundError):
            update_account(self.conn, "missing-account", {"notes": "x"})
        with self.assertRaises(NotFoundError):
            deactivate_account(self.conn, "missing-account")
        with self.assertRaises(ValidationError):
            create_transaction(self.conn, self.payload(id="missing-source", account_id="missing-account"))
        with self.assertRaises(NotFoundError):
            update_transaction(self.conn, "missing-transaction", {"amount": -1})

    def test_credit_card_repayment_creates_balanced_excluded_pair(self):
        result = create_credit_card_repayment(
            self.conn,
            {
                "source_account_id": "cash",
                "credit_account_id": "card",
                "amount": 200,
                "timestamp": "2026-07-25T09:00:00+08:00",
            },
        )

        self.assertEqual(self.balance("cash"), 800)
        self.assertEqual(self.balance("card"), 0)
        self.assertEqual(result["source_transaction"]["amount"], -200)
        self.assertEqual(result["credit_transaction"]["amount"], 200)
        for transaction in result.values():
            self.assertEqual(transaction["transaction_kind"], "credit_repayment")
            self.assertEqual(transaction["excluded_from_stats"], 1)

    def test_credit_card_repayment_rejects_invalid_target_without_balance_changes(self):
        original_balances = (self.balance("cash"), self.balance("card"))
        with self.assertRaises(ValidationError):
            create_credit_card_repayment(
                self.conn,
                {
                    "source_account_id": "cash",
                    "credit_account_id": "cash",
                    "amount": 200,
                    "timestamp": "2026-07-25T09:00:00+08:00",
                },
            )
        self.assertEqual((self.balance("cash"), self.balance("card")), original_balances)

    def test_list_transactions_filters_with_bound_values_and_deterministic_tag_data(self):
        create_transaction(self.conn, self.payload(id="tx-july", description="示例交通卡", amount=-30))
        create_transaction(
            self.conn,
            self.payload(
                id="tx-income",
                timestamp="2026-08-01T10:30:00+08:00",
                amount=200,
                description="工资",
                category_id="income_salary_salary",
                transaction_kind="income",
                tag_ids=["subscription"],
            ),
        )

        rows = list_transactions(
            self.conn,
            {
                "start": "2026-07-01",
                "end": "2026-07-31T23:59:59+08:00",
                "account_id": "cash",
                "category_id": "expense_dining_meal",
                "tag_id": "travel",
                "transaction_kind": "expense",
                "min_amount": -40,
                "max_amount": -20,
                "query": "示例交通",
                "limit": 5000,
                "offset": 0,
            },
        )
        self.assertEqual([row["id"] for row in rows], ["tx-july"])
        self.assertEqual(rows[0]["account_name"], "现金账户")
        self.assertEqual([tag["id"] for tag in rows[0]["tags"]], ["family_member_a", "travel"])
        self.assertEqual(list_transactions(self.conn, {"account_id": "cash' OR 1=1 --"}), [])
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"limit": "not-a-number"})
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"min_amount": "not-a-number"})

    def test_list_transactions_includes_parent_and_child_category_names(self):
        create_transaction(self.conn, self.payload(id="tx-category-display"))

        row = list_transactions(self.conn, {"account_id": "cash"})[0]

        self.assertEqual(row["category_primary_name"], "餐饮")
        self.assertEqual(row["category_secondary_name"], "正餐")

    def test_list_transactions_defaults_to_50_clamps_to_1000_and_applies_offset(self):
        rows = [
            (
                "page-%04d" % index,
                "cash",
                "2026-07-01T00:00:%02d" % (index % 60),
                -1,
                "分页记录",
                "其他",
                999,
                "expense_other_pending",
                "expense",
                0,
                "classified",
            )
            for index in range(1005)
        ]
        self.conn.executemany(
            "INSERT INTO transactions "
            "(id, account_id, timestamp, amount, description, category, balance_after, "
            "category_id, transaction_kind, excluded_from_stats, classification_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

        self.assertEqual(len(list_transactions(self.conn, {})), 50)
        self.assertEqual(len(list_transactions(self.conn, {"limit": 5000})), 1000)
        self.assertEqual(len(list_transactions(self.conn, {"limit": 5000, "offset": 1000})), 5)
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"limit": float("inf")})
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"offset": float("inf")})
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"offset": str(2**63)})

    def test_timestamps_are_stored_and_filtered_as_utc_with_date_boundaries(self):
        earlier = create_transaction(
            self.conn,
            self.payload(
                id="tx-offset-earlier",
                timestamp="2026-07-01T08:00:00+08:00",
                amount=-1,
            ),
        )
        later = create_transaction(
            self.conn,
            self.payload(
                id="tx-offset-later",
                timestamp="2026-07-01T01:00:00+00:00",
                amount=-1,
            ),
        )

        self.assertEqual(earlier["timestamp"], "2026-07-01T00:00:00+00:00")
        self.assertEqual(later["timestamp"], "2026-07-01T01:00:00+00:00")
        rows = list_transactions(self.conn, {"start": "2026-07-01", "end": "2026-07-01"})
        self.assertEqual([row["id"] for row in rows], ["tx-offset-later", "tx-offset-earlier"])
        self.assertEqual(
            [row["id"] for row in list_transactions(self.conn, {"start": "2026-07-01T00:30:00+00:00"})],
            ["tx-offset-later"],
        )
        with self.assertRaises(ValidationError):
            create_transaction(self.conn, self.payload(id="tx-naive", timestamp="2026-07-01T08:00:00"))
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"start": "2026-07-01T08:00:00"})

    def test_unrepresentable_utc_timestamp_and_date_end_boundaries_raise_validation_error(self):
        for timestamp in (
            "9999-12-31T23:59:59-01:00",
            "0001-01-01T00:00:00+01:00",
        ):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValidationError):
                    create_transaction(
                        self.conn,
                        self.payload(id="tx-edge-" + timestamp[:4], timestamp=timestamp),
                    )
        with self.assertRaises(ValidationError):
            list_transactions(self.conn, {"end": "9999-12-31"})


class WalletAnalyticsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir, config = test_config()
        self.app = create_app(config)
        self.client = self.app.test_client()
        self.headers = auth_headers()
        with self.app.app_context():
            conn = connect_database(config["DB_PATH"])
            conn.execute(
                "INSERT INTO accounts (id, name, type, initial_balance, current_balance, credit_limit, "
                "budget_period, statement_day, due_day, due_month_offset, is_active) VALUES "
                "('card', '示例信用卡B', 'credit', 0, 0, 10000, 'billing_cycle', 12, 1, 1, 1),"
                "('cash', '现金', 'debit', 0, 0, NULL, 'calendar_month', NULL, NULL, 1, 1)"
            )
            conn.commit()
            conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        return self.client.open(path, method=method, json=payload, headers=self.headers)

    def add_transaction(self, **overrides):
        payload = {
            "account_id": "card", "timestamp": "2026-07-12T12:00:00+08:00", "amount": -120,
            "description": "示例交通卡午饭", "category": "餐饮", "category_id": "expense_dining_meal",
            "transaction_kind": "expense", "tag_ids": ["family_member_a"],
        }
        payload.update(overrides)
        response = self.request("POST", "/transactions", payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_taxonomy_returns_configured_category_presentation(self):
        response = self.request("GET", "/taxonomy")
        self.assertEqual(response.status_code, 200)
        dining = next(item for item in response.get_json()["categories"] if item["id"] == "expense_dining")
        meal = next(item for item in dining["children"] if item["id"] == "expense_dining_meal")
        fitness = next(
            item for item in response.get_json()["categories"]
            if item["id"] == "expense_leisure"
        )["children"][1]
        self.assertEqual((dining["color"], dining["icon"]), ("#F28E6B", "🍚"))
        self.assertEqual(meal["color"], "#F28E6B")
        self.assertEqual(fitness["color"], "#ED9FC0")

    def test_filters_overview_insights_stats_and_csv_exclude_repayment(self):
        purchase = self.add_transaction()
        repayment = self.request("POST", "/credit-card-repayments", {
            "source_account_id": "cash", "credit_account_id": "card", "amount": 120,
            "timestamp": "2026-07-25",
        })
        self.assertEqual(repayment.status_code, 201, repayment.get_json())
        transactions = self.request("GET", "/transactions?account_id=card&tag_id=family_member_a&query=%E7%A4%BA%E4%BE%8B%E4%BA%A4%E9%80%9A")
        self.assertEqual(transactions.status_code, 200)
        self.assertEqual([row["id"] for row in transactions.get_json()], [purchase["id"]])
        overview = self.request("GET", "/overview?year=2026&month=7")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.get_json()["expense"], 120.0)
        self.assertEqual(overview.get_json()["income"], 0.0)
        insights = self.request("GET", "/accounts/card/insights?mode=billing_cycle&anchor=2026-07-12")
        self.assertEqual(insights.status_code, 200)
        self.assertEqual(insights.get_json()["period_start"], "2026-06-13")
        self.assertEqual(insights.get_json()["expense"], 120.0)
        self.assertEqual(insights.get_json()["available_credit"], 10000.0)
        categories = self.request("GET", "/stats/categories?year=2026&month=7")
        self.assertEqual(categories.status_code, 200)
        self.assertEqual(categories.get_json()[0]["id"], "expense_dining_meal")
        people = self.request("GET", "/stats/people?year=2026&month=7")
        self.assertEqual(people.status_code, 200)
        self.assertEqual(people.get_json()[0]["id"], "family_member_a")
        exported = self.request("GET", "/export.csv?start=2026-07-01&end=2026-07-31")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.headers["Cache-Control"], "no-store, max-age=0")
        self.assertTrue(exported.data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("一级分类".encode(), exported.data)
        exported_rows = list(csv.reader(io.StringIO(exported.data.decode("utf-8-sig"))))
        self.assertEqual(exported_rows[0][0], "交易 ID")
        self.assertIn(purchase["id"], [row[0] for row in exported_rows[1:]])

    def test_tag_stats_include_all_tags_and_count_each_multi_tagged_expense(self):
        self.add_transaction(
            account_id="cash", amount=-120, description="午饭", tag_ids=["family_member_a", "travel"],
        )
        self.add_transaction(
            account_id="cash", amount=-30, description="咖啡", tag_ids=["travel"],
        )
        self.add_transaction(
            account_id="cash", amount=50, description="退款", transaction_kind="refund", tag_ids=["travel"],
        )
        self.add_transaction(
            account_id="cash", amount=-20, description="未标记", tag_ids=[],
        )

        response = self.request("GET", "/stats/tags?year=2026&month=7")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [
            {"id": "travel", "label": "旅行", "group_name": "旅行项目", "value": 150.0, "count": 2, "percentage": 55.55555555555556},
            {"id": "family_member_a", "label": "示例成员A", "group_name": "家庭成员", "value": 120.0, "count": 1, "percentage": 44.44444444444444},
        ])

    def test_overview_treats_credit_card_overpayment_as_an_asset(self):
        response = self.request("POST", "/accounts", {
            "name": "溢缴信用卡", "type": "credit", "initial_balance": 100,
        })
        self.assertEqual(response.status_code, 201, response.get_json())

        overview = self.request("GET", "/overview?year=2026&month=7")

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.get_json()["assets"], 100.0)
        self.assertEqual(overview.get_json()["liabilities"], 0.0)
        self.assertEqual(overview.get_json()["net_assets"], 100.0)

    def test_export_includes_all_matching_rows_beyond_a_page(self):
        for index in range(1002):
            created = self.add_transaction(
                account_id="cash", amount=-index - 1, description=f"批量导出 {index}",
                category_id="expense_dining_meal", tag_ids=[],
            )
            self.assertTrue(created["id"])

        self.add_transaction(account_id="cash", amount=-2000, description="批量导出 不匹配", tag_ids=[])
        query = "query=%E6%89%B9%E9%87%8F%E5%AF%BC%E5%87%BA&min_amount=-1002&max_amount=-1"
        exported = self.request("GET", "/export.csv?" + query)
        self.assertEqual(exported.status_code, 200)
        exported_rows = list(csv.reader(io.StringIO(exported.data.decode("utf-8-sig"))))
        self.assertEqual(len(exported_rows) - 1, 1002)
        paginated = self.request("GET", "/transactions?paginated=1&limit=5000&" + query).json
        self.assertEqual(len(paginated["transactions"]), 1000)
        self.assertEqual(paginated["pagination"], {"total": 1002, "limit": 1000, "offset": 0,
                                                  "has_more": True, "next_offset": 1000})
        last = self.request("GET", "/transactions?paginated=1&offset=1000&" + query).json
        self.assertEqual(len(last["transactions"]), 2)
        self.assertFalse(last["pagination"]["has_more"])
        self.assertCountEqual([row[0] for row in exported_rows[1:]],
                              [row["id"] for row in paginated["transactions"] + last["transactions"]])

    def test_credit_card_repayment_api_creates_two_entries_and_excludes_them_from_overview(self):
        rejected_single_entry = self.request(
            "POST",
            "/transactions",
            {
                "account_id": "cash", "timestamp": "2026-07-25", "amount": -120,
                "description": "还款至示例信用卡B", "transaction_kind": "credit_repayment",
                "excluded_from_stats": True, "tag_ids": [],
            },
        )
        self.assertEqual(rejected_single_entry.status_code, 400, rejected_single_entry.get_json())
        self.assertEqual(rejected_single_entry.get_json()["error"]["field"], "transaction_kind")

        response = self.request(
            "POST",
            "/credit-card-repayments",
            {
                "source_account_id": "cash",
                "credit_account_id": "card",
                "amount": 120,
                "timestamp": "2026-07-25",
            },
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        body = response.get_json()
        self.assertEqual(body["source_transaction"]["amount"], -120.0)
        self.assertEqual(body["credit_transaction"]["amount"], 120.0)
        self.assertEqual(body["source_transaction"]["timestamp"], "2026-07-25T12:00:00+00:00")
        overview = self.request("GET", "/overview?year=2026&month=7")
        self.assertEqual(overview.get_json()["expense"], 0.0)

        blocked = self.request("DELETE", f"/transactions/{body['source_transaction']['id']}")
        self.assertEqual(blocked.status_code, 400, blocked.get_json())
        self.assertIn("reversed as a group", blocked.get_json()["error"]["message"])
        reversed_response = self.request(
            "DELETE", f"/credit-card-repayments/{body['source_transaction']['repayment_group_id']}"
        )
        self.assertEqual(reversed_response.status_code, 200, reversed_response.get_json())
        self.assertEqual(reversed_response.get_json(), {"status": "reversed"})

        invalid = self.request(
            "POST",
            "/credit-card-repayments",
            {
                "source_account_id": "cash",
                "credit_account_id": "cash",
                "amount": 120,
                "timestamp": "2026-07-25",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["error"]["field"], "credit_account_id")

    def test_amount_filters_match_csv_and_signed_boundaries(self):
        saved = [self.add_transaction(amount=value, transaction_kind='expense' if value < 0 else 'income', category_id='expense_dining_meal' if value < 0 else 'income_salary_salary') for value in (-120, -30.5, 50, 200)]
        for query, expected in [('min_amount=-40&max_amount=-30.5', [saved[1]['id']]), ('min_amount=50', [saved[2]['id'], saved[3]['id']]), ('max_amount=0', [saved[0]['id'], saved[1]['id']]), ('min_amount=&max_amount=', [row['id'] for row in saved])]:
            with self.subTest(query=query):
                response = self.request('GET', '/transactions?' + query)
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual([row['id'] for row in response.json], expected)
                exported = self.request('GET', '/export.csv?' + query)
                self.assertEqual(exported.status_code, 200)
                rows = list(csv.reader(io.StringIO(exported.data.decode('utf-8-sig'))))
                self.assertCountEqual([row[0] for row in rows[1:]], expected)

    def test_amount_filter_invalid_values_are_field_errors_for_both_endpoints(self):
        for endpoint in ('/transactions', '/export.csv'):
            for field in ('min_amount', 'max_amount'):
                for value in ('abc', 'NaN', 'Infinity', '1e309'):
                    with self.subTest(endpoint=endpoint, field=field, value=value):
                        response = self.request('GET', f'{endpoint}?{field}={value}')
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(response.json['error']['field'], field)
            response = self.request('GET', endpoint + '?min_amount=20&max_amount=10')
            self.assertEqual(response.status_code, 400)

    def test_paginated_metadata_and_legacy_shape(self):
        ids = [self.add_transaction(amount=-amount)['id'] for amount in (10, 20, 30)]
        legacy = self.request('GET', '/transactions').json
        self.assertIsInstance(legacy, list)
        first = self.request('GET', '/transactions?paginated=1&limit=1&min_amount=-30&max_amount=-20').json
        self.assertEqual(first['pagination'], {'total': 2, 'limit': 1, 'offset': 0, 'has_more': True, 'next_offset': 1})
        second = self.request('GET', '/transactions?paginated=1&limit=1&offset=1&min_amount=-30&max_amount=-20').json
        self.assertEqual(second['pagination']['next_offset'], None)
        self.assertFalse(second['pagination']['has_more'])
        self.assertCountEqual([first['transactions'][0]['id'], second['transactions'][0]['id']], ids[1:])
        empty = self.request('GET', '/transactions?paginated=1&offset=100').json
        self.assertEqual(empty['transactions'], [])
        self.assertEqual(empty['pagination']['total'], 3)
        self.assertFalse(empty['pagination']['has_more'])
        zero = self.request('GET', '/transactions?paginated=1&limit=0').json
        self.assertEqual(zero['transactions'], [])
        self.assertIsNone(zero['pagination']['next_offset'])
        invalid = self.request('GET', '/transactions?paginated=1&limit=bad')
        self.assertEqual(invalid.status_code, 400)

    def test_paginated_count_uses_all_list_filters(self):
        match = self.add_transaction(description='独有备注', amount=-30)
        self.add_transaction(description='其他备注', amount=-30)
        self.add_transaction(description='独有备注', account_id='cash', amount=-30)
        self.add_transaction(description='独有备注', amount=-30, tag_ids=[])
        query = ('account_id=card&category_id=expense_dining_meal&tag_id=family_member_a'
                 '&transaction_kind=expense&start=2026-07-01&end=2026-07-31'
                 '&min_amount=-40&max_amount=-20&query=独有备注')
        data = self.request('GET', '/transactions?paginated=1&' + query).json
        self.assertEqual(data['pagination']['total'], 1)
        self.assertEqual([row['id'] for row in data['transactions']], [match['id']])
        rows = list(csv.reader(io.StringIO(self.request('GET', '/export.csv?' + query).data.decode('utf-8-sig'))))
        self.assertEqual([row[0] for row in rows[1:]], [match['id']])

    def test_insights_charts_cover_full_period_not_first_page(self):
        for i in range(51):
            self.add_transaction(amount=-2, timestamp='2026-07-12T12:00:00+08:00')
        self.add_transaction(amount=-99, transaction_kind='transfer', excluded_from_stats=False)
        self.add_transaction(amount=-99, excluded_from_stats=True)
        self.add_transaction(amount=-99, timestamp='2026-08-12T12:00:00+08:00')
        detail = self.request('GET', '/accounts/card/insights?mode=calendar_month&anchor=2026-07-12&transaction_limit=50').json
        self.assertEqual(len(detail['transactions']), 50)
        self.assertEqual(detail['transaction_pagination'], {'total': 53, 'limit': 50, 'offset': 0, 'has_more': True, 'next_offset': 50})
        self.assertEqual(detail['chart_data']['daily_expenses'], [{'date': '2026-07-12', 'amount': 102.0}])
        self.assertEqual(detail['chart_data']['category_expenses'], [{'category_id': 'expense_dining_meal', 'amount': 102.0}])
        legacy = self.request('GET', '/accounts/card/insights?mode=calendar_month&anchor=2026-07-12').json
        self.assertEqual(len(legacy['transactions']), 53)
        self.assertEqual(legacy['transaction_pagination']['limit'], 100)
        last = self.request('GET', '/accounts/card/insights?mode=calendar_month&anchor=2026-07-12&transaction_limit=50&transaction_offset=50').json
        self.assertEqual(len(last['transactions']), 3)
        self.assertFalse(last['transaction_pagination']['has_more'])
        self.assertEqual(last['chart_data'], detail['chart_data'])
        for query in ('transaction_limit=bad', 'transaction_offset=-1'):
            self.assertEqual(self.request('GET', '/accounts/card/insights?' + query).status_code, 400)


if __name__ == "__main__":
    unittest.main()
