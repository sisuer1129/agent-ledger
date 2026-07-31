import json
import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(TEST_DIR))
sys.path.insert(0, str(APP_DIR))

from db import get_db
from helpers import auth_headers, test_config
from main import create_app


class AccountsBudgetsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir, config = test_config()
        self.app = create_app(config)
        self.client = self.app.test_client()
        self.headers = auth_headers()

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        return self.client.open(path, method=method, json=payload, headers=self.headers)

    def create_account(self, **overrides):
        payload = {
            "name": "示例信用卡B",
            "type": "credit",
            "initial_balance": -200,
            "monthly_budget": 5000,
            "budget_period": "billing_cycle",
            "credit_limit": 20000,
            "statement_day": 12,
            "due_day": 1,
            "due_month_offset": 1,
        }
        payload.update(overrides)
        response = self.request("POST", "/accounts", payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_all_protected_endpoints_require_api_key(self):
        for method, path in (
            ("GET", "/accounts"),
            ("POST", "/accounts"),
            ("GET", "/taxonomy"),
            ("GET", "/classification-rules"),
            ("POST", "/classification-rules"),
            ("GET", "/budgets"),
            ("POST", "/budgets"),
            ("POST", "/budget-impact-preview"),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.open(path, method=method, json={})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json()["error"]["code"], "unauthorized")

    def test_accounts_can_be_created_listed_updated_and_deactivated(self):
        created = self.create_account()
        account_id = created["id"]
        self.assertEqual(created["current_balance"], -200.0)
        self.assertEqual(created["budget_period"], "billing_cycle")

        listed = self.request("GET", "/accounts")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["id"] for row in listed.get_json()], [account_id])

        updated = self.request(
            "PUT", f"/accounts/{account_id}", {"name": "示例信用卡B", "monthly_budget": 6000}
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["name"], "示例信用卡B")
        self.assertEqual(updated.get_json()["current_balance"], -200.0)

        deactivated = self.request("DELETE", f"/accounts/{account_id}")
        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(deactivated.get_json()["is_active"], 0)
        self.assertEqual(self.request("GET", "/accounts").get_json(), [])

    def test_legacy_account_types_remain_editable_without_rewriting_them(self):
        with self.app.app_context():
            conn = get_db()
            conn.executemany(
                "INSERT INTO accounts (id, name, type, initial_balance, current_balance, is_active) "
                "VALUES (?, ?, ?, 0, 0, 1)",
                (("legacy-shopping", "旧购物卡", "shopping_card"), ("legacy-wechat", "旧微信账户", "wechat")),
            )
            conn.commit()

        shopping = self.request("PUT", "/accounts/legacy-shopping", {"notes": "保留旧类型"})
        wechat = self.request("PUT", "/accounts/legacy-wechat", {"notes": "保留旧类型"})
        self.assertEqual(shopping.status_code, 200, shopping.get_json())
        self.assertEqual(wechat.status_code, 200, wechat.get_json())
        self.assertEqual(shopping.get_json()["type"], "shopping_card")
        self.assertEqual(wechat.get_json()["type"], "wechat")

    def test_account_validation_returns_stable_invalid_field_errors(self):
        cases = (
            ({"statement_day": 31}, "statement_day"),
            ({"monthly_budget": -1}, "monthly_budget"),
            ({"credit_limit": -1}, "credit_limit"),
            ({"type": "debit", "budget_period": "billing_cycle"}, "budget_period"),
        )
        for patch, field in cases:
            with self.subTest(field=field):
                payload = {"name": "账户-" + field, "type": "debit", "initial_balance": 0}
                payload.update(patch)
                response = self.request("POST", "/accounts", payload)
                self.assertEqual(response.status_code, 400)
                body = response.get_json()
                self.assertEqual(body["error"]["code"], "invalid_field")
                self.assertEqual(body["error"]["field"], field)

        account = self.create_account(name="可校验账户")
        response = self.request("PUT", f"/accounts/{account['id']}", {"statement_day": 31})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["field"], "statement_day")

    def test_taxonomy_returns_category_tree_and_active_tags(self):
        response = self.request("GET", "/taxonomy")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        dining = next(row for row in body["categories"] if row["id"] == "expense_dining")
        self.assertEqual(dining["children"][0]["id"], "expense_dining_meal")
        self.assertIn("family_member_a", [row["id"] for row in body["tags"]])

    def test_classification_rules_can_be_managed_and_soft_deactivated(self):
        seeded = self.request("GET", "/classification-rules")
        self.assertEqual(seeded.status_code, 200)
        self.assertTrue(seeded.get_json())

        created = self.request(
            "POST",
            "/classification-rules",
            {"rule_type": "tag", "pattern": "孩子", "tag_id": "family_member_a", "priority": 10},
        )
        self.assertEqual(created.status_code, 201)
        rule = created.get_json()
        self.assertEqual(rule["is_active"], 1)

        updated = self.request(
            "PUT", f"/classification-rules/{rule['id']}", {"pattern": "示例成员A|成员A", "priority": 9}
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["priority"], 9)

        deleted = self.request("DELETE", f"/classification-rules/{rule['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["is_active"], 0)
        self.assertNotIn(rule["id"], [row["id"] for row in self.request("GET", "/classification-rules").get_json()])

    def test_budgets_upsert_and_month_lookup(self):
        account = self.create_account(name="预算账户")
        created = self.request(
            "POST",
            "/budgets",
            {"scope_type": "category", "scope_id": "expense_dining", "amount": 3000, "effective_from": "2026-07-01"},
        )
        self.assertEqual(created.status_code, 201)
        budget = created.get_json()
        self.assertEqual(budget["amount"], 3000.0)

        replaced = self.request(
            "POST",
            "/budgets",
            {"scope_type": "category", "scope_id": "expense_dining", "amount": 3200, "effective_from": "2026-07-01"},
        )
        self.assertEqual(replaced.status_code, 201)
        self.assertEqual(replaced.get_json()["id"], budget["id"])
        self.assertEqual(replaced.get_json()["amount"], 3200.0)

        account_budget = self.request(
            "POST",
            "/budgets",
            {"scope_type": "account", "scope_id": account["id"], "amount": 5000, "effective_from": "2026-06-01"},
        )
        self.assertEqual(account_budget.status_code, 201)
        july = self.request("GET", "/budgets?month=2026-07")
        self.assertEqual(july.status_code, 200)
        self.assertEqual({row["scope_id"] for row in july.get_json()}, {"expense_dining", account["id"]})

    def test_budget_summary_uses_transaction_kind_amount_direction_and_expense_roots(self):
        account = self.create_account(name="预算汇总账户")
        account_id = account["id"]
        for payload in (
            {"id": "dining-expense", "amount": -120, "transaction_kind": "expense", "excluded_from_stats": False,
             "category_id": "expense_dining_meal", "description": "午饭"},
            {"id": "dining-refund", "amount": 20, "transaction_kind": "refund", "excluded_from_stats": False,
             "category_id": "expense_dining_meal", "description": "午饭退款"},
            {"id": "ignored-transfer", "amount": -30, "transaction_kind": "transfer", "excluded_from_stats": True,
             "category_id": "expense_dining_meal", "description": "账户转账"},
            {"id": "ignored-income", "amount": 500, "transaction_kind": "income", "excluded_from_stats": False,
             "category_id": "income_salary_salary", "description": "工资"},
        ):
            response = self.request("POST", "/transactions", {
                "account_id": account_id, "timestamp": "2026-07-12", "classification_status": "manual", "tag_ids": [],
                **payload,
            })
            self.assertEqual(response.status_code, 201, response.get_json())

        self.assertEqual(self.request(
            "POST", "/budgets",
            {"scope_type": "total", "scope_id": "total", "amount": 300, "effective_from": "2026-07-01"},
        ).status_code, 201)
        self.assertEqual(self.request(
            "POST", "/budgets",
            {"scope_type": "category", "scope_id": "expense_dining", "amount": 100, "effective_from": "2026-07-01"},
        ).status_code, 201)

        response = self.request("GET", "/budget-summary?month=2026-07")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["total"], {
            "budget_amount": 300.0, "spent_amount": 100.0, "remaining_amount": 200.0,
            "usage_rate": 100 / 300, "status": "normal",
        })
        dining = next(row for row in body["categories"] if row["id"] == "expense_dining")
        self.assertEqual(dining["spent_amount"], 100.0)
        self.assertEqual(dining["budget_amount"], 100.0)
        self.assertEqual(dining["remaining_amount"], 0.0)
        self.assertEqual(dining["status"], "reached")
        self.assertEqual(body["alerts"], [{
            "id": "expense_dining", "name": "餐饮", "budget_amount": 100.0,
            "spent_amount": 100.0, "remaining_amount": 0.0, "usage_rate": 1.0, "status": "reached",
        }])

    def test_zero_budget_with_spending_returns_strict_json_and_exceeded_status(self):
        account = self.create_account(
            name="零预算储值账户", type="stored_value", initial_balance=0,
            budget_period="calendar_month", credit_limit=None, statement_day=None,
            due_day=None,
        )
        self.assertEqual(self.request("POST", "/transactions", {
            "account_id": account["id"], "timestamp": "2026-07-31", "amount": -446.86,
            "description": "零预算消费", "category_id": "expense_dining_meal",
            "transaction_kind": "expense", "excluded_from_stats": False,
            "classification_status": "manual", "tag_ids": [],
        }).status_code, 201)
        for scope_type, scope_id in (("total", "total"), ("category", "expense_dining")):
            self.assertEqual(self.request("POST", "/budgets", {
                "scope_type": scope_type, "scope_id": scope_id, "amount": 0,
                "effective_from": "2026-07-01",
            }).status_code, 201)

        response = self.request("GET", "/budget-summary?month=2026-07")
        self.assertEqual(response.status_code, 200)
        raw_body = response.get_data(as_text=True)
        self.assertNotIn("Infinity", raw_body)
        body = json.loads(raw_body, parse_constant=lambda value: self.fail(f"non-standard JSON number: {value}"))
        self.assertEqual(body["total"]["usage_rate"], 1)
        self.assertEqual(body["total"]["status"], "exceeded")
        dining = next(row for row in body["categories"] if row["id"] == "expense_dining")
        self.assertEqual(dining["usage_rate"], 1)
        self.assertEqual(dining["status"], "exceeded")

    def test_budget_and_rule_reject_invalid_references_and_payloads(self):
        bad_budget = self.request(
            "POST",
            "/budgets",
            {"scope_type": "category", "scope_id": "missing", "amount": -1, "effective_from": "2026-07-01"},
        )
        self.assertEqual(bad_budget.status_code, 400)
        self.assertEqual(bad_budget.get_json()["error"]["field"], "amount")

        bad_rule = self.request(
            "POST", "/classification-rules", {"rule_type": "tag", "pattern": "x", "tag_id": "missing"}
        )
        self.assertEqual(bad_rule.status_code, 400)
        self.assertEqual(bad_rule.get_json()["error"]["field"], "tag_id")

    def test_budget_impact_preview_uses_shared_budget_rules_without_writing(self):
        account = self.create_account(name="预算预览账户")
        original = self.request("POST", "/transactions", {
            "account_id": account["id"], "timestamp": "2026-07-12", "amount": -50,
            "description": "原餐饮", "category_id": "expense_dining_meal", "transaction_kind": "expense",
            "excluded_from_stats": False, "classification_status": "manual", "tag_ids": [],
        }).get_json()
        self.assertEqual(self.request("POST", "/budgets", {
            "scope_type": "category", "scope_id": "expense_dining", "amount": 100,
            "effective_from": "2026-07-01",
        }).status_code, 201)

        with self.app.app_context():
            before_transactions = get_db().execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            before_budgets = get_db().execute("SELECT COUNT(*) FROM budgets").fetchone()[0]

        cases = (
            ("new expense", {
                "timestamp": "2026-07-15", "amount": -20, "transaction_kind": "expense",
                "category_id": "expense_dining_meal", "excluded_from_stats": False,
            }, True, 50.0, 70.0, "normal"),
            ("edit amount", {
                "transaction_id": original["id"], "timestamp": "2026-07-12", "amount": -80,
                "transaction_kind": "expense", "category_id": "expense_dining_meal", "excluded_from_stats": False,
            }, True, 0.0, 80.0, "warning"),
            ("edit category", {
                "transaction_id": original["id"], "timestamp": "2026-07-12", "amount": -30,
                "transaction_kind": "expense", "category_id": "expense_daily_shopping_daily", "excluded_from_stats": False,
            }, False, None, None, "unbudgeted"),
            ("edit across month", {
                "transaction_id": original["id"], "timestamp": "2026-08-02", "amount": -30,
                "transaction_kind": "expense", "category_id": "expense_dining_meal", "excluded_from_stats": False,
            }, True, 0.0, 30.0, "normal"),
            ("edit into refund", {
                "transaction_id": original["id"], "timestamp": "2026-07-12", "amount": 20,
                "transaction_kind": "refund", "category_id": "expense_dining_meal", "excluded_from_stats": False,
            }, True, 0.0, -20.0, "normal"),
            ("edit to excluded", {
                "transaction_id": original["id"], "timestamp": "2026-07-12", "amount": -50,
                "transaction_kind": "expense", "category_id": "expense_dining_meal", "excluded_from_stats": True,
            }, False, None, None, "excluded"),
            ("classified refund", {
                "timestamp": "2026-07-15", "amount": 20, "transaction_kind": "refund",
                "category_id": "expense_dining_meal", "excluded_from_stats": False,
            }, True, 50.0, 30.0, "normal"),
            ("unclassified", {
                "timestamp": "2026-07-15", "amount": -20, "transaction_kind": "expense",
                "category_id": None, "excluded_from_stats": False,
            }, False, None, None, "unclassified"),
            ("excluded transfer", {
                "timestamp": "2026-07-15", "amount": -20, "transaction_kind": "transfer",
                "category_id": "expense_dining_meal", "excluded_from_stats": True,
            }, False, None, None, "excluded"),
        )
        for name, payload, visible, current, projected, status in cases:
            with self.subTest(name=name):
                response = self.request("POST", "/budget-impact-preview", payload)
                self.assertEqual(response.status_code, 200, response.get_json())
                body = response.get_json()
                self.assertEqual(body["visible"], visible)
                self.assertEqual(body["reason"], None if visible else status)
                if visible:
                    self.assertEqual(body["current"]["spent_amount"], current)
                    self.assertEqual(body["projected"]["spent_amount"], projected)
                    self.assertEqual(body["projected"]["status"], status)
                elif status == "unbudgeted":
                    self.assertEqual(body["category"]["name"], "日用购物")

        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM transactions").fetchone()[0], before_transactions)
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM budgets").fetchone()[0], before_budgets)

    def test_account_update_rejects_current_balance_edits(self):
        account = self.create_account(name="余额保护")
        response = self.request("PUT", f"/accounts/{account['id']}", {"current_balance": 999})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["field"], "current_balance")
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT current_balance FROM accounts WHERE id = ?", (account["id"],)).fetchone()[0], -200)
