"""Double-entry ledger with a fund (purpose) dimension.

Every posting is tagged with a physical account (where the money sits or
where it came from / went to) *and* a fund (what the money is for). Postings in
a transaction always sum to zero, so a peso can never appear or vanish without
a recorded origin and destination.

Sign convention (debit positive, credit negative):
  * asset accounts      : +  money in,          -  money out
  * liability accounts  : -  more owed,         +  paid down
  * income accounts     : -  money earned (credit)
  * expense accounts    : +  money spent
  * equity accounts     : -  opening balances / positive adjustments
A fund's balance is the sum of its postings on asset and liability accounts.
"""
import json
from collections import defaultdict
from datetime import date

from .db import audit, system_ids

TYPES = ("income", "expense", "allocation", "transfer", "payment", "opening", "adjustment", "journal")
HOLDING_KINDS = ("asset", "liability")
GENERIC_FIELDS = ("type", "date", "description", "reference")


class LedgerError(ValueError):
    """Raised when a transaction would break the ledger's rules."""


# ---------------------------------------------------------------- validation

def _int_amount(value, label="amount", *, positive=True, allow_zero=False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise LedgerError(f"{label} must be an integer number of centavos")
    if positive and value < 0:
        raise LedgerError(f"{label} must be positive")
    if not allow_zero and value == 0:
        raise LedgerError(f"{label} must not be zero")
    return value


def _date(value) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise LedgerError("date must be in YYYY-MM-DD format") from exc


def _id(value, label) -> int:
    if isinstance(value, bool):
        raise LedgerError(f"{label} is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LedgerError(f"{label} is required") from exc


def _account(conn, account_id, label, kinds):
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (_id(account_id, label),)).fetchone()
    if row is None:
        raise LedgerError(f"{label} does not exist")
    if row["kind"] not in kinds:
        raise LedgerError(f"{label} must be one of: {', '.join(kinds)} (got {row['kind']})")
    return row


def _fund(conn, fund_id, label="fund"):
    row = conn.execute("SELECT * FROM funds WHERE id = ?", (_id(fund_id, label),)).fetchone()
    if row is None:
        raise LedgerError(f"{label} does not exist")
    return row


def _splits(conn, raw, label):
    """Normalise a list of {fund_id, amount} lines, merging duplicate funds."""
    merged = {}
    for i, line in enumerate(raw or []):
        if not isinstance(line, dict):
            raise LedgerError(f"{label} line {i + 1} is invalid")
        amount = line.get("amount")
        if amount in (None, 0, ""):
            continue
        fund = _fund(conn, line.get("fund_id"), f"{label} line {i + 1} fund")
        merged[fund["id"]] = merged.get(fund["id"], 0) + _int_amount(amount, f"{label} line {i + 1} amount")
    return merged


# ---------------------------------------------------------- posting builders

def _p(account_id, fund_id, amount, memo=""):
    return {"account_id": account_id, "fund_id": fund_id, "amount": amount, "memo": memo}


def _build_income(conn, f, sys):
    source = _account(conn, f.get("source_id"), "source", ("income",))
    account = _account(conn, f.get("account_id"), "account", HOLDING_KINDS)
    amount = _int_amount(f.get("amount"))
    splits = _splits(conn, f.get("splits"), "split")
    assigned = sum(splits.values())
    if assigned > amount:
        raise LedgerError("fund splits add up to more than the income amount")
    if assigned < amount:
        splits[sys["unallocated_fund"]] = splits.get(sys["unallocated_fund"], 0) + amount - assigned
    out = []
    for fund_id, amt in splits.items():
        out += [_p(source["id"], fund_id, -amt), _p(account["id"], fund_id, amt)]
    return out


def _build_expense(conn, f, sys):
    account = _account(conn, f.get("account_id"), "paid-from account", HOLDING_KINDS)
    fund = _fund(conn, f.get("fund_id"))
    category = _account(conn, f.get("category_id"), "category", ("expense",))
    amount = _int_amount(f.get("amount"))
    return [_p(account["id"], fund["id"], -amount), _p(category["id"], fund["id"], amount)]


def _build_payment(conn, f, sys):
    ob = conn.execute(
        "SELECT * FROM obligations WHERE id = ?", (_id(f.get("obligation_id"), "obligation"),)
    ).fetchone()
    if ob is None:
        raise LedgerError("obligation does not exist")
    filled = dict(f)
    for key, default in (
        ("account_id", ob["account_id"]),
        ("fund_id", ob["fund_id"]),
        ("category_id", ob["category_id"]),
    ):
        if filled.get(key) in (None, ""):
            filled[key] = default
    if filled.get("amount") in (None, ""):
        filled["amount"] = ob["amount"] * _installments(f)
    if filled.get("fund_id") in (None, ""):
        filled["fund_id"] = sys["unallocated_fund"]
    if filled.get("category_id") in (None, ""):
        raise LedgerError("choose an expense category for this payment")
    return _build_expense(conn, filled, sys)


def _build_allocation(conn, f, sys):
    account = _account(conn, f.get("account_id"), "account", HOLDING_KINDS)
    source = _fund(conn, f.get("from_fund_id"), "from fund")
    raw = f.get("splits")
    if not raw and f.get("to_fund_id") not in (None, ""):
        raw = [{"fund_id": f.get("to_fund_id"), "amount": f.get("amount")}]
    splits = _splits(conn, raw, "allocation")
    if not splits:
        raise LedgerError("allocate to at least one fund")
    if source["id"] in splits:
        raise LedgerError("cannot allocate a fund to itself")
    total = sum(splits.values())
    out = [_p(account["id"], source["id"], -total)]
    out += [_p(account["id"], fund_id, amt) for fund_id, amt in splits.items()]
    return out


def _build_transfer(conn, f, sys):
    src = _account(conn, f.get("from_account_id"), "from account", HOLDING_KINDS)
    dst = _account(conn, f.get("to_account_id"), "to account", HOLDING_KINDS)
    if src["id"] == dst["id"]:
        raise LedgerError("from and to accounts must differ")
    fund = _fund(conn, f.get("fund_id"))
    amount = _int_amount(f.get("amount"))
    fee = f.get("fee") or 0
    fee = _int_amount(fee, "fee", allow_zero=True)
    out = [_p(src["id"], fund["id"], -(amount + fee)), _p(dst["id"], fund["id"], amount)]
    if fee:
        cat = _account(conn, f.get("fee_category_id"), "fee category", ("expense",))
        out.append(_p(cat["id"], fund["id"], fee, "transfer fee"))
    return out


def _natural(account, amount):
    """Convert an amount in the account's natural direction into a posting amount."""
    return -amount if account["kind"] == "liability" else amount


def _build_opening(conn, f, sys):
    account = _account(conn, f.get("account_id"), "account", HOLDING_KINDS)
    fund = _fund(conn, f.get("fund_id"))
    amount = _natural(account, _int_amount(f.get("amount"), positive=False))
    return [_p(account["id"], fund["id"], amount), _p(sys["opening_account"], fund["id"], -amount)]


def _build_adjustment(conn, f, sys):
    account = _account(conn, f.get("account_id"), "account", HOLDING_KINDS)
    fund = _fund(conn, f.get("fund_id"))
    amount = _natural(account, _int_amount(f.get("amount"), positive=False))
    return [_p(account["id"], fund["id"], amount), _p(sys["reconciliation_account"], fund["id"], -amount)]


def _build_journal(conn, f, sys):
    out = []
    for i, line in enumerate(f.get("postings") or []):
        account = _account(
            conn, line.get("account_id"), f"line {i + 1} account",
            ("asset", "liability", "income", "expense", "equity"),
        )
        fund = _fund(conn, line.get("fund_id"), f"line {i + 1} fund")
        amount = _int_amount(line.get("amount"), f"line {i + 1} amount", positive=False)
        out.append(_p(account["id"], fund["id"], amount, str(line.get("memo") or "")))
    return out


BUILDERS = {
    "income": _build_income,
    "expense": _build_expense,
    "payment": _build_payment,
    "allocation": _build_allocation,
    "transfer": _build_transfer,
    "opening": _build_opening,
    "adjustment": _build_adjustment,
    "journal": _build_journal,
}


def _installments(f) -> int:
    raw = f.get("installments")
    if raw in (None, ""):
        return 1
    return _int_amount(_id(raw, "installments"), "installments")


def build_postings(conn, payload: dict) -> list:
    ttype = payload.get("type")
    if ttype not in BUILDERS:
        raise LedgerError(f"type must be one of: {', '.join(TYPES)}")
    postings = BUILDERS[ttype](conn, payload, system_ids(conn))
    if len(postings) < 2:
        raise LedgerError("a transaction needs at least two postings")
    if sum(p["amount"] for p in postings) != 0:
        raise LedgerError("postings do not balance (they must sum to zero)")
    return postings


# ------------------------------------------------------------ write ops

def _normalise(conn, payload: dict):
    if not isinstance(payload, dict):
        raise LedgerError("expected a JSON object")
    postings = build_postings(conn, payload)
    obligation_id = None
    installments = 1
    if payload["type"] == "payment":
        obligation_id = _id(payload.get("obligation_id"), "obligation")
        installments = _installments(payload)
    fields = {
        "date": _date(payload.get("date")),
        "type": payload["type"],
        "description": str(payload.get("description") or "").strip(),
        "reference": str(payload.get("reference") or "").strip(),
        "obligation_id": obligation_id,
        "installments": installments,
        "form": json.dumps({k: v for k, v in payload.items() if k not in GENERIC_FIELDS}, sort_keys=True),
    }
    return fields, postings


def _insert_postings(conn, txn_id, postings):
    conn.executemany(
        "INSERT INTO postings (transaction_id, account_id, fund_id, amount, memo) VALUES (?, ?, ?, ?, ?)",
        [(txn_id, p["account_id"], p["fund_id"], p["amount"], p["memo"]) for p in postings],
    )


def create_transaction(conn, payload: dict) -> int:
    fields, postings = _normalise(conn, payload)
    with conn:
        cur = conn.execute(
            """INSERT INTO transactions (date, type, description, reference, obligation_id, installments, form)
               VALUES (:date, :type, :description, :reference, :obligation_id, :installments, :form)""",
            fields,
        )
        txn_id = cur.lastrowid
        _insert_postings(conn, txn_id, postings)
        audit(conn, "create", "transaction", txn_id, {"after": _snapshot(conn, txn_id)})
    return txn_id


def update_transaction(conn, txn_id: int, payload: dict) -> None:
    current = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if current is None:
        raise LedgerError("transaction not found")
    if current["voided_at"]:
        raise LedgerError("voided transactions cannot be edited")
    fields, postings = _normalise(conn, payload)
    with conn:
        before = _snapshot(conn, txn_id)
        conn.execute(
            """UPDATE transactions SET date = :date, type = :type, description = :description,
                   reference = :reference, obligation_id = :obligation_id, installments = :installments,
                   form = :form, updated_at = datetime('now')
               WHERE id = :id""",
            {**fields, "id": txn_id},
        )
        conn.execute("DELETE FROM postings WHERE transaction_id = ?", (txn_id,))
        _insert_postings(conn, txn_id, postings)
        audit(conn, "update", "transaction", txn_id, {"before": before, "after": _snapshot(conn, txn_id)})


def void_transaction(conn, txn_id: int, reason: str = "") -> None:
    current = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if current is None:
        raise LedgerError("transaction not found")
    if current["voided_at"]:
        raise LedgerError("transaction is already voided")
    with conn:
        conn.execute(
            "UPDATE transactions SET voided_at = datetime('now'), void_reason = ? WHERE id = ?",
            (reason.strip(), txn_id),
        )
        audit(conn, "void", "transaction", txn_id, {"reason": reason.strip(), "before": _snapshot(conn, txn_id)})


def restore_transaction(conn, txn_id: int) -> None:
    """Undo a void. The void and the restore both stay in the audit log."""
    current = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if current is None:
        raise LedgerError("transaction not found")
    if not current["voided_at"]:
        raise LedgerError("transaction is not voided")
    with conn:
        conn.execute("UPDATE transactions SET voided_at = NULL, void_reason = '' WHERE id = ?", (txn_id,))
        audit(conn, "restore", "transaction", txn_id, {
            "voided_at": current["voided_at"], "void_reason": current["void_reason"],
        })


def _snapshot(conn, txn_id):
    t = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    ps = conn.execute(
        "SELECT account_id, fund_id, amount, memo FROM postings WHERE transaction_id = ? ORDER BY id",
        (txn_id,),
    ).fetchall()
    return {
        "date": t["date"], "type": t["type"], "description": t["description"],
        "reference": t["reference"], "obligation_id": t["obligation_id"],
        "installments": t["installments"],
        "postings": [dict(p) for p in ps],
    }


# ------------------------------------------------------------- read ops

def account_kinds(conn) -> dict:
    return {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM accounts")}


def natural_balance(kind: str, raw: int) -> int:
    """Present a raw posting sum the way people think about that account."""
    return raw if kind in ("asset", "expense") else -raw


def balances(conn, as_of: str | None = None) -> dict:
    sql = """SELECT p.account_id, p.fund_id, SUM(p.amount) AS total
             FROM postings p JOIN transactions t ON t.id = p.transaction_id
             WHERE t.voided_at IS NULL"""
    args = []
    if as_of:
        sql += " AND t.date <= ?"
        args.append(as_of)
    sql += " GROUP BY p.account_id, p.fund_id"
    kinds = account_kinds(conn)
    cells = defaultdict(int)          # (account, fund) -> raw sum
    accounts = defaultdict(int)       # account -> raw sum
    funds = defaultdict(int)          # fund -> balance (holding accounts only)
    for row in conn.execute(sql, args):
        cells[(row["account_id"], row["fund_id"])] = row["total"]
        accounts[row["account_id"]] += row["total"]
        if kinds[row["account_id"]] in HOLDING_KINDS:
            funds[row["fund_id"]] += row["total"]
    assets = sum(v for a, v in accounts.items() if kinds[a] == "asset")
    liabilities = -sum(v for a, v in accounts.items() if kinds[a] == "liability")
    return {
        "cells": cells,
        "accounts": {a: natural_balance(kinds[a], v) for a, v in accounts.items()},
        "funds": dict(funds),
        "assets": assets,
        "liabilities": liabilities,
        "net_worth": assets - liabilities,
        "kinds": kinds,
    }


def list_transactions(conn, *, start=None, end=None, fund_id=None, account_id=None, ttype=None,
                      q=None, obligation_id=None, include_voided=False, limit=200, offset=0,
                      ascending=False):
    where, args = [], []
    if not include_voided:
        where.append("t.voided_at IS NULL")
    if start:
        where.append("t.date >= ?")
        args.append(start)
    if end:
        where.append("t.date <= ?")
        args.append(end)
    if ttype:
        where.append("t.type = ?")
        args.append(ttype)
    if obligation_id:
        where.append("t.obligation_id = ?")
        args.append(obligation_id)
    if fund_id:
        where.append("EXISTS (SELECT 1 FROM postings x WHERE x.transaction_id = t.id AND x.fund_id = ?)")
        args.append(fund_id)
    if account_id:
        where.append("EXISTS (SELECT 1 FROM postings x WHERE x.transaction_id = t.id AND x.account_id = ?)")
        args.append(account_id)
    if q:
        where.append("(t.description LIKE ? OR t.reference LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    order = "ASC" if ascending else "DESC"
    sql = "SELECT t.* FROM transactions t"
    if where:
        sql += " WHERE " + " AND ".join(where)
    count = conn.execute(sql.replace("SELECT t.*", "SELECT COUNT(*)", 1), args).fetchone()[0]
    sql += f" ORDER BY t.date {order}, t.id {order}"
    if limit:
        sql += " LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
    rows = conn.execute(sql, args).fetchall()
    return [_hydrate(conn, r) for r in rows], count


def get_transaction(conn, txn_id: int):
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return _hydrate(conn, row) if row else None


def _hydrate(conn, row):
    t = dict(row)
    t["form"] = json.loads(t["form"]) if t["form"] else None
    t["postings"] = [
        dict(p) for p in conn.execute(
            """SELECT p.id, p.account_id, a.name AS account_name, a.kind AS account_kind,
                      p.fund_id, f.name AS fund_name, f.color AS fund_color, p.amount, p.memo
               FROM postings p
               JOIN accounts a ON a.id = p.account_id
               JOIN funds f ON f.id = p.fund_id
               WHERE p.transaction_id = ? ORDER BY p.id""",
            (row["id"],),
        )
    ]
    t["amount"] = sum(p["amount"] for p in t["postings"] if p["amount"] > 0)
    return t


def integrity_report(conn) -> dict:
    """Verify the ledger's invariants. Any problem here means data corruption."""
    problems = []
    unbalanced = conn.execute(
        """SELECT t.id, COALESCE(SUM(p.amount), 0) AS total, COUNT(p.id) AS n
           FROM transactions t LEFT JOIN postings p ON p.transaction_id = t.id
           GROUP BY t.id HAVING total != 0 OR n < 2"""
    ).fetchall()
    for row in unbalanced:
        problems.append(f"transaction #{row['id']} is unbalanced (sum {row['total']}, {row['n']} postings)")
    bal = balances(conn)
    fund_total = sum(bal["funds"].values())
    holding_total = sum(v for (a, _), v in bal["cells"].items() if bal["kinds"][a] in HOLDING_KINDS)
    if fund_total != holding_total:
        problems.append("sum of fund balances does not equal the money held in accounts")
    overdrawn = [
        {"account_id": a, "fund_id": f, "amount": v}
        for (a, f), v in bal["cells"].items()
        if bal["kinds"][a] == "asset" and v < 0
    ]
    counts = conn.execute(
        """SELECT COUNT(*) AS n, SUM(voided_at IS NOT NULL) AS voided FROM transactions"""
    ).fetchone()
    return {
        "ok": not problems,
        "problems": problems,
        "overdrawn": overdrawn,
        "transactions": counts["n"],
        "voided": counts["voided"] or 0,
        "postings": conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0],
        "audit_entries": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "fund_total": fund_total,
        "net_worth": bal["net_worth"],
    }
