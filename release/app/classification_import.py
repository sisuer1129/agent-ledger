"""Safe, classification-only CSV import for the private wallet."""

import argparse
import csv
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from db import connect_database
from repositories import TRANSACTION_KINDS


ID_FIELD = "交易 ID"
REQUIRED_FIELDS = (ID_FIELD, "时间", "账户", "金额", "一级分类", "二级分类", "标签", "交易性质", "是否计入统计", "备注")
LEGACY_REQUIRED_FIELDS = REQUIRED_FIELDS[1:]
REVIEW_OVERRIDE_FIELDS = ("类型", "一级分类", "二级分类", "标签", "交易性质", "是否计入统计", "备注")
TAG_NAME_ALIASES = {"示例成员A": "大宝", "示例成员B": "二宝"}


class ClassificationImportError(RuntimeError):
    pass


def analyze_classification_csv(db_path, csv_path):
    rows = _read_rows(csv_path, REQUIRED_FIELDS)
    conn = _readonly_connection(db_path)
    try:
        updates = _prepare_updates(conn, rows)
        return _report(conn, updates, applied=False)
    finally:
        conn.close()


def apply_classification_csv(db_path, csv_path, backup_dir=None):
    rows = _read_rows(csv_path, REQUIRED_FIELDS)
    conn = _readonly_connection(db_path)
    try:
        _prepare_updates(conn, rows)
    finally:
        conn.close()
    backup_path = _create_backup(db_path, backup_dir)
    conn = connect_database(db_path)
    try:
        updates = _prepare_updates(conn, rows)
        before = _financial_snapshot(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for update in updates:
                conn.execute(
                    "UPDATE transactions SET category_id = ?, transaction_kind = ?, excluded_from_stats = ?, "
                    "classification_status = 'manual' WHERE id = ?",
                    (update["category_id"], update["transaction_kind"], update["excluded_from_stats"], update["transaction_id"]),
                )
                conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (update["transaction_id"],))
                conn.executemany(
                    "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                    ((update["transaction_id"], tag_id) for tag_id in update["tag_ids"]),
                )
            _assert_financial_snapshot(conn, before)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {**_report(conn, updates, applied=True), "backup_path": str(backup_path)}
    finally:
        conn.close()


def enrich_legacy_csv(db_path, csv_path, output_path):
    rows = _read_rows(csv_path, LEGACY_REQUIRED_FIELDS)
    conn = _readonly_connection(db_path)
    try:
        database_rows = conn.execute(
            "SELECT t.id, t.timestamp, t.amount, t.description, a.name AS account_name "
            "FROM transactions t JOIN accounts a ON a.id = t.account_id"
        ).fetchall()
    finally:
        conn.close()
    source_groups = _legacy_groups(rows, is_database=False)
    database_groups = _legacy_groups(database_rows, is_database=True)
    if set(source_groups) != set(database_groups):
        raise ClassificationImportError("legacy CSV does not match the current database transaction set")
    enriched = []
    for key in source_groups:
        source_rows, database_rows_for_key = source_groups[key], database_groups[key]
        if len(source_rows) != len(database_rows_for_key):
            raise ClassificationImportError("legacy CSV has an ambiguous duplicate transaction group")
        for source_row, database_row in zip(source_rows, sorted(database_rows_for_key, key=lambda row: row["id"])):
            enriched.append({ID_FIELD: database_row["id"], **source_row})
    output_path = Path(output_path)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(ID_FIELD,) + tuple(rows[0].keys()) if rows else REQUIRED_FIELDS)
        writer.writeheader()
        writer.writerows(enriched)
    return {"matched": len(enriched), "output_path": str(output_path), "applied": False}


def merge_review_csv(base_csv_path, review_csv_path, output_path):
    """Overlay manually reviewed rows on a full export while preserving IDs."""
    base_rows = _read_rows(base_csv_path, REQUIRED_FIELDS)
    review_rows = _read_rows(review_csv_path, LEGACY_REQUIRED_FIELDS)
    exact_groups = _review_groups(base_rows, include_description=True)
    fallback_groups = _review_groups(base_rows, include_description=False)
    used_ids = set()
    fallback_matches = 0

    for review in review_rows:
        candidates = [row for row in exact_groups[_review_key(review, include_description=True)] if row[ID_FIELD] not in used_ids]
        if len(candidates) != 1:
            candidates = [row for row in fallback_groups[_review_key(review, include_description=False)] if row[ID_FIELD] not in used_ids]
            fallback_matches += 1
        if len(candidates) != 1:
            raise ClassificationImportError("manual review row cannot be uniquely matched to the ID export")
        target = candidates[0]
        used_ids.add(target[ID_FIELD])
        for field in REVIEW_OVERRIDE_FIELDS:
            target[field] = review[field]
        target["备注"] = _clean_person_marker(target["备注"])

    output_path = Path(output_path)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_rows[0].keys())
        writer.writeheader()
        writer.writerows(base_rows)
    return {"base_rows": len(base_rows), "review_rows": len(review_rows), "overrides": len(used_ids), "fallback_matches": fallback_matches}


