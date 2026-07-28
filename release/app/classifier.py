"""Deterministic, side-effect-free transaction classification."""

from dataclasses import dataclass
import re
import sqlite3
from typing import Optional, Pattern, Tuple

from seed_catalog import load_category_seed


EXCLUDED_KINDS = frozenset((
    "transfer", "credit_repayment", "topup_withdrawal", "balance_adjustment",
))
INCOME_CATEGORY_IDS = frozenset(
    child["id"]
    for root in load_category_seed()["roots"]
    if root["type"] == "income"
    for child in root["children"]
)
MAX_RULE_PATTERN_LENGTH = 256

PERSON_TAG_PATTERNS = (
    ("family_member_a", re.compile(r"示例成员A|成员A", re.IGNORECASE)),
    ("family_member_b", re.compile(r"示例成员B", re.IGNORECASE)),
)

DESCRIPTION_CATEGORY_ALIASES = (
    (re.compile(r"房租|租金", re.IGNORECASE), "expense_home_rent"),
    (re.compile(r"示例交通卡|交通卡", re.IGNORECASE), "expense_transport_public"),
)

LEGACY_CATEGORY_ALIASES = {
    "food": "expense_dining_meal",
    "餐饮": "expense_dining_meal",
    "餐饮美食": "expense_dining_meal",
    "餐饮服务": "expense_dining_meal",
    "午餐": "expense_dining_meal",
    "晚餐": "expense_dining_meal",
    "正餐": "expense_dining_meal",
    "早餐": "expense_dining_meal",
    "交通": "expense_transport_other",
    "交通出行": "expense_transport_other",
    "transport": "expense_transport_other",
    "购物": "expense_daily_shopping_general",
    "购物百货": "expense_daily_shopping_general",
    "shopping": "expense_daily_shopping_general",
    "其他收入": "income_other_other",
    "other_income": "income_other_other",
}


@dataclass(frozen=True)
class Classification:
    transaction_kind: str
    excluded_from_stats: bool
    category_id: Optional[str]
    tag_ids: Tuple[str, ...]
    classification_status: str
    legacy_category: Optional[str] = None


@dataclass(frozen=True)
class _CompiledRule:
    rule_type: str
    pattern: Pattern[str]
    category_id: Optional[str]
    tag_id: Optional[str]
    transaction_kind: Optional[str]


def classify_transaction(
    conn,
    description="",
    legacy_category="",
    amount=0,
    account_name=None,
    source_account_name=None,
    source_account=None,
):
    """Classify one transaction without changing transaction or history data.

    ``legacy_category`` is deliberately returned unchanged, so callers can keep
    the import source even when a canonical category is selected.  ``account_name``
    remains a backwards-compatible alias for ``source_account_name``.
    """
    description = description or ""
    legacy_category = legacy_category or ""
    source_name, source_type = _source_account_context(
        conn, source_account_name or account_name or source_account
    )
    text = " ".join((description, legacy_category)).strip()
    rules = _compiled_active_rules(conn)

    excluded_kind = _excluded_kind(
        rules, conn, description, text, source_name, source_type
    )
    if excluded_kind is not None:
        return Classification(
            transaction_kind=excluded_kind,
            excluded_from_stats=True,
            category_id=None,
            tag_ids=(),
            classification_status="classified",
            legacy_category=legacy_category,
        )

    transaction_kind = _ordinary_kind(description, legacy_category, amount)
    tag_ids = _person_tags(description)
    category_id = _category_from_rules(rules, text)

    for rule in rules:
        if rule.rule_type == "tag" and rule.pattern.search(text):
            if rule.tag_id and rule.tag_id not in tag_ids:
                tag_ids.append(rule.tag_id)

    if category_id is None:
        category_id = _description_category(description)
    if category_id is None:
        category_id = LEGACY_CATEGORY_ALIASES.get(_normalise_legacy_category(legacy_category))

    if transaction_kind == "refund" and category_id in INCOME_CATEGORY_IDS:
        category_id = None
    elif category_id == "income_other_other" and transaction_kind == "expense":
        transaction_kind = "income"

    return Classification(
        transaction_kind=transaction_kind,
        excluded_from_stats=False,
        category_id=category_id,
        tag_ids=tuple(tag_ids),
        classification_status="classified" if category_id else "needs_review",
        legacy_category=legacy_category,
    )


def _excluded_kind(rules, conn, description, text, source_name, source_type):
    for rule in rules:
        if rule.rule_type == "kind" and rule.pattern.search(text):
            if rule.transaction_kind == "credit_repayment" and _is_repayment_fee(description):
                continue
            if rule.transaction_kind in EXCLUDED_KINDS:
                return rule.transaction_kind

    if "平账" in description:
        return "balance_adjustment"
    if _is_clear_repayment(description):
        return "credit_repayment"
    if _is_clear_stored_value_inbound(conn, description, source_name, source_type):
        return "topup_withdrawal"
    if _is_clear_wallet_topup_or_withdrawal(description):
        return "topup_withdrawal"
    if _is_clear_own_account_transfer(conn, description, source_name):
        return "transfer"
    return None


