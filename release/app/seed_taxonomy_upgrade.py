"""Preview and safely apply the seed-driven wallet taxonomy."""

import argparse
import json
import re
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from db import connect_database
from seed_catalog import load_category_seed
from taxonomy import _seed_categories, _seed_tags


class SeedTaxonomyUpgradeError(RuntimeError):
    pass


def upgrade_seed_taxonomy(db_path, apply=False, backup_dir=None):
    """Install the configured taxonomy without changing financial data.

    Preview mode is read-only.  Application mode creates a recoverable SQLite
    backup before any write, then performs all taxonomy and transaction updates
    in one transaction.
    """
    if not apply:
        conn = _readonly_connection(db_path)
        try:
            return _preview(conn)
        finally:
            conn.close()

    backup_path = _create_backup(db_path, backup_dir)
    conn = connect_database(db_path)
    try:
        before = _financial_snapshot(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            _seed_categories(conn)
            _seed_tags(conn)
            managed_ids = _managed_category_ids()
            migrations = _transaction_migrations(conn, managed_ids)
            _migrate_transactions(conn, migrations)
            retired = _retired_category_ids(conn, managed_ids)
            _deactivate_retired_categories(conn, retired)
            _assert_financial_snapshot(conn, before)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            **_report(conn, migrations, retired),
            "applied": True,
            "active_categories": _active_category_count(conn),
            "backup_path": str(backup_path),
        }
    finally:
        conn.close()


def _preview(conn):
    managed_ids = _managed_category_ids()
    retired = _retired_category_ids(conn, managed_ids)
    migrations = _transaction_migrations(conn, managed_ids)
    return {**_report(conn, migrations, retired), "applied": False}


def _report(conn, migrations, retired):
    review_items = [item for item in migrations if item["action"] in {"preserve_existing", "remain_uncategorized", "failed"}]
    actions = {name: sum(item["action"] == name for item in migrations) for name in (
        "update", "preserve_existing", "remain_uncategorized", "failed", "no_op"
    )}
    review_examples = [
        {"record": f"record-{index}", "action": item["action"], "reason": item["reason"]}
        for index, item in enumerate(review_items[:3], 1)
    ]
    return {
        "active_categories_before": _active_category_count(conn),
        "seed_roots": len(load_category_seed()["roots"]),
        "categories_to_upsert": len(_managed_category_ids()),
        "categories_to_deactivate": len(retired),
        "transactions_auto_mapped": actions["update"] + actions["no_op"],
        "transactions_needing_review": len(review_items),
        "review_items": review_examples,
        "total": len(migrations),
        "updated": actions["update"],
        "preserved_existing": actions["preserve_existing"],
        "remained_uncategorized": actions["remain_uncategorized"],
        "unmatched": actions["preserve_existing"] + actions["remain_uncategorized"],
        "failed": actions["failed"],
        "no_op": actions["no_op"],
        "review_examples": review_examples,
    }


def _managed_category_ids():
    return {
        item["id"]
        for root in load_category_seed()["roots"]
        for item in (root, *root["children"])
    }


def _retired_category_ids(conn, managed_ids):
    return [
        row["id"]
        for row in conn.execute(
            "SELECT c.id FROM categories c WHERE c.is_active = 1 AND c.id NOT IN (" + _placeholders(managed_ids) + ") "
            "AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.category_id = c.id)",
            tuple(sorted(managed_ids)),
        )
    ]


def _deactivate_retired_categories(conn, retired_ids):
    if not retired_ids:
        return
    conn.execute(
        "UPDATE categories SET is_active = 0 WHERE id IN (" + _placeholders(retired_ids) + ")",
        tuple(sorted(retired_ids)),
    )


def _transaction_migrations(conn, managed_ids):
    rows = conn.execute(
        "SELECT t.id, t.category_id, t.description, t.transaction_kind, "
        "child.name AS secondary_name, parent.name AS primary_name "
        "FROM transactions t "
        "LEFT JOIN categories child ON child.id = t.category_id "
        "LEFT JOIN categories parent ON parent.id = child.parent_id "
        "WHERE t.category_id IS NULL OR t.category_id NOT IN (" + _placeholders(managed_ids) + ") "
        "ORDER BY t.timestamp, t.id",
        tuple(sorted(managed_ids)),
    ).fetchall()
    items = []
    for row in rows:
        tag_ids = {
            tag["tag_id"]
            for tag in conn.execute(
                "SELECT tag_id FROM transaction_tags WHERE transaction_id = ?", (row["id"],)
            )
        }
        target, reason = _resolve_legacy_target_with_reason(
            row["primary_name"] or "",
            row["secondary_name"] or "",
            row["description"] or "",
            tag_ids,
            transaction_kind=row["transaction_kind"] or "",
        )
        valid_target = _valid_target_category(conn, target, row["transaction_kind"] or "")
        if row["category_id"]:
            action = "update" if valid_target else "preserve_existing"
        else:
            action = "update" if valid_target else "remain_uncategorized"
        items.append({
            "transaction_id": row["id"],
            "previous_category_id": row["category_id"],
            "category_id": row["category_id"],
            "primary_name": row["primary_name"],
            "secondary_name": row["secondary_name"],
            "description": row["description"],
            "proposed_category_id": target,
            "target_category_id": target if valid_target else None,
            "action": action,
            "reason": reason if valid_target else ("no_reliable_category" if target is None else "invalid_or_inactive_category"),
            "needs_review": action != "update",
        })
    return items


