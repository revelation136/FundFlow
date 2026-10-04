"""Deleting accounts, funds and obligations that already appear in the ledger.

Two ways to remove something with history:

* **merge** - move every posting (and obligation link) to another account/fund,
  then delete the original. Nothing is lost; balances move with it.
* **purge** - permanently delete the original *and every transaction that
  touches it*. Whole transactions are removed so the ledger stays balanced, and
  a full snapshot of each one is written to the audit log first.
"""
import json
from collections import defaultdict

from .db import audit
from .ledger import HOLDING_KINDS, LedgerError, _snapshot, balances

KIND_NOUN = {"asset": "account", "liability": "credit card / loan", "income": "income source",
             "expense": "expense category", "equity": "bookkeeping account"}

# Keys in a transaction's saved form that hold account or fund ids.
FORM_KEYS = {
    "account": (("source_id", "account_id", "category_id", "from_account_id", "to_account_id", "fee_category_id"),
                (("postings", "account_id"),)),
    "fund": (("fund_id", "from_fund_id", "to_fund_id"), (("splits", "fund_id"), ("postings", "fund_id"))),
}


def _row(conn, table, ident, label):
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (ident,)).fetchone()
    if row is None:
        raise LedgerError(f"{label} not found")
    return row


def _txn_ids(conn, column, ident) -> list:
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT transaction_id FROM postings WHERE {column} = ? ORDER BY transaction_id", (ident,)
    )]


def usage(conn, kind: str, ident: int) -> dict:
    """What would be affected by deleting this account / fund / obligation."""
    if kind == "obligation":
        ob = _row(conn, "obligations", ident, "obligation")
        rows = conn.execute(
            "SELECT id, voided_at FROM transactions WHERE obligation_id = ?", (ident,)
        ).fetchall()
        paid = conn.execute(
            """SELECT COALESCE(SUM(p.amount), 0) FROM postings p
               JOIN transactions t ON t.id = p.transaction_id JOIN accounts a ON a.id = p.account_id
               WHERE t.obligation_id = ? AND t.voided_at IS NULL AND a.kind = 'expense'""",
            (ident,),
        ).fetchone()[0]
        return {"name": ob["name"], "transactions": len(rows),
                "voided": sum(1 for r in rows if r["voided_at"]), "amount": paid, "obligations": [],
                "impact": _purge_impact(conn, "obligation", ident, [r["id"] for r in rows])}
    table, column = ("accounts", "account_id") if kind == "account" else ("funds", "fund_id")
    row = _row(conn, table, ident, kind)
    ids = _txn_ids(conn, column, ident)
    voided = conn.execute(
        f"SELECT COUNT(*) FROM transactions WHERE voided_at IS NOT NULL AND id IN "
        f"(SELECT transaction_id FROM postings WHERE {column} = ?)", (ident,)
    ).fetchone()[0]
    where = "account_id = ? OR category_id = ?" if kind == "account" else "fund_id = ?"
    obs = [r["name"] for r in conn.execute(
        f"SELECT name FROM obligations WHERE {where}", (ident,) * where.count("?")
    )]
    return {"name": row["name"], "transactions": len(ids), "voided": voided, "obligations": obs,
            "impact": _purge_impact(conn, kind, ident, ids)}


def _purge_impact(conn, kind, ident, txn_ids) -> list:
    """Balance changes elsewhere if these transactions were deleted outright."""
    if not txn_ids:
        return []
    marks = ",".join("?" * len(txn_ids))
    rows = conn.execute(
        f"""SELECT p.account_id, a.name AS account_name, a.kind, p.fund_id, f.name AS fund_name, p.amount
            FROM postings p JOIN transactions t ON t.id = p.transaction_id
            JOIN accounts a ON a.id = p.account_id JOIN funds f ON f.id = p.fund_id
            WHERE t.voided_at IS NULL AND t.id IN ({marks}) AND a.kind IN ('asset', 'liability')""",
        txn_ids,
    ).fetchall()
    funds, accounts, names = defaultdict(int), defaultdict(int), {}
    for r in rows:
        if not (kind == "fund" and r["fund_id"] == ident):
            funds[r["fund_id"]] -= r["amount"]
            names[("fund", r["fund_id"])] = r["fund_name"]
        if not (kind == "account" and r["account_id"] == ident):
            # Removing a posting reverses it; liabilities are shown as amount owed.
            accounts[r["account_id"]] += -r["amount"] if r["kind"] == "asset" else r["amount"]
            names[("account", r["account_id"])] = (r["account_name"], r["kind"])
    bal = balances(conn)
    out = []
    for fid, delta in funds.items():
        if delta:
            after = bal["funds"].get(fid, 0) + delta
            out.append({"type": "fund", "id": fid, "name": names[("fund", fid)], "delta": delta,
                        "after": after, "goes_negative": after < 0})
    for aid, delta in accounts.items():
        if delta:
            name, akind = names[("account", aid)]
            after = bal["accounts"].get(aid, 0) + delta
            out.append({"type": "account", "id": aid, "name": name, "delta": delta, "after": after,
                        "goes_negative": akind == "asset" and after < 0})
    out.sort(key=lambda x: (not x["goes_negative"], -abs(x["delta"])))
    return out