def _is_clear_repayment(description):
    if _is_repayment_fee(description):
        return False
    if "还款至" in description:
        return True
    if re.search(r"还.{0,30}信用卡|信用卡.{0,12}还款", description, re.IGNORECASE):
        return True
    return re.search(r"^[^，。；：:]{2,40}还款$", description, re.IGNORECASE) is not None


def _is_repayment_fee(description):
    return re.search(r"手续费|服务费", description, re.IGNORECASE) is not None


def _ordinary_kind(description, legacy_category, amount):
    text = " ".join((description, legacy_category))
    if re.search(r"退款|退货|返款|refund", text, re.IGNORECASE):
        return "refund"
    return "income" if amount > 0 else "expense"


def _person_tags(description):
    return [tag_id for tag_id, pattern in PERSON_TAG_PATTERNS if pattern.search(description)]


def _category_from_rules(rules, text):
    for rule in rules:
        if rule.rule_type == "category" and rule.category_id and rule.pattern.search(text):
            return rule.category_id
    return None


def _description_category(description):
    for pattern, category_id in DESCRIPTION_CATEGORY_ALIASES:
        if pattern.search(description):
            return category_id
    return None


def _normalise_legacy_category(legacy_category):
    return re.sub(r"\s+", "", legacy_category).casefold()


def _is_clear_wallet_topup_or_withdrawal(description):
    if re.search(r"示例交通卡|交通卡", description, re.IGNORECASE):
        return False
    return re.search(
        r"示例电子钱包充值|电子钱包充值|余额充值|钱包充值|充值到(?:余额|钱包)|"
        r"(?:余额|钱包)提现|提现到(?:余额|钱包)|充值.{0,30}储值卡",
        description,
        re.IGNORECASE,
    ) is not None


def _is_clear_own_account_transfer(conn, description, source_name):
    direction = re.search(r"(?:转账|转)(?:至|到|入|给)|转入", description, re.IGNORECASE)
    if direction is None:
        return False
    account_names = _own_account_names(conn)
    destination_text = description[direction.end():]
    for name in account_names:
        if len(name.strip()) < 3 or name == source_name:
            continue
        if name in destination_text:
            return True
    if direction.group() == "转入":
        source_text = description[:direction.start()]
        for name in account_names:
            if len(name.strip()) < 3 or name == source_name:
                continue
            if name in source_text:
                return True
    return False


def _is_clear_stored_value_inbound(conn, description, source_name, source_type):
    if not _is_stored_value_account(source_name, source_type) or "充值" not in description:
        return False
    funding_source = description.split("充值", 1)[0]
    if not funding_source:
        return False
    return any(
        name != source_name and len(name.strip()) >= 3 and name in funding_source
        for name in _own_account_names(conn)
    )


def _is_stored_value_account(source_name, source_type):
    if not source_name:
        return False
    return (
        source_type in {"stored_value", "prepaid"}
        or "储值卡" in source_name
    )


def _source_account_context(conn, account):
    name = _account_name(account)
    account_type = _account_type(account)
    if name and account_type is None:
        try:
            row = conn.execute("SELECT type FROM accounts WHERE name = ?", (name,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is not None:
            account_type = row["type"] if hasattr(row, "keys") else row[0]
    return name, account_type


def _account_name(account):
    if account is None:
        return None
    if isinstance(account, str):
        return account
    if isinstance(account, dict):
        return account.get("name")
    if hasattr(account, "keys") and "name" in account.keys():
        return account["name"]
    return None


def _account_type(account):
    if isinstance(account, dict):
        return account.get("type")
    if hasattr(account, "keys") and "type" in account.keys():
        return account["type"]
    return None


def _own_account_names(conn):
    try:
        rows = conn.execute("SELECT name FROM accounts WHERE name IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(row["name"] if hasattr(row, "keys") else row[0] for row in rows)


def _compiled_active_rules(conn):
    try:
        rows = conn.execute(
            "SELECT id, rule_type, pattern, category_id, tag_id, transaction_kind, priority "
            "FROM classification_rules WHERE is_active = 1 "
            "ORDER BY priority DESC, id ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        return ()

    rules = []
    for row in rows:
        pattern = _compile_safe_pattern(row["pattern"])
        if pattern is None:
            continue
        rules.append(_CompiledRule(
            rule_type=row["rule_type"],
            pattern=pattern,
            category_id=row["category_id"],
            tag_id=row["tag_id"],
            transaction_kind=row["transaction_kind"],
        ))
    return tuple(rules)


def _compile_safe_pattern(pattern):
    """Allow only literals, alternation, and simple non-negated character classes."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_RULE_PATTERN_LENGTH:
        return None
    if any(character in pattern for character in "(){}*+?\\^$."):
        return None
    if pattern.startswith("|") or pattern.endswith("|") or "||" in pattern:
        return None

    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "]":
            return None
        if character == "[":
            closing = pattern.find("]", index + 1)
            if closing == -1 or closing == index + 1:
                return None
            content = pattern[index + 1:closing]
            if "[" in content or "\\" in content or "^" in content:
                return None
            index = closing
        index += 1

    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
