"""Seed-driven wallet categories, tags, card cycles, and default rules."""

from types import MappingProxyType

from seed_catalog import load_category_seed, load_tag_seed

CARD_SETTINGS = MappingProxyType({
    "示例信用卡A": (5, 20, 1),
    "示例信用卡B": (10, 25, 1),
    "示例信用卡C": (15, 5, 0),
    "示例信用卡D": (20, 10, 1),
    "示例信用卡E": (25, 15, 0),
})

# (id, rule_type, pattern, category_id, tag_id, transaction_kind, priority)
CLASSIFICATION_RULE_SEEDS = (
    ("kind-credit-repayment", "kind", "信用卡还款", None, None, "credit_repayment", 1),
    ("kind-repayment-to", "kind", "还款至", None, None, "credit_repayment", 2),
    ("kind-balance-adjustment", "kind", "平账调整", None, None, "balance_adjustment", 3),
    ("tag-family_member_a", "tag", "示例成员A|成员A", None, "family_member_a", None, 20),
    ("tag-family_member_b", "tag", "示例成员B", None, "family_member_b", None, 21),
    (
        "tag-subscription", "tag", "订阅|Gemini|GPT|OpenAI|Claude|硅基流动",
        None, "subscription", None, 22,
    ),
    (
        "category-ai-subscription", "category", "Gemini|GPT|OpenAI|Claude|硅基流动",
        "expense_digital_ai", None, None, 100,
    ),
    (
        "category-vpn-network", "category", "VPN|示例代理服务|[Pp]roxy|网络代理|代理服务器",
        "expense_network_vpn", None, None, 101,
    ),
    ("category-vps-server", "category", "VPS|服务器", "expense_network_service", None, None, 102),
    ("category-domain-cloud", "category", "域名|云服务", "expense_digital_cloud", None, None, 103),
)


def seed_taxonomy(conn):
    """Insert canonical editable seed data without changing existing user data."""
    _seed_categories(conn)
    _seed_tags(conn)
    _seed_rules(conn)
    _seed_card_settings(conn)


def _seed_categories(conn):
    for root_sort_order, root in enumerate(load_category_seed()["roots"], 1):
        _insert_category(conn, root["id"], root["name"], root["type"], None, root_sort_order)
        for child_sort_order, child in enumerate(root["children"], 1):
            _insert_category(
                conn, child["id"], child["name"], root["type"], root["id"], child_sort_order
            )


def _insert_category(conn, category_id, name, category_type, parent_id, sort_order):
    conn.execute(
        "INSERT INTO categories "
        "(id, name, type, icon, keywords, parent_id, sort_order, is_active) "
        "VALUES (?, ?, ?, '', '', ?, ?, 1) "
        "ON CONFLICT(id) DO UPDATE SET "
        "name = excluded.name, type = excluded.type, parent_id = excluded.parent_id, "
        "sort_order = excluded.sort_order, is_active = 1",
        (category_id, name, category_type, parent_id, sort_order),
    )


def _seed_tags(conn):
    for tag in load_tag_seed()["tags"]:
        conn.execute(
            "INSERT INTO tags (id, name, group_name, keywords, is_active) "
            "VALUES (?, ?, ?, '', 1) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "group_name = excluded.group_name, is_active = 1",
            (tag["id"], tag["name"], tag["group"]),
        )


def _seed_rules(conn):
    conn.executemany(
        "INSERT OR IGNORE INTO classification_rules "
        "(id, rule_type, pattern, category_id, tag_id, transaction_kind, priority, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        CLASSIFICATION_RULE_SEEDS,
    )


def _seed_card_settings(conn):
    for account_name, (statement_day, due_day, due_month_offset) in CARD_SETTINGS.items():
        conn.execute(
            "UPDATE accounts SET "
            "statement_day = COALESCE(statement_day, ?), "
            "due_day = COALESCE(due_day, ?), "
            "due_month_offset = CASE "
            "WHEN due_month_offset IS NULL THEN ? "
            "WHEN statement_day IS NULL AND due_day IS NULL "
            "AND budget_period = 'calendar_month' THEN ? "
            "ELSE due_month_offset END, "
            "budget_period = CASE "
            "WHEN statement_day IS NULL AND due_day IS NULL "
            "AND budget_period = 'calendar_month' THEN 'billing_cycle' "
            "ELSE budget_period END "
            "WHERE name = ? AND type = 'credit'",
            (statement_day, due_day, due_month_offset, due_month_offset, account_name),
        )