def _remap_forms(conn, txn_ids, kind, old, new) -> None:
    """Keep each transaction's saved form in step with its postings, so it stays editable."""
    keys, lists = FORM_KEYS[kind]
    for tid in txn_ids:
        raw = conn.execute("SELECT form FROM transactions WHERE id = ?", (tid,)).fetchone()["form"]
        if not raw:
            continue
        form = json.loads(raw)
        for k in keys:
            if form.get(k) not in (None, "") and str(form[k]) == str(old):
                form[k] = new
        for list_key, field in lists:
            for line in form.get(list_key) or []:
                if isinstance(line, dict) and line.get(field) not in (None, "") and str(line[field]) == str(old):
                    line[field] = new
        conn.execute("UPDATE transactions SET form = ? WHERE id = ?", (json.dumps(form, sort_keys=True), tid))


def _void_no_ops(conn, txn_ids, reason) -> list:
    """After a merge, e.g. an allocation from Unallocated to the merged fund nets to nothing."""
    voided = []
    for tid in txn_ids:
        if conn.execute("SELECT voided_at FROM transactions WHERE id = ?", (tid,)).fetchone()["voided_at"]:
            continue
        net = defaultdict(int)
        for p in conn.execute("SELECT account_id, fund_id, amount FROM postings WHERE transaction_id = ?", (tid,)):
            net[(p["account_id"], p["fund_id"])] += p["amount"]
        if not any(net.values()):
            conn.execute(
                "UPDATE transactions SET voided_at = datetime('now'), void_reason = ? WHERE id = ?", (reason, tid)
            )
            voided.append(tid)
    return voided


def _purge(conn, txn_ids) -> list:
    """Hard-delete whole transactions. Must run inside the caller's `with conn:` block."""
    snapshots = [{"id": tid, **_snapshot(conn, tid)} for tid in txn_ids]
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('purge_authorized', '1')")
    conn.executemany("DELETE FROM postings WHERE transaction_id = ?", [(t,) for t in txn_ids])
    conn.executemany("DELETE FROM transactions WHERE id = ?", [(t,) for t in txn_ids])
    conn.execute("DELETE FROM meta WHERE key = 'purge_authorized'")
    return snapshots


# ------------------------------------------------------------------ accounts

def delete_account(conn, ident: int, mode: str = "delete", into: int | None = None) -> dict:
    src = _row(conn, "accounts", ident, "account")
    if src["is_system"]:
        raise LedgerError("system accounts cannot be deleted")
    ids = _txn_ids(conn, "account_id", ident)
    if mode == "merge":
        if into in (None, ""):
            raise LedgerError("choose the account to move everything into")
        dst = _row(conn, "accounts", int(into), "target account")
        if dst["id"] == src["id"]:
            raise LedgerError("choose a different account to move everything into")
        if dst["kind"] != src["kind"]:
            raise LedgerError(f"{src['name']} can only be merged into another {KIND_NOUN[src['kind']]}")
        with conn:
            conn.execute("UPDATE postings SET account_id = ? WHERE account_id = ?", (dst["id"], ident))
            conn.execute("UPDATE obligations SET account_id = ? WHERE account_id = ?", (dst["id"], ident))
            conn.execute("UPDATE obligations SET category_id = ? WHERE category_id = ?", (dst["id"], ident))
            _remap_forms(conn, ids, "account", ident, dst["id"])
            no_ops = _void_no_ops(conn, ids, f"No effect after merging {src['name']} into {dst['name']}")
            conn.execute("DELETE FROM accounts WHERE id = ?", (ident,))
            audit(conn, "merge", "account", ident, {
                "before": dict(src), "into": dst["id"], "into_name": dst["name"],
                "transactions": ids, "voided_as_no_op": no_ops,
            })
        return {"merged_into": dst["name"], "transactions": len(ids), "voided_as_no_op": len(no_ops)}
    if mode != "delete":
        raise LedgerError("mode must be 'merge' or 'delete'")
    with conn:
        snapshots = _purge(conn, ids)
        conn.execute("UPDATE obligations SET account_id = NULL WHERE account_id = ?", (ident,))
        conn.execute("UPDATE obligations SET category_id = NULL WHERE category_id = ?", (ident,))
        conn.execute("DELETE FROM accounts WHERE id = ?", (ident,))
        audit(conn, "delete", "account", ident, {"before": dict(src), "deleted_transactions": snapshots})
    return {"deleted_transactions": len(ids)}