def _migrate_transactions(conn, migrations):
    for item in migrations:
        if item["action"] == "update":
            conn.execute(
                "UPDATE transactions SET category_id = ?, classification_status = 'classified' WHERE id = ?",
                (item["target_category_id"], item["transaction_id"]),
            )
        elif item["action"] in {"preserve_existing", "remain_uncategorized", "failed"}:
            conn.execute(
                "UPDATE transactions SET classification_status = 'needs_review' "
                "WHERE id = ? AND classification_status != 'needs_review'",
                (item["transaction_id"],),
            )


def _valid_target_category(conn, category_id, transaction_kind):
    if not category_id:
        return False
    row = conn.execute("SELECT type FROM categories WHERE id = ? AND is_active = 1", (category_id,)).fetchone()
    if row is None:
        return False
    expected_types = {
        "expense": {"expense"},
        "refund": {"expense"},
        "income": {"income"},
        "transfer": {"transaction"},
        "credit_repayment": {"transaction"},
        "topup_withdrawal": {"transaction"},
        "balance_adjustment": {"transaction"},
    }.get(transaction_kind, set())
    return row["type"] in expected_types


def resolve_legacy_target(primary, secondary, description, tag_ids, transaction_kind=""):
    """Resolve only confirmed historical cases; return ``None`` for review."""
    return _resolve_legacy_target_with_reason(
        primary, secondary, description, tag_ids, transaction_kind=transaction_kind
    )[0]


def _resolve_legacy_target_with_reason(primary, secondary, description, tag_ids, transaction_kind=""):
    primary, secondary, description = (primary or "").strip(), (secondary or "").strip(), description or ""
    text = description.casefold()
    tags = set(tag_ids or ())

    if transaction_kind == "credit_repayment":
        return "nonexpense_flow_credit_repayment", "transaction_kind"
    if transaction_kind == "topup_withdrawal":
        return "nonexpense_flow_topup_withdrawal", "transaction_kind"
    if transaction_kind == "balance_adjustment":
        return "nonexpense_flow_balance_adjustment", "transaction_kind"
    if transaction_kind == "transfer":
        return (
            "nonexpense_internal_transfer_family" if primary == "内部转账" else "nonexpense_flow_account_transfer",
            "transaction_kind",
        )
    if transaction_kind == "refund":
        return "nonexpense_refund_merchant", "transaction_kind"

    education_context = primary == "教育成长" or secondary in {"子女教育", "课程培训", "补课费", "学费"}
    if education_context and (tags & {"family_member_a", "family_member_b", "child"} or re.search(r"示例成员A|成员A|示例成员B", description)):
        return "expense_family_child_education", "person_education_context"
    if re.search(r"车辆购置.*(?:分期|车贷)|(?:分期|车贷).*车辆购置", description, re.IGNORECASE):
        return "expense_vehicle_loan", "vehicle_loan_context"
    if primary in {"人情与家庭", "家庭"} and secondary in {"子女支出", "家庭支持"} and re.search(
        r"大学|学校|学费|课程|补课", description, re.IGNORECASE
    ):
        return "expense_family_child_education", "child_education_context"
    if re.search(r"固定车位|小区停车|月租车位", description, re.IGNORECASE):
        return "expense_vehicle_fixed_parking", "fixed_parking_context"
    if re.search(r"停车场|商场停车|临停", description, re.IGNORECASE):
        return "expense_transport_temporary_parking", "temporary_parking_context"
    if re.search(r"酒店|住宿", description, re.IGNORECASE):
        return "expense_travel_hotel", "travel_lodging_context"
    if re.search(r"签证|护照", description, re.IGNORECASE):
        return "expense_travel_visa_documents", "travel_document_context"
    if re.search(r"旅行|旅游|trip|japan|日本", description, re.IGNORECASE) and re.search(r"保险", description, re.IGNORECASE):
        return "expense_travel_insurance", "travel_insurance_context"

    for mapping in load_category_seed()["legacy_mappings"]:
        if tuple(mapping["from"]) == (primary, secondary):
            return mapping["to"], "exact_legacy_mapping"
    return None, "legacy_category_has_no_seed_mapping"


def _active_category_count(conn):
    return conn.execute("SELECT COUNT(*) FROM categories WHERE is_active = 1").fetchone()[0]


def _financial_snapshot(conn):
    amount = sum((Decimal(str(row["amount"])) for row in conn.execute("SELECT amount FROM transactions")), Decimal("0"))
    balances = {
        row["id"]: Decimal(str(row["current_balance"]))
        for row in conn.execute("SELECT id, current_balance FROM accounts")
    }
    return {
        "transaction_count": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        "amount_checksum": format(amount.quantize(Decimal("0.01")), "f"),
        "balances": balances,
    }


def _assert_financial_snapshot(conn, before):
    if _financial_snapshot(conn) != before:
        raise SeedTaxonomyUpgradeError("taxonomy upgrade changed financial records or balances")


def _create_backup(db_path, backup_dir):
    source_path = Path(db_path)
    directory = Path(backup_dir) if backup_dir else source_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (
        source_path.stem + ".pre-seed-taxonomy-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + source_path.suffix
    )
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def _readonly_connection(db_path):
    conn = sqlite3.connect("file:" + str(Path(db_path).resolve()) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _placeholders(values):
    if not values:
        raise SeedTaxonomyUpgradeError("seed taxonomy has no categories")
    return ",".join("?" for _ in values)


def main():
    parser = argparse.ArgumentParser(description="Preview or apply seed-driven wallet taxonomy")
    parser.add_argument("db_path")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir")
    args = parser.parse_args()
    print(json.dumps(
        upgrade_seed_taxonomy(args.db_path, apply=args.apply, backup_dir=args.backup_dir),
        ensure_ascii=False,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
