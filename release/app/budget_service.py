"""Shared, transaction-derived monthly budget calculations."""

from datetime import date


def budget_status(budget_amount, spent_amount):
    if budget_amount is None:
        return "unbudgeted"
    usage_rate = spent_amount / budget_amount if budget_amount else (1 if spent_amount == 0 else float("inf"))
    if usage_rate < 0.8:
        return "normal"
    if usage_rate < 1:
        return "warning"
    if usage_rate == 1:
        return "reached"
    return "exceeded"


def _budget_record(budget_amount, spent_amount):
    if budget_amount is None:
        return {
            "budget_amount": None, "spent_amount": spent_amount, "remaining_amount": None,
            "usage_rate": None, "status": "unbudgeted",
        }
    usage_rate = spent_amount / budget_amount if budget_amount else (0 if spent_amount == 0 else 1)
    return {
        "budget_amount": budget_amount,
        "spent_amount": spent_amount,
        "remaining_amount": budget_amount - spent_amount,
        "usage_rate": usage_rate,
        "status": budget_status(budget_amount, spent_amount),
    }


def current_budgets(conn, month_start):
    rows = conn.execute(
        "SELECT b.* FROM budgets b JOIN ("
        " SELECT scope_type, scope_id, MAX(effective_from) AS effective_from FROM budgets "
        " WHERE effective_from <= ? GROUP BY scope_type, scope_id"
        ") current ON current.scope_type = b.scope_type AND current.scope_id = b.scope_id "
        "AND current.effective_from = b.effective_from ORDER BY b.scope_type, b.scope_id",
        (month_start,),
    ).fetchall()
    return [dict(row) for row in rows]


def monthly_budget_summary(conn, start, end):
    """Return one consistent budget view for all UI surfaces.

    Only ordinary expense outflows count. A refund must be a positive refund
    with an explicit expense category to reduce its parent category and total.
    """
    budget_rows = current_budgets(conn, start.isoformat())
    total_budget = next(
        (float(row["amount"]) for row in budget_rows if row["scope_type"] == "total" and row["scope_id"] == "total"),
        None,
    )
    category_budgets = {
        row["scope_id"]: float(row["amount"])
        for row in budget_rows if row["scope_type"] == "category"
    }
    roots = [dict(row) for row in conn.execute(
        "SELECT id, name FROM categories WHERE is_active = 1 AND type = 'expense' AND parent_id IS NULL "
        "ORDER BY sort_order, name, id"
    )]
    root_ids = {row["id"] for row in roots}
    spent_rows = conn.execute(
        "SELECT COALESCE(parent.id, category.id) AS root_id, "
        "COALESCE(SUM(CASE "
        " WHEN t.transaction_kind = 'expense' AND t.amount < 0 THEN -t.amount "
        " WHEN t.transaction_kind = 'refund' AND t.amount > 0 THEN -t.amount "
        " ELSE 0 END), 0) AS spent_amount "
        "FROM transactions t JOIN categories category ON category.id = t.category_id "
        "LEFT JOIN categories parent ON parent.id = category.parent_id "
        "WHERE t.excluded_from_stats = 0 AND t.transaction_kind IN ('expense', 'refund') "
        "AND t.timestamp >= ? AND t.timestamp < ? "
        "AND COALESCE(parent.type, category.type) = 'expense' "
        "GROUP BY COALESCE(parent.id, category.id)",
        (start.isoformat() + "T00:00:00+00:00", end.isoformat() + "T00:00:00+00:00"),
    ).fetchall()
    spent_by_root = {row["root_id"]: float(row["spent_amount"]) for row in spent_rows if row["root_id"] in root_ids}
    uncategorized_expense = conn.execute(
        "SELECT COALESCE(SUM(-amount), 0) AS spent_amount FROM transactions "
        "WHERE excluded_from_stats = 0 AND transaction_kind = 'expense' AND amount < 0 "
        "AND category_id IS NULL AND timestamp >= ? AND timestamp < ?",
        (start.isoformat() + "T00:00:00+00:00", end.isoformat() + "T00:00:00+00:00"),
    ).fetchone()["spent_amount"]
    categories = []
    for root in roots:
        record = _budget_record(category_budgets.get(root["id"]), spent_by_root.get(root["id"], 0.0))
        categories.append({"id": root["id"], "name": root["name"], **record})
    alerts = [row for row in categories if row["budget_amount"] is not None and row["usage_rate"] >= 0.8]
    alerts.sort(key=lambda row: (
        0 if row["status"] == "exceeded" else 1,
        -(max(0, -row["remaining_amount"]) if row["status"] == "exceeded" else row["usage_rate"]),
        row["id"],
    ))
    total_spent = sum(spent_by_root.values()) + float(uncategorized_expense or 0)
    allocated_amount = sum(amount for category_id, amount in category_budgets.items() if category_id in root_ids)
    return {
        "total": _budget_record(total_budget, total_spent),
        "categories": categories,
        "alerts": alerts[:2],
        "configured_category_count": sum(1 for row in categories if row["budget_amount"] is not None),
        "category_budget_amount": allocated_amount,
        "unallocated_amount": total_budget - allocated_amount if total_budget is not None else None,
    }


