"""Authenticated JSON endpoints for the private wallet API."""

import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from io import StringIO
import math
import sqlite3
from uuid import uuid4

from flask import Response, current_app, jsonify, request

from billing import billing_period_for
from budget_service import current_budgets, monthly_budget_impact_preview, monthly_budget_summary
from classifier import classify_transaction
from db import get_db
from seed_catalog import load_category_color_seed
from repositories import (
    ACCOUNT_TYPES,
    _atomic,
    ConflictError,
    NotFoundError,
    ValidationError,
    TRANSACTION_KINDS,
    _validate_account_values,
    create_credit_card_repayment,
    create_transaction,
    delete_credit_card_repayment,
    deactivate_account,
    delete_transaction,
    list_transactions,
    count_transactions,
    transaction_pagination,
    update_transaction,
    update_account,
)


RULE_TYPES = frozenset({"category", "tag", "kind"})
RULE_FIELDS = frozenset({"rule_type", "pattern", "category_id", "tag_id", "transaction_kind", "priority"})
ACCOUNT_CREATE_FIELDS = frozenset({
    "name", "type", "initial_balance", "icon", "notes", "monthly_budget", "budget_period",
    "credit_limit", "statement_day", "due_day", "due_month_offset",
})
ACCOUNT_UPDATE_FIELDS = ACCOUNT_CREATE_FIELDS - {"initial_balance"}


def error(code, message, status, field=None):
    body = {"error": {"code": code, "message": message}}
    if field is not None:
        body["error"]["field"] = field
    return jsonify(body), status


def _invalid(field, message):
    return error("invalid_field", message, 400, field)


def _json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiValidationError("body", "request body must be a JSON object")
    return data


class ApiValidationError(Exception):
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(message)


def auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.headers.get("X-API-Key") != current_app.config["API_KEY"]:
            return error("unauthorized", "valid X-API-Key required", 401)
        return view(*args, **kwargs)
    return wrapped


