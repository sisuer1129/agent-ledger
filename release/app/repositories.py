"""SQLite repositories for wallet records.

The repository is deliberately independent of Flask so routes and migration
tools can share the same validation and atomic balance bookkeeping.
"""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import math
import sqlite3
from uuid import uuid4


class RepositoryError(Exception):
    """Base error for repository callers that need stable error handling."""


class ValidationError(RepositoryError):
    pass


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


TRANSACTION_KINDS = frozenset(
    {"expense", "income", "refund", "transfer", "credit_repayment", "topup_withdrawal", "balance_adjustment"}
)
CLASSIFICATION_STATUSES = frozenset({"unreviewed", "classified", "needs_review", "manual"})
LEGACY_CLASSIFICATION_STATUS_ALIASES = {"auto_classified": "classified", "migrated": "classified"}
ACCOUNT_TYPES = frozenset({
    "debit", "credit", "cash", "stored_value", "prepaid", "other",
    # Retained for existing ledgers created by earlier account-type profiles.
    "shopping_card", "wechat",
})
SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
ACCOUNT_FIELDS = frozenset(
    {
        "name",
        "type",
        "icon",
        "notes",
        "monthly_budget",
        "budget_period",
        "credit_limit",
        "statement_day",
        "due_day",
        "due_month_offset",
    }
)
TRANSACTION_FIELDS = frozenset(
    {
        "account_id",
        "timestamp",
        "amount",
        "description",
        "category",
        "category_id",
        "transaction_kind",
        "excluded_from_stats",
        "classification_status",
        "tag_ids",
    }
)


@contextmanager
def _atomic(conn):
    """Start an explicit transaction, or a savepoint for an existing one."""
    if conn.in_transaction:
        savepoint = "wallet_repository_operation"
        conn.execute("SAVEPOINT " + savepoint)
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
            conn.execute("RELEASE SAVEPOINT " + savepoint)
            raise
        else:
            conn.execute("RELEASE SAVEPOINT " + savepoint)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def create_transaction(conn, payload):
    """Create one transaction and apply its amount to the active account."""
    data = _validate_create_payload(conn, payload)
    with _atomic(conn):
        # Revalidate inside the write transaction so a concurrent change cannot
        # make a validated account/category/tag stale before balances change.
        account = _require_active_account(conn, data["account_id"])
        _validate_category(conn, data["category_id"])
        _validate_tag_ids(conn, data["tag_ids"])
        balance_after = _computed_account_balance(account["current_balance"], data["amount"])
        conn.execute(
            "UPDATE accounts SET current_balance = ? WHERE id = ?",
            (balance_after, data["account_id"]),
        )
        try:
            conn.execute(
                "INSERT INTO transactions "
                "(id, account_id, timestamp, amount, description, category, balance_after, "
                "category_id, transaction_kind, excluded_from_stats, classification_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["id"], data["account_id"], data["timestamp"], data["amount"],
                    data["description"], data["category"], balance_after, data["category_id"],
                    data["transaction_kind"], data["excluded_from_stats"],
                    data["classification_status"],
                ),
            )
        except sqlite3.IntegrityError as error:
            if "transactions.id" in str(error).lower() or "unique" in str(error).lower():
                raise ConflictError("transaction id already exists") from error
            raise
        _insert_transaction_tags(conn, data["id"], data["tag_ids"])
        return _transaction_dict(conn, data["id"])


