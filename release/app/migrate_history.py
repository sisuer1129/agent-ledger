"""Read-only analysis and reconciled application of wallet v2 classifications."""

import argparse
import csv
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, Tuple

from classifier import classify_transaction
from db import connect_database, initialize_database_path


class HistoryMigrationError(RuntimeError):
    """Raised when a migration cannot preserve its financial invariants."""


@dataclass(frozen=True)
class MigrationProposal:
    transaction_id: str
    previous_category_id: Optional[str]
    proposed_category_id: Optional[str]
    action: str
    classification: Optional[Any]
    reason: str
    current_tag_ids: Tuple[str, ...]

    @property
    def classification_status(self):
        if self.action in {"preserve_existing", "remain_uncategorized", "failed"}:
            return "needs_review"
        return self.classification.classification_status


def analyze_csv(csv_path):
    """Analyze an exported legacy ledger without opening or creating a database."""
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        fields = _csv_fields(headers)
        rows = list(reader)
    proposed = [_classify_csv_row(row, fields) for row in rows]
    checksum = _checksum(_csv_decimal(row, fields["amount"]) for row in rows)
    return _report(len(rows), proposed, checksum, balances_unchanged=True, applied=False)


def normalize_history(db_path, apply=False, backup_dir=None):
    """Dry-run or apply normalized columns, tag links, and nothing else.

    Dry-runs use SQLite read-only mode, so even journal settings are untouched.
    Apply makes a uniquely named sibling SQLite backup before any write, then
    checks count, signed amount checksum and every account balance before commit.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise HistoryMigrationError("database does not exist")
    if not apply:
        conn = _readonly_connection(db_path)
        try:
            rows = _database_rows(conn)
            proposed = _proposals(conn, rows)
            return _report(len(rows), proposed, _checksum(row["amount"] for row in rows), True, False)
        finally:
            conn.close()

    backup_path = _create_backup(db_path, backup_dir)
    conn = connect_database(db_path)
    try:
        rows_before = _database_rows(conn)
        balances_before = _balances(conn)
        checksum_before = _checksum(row["amount"] for row in rows_before)
        proposed = _proposals(conn, rows_before)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for proposal in proposed:
                _apply_proposal(conn, proposal)
            rows_after = _database_rows(conn)
            checksum_after = _checksum(row["amount"] for row in rows_after)
            balances_after = _balances(conn)
            if len(rows_after) != len(rows_before) or checksum_after != checksum_before or balances_after != balances_before:
                raise HistoryMigrationError("reconciliation failed: record count, amounts, or balances changed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        report = _report(len(rows_before), proposed, checksum_before, balances_after == balances_before, True)
        report["backup_path"] = str(backup_path)
        return report
    finally:
        conn.close()


def _readonly_connection(db_path):
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _database_rows(conn):
    return conn.execute(
        "SELECT t.*, a.name AS account_name, a.type AS account_type FROM transactions t "
        "LEFT JOIN accounts a ON a.id = t.account_id ORDER BY t.timestamp, t.id"
    ).fetchall()


def _proposals(conn, rows):
    return [_proposal(conn, row) for row in rows]


def _proposal(conn, row):
    previous_category_id = row["category_id"]
    current_tag_ids = _transaction_tag_ids(conn, row["id"])
    try:
        classification = classify_transaction(
            conn, row["description"], row["category"], row["amount"],
            source_account={"name": row["account_name"], "type": row["account_type"]},
        )
        proposed_category_id = classification.category_id
        valid_target = _valid_target_category(conn, proposed_category_id, classification.transaction_kind)
        if previous_category_id:
            if not valid_target:
                return MigrationProposal(
                    row["id"], previous_category_id, proposed_category_id, "preserve_existing", classification,
                    "no_reliable_category" if proposed_category_id is None else "invalid_or_inactive_category",
                    current_tag_ids,
                )
            if _is_no_op(row, classification, current_tag_ids):
                return MigrationProposal(
                    row["id"], previous_category_id, proposed_category_id, "no_op", classification,
                    "already_at_verified_target", current_tag_ids,
                )
            return MigrationProposal(
                row["id"], previous_category_id, proposed_category_id, "update", classification,
                "verified_category", current_tag_ids,
            )
        if valid_target:
            return MigrationProposal(
                row["id"], None, proposed_category_id, "update", classification,
                "verified_category", current_tag_ids,
            )
        if _is_no_op(row, classification, current_tag_ids):
            return MigrationProposal(
                row["id"], None, proposed_category_id, "no_op", classification,
                "already_uncategorized", current_tag_ids,
            )
        return MigrationProposal(
            row["id"], None, proposed_category_id, "remain_uncategorized", classification,
            "no_reliable_category" if proposed_category_id is None else "invalid_or_inactive_category",
            current_tag_ids,
        )
    except Exception:
        return MigrationProposal(
            row["id"], previous_category_id, None, "failed", None,
            "classification_failed", current_tag_ids,
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
    }.get(transaction_kind, set())
    return row["type"] in expected_types


def _transaction_tag_ids(conn, transaction_id):
    return tuple(row["tag_id"] for row in conn.execute(
        "SELECT tag_id FROM transaction_tags WHERE transaction_id = ? ORDER BY tag_id", (transaction_id,)
    ))


def _is_no_op(row, classification, current_tag_ids):
    return (
        row["category_id"] == classification.category_id
        and row["transaction_kind"] == classification.transaction_kind
        and int(row["excluded_from_stats"] or 0) == int(classification.excluded_from_stats)
        and row["classification_status"] == classification.classification_status
        and current_tag_ids == tuple(sorted(classification.tag_ids))
    )


def _apply_proposal(conn, proposal):
    if proposal.action == "no_op":
        return
    if proposal.action == "update":
        classification = proposal.classification
        conn.execute(
            "UPDATE transactions SET category_id = ?, transaction_kind = ?, excluded_from_stats = ?, "
            "classification_status = ? WHERE id = ?",
            (proposal.proposed_category_id, classification.transaction_kind,
             int(classification.excluded_from_stats), classification.classification_status, proposal.transaction_id),
        )
        conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (proposal.transaction_id,))
        conn.executemany(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            ((proposal.transaction_id, tag_id) for tag_id in classification.tag_ids),
        )
        return
    if proposal.action == "remain_uncategorized":
        classification = proposal.classification
        # The category was already empty.  Deliberately omit category_id from
        # this update so None is never used as an implicit clearing command.
        conn.execute(
            "UPDATE transactions SET transaction_kind = ?, excluded_from_stats = ?, classification_status = ? "
            "WHERE id = ?",
            (classification.transaction_kind, int(classification.excluded_from_stats),
             classification.classification_status, proposal.transaction_id),
        )
        conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (proposal.transaction_id,))
        conn.executemany(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            ((proposal.transaction_id, tag_id) for tag_id in classification.tag_ids),
        )
        return
    # A missing or unsafe proposed category is never a request to clear an
    # existing value.  Keep categories and tags untouched; only surface review.
    conn.execute(
        "UPDATE transactions SET classification_status = 'needs_review' "
        "WHERE id = ? AND classification_status != 'needs_review'",
        (proposal.transaction_id,),
    )


def _balances(conn):
    return {
        row["id"]: _decimal(row["current_balance"])
        for row in conn.execute("SELECT id, current_balance FROM accounts ORDER BY id")
    }


def _create_backup(db_path, backup_dir=None):
    directory = Path(backup_dir) if backup_dir else db_path.parent
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = directory / f"{db_path.stem}.pre-normalize-{stamp}{db_path.suffix}"
    if destination.exists():
        raise HistoryMigrationError("backup path already exists")
    directory.mkdir(parents=True, exist_ok=True)
    # SQLite backup includes committed WAL pages; copy2 alone is unsafe here.
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def _report(total, proposed, checksum, balances_unchanged, applied):
    classifications = [item.classification if isinstance(item, MigrationProposal) else item for item in proposed]
    automatic = sum(
        item is not None and item.classification_status != "needs_review" for item in classifications
    )
    actions = {name: sum(getattr(item, "action", None) == name for item in proposed) for name in (
        "update", "preserve_existing", "remain_uncategorized", "failed", "no_op"
    )}
    review_examples = []
    for item in proposed:
        if getattr(item, "action", None) in {"preserve_existing", "remain_uncategorized", "failed"}:
            review_examples.append({"record": f"record-{len(review_examples) + 1}", "reason": item.reason})
        if len(review_examples) == 3:
            break
    report = {
        "total_transactions": total,
        "auto_classified": automatic,
        "needs_review": total - automatic,
        "amount_checksum_before": _decimal_text(checksum),
        "amount_checksum_after": _decimal_text(checksum),
        "balances_unchanged": balances_unchanged,
        "applied": applied,
        "updated": actions["update"],
        "preserved_existing": actions["preserve_existing"],
        "remained_uncategorized": actions["remain_uncategorized"],
        "unmatched": actions["preserve_existing"] + actions["remain_uncategorized"],
        "failed": actions["failed"],
        "no_op": actions["no_op"],
        "review_examples": review_examples,
    }
    report["total_records"] = total
    report["transaction_amount_before"] = report["amount_checksum_before"]
    report["transaction_amount_after"] = report["amount_checksum_after"]
    return report


def _checksum(values):
    return sum((_decimal(value) for value in values), Decimal("0"))


def _decimal(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError) as exc:
        raise HistoryMigrationError("invalid amount in source ledger") from exc


def _decimal_text(value):
    return format(value.quantize(Decimal("0.01")), "f")


def _csv_fields(headers):
    aliases = {
        "timestamp": ("时间", "日期", "timestamp", "time"),
        "account": ("账户", "account"),
        "amount": ("金额", "amount"),
        "category": ("分类", "category"),
        "description": ("备注", "描述", "description", "note"),
    }
    resolved = {}
    for key, candidates in aliases.items():
        resolved[key] = next((candidate for candidate in candidates if candidate in headers), None)
        if resolved[key] is None:
            raise HistoryMigrationError(f"CSV missing required {key} column")
    return resolved


def _csv_decimal(row, field):
    raw = (row.get(field) or "").replace(",", "").replace("¥", "").strip()
    return _decimal(raw)


def _classify_csv_row(row, fields):
    """Conservative stateless fallback for CSV-only preview mode."""
    text = " ".join((row.get(fields["description"]) or "", row.get(fields["category"]) or ""))
    amount = _csv_decimal(row, fields["amount"])
    if re.search(r"平账|还款至|信用卡.{0,12}还款", text):
        return _Preview("classified")
    if re.search(r"示例成员A|成员A|示例成员B|餐饮|交通|购物|工资|奖金|津贴|AI|服务器|VPS", text, re.I):
        return _Preview("classified")
    return _Preview("needs_review")


class _Preview:
    def __init__(self, classification_status):
        self.classification_status = classification_status


def main():
    parser = argparse.ArgumentParser(description="Reconciled wallet history normalization")
    parser.add_argument("db_path", nargs="?", type=Path)
    parser.add_argument("--db", dest="db_option", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--initialize-schema", action="store_true")
    args = parser.parse_args()
    if args.csv and (args.db_path or args.db_option or args.apply or args.initialize_schema):
        parser.error("--csv is read-only and cannot be combined with database options")
    db_path = args.db_option or args.db_path
    if args.csv:
        report = analyze_csv(args.csv)
    else:
        if db_path is None:
            parser.error("--db is required unless --csv is used")
        if args.initialize_schema:
            initialize_database_path(db_path)
            report = {"initialized_schema": True, "applied": False}
        else:
            report = normalize_history(db_path, apply=args.apply)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
