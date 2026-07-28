import csv
import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
APP_DIR = TEST_DIR.parents[0] / "release" / "app"
sys.path.insert(0, str(APP_DIR))

from classification_import import (
    ClassificationImportError,
    analyze_classification_csv,
    apply_classification_csv,
    clean_person_marker_notes,
    enrich_legacy_csv,
    merge_review_csv,
    resolve_category,
)
from db import connect_database, initialize_database_path
from repositories import create_transaction


HEADERS = ["交易 ID", "时间", "账户", "金额", "类型", "一级分类", "二级分类", "标签", "交易性质", "是否计入统计", "备注"]
LEGACY_HEADERS = HEADERS[1:]


class ClassificationImportTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "wallet.db"
        initialize_database_path(self.db_path)
        self.conn = connect_database(self.db_path)
        self.conn.execute(
            "INSERT INTO accounts (id, name, type, initial_balance, current_balance, is_active) VALUES "
            "('cash', '现金', 'debit', 100, 100, 1)"
        )
        self.conn.commit()
        self.transaction = create_transaction(self.conn, {
            "id": "tx-import", "account_id": "cash", "timestamp": "2026-07-26T12:00:00+00:00",
            "amount": -12.5, "description": "午饭", "category_id": "expense_dining_meal",
            "transaction_kind": "expense", "excluded_from_stats": False,
            "classification_status": "needs_review", "tag_ids": [],
        })
        self.csv_path = Path(self.temp_dir.name) / "classified.csv"
        self.legacy_path = Path(self.temp_dir.name) / "legacy.csv"

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def write_csv(self, path, headers, rows):
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

    def classified_row(self, transaction_id="tx-import"):
        return [transaction_id, "2026-07-26T12:00:00+00:00", "现金", "-12.5", "支出", "餐饮", "正餐", "示例成员A", "expense", "是", "午饭"]

    def test_resolve_category_accepts_seed_names_and_rejects_retired_name(self):
        self.assertEqual(
            resolve_category(self.conn, "车辆", "固定停车"),
            "expense_vehicle_fixed_parking",
        )
        with self.assertRaisesRegex(ClassificationImportError, "unknown or inactive category"):
            resolve_category(self.conn, "车辆", "停车")

    def test_analyze_requires_unique_existing_transaction_ids_and_resolves_taxonomy(self):
        self.write_csv(self.csv_path, HEADERS, [self.classified_row()])
        report = analyze_classification_csv(self.db_path, self.csv_path)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["changed"], 1)
        self.assertFalse(report["applied"])

        self.write_csv(self.csv_path, HEADERS, [self.classified_row(), self.classified_row()])
        with self.assertRaises(ClassificationImportError):
            analyze_classification_csv(self.db_path, self.csv_path)

    def test_apply_only_changes_classification_fields_and_preserves_financial_values(self):
        self.write_csv(self.csv_path, HEADERS, [self.classified_row()])
        before = self.conn.execute("SELECT current_balance FROM accounts WHERE id = 'cash'").fetchone()[0]
        result = apply_classification_csv(self.db_path, self.csv_path, self.temp_dir.name)
        self.assertTrue(result["applied"])
        self.assertTrue(Path(result["backup_path"]).exists())
        after = self.conn.execute("SELECT current_balance FROM accounts WHERE id = 'cash'").fetchone()[0]
        row = self.conn.execute("SELECT * FROM transactions WHERE id = 'tx-import'").fetchone()
        self.assertEqual(before, after)
        self.assertEqual(row["amount"], -12.5)
        self.assertEqual(row["category_id"], "expense_dining_meal")
        self.assertEqual(row["classification_status"], "manual")
        self.assertEqual(
            [tag["tag_id"] for tag in self.conn.execute("SELECT tag_id FROM transaction_tags WHERE transaction_id = 'tx-import'")],
            ["family_member_a"],
        )

    def test_enrich_legacy_csv_requires_full_one_to_one_match(self):
        self.write_csv(self.legacy_path, LEGACY_HEADERS, [self.classified_row()[1:]])
        enriched_path = Path(self.temp_dir.name) / "enriched.csv"
        report = enrich_legacy_csv(self.db_path, self.legacy_path, enriched_path)
        self.assertEqual(report["matched"], 1)
        with enriched_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0][0], "交易 ID")
        self.assertEqual(rows[1][0], "tx-import")

    def test_merge_review_csv_keeps_transaction_id_and_uses_unique_fallback_key(self):
        base_path = Path(self.temp_dir.name) / "base.csv"
        review_path = Path(self.temp_dir.name) / "review.csv"
        output_path = Path(self.temp_dir.name) / "merged.csv"
        base = self.classified_row()
        self.write_csv(base_path, HEADERS, [base])
        reviewed = base[1:]
        reviewed[-1] = "午饭（已人工复核）"
        reviewed[4:7] = ["日用购物", "食品杂货", "家庭公共"]
        self.write_csv(review_path, LEGACY_HEADERS, [reviewed])

        result = merge_review_csv(base_path, review_path, output_path)

        self.assertEqual(result, {"base_rows": 1, "review_rows": 1, "overrides": 1, "fallback_matches": 1})
        with output_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["交易 ID"], "tx-import")
        self.assertEqual(rows[0]["一级分类"], "日用购物")
        self.assertEqual(rows[0]["备注"], "午饭（已人工复核）")

    def test_clean_person_marker_notes_only_changes_the_authorized_marker(self):
        self.conn.execute("UPDATE transactions SET description = '示例成员A标记 - 午饭（示例成员A标记）' WHERE id = 'tx-import'")
        self.conn.commit()

        preview = clean_person_marker_notes(self.db_path)
        applied = clean_person_marker_notes(self.db_path, self.temp_dir.name, apply=True)

        self.assertEqual(preview, {"applied": False, "matched": 1})
        self.assertTrue(applied["applied"])
        self.assertEqual(
            self.conn.execute("SELECT description FROM transactions WHERE id = 'tx-import'").fetchone()[0],
            "午饭",
        )

    def test_non_consumption_rows_keep_a_visible_category_after_taxonomy_upgrade(self):
        row = self.classified_row()
        row[5:9] = ["资金往来", "信用卡还款", "", "credit_repayment"]
        row[9] = "否"
        self.write_csv(self.csv_path, HEADERS, [row])

        apply_classification_csv(self.db_path, self.csv_path, self.temp_dir.name)

        category_id = self.conn.execute(
            "SELECT category_id FROM transactions WHERE id = 'tx-import'"
        ).fetchone()[0]
        self.assertEqual(category_id, "nonexpense_flow_credit_repayment")


if __name__ == "__main__":
    unittest.main()