def create_credit_card_repayment(conn, payload):
    """Record one credit-card repayment as two linked, statistics-excluded entries.

    The source account decreases and the credit-card balance increases by the
    same amount.  Nested ``create_transaction`` calls use savepoints, so both
    entries and both balance changes are committed or rolled back together.
    """
    data = _validate_credit_card_repayment_payload(payload)
    with _atomic(conn):
        source_account = _require_active_account(conn, data["source_account_id"])
        credit_account = _require_active_account(conn, data["credit_account_id"])
        if source_account["id"] == credit_account["id"]:
            raise ValidationError("credit_account_id must be different from source_account_id")
        if credit_account["type"] != "credit":
            raise ValidationError("credit_account_id must reference a credit account")

        source_description = data["description"] or ("还款至" + credit_account["name"])
        credit_description = data["description"] or (source_account["name"] + "信用卡还款")
        repayment_group_id = str(uuid4())
        source_transaction = create_transaction(
            conn,
            {
                "account_id": source_account["id"],
                "timestamp": data["timestamp"],
                "amount": -data["amount"],
                "description": source_description,
                "category_id": None,
                "transaction_kind": "credit_repayment",
                "excluded_from_stats": True,
                "classification_status": "classified",
                "tag_ids": [],
            },
        )
        credit_transaction = create_transaction(
            conn,
            {
                "account_id": credit_account["id"],
                "timestamp": data["timestamp"],
                "amount": data["amount"],
                "description": credit_description,
                "category_id": None,
                "transaction_kind": "credit_repayment",
                "excluded_from_stats": True,
                "classification_status": "classified",
                "tag_ids": [],
            },
        )
        conn.execute(
            "UPDATE transactions SET repayment_group_id = ? WHERE id IN (?, ?)",
            (repayment_group_id, source_transaction["id"], credit_transaction["id"]),
        )
        source_transaction = _transaction_dict(conn, source_transaction["id"])
        credit_transaction = _transaction_dict(conn, credit_transaction["id"])
    return {
        "source_transaction": source_transaction,
        "credit_transaction": credit_transaction,
    }


def update_transaction(conn, transaction_id, payload):
    """Atomically replace selected transaction fields and reconcile balances."""
    _require_mapping(payload)
    unknown = set(payload) - TRANSACTION_FIELDS
    if unknown:
        raise ValidationError("unsupported transaction fields: " + ", ".join(sorted(unknown)))

    with _atomic(conn):
        old = _require_transaction(conn, transaction_id)
        if old["transaction_kind"] == "credit_repayment":
            raise ValidationError("credit repayment entries must be reversed as a group")
        merged = {column: old[column] for column in TRANSACTION_FIELDS - {"tag_ids"}}
        merged["tag_ids"] = _transaction_tag_ids(conn, transaction_id)
        merged.update(payload)
        data = _validate_transaction_values(conn, merged, transaction_id=transaction_id)

        old_account = _require_account(conn, old["account_id"])
        new_account = _require_active_account(conn, data["account_id"])
        _validate_category(conn, data["category_id"])
        _validate_tag_ids(conn, data["tag_ids"])

        old_amount = float(old["amount"])
        new_amount = data["amount"]
        if old["account_id"] == data["account_id"]:
            balance_after = _computed_account_balance(
                old_account["current_balance"], -old_amount, new_amount
            )
            conn.execute(
                "UPDATE accounts SET current_balance = ? WHERE id = ?",
                (balance_after, data["account_id"]),
            )
        else:
            source_balance = _computed_account_balance(old_account["current_balance"], -old_amount)
            destination_balance = _computed_account_balance(
                new_account["current_balance"], new_amount
            )
            conn.execute(
                "UPDATE accounts SET current_balance = ? WHERE id = ?",
                (source_balance, old["account_id"]),
            )
            conn.execute(
                "UPDATE accounts SET current_balance = ? WHERE id = ?",
                (destination_balance, data["account_id"]),
            )
            balance_after = destination_balance

        conn.execute(
            "UPDATE transactions SET account_id = ?, timestamp = ?, amount = ?, description = ?, "
            "category = ?, balance_after = ?, category_id = ?, transaction_kind = ?, "
            "excluded_from_stats = ?, classification_status = ? WHERE id = ?",
            (
                data["account_id"], data["timestamp"], data["amount"], data["description"],
                data["category"], balance_after, data["category_id"], data["transaction_kind"],
                data["excluded_from_stats"], data["classification_status"], transaction_id,
            ),
        )
        _replace_transaction_tags_validated(conn, transaction_id, data["tag_ids"])
        return _transaction_dict(conn, transaction_id)