def _month_end(start):
    return date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)


def transaction_budget_contribution(conn, transaction):
    """Return the expense-root and signed budget impact for one transaction.

    Positive impacts consume budget; refunds return a negative impact.  This
    is intentionally the single eligibility/direction rule used by previews.
    """
    if transaction.get("excluded_from_stats"):
        return None
    amount = float(transaction.get("amount") or 0)
    kind = transaction.get("transaction_kind")
    if not ((kind == "expense" and amount < 0) or (kind == "refund" and amount > 0)):
        return None
    category_id = transaction.get("category_id")
    if not category_id:
        return None
    category = conn.execute(
        "SELECT COALESCE(parent.id, current.id) AS root_id, "
        "COALESCE(parent.type, current.type) AS root_type "
        "FROM categories current LEFT JOIN categories parent ON parent.id = current.parent_id "
        "WHERE current.id = ?",
        (category_id,),
    ).fetchone()
    if category is None or category["root_type"] != "expense":
        return None
    return {"root_id": category["root_id"], "spent_amount": -amount}


def monthly_budget_impact_preview(conn, values, original_transaction=None):
    """Preview the saved state of one transaction without persisting anything."""
    transaction_date = date.fromisoformat(values["timestamp"][:10])
    start = transaction_date.replace(day=1)
    summary = monthly_budget_summary(conn, start, _month_end(start))
    contribution = transaction_budget_contribution(conn, values)
    if contribution is None:
        reason = "unclassified" if not values.get("category_id") else "excluded"
        return {"visible": False, "reason": reason}

    category = next((row for row in summary["categories"] if row["id"] == contribution["root_id"]), None)
    if category is None or category["budget_amount"] is None:
        return {
            "visible": False,
            "reason": "unbudgeted",
            "category": {"id": contribution["root_id"], "name": category["name"] if category else ""},
        }

    baseline_spent = category["spent_amount"]
    if original_transaction is not None:
        original_date = date.fromisoformat(str(original_transaction["timestamp"])[:10])
        if original_date.year == start.year and original_date.month == start.month:
            original = transaction_budget_contribution(conn, original_transaction)
            if original is not None and original["root_id"] == contribution["root_id"]:
                baseline_spent -= original["spent_amount"]

    projected = _budget_record(category["budget_amount"], baseline_spent + contribution["spent_amount"])
    return {
        "visible": True,
        "reason": None,
        "category": {"id": category["id"], "name": category["name"]},
        "current": _budget_record(category["budget_amount"], baseline_spent),
        "projected": projected,
    }
