"""One-time, reviewable merge of active credit cards with explicit exclusions.

Usage: python merge_credit_accounts.py /path/to/agent-ledger.db --exclude EXCLUDED_NAME [--apply]
The default is a read-only preview. Stop the application before --apply.
"""

import argparse
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
from uuid import uuid4


TARGET_NAME = "信用卡"


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def preview(conn, excluded_names):
    conn.row_factory = sqlite3.Row
    if not excluded_names or TARGET_NAME in excluded_names:
        raise ValueError("必须指定至少一个排除账户，且目标账户不能被排除")
    cards = [dict(row) for row in conn.execute(
        "SELECT * FROM accounts WHERE type = 'credit' AND is_active = 1 ORDER BY name"
    )]
    missing_exclusions = excluded_names - {card["name"] for card in cards}
    if missing_exclusions:
        raise ValueError("排除账户不存在或不是有效信用卡：" + "、".join(sorted(missing_exclusions)))
    sources = [card for card in cards if card["name"] != TARGET_NAME and card["name"] not in excluded_names]
    target = next((card for card in cards if card["name"] == TARGET_NAME), None)
    if not sources:
        raise ValueError("没有待合并的有效信用卡账户")
    if conn.execute("SELECT 1 FROM accounts WHERE name = ? AND (type != 'credit' OR is_active != 1)", (TARGET_NAME,)).fetchone():
        raise ValueError("名称“信用卡”已被其他或停用账户占用")
    ids = [card["id"] for card in sources] + ([target["id"]] if target else [])
    placeholders = ",".join("?" for _ in ids)
    budgets = conn.execute(
        f"SELECT COUNT(*) FROM budgets WHERE scope_type = 'account' AND scope_id IN ({placeholders})", ids
    ).fetchone()[0]
    if budgets:
        raise ValueError(f"涉及 {budgets} 条账户级预算，需先决定预算合并规则")
    configured = [card["name"] for card in sources + ([target] if target else [])
                  if card["monthly_budget"] is not None]
    if configured:
        raise ValueError("这些账户设有月度预算，需先决定预算合并规则：" + "、".join(configured))
    balance = sum((money(card["current_balance"]) for card in sources), Decimal(0))
    if target:
        balance += money(target["current_balance"])
    count = conn.execute(
        f"SELECT COUNT(*) FROM transactions WHERE account_id IN ({placeholders})", ids
    ).fetchone()[0]
    return {"sources": sources, "target": target, "balance": balance, "transaction_count": count, "ids": ids}


def merge(conn, excluded_names):
    """Move history atomically; preserve original card IDs for audit and rollback."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        plan = preview(conn, excluded_names)
        target_id = plan["target"]["id"] if plan["target"] else str(uuid4())
        source_ids = [card["id"] for card in plan["sources"]]
        if not plan["target"]:
            conn.execute(
                "INSERT INTO accounts (id, name, type, initial_balance, current_balance, "
                "budget_period, is_active) VALUES (?, ?, 'credit', 0, 0, 'calendar_month', 1)",
                (target_id, TARGET_NAME),
            )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS credit_merge_origins ("
            "transaction_id TEXT PRIMARY KEY, original_account_id TEXT NOT NULL, "
            "merged_account_id TEXT NOT NULL, merged_at TEXT NOT NULL)"
        )
        merged_at = datetime.now().astimezone().isoformat()
        placeholders = ",".join("?" for _ in source_ids)
        conn.execute(
            "INSERT INTO credit_merge_origins "
            "SELECT id, account_id, ?, ? FROM transactions "
            f"WHERE account_id IN ({placeholders})",
            (target_id, merged_at, *source_ids),
        )
        conn.execute(
            f"UPDATE transactions SET account_id = ? WHERE account_id IN ({placeholders})",
            (target_id, *source_ids),
        )
        amounts = [money(row["amount"]) for row in conn.execute(
            "SELECT amount FROM transactions WHERE account_id = ? ORDER BY timestamp, id", (target_id,)
        )]
        initial = plan["balance"] - sum(amounts, Decimal(0))
        running = initial
        for row in conn.execute(
            "SELECT id, amount FROM transactions WHERE account_id = ? ORDER BY timestamp, id", (target_id,)
        ):
            running += money(row["amount"])
            conn.execute("UPDATE transactions SET balance_after = ? WHERE id = ?", (float(running), row["id"]))
        if running != plan["balance"]:
            raise AssertionError("合并余额核对失败")
        conn.execute(
            "UPDATE accounts SET initial_balance = ?, current_balance = ?, "
            "monthly_budget = NULL, budget_period = 'calendar_month', credit_limit = NULL, "
            "statement_day = NULL, due_day = NULL, due_month_offset = 1 "
            "WHERE id = ?",
            (float(initial), float(plan["balance"]), target_id),
        )
        conn.execute(
            f"UPDATE accounts SET is_active = 0 WHERE id IN ({placeholders})", source_ids
        )
        conn.commit()
        return target_id, plan
    except BaseException:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--exclude", action="append", required=True, help="信用卡账户名，可多次指定")
    parser.add_argument("--apply", action="store_true", help="execute after reviewing preview and backing up database")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("数据库文件不存在")
    uri = args.database.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        plan = preview(conn, set(args.exclude))
        print("合并来源：" + "、".join(card["name"] for card in plan["sources"]))
        print(f"目标：{TARGET_NAME}（{'已有' if plan['target'] else '新建'}）")
        print(f"涉及交易：{plan['transaction_count']} 笔；合并余额：¥{plan['balance']}")
        print("保留独立账户：" + "、".join(args.exclude))
        if args.apply:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = args.database.with_name(args.database.name + f".pre-credit-merge-{stamp}.bak")
            if backup_path.exists():
                raise FileExistsError(f"备份文件已存在：{backup_path}")
            with sqlite3.connect(backup_path) as backup:
                conn.backup(backup)
            print(f"合并前备份：{backup_path}")
            target_id, _ = merge(conn, set(args.exclude))
            print(f"合并完成，目标账户 ID：{target_id}")
        else:
            print("只读预览；执行前停止应用并备份数据库，再加 --apply")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
