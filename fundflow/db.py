"""SQLite schema and connection handling."""
import json
import sqlite3

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Ledger accounts. "asset"/"liability" are places money physically sits
-- (bank, e-wallet, cash, credit card). "income" accounts are sources,
-- "expense" accounts are destinations/categories, "equity" is bookkeeping.
CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('asset','liability','income','expense','equity')),
    subtype    TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    is_system  INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (kind, name)
);

-- Funds are purposes/earmarks. Money in one bank account can belong to many funds.
CREATE TABLE IF NOT EXISTS funds (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#2a78d6',
    target      INTEGER,
    is_system   INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Recurring commitments: amortizations, insurance premiums, subscriptions...
CREATE TABLE IF NOT EXISTS obligations (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('amortization','insurance','subscription','bill','other')),
    amount         INTEGER NOT NULL CHECK (amount > 0),
    frequency      TEXT NOT NULL CHECK (frequency IN ('monthly','quarterly','semiannual','annual')),
    first_due      TEXT NOT NULL,
    total_payments INTEGER CHECK (total_payments IS NULL OR total_payments > 0),
    prior_payments INTEGER NOT NULL DEFAULT 0 CHECK (prior_payments >= 0),
    fund_id        INTEGER REFERENCES funds(id),
    account_id     INTEGER REFERENCES accounts(id),
    category_id    INTEGER REFERENCES accounts(id),
    notes          TEXT NOT NULL DEFAULT '',
    archived       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY,
    date          TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('income','expense','allocation','transfer','payment','opening','adjustment','journal')),
    description   TEXT NOT NULL DEFAULT '',
    reference     TEXT NOT NULL DEFAULT '',
    obligation_id INTEGER REFERENCES obligations(id),
    installments  INTEGER NOT NULL DEFAULT 1 CHECK (installments > 0),
    form          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    voided_at     TEXT,
    void_reason   TEXT NOT NULL DEFAULT ''
);

-- Each posting moves money for one (account, fund) pair. Postings of a
-- transaction always sum to zero (double entry).
CREATE TABLE IF NOT EXISTS postings (
    id             INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    fund_id        INTEGER NOT NULL REFERENCES funds(id),
    amount         INTEGER NOT NULL CHECK (amount <> 0),
    memo           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_postings_txn ON postings(transaction_id);
CREATE INDEX IF NOT EXISTS idx_postings_account ON postings(account_id);
CREATE INDEX IF NOT EXISTS idx_postings_fund ON postings(fund_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date, id);
CREATE INDEX IF NOT EXISTS idx_transactions_obligation ON transactions(obligation_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY,
    at        TEXT NOT NULL DEFAULT (datetime('now')),
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    detail    TEXT NOT NULL DEFAULT '{}'
);

-- Transactions are voided, not deleted. The only exception is an explicit,
-- confirmed purge (see maintenance.py), which raises a flag for the duration
-- of its own database transaction and records full snapshots in the audit log.
CREATE TRIGGER IF NOT EXISTS transactions_no_delete
BEFORE DELETE ON transactions
WHEN NOT EXISTS (SELECT 1 FROM meta WHERE key = 'purge_authorized')
BEGIN
    SELECT RAISE(ABORT, 'transactions cannot be deleted; void them instead');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;
"""

DEFAULT_SETTINGS = {
    "currency_symbol": "₱",
    "currency_code": "PHP",
    "locale": "en-PH",
}

# name, kind, subtype, is_system
SEED_ACCOUNTS = [
    ("Opening Balance", "equity", "", 1),
    ("Reconciliation", "equity", "", 1),
    ("Salary", "income", "", 0),
    ("Bank & Transfer Fees", "expense", "", 0),
]


# Validated categorical palette (light-mode steps). Fund colors are drawn from
# it in fixed order so each fund keeps a stable, distinguishable identity.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def next_fund_color(conn: sqlite3.Connection) -> str:
    used = {r["color"] for r in conn.execute("SELECT color FROM funds WHERE archived = 0")}
    for color in PALETTE:
        if color not in used:
            return color
    count = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
    return PALETTE[count % len(PALETTE)]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Default rollback journal (not WAL): keeps the database a single file,
    # which plays nicely with OneDrive/Dropbox style folder sync.
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if have is None:
        with conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value))
            conn.execute(
                "INSERT INTO funds (name, description, color, is_system) VALUES (?, ?, ?, 1)",
                ("Unallocated", "Money that has not been assigned a purpose yet.", "#898781"),
            )
            for name, kind, subtype, is_system in SEED_ACCOUNTS:
                conn.execute(
                    "INSERT INTO accounts (name, kind, subtype, is_system) VALUES (?, ?, ?, ?)",
                    (name, kind, subtype, is_system),
                )
        return
    version = int(have["value"])
    if version < 2:
        # v2: the no-delete trigger gained an escape hatch for confirmed purges.
        with conn:
            conn.execute("DROP TRIGGER IF EXISTS transactions_no_delete")
            conn.execute(
                """CREATE TRIGGER transactions_no_delete
                   BEFORE DELETE ON transactions
                   WHEN NOT EXISTS (SELECT 1 FROM meta WHERE key = 'purge_authorized')
                   BEGIN
                       SELECT RAISE(ABORT, 'transactions cannot be deleted; void them instead');
                   END"""
            )
            conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")


def system_ids(conn: sqlite3.Connection) -> dict:
    unallocated = conn.execute("SELECT id FROM funds WHERE is_system = 1 ORDER BY id LIMIT 1").fetchone()
    opening = conn.execute(
        "SELECT id FROM accounts WHERE kind = 'equity' AND name = 'Opening Balance'"
    ).fetchone()
    recon = conn.execute(
        "SELECT id FROM accounts WHERE kind = 'equity' AND name = 'Reconciliation'"
    ).fetchone()
    return {
        "unallocated_fund": unallocated["id"],
        "opening_account": opening["id"],
        "reconciliation_account": recon["id"],
    }


def settings(conn: sqlite3.Connection) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM meta WHERE key NOT IN ('schema_version', 'purge_authorized')"):
        out[row["key"]] = row["value"]
    return out


def audit(conn: sqlite3.Connection, action: str, entity: str, entity_id, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log (action, entity, entity_id, detail) VALUES (?, ?, ?, ?)",
        (action, entity, entity_id, json.dumps(detail, ensure_ascii=False, sort_keys=True)),
    )
