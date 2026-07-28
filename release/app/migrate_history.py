"""Read-only analysis and reconciled application of wallet v2 classifications."""

import argparse
import csv
import json
import re
import shutil
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from classifier import classify_transaction
from db import connect_database, initialize_database_path


class HistoryMigrationError(RuntimeError):
    """Raised when a migration cannot preserve its financial invariants."""


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
            for transaction_id, classification in proposed:
                conn.execute(
                    "UPDATE transactions SET category_id = ?, transaction_kind = ?, excluded_from_stats = ?, "
                    "classification_status = ? WHERE id = ?",
                    (classification.category_id, classification.transaction_kind,
                     int(classification.excluded_from_stats), classification.classification_status, transaction_id),
                )
                conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (transaction_id,))
                conn.executemany(
                    "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    ((transaction_id, tag_id) for tag_id in classification.tag_ids),
                )
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
    return [
        (row["id"], classify_transaction(
            conn, row["description"], row["category"], row["amount"],
            source_account={"name": row["account_name"], "type": row["account_type"]},
        )) for row in rows
    ]


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
    classifications = [item[1] if isinstance(item, tuple) else item for item in proposed]
    automatic = sum(item.classification_status != "needs_review" for item in classifications)
    report = {
        "total_transactions": total,
        "auto_classified": automatic,
        "needs_review": total - automatic,
        "amount_checksum_before": _decimal_text(checksum),
        "amount_checksum_after": _decimal_text(checksum),
        "balances_unchanged": balances_unchanged,
        "applied": applied,
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