# --------------------------------------------------------------------- funds

def delete_fund(conn, ident: int, mode: str = "delete", into: int | None = None) -> dict:
    src = _row(conn, "funds", ident, "fund")
    if src["is_system"]:
        raise LedgerError("the Unallocated fund cannot be deleted")
    ids = _txn_ids(conn, "fund_id", ident)
    if mode == "merge":
        if into in (None, ""):
            raise LedgerError("choose the fund to move everything into")
        dst = _row(conn, "funds", int(into), "target fund")
        if dst["id"] == src["id"]:
            raise LedgerError("choose a different fund to move everything into")
        with conn:
            conn.execute("UPDATE postings SET fund_id = ? WHERE fund_id = ?", (dst["id"], ident))
            conn.execute("UPDATE obligations SET fund_id = ? WHERE fund_id = ?", (dst["id"], ident))
            _remap_forms(conn, ids, "fund", ident, dst["id"])
            no_ops = _void_no_ops(conn, ids, f"No effect after merging {src['name']} into {dst['name']}")
            conn.execute("DELETE FROM funds WHERE id = ?", (ident,))
            audit(conn, "merge", "fund", ident, {
                "before": dict(src), "into": dst["id"], "into_name": dst["name"],
                "transactions": ids, "voided_as_no_op": no_ops,
            })
        return {"merged_into": dst["name"], "transactions": len(ids), "voided_as_no_op": len(no_ops)}
    if mode != "delete":
        raise LedgerError("mode must be 'merge' or 'delete'")
    with conn:
        snapshots = _purge(conn, ids)
        conn.execute("UPDATE obligations SET fund_id = NULL WHERE fund_id = ?", (ident,))
        conn.execute("DELETE FROM funds WHERE id = ?", (ident,))
        audit(conn, "delete", "fund", ident, {"before": dict(src), "deleted_transactions": snapshots})
    return {"deleted_transactions": len(ids)}


# --------------------------------------------------------------- obligations

def _as_expense(conn, tid) -> tuple:
    """Rebuild a payment's form as a plain expense (or a journal if it has an unusual shape)."""
    ps = [dict(p) for p in conn.execute(
        """SELECT p.account_id, p.fund_id, p.amount, p.memo, a.kind FROM postings p
           JOIN accounts a ON a.id = p.account_id WHERE p.transaction_id = ? ORDER BY p.id""", (tid,)
    )]
    paid_from = [p for p in ps if p["kind"] in HOLDING_KINDS]
    spent = [p for p in ps if p["kind"] == "expense"]
    if len(ps) == 2 and len(paid_from) == 1 and len(spent) == 1 and spent[0]["amount"] > 0:
        return "expense", {
            "account_id": paid_from[0]["account_id"], "fund_id": paid_from[0]["fund_id"],
            "category_id": spent[0]["account_id"], "amount": spent[0]["amount"],
        }
    return "journal", {"postings": [
        {"account_id": p["account_id"], "fund_id": p["fund_id"], "amount": p["amount"], "memo": p["memo"]} for p in ps
    ]}


def delete_obligation(conn, ident: int, payments: str = "keep") -> dict:
    ob = _row(conn, "obligations", ident, "obligation")
    ids = [r[0] for r in conn.execute("SELECT id FROM transactions WHERE obligation_id = ? ORDER BY id", (ident,))]
    if payments not in ("keep", "delete"):
        raise LedgerError("payments must be 'keep' or 'delete'")
    with conn:
        if payments == "delete":
            detail = {"before": dict(ob), "deleted_transactions": _purge(conn, ids)}
        else:
            for tid in ids:
                ttype, form = _as_expense(conn, tid)
                conn.execute(
                    """UPDATE transactions SET obligation_id = NULL, installments = 1, type = ?, form = ?,
                           updated_at = datetime('now') WHERE id = ?""",
                    (ttype, json.dumps(form, sort_keys=True), tid),
                )
            detail = {"before": dict(ob), "payments_kept_as_expenses": ids}
        conn.execute("DELETE FROM obligations WHERE id = ?", (ident,))
        audit(conn, "delete", "obligation", ident, detail)
    return {"payments": len(ids), "payments_mode": payments}