def delete_transaction(conn, transaction_id):
    """Delete a transaction and reverse its effect on the account balance."""
    with _atomic(conn):
        transaction = _require_transaction(conn, transaction_id)
        if transaction["transaction_kind"] == "credit_repayment":
            raise ValidationError("credit repayment entries must be reversed as a group")
        account = _require_account(conn, transaction["account_id"])
        restored_balance = _computed_account_balance(
            account["current_balance"], -float(transaction["amount"])
        )
        conn.execute(
            "UPDATE accounts SET current_balance = ? WHERE id = ?",
            (restored_balance, transaction["account_id"]),
        )
        conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))


def delete_credit_card_repayment(conn, repayment_group_id):
    """Reverse both entries of one newly linked credit-card repayment."""
    if not isinstance(repayment_group_id, str) or not repayment_group_id.strip():
        raise ValidationError("repayment_group_id is required")
    with _atomic(conn):
        rows = conn.execute(
            "SELECT id, account_id, amount, transaction_kind FROM transactions "
            "WHERE repayment_group_id = ? ORDER BY id",
            (repayment_group_id,),
        ).fetchall()
        if not rows:
            raise NotFoundError("credit repayment not found")
        if len(rows) != 2 or any(row["transaction_kind"] != "credit_repayment" for row in rows):
            raise ConflictError("credit repayment group is incomplete")
        for row in rows:
            account = _require_account(conn, row["account_id"])
            restored_balance = _computed_account_balance(
                account["current_balance"], -float(row["amount"])
            )
            conn.execute(
                "UPDATE accounts SET current_balance = ? WHERE id = ?",
                (restored_balance, row["account_id"]),
            )
        conn.execute("DELETE FROM transactions WHERE repayment_group_id = ?", (repayment_group_id,))


def replace_transaction_tags(conn, transaction_id, tag_ids):
    """Replace tag links only after every requested tag has been validated."""
    normalized = _normalise_tag_ids(tag_ids)
    with _atomic(conn):
        _require_transaction(conn, transaction_id)
        _validate_tag_ids(conn, normalized)
        _replace_transaction_tags_validated(conn, transaction_id, normalized)


def update_account(conn, account_id, payload):
    """Update account profile/settings only; balances are repository-controlled."""
    _require_mapping(payload)
    unknown = set(payload) - ACCOUNT_FIELDS
    if unknown:
        raise ValidationError("unsupported account fields: " + ", ".join(sorted(unknown)))
    if not payload:
        raise ValidationError("at least one account field is required")

    with _atomic(conn):
        current = _require_account(conn, account_id, missing_error=NotFoundError)
        values = {field: current[field] for field in ACCOUNT_FIELDS}
        values.update(payload)
        _validate_account_values(values)
        fields = sorted(payload)
        assignments = ", ".join(field + " = ?" for field in fields)
        try:
            conn.execute(
                "UPDATE accounts SET " + assignments + " WHERE id = ?",
                tuple(values[field] for field in fields) + (account_id,),
            )
        except sqlite3.IntegrityError as error:
            if "unique" in str(error).lower():
                raise ConflictError("account name already exists") from error
            raise
        return _account_dict(conn, account_id)


def deactivate_account(conn, account_id):
    """Soft deactivate an account without altering its history."""
    with _atomic(conn):
        _require_account(conn, account_id, missing_error=NotFoundError)
        conn.execute("UPDATE accounts SET is_active = 0 WHERE id = ?", (account_id,))
        return _account_dict(conn, account_id)


