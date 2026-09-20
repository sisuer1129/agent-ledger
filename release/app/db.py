import argparse
import sqlite3
import time
from pathlib import Path

from flask import current_app, g

from taxonomy import seed_taxonomy


SCHEMA_VERSION = 6
WAL_RETRY_DELAY_SECONDS = 0.05
WAL_RETRY_ATTEMPTS = 100


def connect_database(path):
    database_path = Path(path)
    if database_path.parent != Path("."):
        database_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(database_path), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _enable_wal_mode(conn)
    return conn


def _enable_wal_mode(conn):
    for attempt in range(WAL_RETRY_ATTEMPTS):
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or attempt == WAL_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(WAL_RETRY_DELAY_SECONDS)


def get_db():
    if "db" not in g:
        g.db = connect_database(current_app.config["DB_PATH"])
    return g.db


def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def column_exists(conn, table, column):
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def schema_version(conn):
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["value"]) if row else 0


def _create_base_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS accounts ("
        "id TEXT PRIMARY KEY, name TEXT UNIQUE, type TEXT, "
        "initial_balance REAL DEFAULT 0, current_balance REAL DEFAULT 0, "
        "icon TEXT, notes TEXT, is_active INTEGER DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transactions ("
        "id TEXT PRIMARY KEY, account_id TEXT, timestamp TIMESTAMP, amount REAL, "
        "description TEXT, category TEXT, balance_after REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS categories ("
        "id TEXT PRIMARY KEY, name TEXT UNIQUE, type TEXT, icon TEXT, keywords TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


def _add_column_if_absent(conn, table, column, definition):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_to_version_2(conn):
    account_columns = (
        ("monthly_budget", "REAL"),
        ("budget_period", "TEXT NOT NULL DEFAULT 'calendar_month'"),
        ("credit_limit", "REAL"),
        ("statement_day", "INTEGER"),
        ("due_day", "INTEGER"),
        ("due_month_offset", "INTEGER NOT NULL DEFAULT 1"),
    )
    transaction_columns = (
        ("category_id", "TEXT"),
        ("transaction_kind", "TEXT"),
        ("excluded_from_stats", "INTEGER NOT NULL DEFAULT 0"),
        ("classification_status", "TEXT NOT NULL DEFAULT 'unreviewed'"),
    )
    category_columns = (
        ("parent_id", "TEXT"),
        ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
    )

    for column, definition in account_columns:
        _add_column_if_absent(conn, "accounts", column, definition)
    for column, definition in transaction_columns:
        _add_column_if_absent(conn, "transactions", column, definition)
    for column, definition in category_columns:
        _add_column_if_absent(conn, "categories", column, definition)

    conn.execute(
        "CREATE TABLE IF NOT EXISTS tags ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, group_name TEXT NOT NULL, "
        "keywords TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transaction_tags ("
        "transaction_id TEXT NOT NULL, tag_id TEXT NOT NULL, "
        "PRIMARY KEY (transaction_id, tag_id), "
        "FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE, "
        "FOREIGN KEY (tag_id) REFERENCES tags(id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS budgets ("
        "id TEXT PRIMARY KEY, "
        "scope_type TEXT NOT NULL CHECK(scope_type IN ('account','category')), "
        "scope_id TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0), "
        "effective_from TEXT NOT NULL, "
        "UNIQUE(scope_type, scope_id, effective_from))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS classification_rules ("
        "id TEXT PRIMARY KEY, "
        "rule_type TEXT NOT NULL CHECK(rule_type IN ('category','tag','kind')), "
        "pattern TEXT NOT NULL, category_id TEXT, tag_id TEXT, transaction_kind TEXT, "
        "priority INTEGER NOT NULL DEFAULT 100, is_active INTEGER NOT NULL DEFAULT 1)"
    )


def _migrate_to_version_3(conn):
    """Allow the same leaf name under different primary categories.

    Category identity is the stable ID; names are display labels and only need
    to be unique within one parent category.
    """
    conn.execute(
        "CREATE TABLE categories_seed_v3 ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT, icon TEXT, keywords TEXT, "
        "parent_id TEXT, sort_order INTEGER NOT NULL DEFAULT 0, "
        "is_active INTEGER NOT NULL DEFAULT 1, "
        "UNIQUE(parent_id, name))"
    )
    conn.execute(
        "INSERT INTO categories_seed_v3 "
        "(id, name, type, icon, keywords, parent_id, sort_order, is_active) "
        "SELECT id, name, type, icon, keywords, parent_id, sort_order, is_active "
        "FROM categories"
    )
    conn.execute("DROP TABLE categories")
    conn.execute("ALTER TABLE categories_seed_v3 RENAME TO categories")


def _migrate_to_version_4(conn):
    """Allow one independently configured monthly total budget.

    SQLite cannot alter a CHECK constraint in place, so copy the existing
    account/category budget rows into an equivalent table that additionally
    accepts the `total` scope. Existing budget data is preserved verbatim.
    """
    conn.execute(
        "CREATE TABLE budgets_v4 ("
        "id TEXT PRIMARY KEY, "
        "scope_type TEXT NOT NULL CHECK(scope_type IN ('account','category','total')), "
        "scope_id TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0), "
        "effective_from TEXT NOT NULL, "
        "UNIQUE(scope_type, scope_id, effective_from))"
    )
    conn.execute(
        "INSERT INTO budgets_v4 (id, scope_type, scope_id, amount, effective_from) "
        "SELECT id, scope_type, scope_id, amount, effective_from FROM budgets"
    )
    conn.execute("DROP TABLE budgets")
    conn.execute("ALTER TABLE budgets_v4 RENAME TO budgets")


def _migrate_to_version_5(conn):
    """Link the two entries created for each credit-card repayment."""
    _add_column_if_absent(conn, "transactions", "repayment_group_id", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_repayment_group "
        "ON transactions(repayment_group_id)"
    )


def _migrate_to_version_6(conn):
    conn.execute(
        "CREATE TABLE idempotency_requests ("
        "operation TEXT NOT NULL, request_key TEXT PRIMARY KEY NOT NULL, request_hash TEXT NOT NULL, "
        "response_json TEXT NOT NULL, response_status INTEGER NOT NULL)"
    )


def _migration_lock_acquired(conn):
    """Provide a private synchronization point for migration tests."""


def _after_schema_version_update(conn):
    """Provide a private late-migration failure point for migration tests."""


def initialize_database_path(db_path):
    """Upgrade one existing wallet database and seed the v2 taxonomy.

    This is deliberately explicit for deployment: callers must have made their
    own recoverable backup before invoking it against a production database.
    """
    conn = connect_database(db_path)
    try:
        _create_base_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            _migration_lock_acquired(conn)
            current_version = schema_version(conn)
            if current_version < 2:
                _migrate_to_version_2(conn)
            if current_version < 3:
                _migrate_to_version_3(conn)
            if current_version < 4:
                _migrate_to_version_4(conn)
            if current_version < 5:
                _migrate_to_version_5(conn)
            if current_version < 6:
                _migrate_to_version_6(conn)
            if current_version < SCHEMA_VERSION:
                conn.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(SCHEMA_VERSION),),
                )
                _after_schema_version_update(conn)
            # Taxonomy is editable data, not a schema migration. Schema v3+
            # already initialized it in previous releases; adopt those values
            # without restoring deleted defaults or overwriting preferences.
            taxonomy_initialized = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'taxonomy_initialized'"
            ).fetchone()
            if taxonomy_initialized is None:
                if current_version < 3:
                    seed_taxonomy(conn)
                conn.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('taxonomy_initialized', '1')"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()



def init_database(app):
    initialize_database_path(app.config["DB_PATH"])
    if not app.extensions.get("wallet_db_teardown_registered"):
        app.teardown_appcontext(close_db)
        app.extensions["wallet_db_teardown_registered"] = True


def main():
    parser = argparse.ArgumentParser(description="Initialize the wallet v2 schema explicitly")
    parser.add_argument("--initialize-schema", metavar="DB_PATH", required=True)
    args = parser.parse_args()
    initialize_database_path(args.initialize_schema)
    print("schema initialized")


if __name__ == "__main__":
    main()