def clean_person_marker_notes(db_path, backup_dir=None, apply=False):
    conn = _readonly_connection(db_path)
    try:
        candidates = [
            (row["id"], row["description"], _clean_person_marker(row["description"]))
            for row in conn.execute("SELECT id, description FROM transactions")
        ]
        changes = [(transaction_id, description) for transaction_id, original, description in candidates if original != description]
    finally:
        conn.close()
    if not apply:
        return {"applied": False, "matched": len(changes)}
    backup_path = _create_backup(db_path, backup_dir)
    conn = connect_database(db_path)
    try:
        before = _financial_snapshot(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany("UPDATE transactions SET description = ? WHERE id = ?", ((description, transaction_id) for transaction_id, description in changes))
            _assert_financial_snapshot(conn, before)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {"applied": True, "matched": len(changes), "backup_path": str(backup_path)}
    finally:
        conn.close()


def _prepare_updates(conn, rows):
    seen_ids = set()
    updates = []
    for row in rows:
        transaction_id = row[ID_FIELD].strip()
        if not transaction_id:
            raise ClassificationImportError("transaction ID is required")
        if transaction_id in seen_ids:
            raise ClassificationImportError("CSV contains duplicate transaction IDs")
        seen_ids.add(transaction_id)
        transaction = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if transaction is None:
            raise ClassificationImportError("CSV references a transaction ID that does not exist")
        category_id = _resolve_category(conn, row)
        tag_ids = _resolve_tags(conn, row["标签"])
        transaction_kind = row["交易性质"].strip()
        if transaction_kind not in TRANSACTION_KINDS:
            raise ClassificationImportError("invalid transaction kind: " + transaction_kind)
        excluded_from_stats = _resolve_included(row["是否计入统计"])
        updates.append({
            "transaction_id": transaction_id,
            "category_id": category_id,
            "tag_ids": tag_ids,
            "transaction_kind": transaction_kind,
            "excluded_from_stats": excluded_from_stats,
            "changed": (
                transaction["category_id"] != category_id
                or transaction["transaction_kind"] != transaction_kind
                or int(transaction["excluded_from_stats"]) != excluded_from_stats
                or _transaction_tag_ids(conn, transaction_id) != tag_ids
            ),
        })
    return updates


def _resolve_category(conn, row):
    return resolve_category(conn, row["一级分类"], row["二级分类"])


def resolve_category(conn, primary, secondary):
    """Resolve only the active seed category tree used by the wallet."""
    primary = (primary or "").strip()
    secondary = (secondary or "").strip()
    if not primary and not secondary:
        return None
    if secondary:
        category = conn.execute(
            "SELECT child.id, child.parent_id FROM categories child "
            "JOIN categories parent ON parent.id = child.parent_id "
            "WHERE child.name = ? AND parent.name = ? "
            "AND child.is_active = 1 AND parent.is_active = 1",
            (secondary, primary),
        ).fetchone()
    else:
        category = conn.execute(
            "SELECT id, parent_id FROM categories "
            "WHERE name = ? AND parent_id IS NULL AND is_active = 1",
            (primary,),
        ).fetchone()
    if category is None:
        raise ClassificationImportError("unknown or inactive category: " + primary + "/" + secondary)
    return category["id"]


def _resolve_tags(conn, raw):
    names = [name.strip() for name in raw.split("、") if name.strip()]
    if len(names) != len(set(names)):
        raise ClassificationImportError("CSV contains duplicate tags for one transaction")
    tag_ids = []
    for name in names:
        tag = conn.execute(
            "SELECT id FROM tags WHERE name = ? AND is_active = 1",
            (TAG_NAME_ALIASES.get(name, name),),
        ).fetchone()
        if tag is None:
            raise ClassificationImportError("unknown or inactive tag: " + name)
        tag_ids.append(tag["id"])
    if len(tag_ids) != len(set(tag_ids)):
        raise ClassificationImportError("CSV contains duplicate tags for one transaction")
    return sorted(tag_ids)


def _resolve_included(raw):
    value = raw.strip()
    if value == "是":
        return 0
    if value == "否":
        return 1
    raise ClassificationImportError("是否计入统计 must be 是 or 否")


def _transaction_tag_ids(conn, transaction_id):
    return [row["tag_id"] for row in conn.execute(
        "SELECT tag_id FROM transaction_tags WHERE transaction_id = ? ORDER BY tag_id", (transaction_id,)
    )]


def _report(conn, updates, applied):
    snapshot = _financial_snapshot(conn)
    return {
        "total_rows": len(updates), "matched": len(updates),
        "changed": sum(update["changed"] for update in updates), "applied": applied,
        "transaction_count": snapshot["transaction_count"], "amount_checksum": snapshot["amount_checksum"],
        "balances_unchanged": True,
    }


def _financial_snapshot(conn):
    amount = sum((_decimal(row["amount"]) for row in conn.execute("SELECT amount FROM transactions")), Decimal("0"))
    balances = {row["id"]: _decimal(row["current_balance"]) for row in conn.execute("SELECT id, current_balance FROM accounts")}
    return {"transaction_count": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], "amount_checksum": _decimal_text(amount), "balances": balances}