def list_transactions(conn, filters=None):
    """Return ledger rows with account and deterministic tag metadata.

    SQL structure is assembled only from fixed clauses. Every caller supplied
    value, including pagination values, is passed as a SQLite bound parameter.
    Transactions are stored as UTC ISO timestamps; aware filter timestamps are
    normalized to the same format. Date-only ``start`` begins that UTC day and
    date-only ``end`` includes the whole UTC day.
    """
    filters = filters or {}
    _require_mapping(filters)
    limit = _pagination_value(filters.get("limit", 50), default=50, maximum=1000)
    offset = _pagination_value(
        filters.get("offset", 0), default=0, maximum=SQLITE_INTEGER_MAX, clamp=False
    )
    where, params = _transaction_where(filters)
    rows = conn.execute(
        "SELECT t.*, a.name AS account_name, c.name AS category_name, parent.name AS category_parent_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "LEFT JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories parent ON parent.id = c.parent_id" + where + " "
        "ORDER BY t.timestamp DESC, t.id ASC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["category_primary_name"] = row["category_parent_name"] or row["category_name"]
        item["category_secondary_name"] = row["category_name"] if row["category_parent_name"] else None
        tags = [
            dict(tag)
            for tag in conn.execute(
                "SELECT tg.id, tg.name, tg.group_name FROM transaction_tags tt "
                "JOIN tags tg ON tg.id = tt.tag_id WHERE tt.transaction_id = ? "
                "ORDER BY tg.group_name ASC, tg.name ASC, tg.id ASC",
                (row["id"],),
            )
        ]
        item["tags"] = tags
        item["tag_ids"] = [tag["id"] for tag in tags]
        result.append(item)
    return result


def _transaction_where(filters):
    """Shared signed-amount and reference filters for rows and counts."""
    clauses, params = [], []
    for key, column, operator in (
        ("start", "t.timestamp", ">="),
        ("end", "t.timestamp", "<="),
        ("account_id", "t.account_id", "="),
        ("category_id", "t.category_id", "="),
        ("transaction_kind", "t.transaction_kind", "="),
        ("min_amount", "t.amount", ">="),
        ("max_amount", "t.amount", "<="),
    ):
        if key not in filters or filters[key] is None:
            continue
        value = filters[key]
        if key in {"start", "end"}:
            operator, value = _timestamp_filter(value, key, is_end=(key == "end"))
        elif key in {"min_amount", "max_amount"}:
            value = _finite_number(value, key)
        clauses.append(column + " " + operator + " ?")
        params.append(value)
    if filters.get("tag_id") is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM transaction_tags filter_tt "
            "WHERE filter_tt.transaction_id = t.id AND filter_tt.tag_id = ?)"
        )
        params.append(filters["tag_id"])
    if filters.get("query") is not None:
        clauses.append("(t.description LIKE ? ESCAPE '\\' OR t.category LIKE ? ESCAPE '\\')")
        escaped = _escape_like(str(filters["query"]))
        params.extend(("%" + escaped + "%", "%" + escaped + "%"))

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def count_transactions(conn, filters=None):
    filters = filters or {}
    _require_mapping(filters)
    where, params = _transaction_where(filters)
    return conn.execute(
        "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id" + where,
        tuple(params),
    ).fetchone()[0]


def transaction_pagination(filters, total):
    limit = _pagination_value(filters.get("limit", 50), default=50, maximum=1000)
    offset = _pagination_value(filters.get("offset", 0), default=0, maximum=SQLITE_INTEGER_MAX, clamp=False)
    has_more = limit > 0 and offset + limit < total
    return {"total": total, "limit": limit, "offset": offset, "has_more": has_more,
            "next_offset": offset + limit if has_more else None}


def _validate_create_payload(conn, payload):
    _require_mapping(payload)
    unknown = set(payload) - (TRANSACTION_FIELDS | {"id"})
    if unknown:
        raise ValidationError("unsupported transaction fields: " + ", ".join(sorted(unknown)))
    required = {"account_id", "timestamp", "amount", "description"}
    missing = required - set(payload)
    if missing:
        raise ValidationError("missing transaction fields: " + ", ".join(sorted(missing)))
    data = {
        "id": payload.get("id") or str(uuid4()),
        "account_id": payload["account_id"],
        "timestamp": payload["timestamp"],
        "amount": payload["amount"],
        "description": payload["description"],
        "category": payload.get("category"),
        "category_id": payload.get("category_id"),
        "transaction_kind": payload.get("transaction_kind"),
        "excluded_from_stats": payload.get("excluded_from_stats", False),
        "classification_status": payload.get("classification_status", "unreviewed"),
        "tag_ids": payload.get("tag_ids", []),
    }
    if not isinstance(data["id"], str) or not data["id"].strip():
        raise ValidationError("transaction id must be a non-empty string")
    return _validate_transaction_values(conn, data)