def idempotent_create(view):
    """Commit an optional request key and its successful mutation together.

    Hash the original JSON before defaults/classification so retries without a
    timestamp replay the first result. No key preserves the legacy API behavior.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if key is None:
            return view(*args, **kwargs)
        if not 1 <= len(key) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            return _invalid("idempotency_key", "Idempotency-Key must contain 1 to 128 printable ASCII characters without spaces")
        payload = _json_object()
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        operation = request.path
        conn = get_db()
        with _atomic(conn):
            prior = conn.execute(
                "SELECT operation, request_hash, response_json, response_status FROM idempotency_requests "
                "WHERE request_key = ?", (key,),
            ).fetchone()
            if prior is not None:
                if prior["operation"] != operation or prior["request_hash"] != request_hash:
                    return error("conflict", "Idempotency-Key was already used with a different request", 409, "idempotency_key")
                return jsonify(json.loads(prior["response_json"])), prior["response_status"]
            response = current_app.make_response(view(*args, **kwargs))
            if 200 <= response.status_code < 300:
                conn.execute(
                    "INSERT INTO idempotency_requests (operation, request_key, request_hash, response_json, response_status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (operation, key, request_hash, json.dumps(response.get_json()), response.status_code),
                )
            return response
    return wrapped


def _finite_number(value, field, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiValidationError(field, field + " must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ApiValidationError(field, field + " must be a finite number" if not math.isfinite(number) else "invalid " + field)
    return number


def _account_values(data, creating=False):
    unknown = set(data) - (ACCOUNT_CREATE_FIELDS if creating else ACCOUNT_UPDATE_FIELDS)
    if unknown:
        field = sorted(unknown)[0]
        raise ApiValidationError(field, "unsupported account field")
    if creating:
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise ApiValidationError("name", "name is required")
        if data.get("type") not in ACCOUNT_TYPES:
            raise ApiValidationError("type", "invalid account type")
    if "initial_balance" in data:
        _finite_number(data["initial_balance"], "initial_balance")
    for field in ("monthly_budget", "credit_limit"):
        if field in data and data[field] is not None:
            _finite_number(data[field], field, minimum=0)
    for field in ("statement_day", "due_day"):
        if field in data and data[field] is not None and (
            isinstance(data[field], bool) or not isinstance(data[field], int) or not 1 <= data[field] <= 28
        ):
            raise ApiValidationError(field, field + " must be between 1 and 28")
    if "due_month_offset" in data and data["due_month_offset"] is not None and data["due_month_offset"] not in {0, 1}:
        raise ApiValidationError("due_month_offset", "due_month_offset must be 0 or 1")
    if "budget_period" in data and data["budget_period"] not in {"calendar_month", "billing_cycle"}:
        raise ApiValidationError("budget_period", "invalid budget_period")
    if data.get("budget_period") == "billing_cycle" and data.get("type") not in {None, "credit"}:
        raise ApiValidationError("budget_period", "billing_cycle requires a credit account")
    return data


def _repository_validation(error_value):
    message = str(error_value)
    field = "payload"
    for candidate in ("category_id", "transaction_kind", "amount", "credit_account_id", "source_account_id", "current_balance", "monthly_budget", "credit_limit", "budget_period", "statement_day", "due_day", "due_month_offset", "name", "type", "icon", "notes"):
        if candidate in message:
            field = candidate
            break
    return _invalid(field, message)


def _active_reference(conn, table, reference_id):
    return conn.execute(
        "SELECT id FROM " + table + " WHERE id = ? AND is_active = 1", (reference_id,)
    ).fetchone() is not None


def _rule_values(conn, data, partial=False, current=None):
    unknown = set(data) - RULE_FIELDS
    if unknown:
        field = sorted(unknown)[0]
        raise ApiValidationError(field, "unsupported classification rule field")
    values = dict(current or {})
    values.update(data)
    if values.get("rule_type") not in RULE_TYPES:
        raise ApiValidationError("rule_type", "rule_type must be category, tag, or kind")
    if not isinstance(values.get("pattern"), str) or not values["pattern"].strip():
        raise ApiValidationError("pattern", "pattern is required")
    if len(values["pattern"]) > 500:
        raise ApiValidationError("pattern", "pattern is too long")
    priority = values.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ApiValidationError("priority", "priority must be an integer")
    values["priority"] = priority
    if values["rule_type"] == "category":
        if not isinstance(values.get("category_id"), str) or not _active_reference(conn, "categories", values["category_id"]):
            raise ApiValidationError("category_id", "category_id must reference an active category")
        values["tag_id"] = None
        values["transaction_kind"] = None
    elif values["rule_type"] == "tag":
        if not isinstance(values.get("tag_id"), str) or not _active_reference(conn, "tags", values["tag_id"]):
            raise ApiValidationError("tag_id", "tag_id must reference an active tag")
        values["category_id"] = None
        values["transaction_kind"] = None
    else:
        if values.get("transaction_kind") not in TRANSACTION_KINDS:
            raise ApiValidationError("transaction_kind", "invalid transaction_kind")
        values["category_id"] = None
        values["tag_id"] = None
    return values


def _budget_values(conn, data):
    required = {"scope_type", "scope_id", "amount", "effective_from"}
    unknown = set(data) - required
    if unknown:
        raise ApiValidationError(sorted(unknown)[0], "unsupported budget field")
    missing = required - set(data)
    if missing:
        field = sorted(missing)[0]
        raise ApiValidationError(field, field + " is required")
    if data["scope_type"] not in {"account", "category", "total"}:
        raise ApiValidationError("scope_type", "scope_type must be account, category, or total")
    if not isinstance(data["scope_id"], str) or not data["scope_id"].strip():
        raise ApiValidationError("scope_id", "scope_id is required")
    amount = _finite_number(data["amount"], "amount", minimum=0)
    if not isinstance(data["effective_from"], str):
        raise ApiValidationError("effective_from", "effective_from must be an ISO date")
    try:
        effective_from = date.fromisoformat(data["effective_from"])
    except ValueError as exc:
        raise ApiValidationError("effective_from", "effective_from must be an ISO date") from exc
    if effective_from.day != 1:
        raise ApiValidationError("effective_from", "effective_from must be the first day of a month")
    if data["scope_type"] == "total":
        if data["scope_id"] != "total":
            raise ApiValidationError("scope_id", "total budget scope_id must be total")
        return data["scope_type"], data["scope_id"], amount, effective_from.isoformat()
    table = "accounts" if data["scope_type"] == "account" else "categories"
    if not _active_reference(conn, table, data["scope_id"]):
        raise ApiValidationError("scope_id", "scope_id must reference an active " + data["scope_type"])
    return data["scope_type"], data["scope_id"], amount, effective_from.isoformat()


def _month_bounds(year, month):
    if isinstance(year, bool) or isinstance(month, bool):
        raise ApiValidationError("month", "year and month must be integers")
    try:
        start = date(int(year), int(month), 1)
    except (TypeError, ValueError):
        raise ApiValidationError("month", "month must be between 1 and 12")
    next_month = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, next_month


def _budget_impact_preview_values(data):
    allowed = {"timestamp", "amount", "transaction_kind", "category_id", "excluded_from_stats", "transaction_id"}
    unknown = set(data) - allowed
    if unknown:
        raise ApiValidationError(sorted(unknown)[0], "unsupported budget preview field")
    required = {"timestamp", "amount", "transaction_kind", "excluded_from_stats"}
    missing = required - set(data)
    if missing:
        field = sorted(missing)[0]
        raise ApiValidationError(field, field + " is required")
    if not isinstance(data["timestamp"], str):
        raise ApiValidationError("timestamp", "timestamp must be an ISO date")
    try:
        date.fromisoformat(data["timestamp"][:10])
    except ValueError as exc:
        raise ApiValidationError("timestamp", "timestamp must be an ISO date") from exc
    amount = _finite_number(data["amount"], "amount")
    if data["transaction_kind"] not in TRANSACTION_KINDS:
        raise ApiValidationError("transaction_kind", "invalid transaction_kind")
    if not isinstance(data["excluded_from_stats"], bool):
        raise ApiValidationError("excluded_from_stats", "excluded_from_stats must be a boolean")
    category_id = data.get("category_id")
    if category_id is not None and not isinstance(category_id, str):
        raise ApiValidationError("category_id", "category_id must be a string or null")
    transaction_id = data.get("transaction_id")
    if transaction_id is not None and (not isinstance(transaction_id, str) or not transaction_id):
        raise ApiValidationError("transaction_id", "transaction_id must be a non-empty string")
    return {
        "timestamp": data["timestamp"], "amount": amount, "transaction_kind": data["transaction_kind"],
        "category_id": category_id, "excluded_from_stats": data["excluded_from_stats"],
        "transaction_id": transaction_id,
    }


def _stats_for_period(conn, start, end, account_id=None):
    clauses = ["excluded_from_stats = 0", "transaction_kind IN ('expense', 'income', 'refund')", "timestamp >= ?", "timestamp < ?"]
    params = [start.isoformat() + "T00:00:00+00:00", end.isoformat() + "T00:00:00+00:00"]
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN transaction_kind = 'income' AND amount > 0 THEN amount ELSE 0 END), 0) AS income, "
        "COALESCE(SUM(CASE WHEN transaction_kind = 'expense' AND amount < 0 THEN -amount ELSE 0 END), 0) AS expense, "
        "COALESCE(SUM(CASE WHEN transaction_kind = 'refund' THEN amount ELSE 0 END), 0) AS refunds "
        "FROM transactions WHERE " + " AND ".join(clauses), params,
    ).fetchone()
    return {"income": float(row["income"]), "expense": float(row["expense"]), "refunds": float(row["refunds"])}


def _parse_anchor(value):
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiValidationError("anchor", "anchor must be YYYY-MM-DD") from exc


def _transaction_request_filters(pagination=False):
    keys = ("start", "end", "account_id", "category_id", "tag_id", "transaction_kind",
            "min_amount", "max_amount", "query")
    if pagination:
        keys += ("limit", "offset")
    filters = {key: request.args.get(key) for key in keys if request.args.get(key) not in (None, "")}
    for field in ("min_amount", "max_amount"):
        if field in filters:
            try:
                value = float(filters[field])
            except (ValueError, OverflowError) as exc:
                raise ApiValidationError(field, field + " must be a finite number") from exc
            filters[field] = _finite_number(value, field)
    if "min_amount" in filters and "max_amount" in filters and filters["min_amount"] > filters["max_amount"]:
        raise ApiValidationError("min_amount", "min_amount must not exceed max_amount")
    return filters


def _account_chart_data(conn, account_id, start, end):
    clause = (" FROM transactions WHERE account_id = ? AND timestamp >= ? AND timestamp < ? "
              "AND transaction_kind = 'expense' AND amount < 0 AND excluded_from_stats = 0 ")
    params = (account_id, start.isoformat() + "T00:00:00+00:00", end.isoformat() + "T00:00:00+00:00")
    daily = conn.execute("SELECT substr(timestamp, 1, 10) AS date, SUM(-amount) AS amount" + clause
                         + "GROUP BY substr(timestamp, 1, 10) ORDER BY date", params).fetchall()
    categories = conn.execute("SELECT category_id, SUM(-amount) AS amount" + clause
                              + "GROUP BY category_id ORDER BY amount DESC, category_id", params).fetchall()
    return {"daily_expenses": [dict(row) for row in daily],
            "category_expenses": [dict(row) for row in categories]}


def register_routes(app):
    @app.errorhandler(ApiValidationError)
    def handle_api_validation(exc):
        return _invalid(exc.field, exc.message)

    @app.errorhandler(ValidationError)
    def handle_repository_validation(exc):
        return _repository_validation(exc)

    @app.errorhandler(NotFoundError)
    def handle_not_found(exc):
        return error("not_found", str(exc), 404)

    @app.errorhandler(ConflictError)
    def handle_conflict(exc):
        return error("conflict", str(exc), 409)

    @app.get("/accounts")
    @auth
    def list_accounts():
        rows = get_db().execute(
            "SELECT * FROM accounts WHERE is_active = 1 ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.post("/accounts")
    @auth
    def create_account():
        data = _account_values(_json_object(), creating=True)
        current = {
            "name": data["name"].strip(), "type": data["type"], "icon": data.get("icon"),
            "notes": data.get("notes"), "monthly_budget": data.get("monthly_budget"),
            "budget_period": data.get("budget_period", "calendar_month"),
            "credit_limit": data.get("credit_limit"), "statement_day": data.get("statement_day"),
            "due_day": data.get("due_day"), "due_month_offset": data.get("due_month_offset", 1),
        }
        try:
            _validate_account_values(current)
        except ValidationError as exc:
            return _repository_validation(exc)
        balance = _finite_number(data.get("initial_balance", 0), "initial_balance")
        account_id = str(uuid4())
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO accounts (id, name, type, initial_balance, current_balance, icon, notes, "
                "monthly_budget, budget_period, credit_limit, statement_day, due_day, due_month_offset, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (account_id, current["name"], current["type"], balance, balance, current["icon"], current["notes"],
                 current["monthly_budget"], current["budget_period"], current["credit_limit"], current["statement_day"],
                 current["due_day"], current["due_month_offset"]),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            return error("conflict", "account name already exists", 409, "name")
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return jsonify(dict(row)), 201

    @app.put("/accounts/<account_id>")
    @auth
    def edit_account(account_id):
        data = _account_values(_json_object(), creating=False)
        if not data:
            return _invalid("payload", "at least one account field is required")
        return jsonify(update_account(get_db(), account_id, data))

    @app.delete("/accounts/<account_id>")
    @auth
    def remove_account(account_id):
        return jsonify(deactivate_account(get_db(), account_id))

    @app.get("/taxonomy")
    @auth
    def taxonomy():
        conn = get_db()
        presentation = load_category_color_seed()
        category_rows = [dict(row) for row in conn.execute(
            "SELECT id, name, type, icon, keywords, parent_id, sort_order, is_active FROM categories "
            "WHERE is_active = 1 AND type IN ('expense', 'income') "
            "ORDER BY type, parent_id IS NOT NULL, parent_id, sort_order, name"
        )]
        children = {}
        roots = []
        for row in category_rows:
            row["children"] = []
            if row["parent_id"] is None:
                roots.append(row)
            else:
                children.setdefault(row["parent_id"], []).append(row)
        for root in roots:
            root["children"] = children.get(root["id"], [])
        for row in category_rows:
            configured = presentation.get(row["id"], presentation["_fallback"])
            row["color"] = configured["color"]
            row["root_color"] = configured["root_color"]
            if configured["icon"]:
                row["icon"] = configured["icon"]
        tags = [dict(row) for row in conn.execute(
            "SELECT id, name, group_name, keywords, is_active FROM tags WHERE is_active = 1 "
            "ORDER BY group_name, name, id"
        )]
        return jsonify({
            "categories": roots,
            "tags": tags,
            "category_fallback_color": presentation["_fallback"]["color"],
        })

    @app.get("/classification-rules")
    @auth
    def list_rules():
        rows = get_db().execute(
            "SELECT * FROM classification_rules WHERE is_active = 1 ORDER BY priority, id"
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.post("/classification-rules")
    @auth
    def create_rule():
        conn = get_db()
        values = _rule_values(conn, _json_object())
        rule_id = str(uuid4())
        conn.execute(
            "INSERT INTO classification_rules (id, rule_type, pattern, category_id, tag_id, transaction_kind, priority, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (rule_id, values["rule_type"], values["pattern"].strip(), values.get("category_id"),
             values.get("tag_id"), values.get("transaction_kind"), values["priority"]),
        )
        conn.commit()
        return jsonify(dict(conn.execute("SELECT * FROM classification_rules WHERE id = ?", (rule_id,)).fetchone())), 201

    @app.put("/classification-rules/<rule_id>")
    @auth
    def edit_rule(rule_id):
        conn = get_db()
        current = conn.execute("SELECT * FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
        if current is None:
            return error("not_found", "classification rule not found", 404)
        data = _json_object()
        if not data:
            return _invalid("payload", "at least one classification rule field is required")
        values = _rule_values(conn, data, current=dict(current))
        conn.execute(
            "UPDATE classification_rules SET rule_type = ?, pattern = ?, category_id = ?, tag_id = ?, "
            "transaction_kind = ?, priority = ? WHERE id = ?",
            (values["rule_type"], values["pattern"].strip(), values.get("category_id"), values.get("tag_id"),
             values.get("transaction_kind"), values["priority"], rule_id),
        )
        conn.commit()
        return jsonify(dict(conn.execute("SELECT * FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()))

    @app.delete("/classification-rules/<rule_id>")
    @auth
    def remove_rule(rule_id):
        conn = get_db()
        current = conn.execute("SELECT * FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()
        if current is None:
            return error("not_found", "classification rule not found", 404)
        conn.execute("UPDATE classification_rules SET is_active = 0 WHERE id = ?", (rule_id,))
        conn.commit()
        return jsonify(dict(conn.execute("SELECT * FROM classification_rules WHERE id = ?", (rule_id,)).fetchone()))

    @app.get("/budgets")
    @auth
    def list_budgets():
        month = request.args.get("month")
        if month is None:
            month = date.today().strftime("%Y-%m")
        try:
            requested = date.fromisoformat(month + "-01")
        except ValueError:
            return _invalid("month", "month must be YYYY-MM")
        start = requested.isoformat()
        return jsonify(current_budgets(get_db(), start))

    @app.post("/budgets")
    @auth
    def save_budget():
        conn = get_db()
        scope_type, scope_id, amount, effective_from = _budget_values(conn, _json_object())
        budget_id = str(uuid4())
        conn.execute(
            "INSERT INTO budgets (id, scope_type, scope_id, amount, effective_from) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_type, scope_id, effective_from) DO UPDATE SET amount = excluded.amount",
            (budget_id, scope_type, scope_id, amount, effective_from),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM budgets WHERE scope_type = ? AND scope_id = ? AND effective_from = ?",
            (scope_type, scope_id, effective_from),
        ).fetchone()
        return jsonify(dict(row)), 201

    @app.get("/budget-summary")
    @auth
    def budget_summary():
        month = request.args.get("month", date.today().strftime("%Y-%m"))
        try:
            start = date.fromisoformat(month + "-01")
        except ValueError:
            return _invalid("month", "month must be YYYY-MM")
        end = _month_bounds(start.year, start.month)[1]
        return jsonify({"month": month, **monthly_budget_summary(get_db(), start, end)})

    @app.post("/budget-impact-preview")
    @auth
    def budget_impact_preview():
        values = _budget_impact_preview_values(_json_object())
        transaction_id = values.pop("transaction_id")
        original = None
        if transaction_id is not None:
            row = get_db().execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
            if row is None:
                return error("not_found", "transaction not found", 404)
            original = dict(row)
        return jsonify(monthly_budget_impact_preview(get_db(), values, original))

    @app.get("/transactions")
    @auth
    def transaction_list():
        filters = _transaction_request_filters(pagination=True)
        conn = get_db()
        rows = list_transactions(conn, filters)
        if request.args.get("paginated") == "1":
            return jsonify({"transactions": rows,
                            "pagination": transaction_pagination(filters, count_transactions(conn, filters))})
        return jsonify(rows)

    @app.post("/transactions")
    @auth
    @idempotent_create
    def transaction_create():
        data = _json_object()
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        elif isinstance(data["timestamp"], str) and len(data["timestamp"]) == 10:
            data["timestamp"] += "T12:00:00+00:00"
        if "transaction_kind" not in data or "excluded_from_stats" not in data or "category_id" not in data:
            classification = classify_transaction(
                get_db(), data.get("description", ""), data.get("category", ""), data.get("amount", 0),
                source_account=data.get("account_id"),
            )
            data.setdefault("transaction_kind", classification.transaction_kind)
            data.setdefault("excluded_from_stats", classification.excluded_from_stats)
            data.setdefault("category_id", classification.category_id)
            data.setdefault("classification_status", classification.classification_status)
            data.setdefault("tag_ids", list(classification.tag_ids))
        if data.get("transaction_kind") == "credit_repayment":
            return _invalid("transaction_kind", "use /credit-card-repayments for credit card repayments")
        return jsonify(create_transaction(get_db(), data)), 201

    @app.post("/credit-card-repayments")
    @auth
    @idempotent_create
    def credit_card_repayment_create():
        data = _json_object()
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        elif isinstance(data["timestamp"], str) and len(data["timestamp"]) == 10:
            data["timestamp"] += "T12:00:00+00:00"
        return jsonify(create_credit_card_repayment(get_db(), data)), 201

    @app.delete("/credit-card-repayments/<repayment_group_id>")
    @auth
    def credit_card_repayment_delete(repayment_group_id):
        delete_credit_card_repayment(get_db(), repayment_group_id)
        return jsonify({"status": "reversed"})

    @app.put("/transactions/<transaction_id>")
    @auth
    def transaction_edit(transaction_id):
        data = _json_object()
        if isinstance(data.get("timestamp"), str) and len(data["timestamp"]) == 10:
            data["timestamp"] += "T12:00:00+00:00"
        return jsonify(update_transaction(get_db(), transaction_id, data))

    @app.delete("/transactions/<transaction_id>")
    @auth
    def transaction_delete(transaction_id):
        delete_transaction(get_db(), transaction_id)
        return jsonify({"status": "deleted"})

    @app.get("/overview")
    @auth
    def overview():
        start, end = _month_bounds(request.args.get("year", date.today().year), request.args.get("month", date.today().month))
        values = _stats_for_period(get_db(), start, end)
        accounts = get_db().execute("SELECT type, current_balance FROM accounts WHERE is_active = 1").fetchall()
        assets = sum(
            float(row["current_balance"] or 0)
            for row in accounts if row["type"] != "credit"
        ) + sum(
            max(0.0, float(row["current_balance"] or 0))
            for row in accounts if row["type"] == "credit"
        )
        liabilities = sum(
            max(0.0, -float(row["current_balance"] or 0))
            for row in accounts if row["type"] == "credit"
        )
        values.update({"period_start": start.isoformat(), "period_end": (end - timedelta(days=1)).isoformat(),
                       "assets": assets, "liabilities": liabilities, "net_assets": assets - liabilities})
        return jsonify(values)

    @app.get("/accounts/<account_id>/insights")
    @auth
    def account_insights(account_id):
        conn = get_db()
        account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if account is None:
            return error("not_found", "account not found", 404)
        anchor = _parse_anchor(request.args.get("anchor"))
        mode = request.args.get("mode", "last_30_days")
        result = {"account": dict(account), "mode": mode}
        if mode == "billing_cycle":
            try:
                period = billing_period_for(anchor, account["statement_day"], account["due_day"], account["due_month_offset"])
            except (TypeError, ValueError):
                return _invalid("mode", "account lacks valid billing cycle settings")
            start, end = period.start, period.end + timedelta(days=1)
            result.update({"period_start": period.start.isoformat(), "period_end": period.end.isoformat(),
                           "statement_date": period.statement_date.isoformat(), "due_date": period.due_date.isoformat()})
        elif mode == "calendar_month":
            start, end = _month_bounds(anchor.year, anchor.month)
            result.update({"period_start": start.isoformat(), "period_end": (end - timedelta(days=1)).isoformat()})
        elif mode == "last_30_days":
            start, end = anchor - timedelta(days=29), anchor + timedelta(days=1)
            result.update({"period_start": start.isoformat(), "period_end": (end - timedelta(days=1)).isoformat()})
        else:
            return _invalid("mode", "mode must be billing_cycle, calendar_month, or last_30_days")
        result.update(_stats_for_period(conn, start, end, account_id))
        current_balance = float(account["current_balance"] or 0)
        if account["type"] == "credit" and account["credit_limit"] is not None:
            occupied = max(0.0, -current_balance)
            result.update({"credit_limit": float(account["credit_limit"]), "occupied_credit": occupied,
                           "available_credit": max(0.0, float(account["credit_limit"]) - occupied)})
        filters = {"account_id": account_id, "start": start.isoformat(),
                   "end": (end - timedelta(days=1)).isoformat(),
                   "limit": request.args.get("transaction_limit", 100),
                   "offset": request.args.get("transaction_offset", 0)}
        result["transactions"] = list_transactions(conn, filters)
        result["transaction_pagination"] = transaction_pagination(filters, count_transactions(conn, filters))
        result["chart_data"] = _account_chart_data(conn, account_id, start, end)
        return jsonify(result)

    @app.get("/stats/categories")
    @auth
    def category_stats():
        start, end = _month_bounds(request.args.get("year", date.today().year), request.args.get("month", date.today().month))
        rows = get_db().execute(
            "SELECT COALESCE(c.id, 'uncategorized') AS id, COALESCE(c.name, '未分类') AS label, "
            "SUM(-t.amount) AS value, COUNT(*) AS count FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
            "WHERE t.excluded_from_stats = 0 AND t.transaction_kind = 'expense' AND t.amount < 0 "
            "AND t.timestamp >= ? AND t.timestamp < ? GROUP BY c.id, c.name ORDER BY value DESC, id",
            (start.isoformat() + "T00:00:00+00:00", end.isoformat() + "T00:00:00+00:00"),
        ).fetchall()
        total = sum(float(row["value"]) for row in rows)
        return jsonify([{**dict(row), "value": float(row["value"]), "percentage": (float(row["value"]) / total * 100 if total else 0)} for row in rows])

    @app.get("/stats/people")
    @auth
    def people_stats():
        start, end = _month_bounds(request.args.get("year", date.today().year), request.args.get("month", date.today().month))
        rows = get_db().execute(
            "SELECT tg.id, tg.name AS label, SUM(-t.amount) AS value, COUNT(DISTINCT t.id) AS count "
            "FROM transactions t JOIN transaction_tags tt ON tt.transaction_id = t.id JOIN tags tg ON tg.id = tt.tag_id "
            "WHERE tg.group_name IN ('person', '家庭成员') AND t.excluded_from_stats = 0 AND t.transaction_kind = 'expense' AND t.amount < 0 "
            "AND t.timestamp >= ? AND t.timestamp < ? GROUP BY tg.id, tg.name ORDER BY value DESC, tg.id",
            (start.isoformat() + "T00:00:00+00:00", end.isoformat() + "T00:00:00+00:00"),
        ).fetchall()
        total = sum(float(row["value"]) for row in rows)
        return jsonify([{**dict(row), "value": float(row["value"]), "percentage": (float(row["value"]) / total * 100 if total else 0)} for row in rows])

    @app.get("/export.csv")
    @auth
    def export_csv():
        conn = get_db()
        filters = _transaction_request_filters()
        rows, offset = [], 0
        while True:
            page = list_transactions(conn, filters | {"limit": 1000, "offset": offset})
            rows.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["交易 ID", "时间", "账户", "金额", "类型", "一级分类", "二级分类", "标签", "交易性质", "是否计入统计", "备注"])
        for row in rows:
            category = conn.execute("SELECT id, name, parent_id FROM categories WHERE id = ?", (row["category_id"],)).fetchone() if row.get("category_id") else None
            primary, secondary = "", ""
            if category:
                if category["parent_id"]:
                    parent = conn.execute("SELECT name FROM categories WHERE id = ?", (category["parent_id"],)).fetchone()
                    primary, secondary = (parent["name"] if parent else ""), category["name"]
                else:
                    primary = category["name"]
            writer.writerow([row["id"], row["timestamp"], row["account_name"], row["amount"], "收入" if row["amount"] > 0 else "支出", primary, secondary,
                             "、".join(tag["name"] for tag in row["tags"]), row["transaction_kind"], "是" if not row["excluded_from_stats"] else "否", row["description"]])
        return Response(
            "\ufeff" + output.getvalue(),
            content_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=wallet-export.csv",
                "Cache-Control": "no-store, max-age=0",
            },
        )