def _assert_financial_snapshot(conn, before):
    if _financial_snapshot(conn) != before:
        raise ClassificationImportError("classification import changed financial records or balances")


def _read_rows(csv_path, required_fields):
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or any(field not in reader.fieldnames for field in required_fields):
            raise ClassificationImportError("CSV does not contain the required wallet export columns")
        rows = list(reader)
    if not rows:
        raise ClassificationImportError("CSV has no transaction rows")
    return rows


def _legacy_groups(rows, is_database):
    groups = defaultdict(list)
    for row in rows:
        key = (
            str(row["timestamp"] if is_database else row["时间"]),
            str(row["account_name"] if is_database else row["账户"]),
            _decimal_text(_decimal(row["amount"] if is_database else row["金额"])),
            str(row["description"] if is_database else row["备注"]),
        )
        groups[key].append(row)
    return groups


def _review_groups(rows, include_description):
    groups = defaultdict(list)
    for row in rows:
        groups[_review_key(row, include_description)].append(row)
    return groups


def _review_key(row, include_description):
    key = (
        _normalise_timestamp(row["时间"]),
        str(row["账户"]),
        _decimal_text(_decimal(row["金额"])),
    )
    return key + ((str(row["备注"]),) if include_description else ())


def _normalise_timestamp(value):
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    # Excel serial dates can round fractional seconds.  The wallet has no
    # same-second duplicates for this import, so matching at second precision
    # is the stable, reviewable key across CSV and XLSX exports.
    parsed = parsed.replace(microsecond=0)
    return parsed.isoformat() if parsed.tzinfo else parsed.isoformat() + "+00:00"


def _clean_person_marker(description):
    text = str(description or "")
    text = re.sub(r"\s*[|｜—–\-/,，;；:：]\s*示例成员A标记\s*", " ", text)
    text = re.sub(r"\s*示例成员A标记\s*[|｜—–\-/,，;；:：]\s*", " ", text)
    text = re.sub(r"\s*[（(]\s*示例成员A标记\s*[)）]\s*", " ", text)
    text = re.sub(r"\s*示例成员A标记\s*", " ", text)
    text = re.sub(r"[（(]\s*[)）]", "", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -—–|｜/,，;；:：")


def _readonly_connection(db_path):
    conn = sqlite3.connect("file:" + str(Path(db_path).resolve()) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _create_backup(db_path, backup_dir=None):
    source_path = Path(db_path)
    directory = Path(backup_dir) if backup_dir else source_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (source_path.stem + ".pre-classification-import-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + source_path.suffix)
    source, target = sqlite3.connect(str(source_path)), sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def _decimal(value):
    try:
        return Decimal(str(value).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError) as error:
        raise ClassificationImportError("CSV has an invalid amount") from error


def _decimal_text(value):
    return format(value.quantize(Decimal("0.01")), "f")


def main():
    parser = argparse.ArgumentParser(description="Safely import wallet classifications from CSV")
    parser.add_argument("mode", choices=("analyze", "enrich-legacy", "apply", "merge-review", "clean-person-marker-notes"))
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--backup-dir")
    parser.add_argument("--apply", action="store_true", help="apply a preview-only maintenance command")
    args = parser.parse_args()
    if args.mode == "merge-review":
        if len(args.paths) != 3:
            parser.error("merge-review requires BASE_ID_CSV REVIEW_CSV OUTPUT_CSV")
        result = merge_review_csv(*args.paths)
    elif args.mode == "clean-person-marker-notes":
        if len(args.paths) != 1:
            parser.error("clean-person-marker-notes requires DB_PATH")
        result = clean_person_marker_notes(args.paths[0], args.backup_dir, apply=args.apply)
    elif len(args.paths) < 2:
        parser.error(args.mode + " requires DB_PATH CSV_PATH")
    elif args.mode == "analyze":
        result = analyze_classification_csv(args.paths[0], args.paths[1])
    elif args.mode == "enrich-legacy":
        if len(args.paths) != 3:
            parser.error("enrich-legacy requires OUTPUT_PATH")
        result = enrich_legacy_csv(*args.paths)
    else:
        result = apply_classification_csv(args.paths[0], args.paths[1], args.backup_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