def _validate_credit_card_repayment_payload(payload):
    _require_mapping(payload)
    allowed = {"source_account_id", "credit_account_id", "amount", "timestamp", "description"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValidationError("unsupported credit card repayment fields: " + ", ".join(sorted(unknown)))
    required = {"source_account_id", "credit_account_id", "amount", "timestamp"}
    missing = required - set(payload)
    if missing:
        raise ValidationError("missing credit card repayment fields: " + ", ".join(sorted(missing)))
    for field in ("source_account_id", "credit_account_id"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValidationError(field + " is required")
    if "description" in payload and payload["description"] is not None and (
        not isinstance(payload["description"], str) or not payload["description"].strip()
    ):
        raise ValidationError("description must be a non-empty string or null")
    return {
        "source_account_id": payload["source_account_id"],
        "credit_account_id": payload["credit_account_id"],
        "amount": _finite_number(payload["amount"], "amount", nonzero=True, minimum=0),
        "timestamp": _iso_timestamp(payload["timestamp"]),
        "description": payload.get("description"),
    }


def _validate_transaction_values(conn, data, transaction_id=None):
    result = dict(data)
    if not isinstance(result["account_id"], str) or not result["account_id"].strip():
        raise ValidationError("account_id is required")
    result["timestamp"] = _iso_timestamp(result["timestamp"])
    result["amount"] = _finite_number(result["amount"], "amount", nonzero=True)
    if not isinstance(result["description"], str) or not result["description"].strip():
        raise ValidationError("description is required")
    if result["category"] is not None and not isinstance(result["category"], str):
        raise ValidationError("category must be a string or null")
    if result["category_id"] is not None and (
        not isinstance(result["category_id"], str) or not result["category_id"].strip()
    ):
        raise ValidationError("category_id must be a non-empty string or null")
    if result["transaction_kind"] is None:
        result["transaction_kind"] = "income" if result["amount"] > 0 else "expense"
    if result["transaction_kind"] not in TRANSACTION_KINDS:
        raise ValidationError("invalid transaction_kind")
    result["excluded_from_stats"] = _boolean(result["excluded_from_stats"], "excluded_from_stats")
    result["classification_status"] = LEGACY_CLASSIFICATION_STATUS_ALIASES.get(
        result["classification_status"], result["classification_status"]
    )
    if result["classification_status"] not in CLASSIFICATION_STATUSES:
        raise ValidationError("invalid classification_status")
    result["tag_ids"] = _normalise_tag_ids(result["tag_ids"])
    return result


def _validate_category(conn, category_id):
    if category_id is None:
        return
    row = conn.execute(
        "SELECT id FROM categories WHERE id = ? AND is_active = 1", (category_id,)
    ).fetchone()
    if row is None:
        raise ValidationError("category does not exist or is inactive")


def _validate_tag_ids(conn, tag_ids):
    if not tag_ids:
        return
    placeholders = ", ".join("?" for _tag in tag_ids)
    rows = conn.execute(
        "SELECT id FROM tags WHERE is_active = 1 AND id IN (" + placeholders + ")", tuple(tag_ids)
    ).fetchall()
    if {row["id"] for row in rows} != set(tag_ids):
        raise ValidationError("one or more tags do not exist or are inactive")


def _insert_transaction_tags(conn, transaction_id, tag_ids):
    conn.executemany(
        "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
        ((transaction_id, tag_id) for tag_id in tag_ids),
    )


def _replace_transaction_tags_validated(conn, transaction_id, tag_ids):
    conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (transaction_id,))
    _insert_transaction_tags(conn, transaction_id, tag_ids)


def _transaction_tag_ids(conn, transaction_id):
    return [
        row["tag_id"]
        for row in conn.execute(
            "SELECT tag_id FROM transaction_tags WHERE transaction_id = ? ORDER BY tag_id", (transaction_id,)
        )
    ]


def _transaction_dict(conn, transaction_id):
    return dict(_require_transaction(conn, transaction_id))


def _account_dict(conn, account_id):
    return dict(_require_account(conn, account_id))


def _require_transaction(conn, transaction_id):
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if row is None:
        raise NotFoundError("transaction not found")
    return row


def _require_account(conn, account_id, missing_error=ValidationError):
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise missing_error("account does not exist")
    return row


def _require_active_account(conn, account_id):
    row = _require_account(conn, account_id)
    if not row["is_active"]:
        raise ValidationError("account is inactive")
    return row


def _validate_account_values(values):
    if not isinstance(values["name"], str) or not values["name"].strip():
        raise ValidationError("name is required")
    if values["type"] not in ACCOUNT_TYPES:
        raise ValidationError("invalid account type")
    if values["monthly_budget"] is not None:
        _finite_number(values["monthly_budget"], "monthly_budget", minimum=0)
    if values["credit_limit"] is not None:
        _finite_number(values["credit_limit"], "credit_limit", minimum=0)
    if values["budget_period"] not in {"calendar_month", "billing_cycle"}:
        raise ValidationError("invalid budget_period")
    if values["budget_period"] == "billing_cycle" and values["type"] != "credit":
        raise ValidationError("billing_cycle requires a credit account")
    for field in ("statement_day", "due_day"):
        if values[field] is not None and (
            isinstance(values[field], bool) or not isinstance(values[field], int) or not 1 <= values[field] <= 28
        ):
            raise ValidationError(field + " must be between 1 and 28")
    if values["due_month_offset"] is not None and (
        isinstance(values["due_month_offset"], bool)
        or not isinstance(values["due_month_offset"], int)
        or values["due_month_offset"] not in {0, 1}
    ):
        raise ValidationError("due_month_offset must be 0 or 1")
    for field in ("icon", "notes"):
        if values[field] is not None and not isinstance(values[field], str):
            raise ValidationError(field + " must be a string or null")


def _require_mapping(value):
    if not isinstance(value, dict):
        raise ValidationError("payload must be an object")


def _finite_number(value, field, nonzero=False, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(field + " must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValidationError(field + " must be a finite number") from error
    if not math.isfinite(number) or (nonzero and number == 0) or (minimum is not None and number < minimum):
        raise ValidationError("invalid " + field)
    return number


def _boolean(value, field):
    if isinstance(value, bool):
        return int(value)
    if value in {0, 1}:
        return int(value)
    raise ValidationError(field + " must be a boolean")


def _iso_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValidationError("timestamp must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ValidationError("timestamp must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("timestamp must include a timezone offset")
    try:
        return parsed.astimezone(timezone.utc).isoformat()
    except OverflowError as error:
        raise ValidationError("timestamp cannot be represented in UTC") from error


def _timestamp_filter(value, field, is_end):
    """Normalize an aware timestamp or an inclusive UTC date-only boundary."""
    if isinstance(value, str) and len(value) == 10:
        try:
            day = date.fromisoformat(value)
        except ValueError as error:
            raise ValidationError(field + " must be an ISO date or timestamp") from error
        try:
            boundary = datetime.combine(
                day + timedelta(days=1) if is_end else day,
                time.min,
                tzinfo=timezone.utc,
            )
        except OverflowError as error:
            raise ValidationError(field + " date boundary is out of range") from error
        return ("<" if is_end else ">="), boundary.isoformat()
    return ("<=" if is_end else ">="), _iso_timestamp(value)


def _normalise_tag_ids(tag_ids):
    if not isinstance(tag_ids, (list, tuple)):
        raise ValidationError("tag_ids must be a list")
    normalized = []
    for tag_id in tag_ids:
        if not isinstance(tag_id, str) or not tag_id.strip():
            raise ValidationError("tag ids must be non-empty strings")
        if tag_id not in normalized:
            normalized.append(tag_id)
    return normalized


def _pagination_value(value, default, maximum, clamp=True):
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValidationError("pagination value must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValidationError("pagination value must be an integer") from error
    if str(value).strip() not in {str(number), "+" + str(number)} and not isinstance(value, int):
        raise ValidationError("pagination value must be an integer")
    if number < 0:
        raise ValidationError("pagination value cannot be negative")
    if maximum is not None and number > maximum:
        if clamp:
            return maximum
        raise ValidationError("pagination value exceeds SQLite maximum")
    return number


def _computed_account_balance(current_balance, *deltas):
    balance = _finite_number(current_balance or 0, "current_balance")
    for delta in deltas:
        balance += _finite_number(delta, "transaction amount")
    return _finite_number(balance, "computed account balance")


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
