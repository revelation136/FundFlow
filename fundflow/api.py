"""JSON API. All money values are integer centavos."""
import csv
import io
import sqlite3
from datetime import date

from flask import Blueprint, Response, current_app, jsonify, request

from . import __version__, ledger, obligations, trace
from .db import PALETTE, audit, next_fund_color, settings, system_ids
from .ledger import HOLDING_KINDS, LedgerError

bp = Blueprint("api", __name__, url_prefix="/api")

ACCOUNT_KINDS = ("asset", "liability", "income", "expense")


def conn() -> sqlite3.Connection:
    return current_app.get_conn()


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise LedgerError("expected a JSON object body")
    return data


@bp.errorhandler(LedgerError)
def ledger_error(exc):
    return jsonify(error=str(exc)), 400


@bp.errorhandler(sqlite3.IntegrityError)
def integrity_error(exc):
    msg = str(exc)
    if "UNIQUE" in msg:
        msg = "that name is already in use"
    return jsonify(error=msg), 400


def _rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args)]


def _usage(c, column) -> dict:
    """How many transactions (voided included) touch each account / fund."""
    return {
        r[0]: r[1]
        for r in c.execute(f"SELECT {column}, COUNT(DISTINCT transaction_id) FROM postings GROUP BY {column}")
    }


def _obligations_using(c, where, ident) -> list:
    return [r["name"] for r in c.execute(f"SELECT name FROM obligations WHERE {where}", (ident,) * where.count("?"))]


def _accounts_by_id():
    return {r["id"]: r for r in _rows("SELECT * FROM accounts")}


def _funds_by_id():
    return {r["id"]: r for r in _rows("SELECT * FROM funds")}


def _origin_label(key, accounts):
    if key == trace.UNFUNDED:
        return {"key": key, "label": "Unfunded (overdrawn fund)", "kind": "unfunded"}
    if key == trace.DEFICIT:
        return {"key": key, "label": "Deficit", "kind": "deficit"}
    acct = accounts.get(int(key[1:]))
    if acct is None:
        return {"key": key, "label": key, "kind": "unknown"}
    label = acct["name"]
    if acct["kind"] == "expense":
        label = f"Refund: {acct['name']}"
    return {"key": key, "label": label, "kind": acct["kind"], "account_id": acct["id"]}


def _labelled(comp: dict, accounts) -> list:
    total = sum(v for v in comp.values() if v > 0) or 1
    out = []
    for key, cents in sorted(comp.items(), key=lambda kv: -kv[1]):
        item = _origin_label(key, accounts)
        item["amount"] = cents
        item["share"] = round(cents / total, 4) if cents > 0 else 0
        out.append(item)
    return out


# --------------------------------------------------------------- meta

@bp.get("/meta")
def meta():
    return jsonify(
        version=__version__,
        today=date.today().isoformat(),
        settings=settings(conn()),
        system=system_ids(conn()),
        obligation_kinds=obligations.KINDS,
        palette=PALETTE,
        frequencies=list(obligations.FREQUENCIES),
    )


@bp.put("/settings")
def update_settings():
    data = body()
    c = conn()
    with c:
        for key in ("currency_symbol", "currency_code", "locale"):
            if key in data:
                value = str(data[key]).strip()
                if not value:
                    raise LedgerError(f"{key} cannot be empty")
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        audit(c, "update", "settings", None, {k: data[k] for k in data})
    return jsonify(settings=settings(c))


# ------------------------------------------------------------ accounts

@bp.get("/accounts")
def list_accounts():
    c = conn()
    bal = ledger.balances(c)
    used = _usage(c, "account_id")
    out = []
    for a in _rows("SELECT * FROM accounts ORDER BY kind, archived, name"):
        a["balance"] = bal["accounts"].get(a["id"], 0)
        a["txn_count"] = used.get(a["id"], 0)
        out.append(a)
    return jsonify(accounts=out)


@bp.post("/accounts")
def create_account():
    data = body()
    name = str(data.get("name") or "").strip()
    kind = data.get("kind")
    if not name:
        raise LedgerError("name is required")
    if kind not in ACCOUNT_KINDS:
        raise LedgerError(f"kind must be one of: {', '.join(ACCOUNT_KINDS)}")
    c = conn()
    with c:
        cur = c.execute(
            "INSERT INTO accounts (name, kind, subtype, notes) VALUES (?, ?, ?, ?)",
            (name, kind, str(data.get("subtype") or ""), str(data.get("notes") or "")),
        )
        audit(c, "create", "account", cur.lastrowid, {"name": name, "kind": kind})
    return jsonify(account=dict(c.execute("SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,)).fetchone())), 201


@bp.put("/accounts/<int:account_id>")
def update_account(account_id):
    data = body()
    c = conn()
    acct = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if acct is None:
        return jsonify(error="account not found"), 404
    changes = {}
    if "name" in data:
        name = str(data["name"] or "").strip()
        if not name:
            raise LedgerError("name is required")
        changes["name"] = name
    for key in ("subtype", "notes"):
        if key in data:
            changes[key] = str(data[key] or "")
    if "kind" in data and data["kind"] != acct["kind"]:
        if data["kind"] not in ACCOUNT_KINDS:
            raise LedgerError(f"kind must be one of: {', '.join(ACCOUNT_KINDS)}")
        if acct["is_system"]:
            raise LedgerError("system accounts cannot change type")
        if _usage(c, "account_id").get(account_id):
            raise LedgerError(
                "this account already has transactions, so it can only switch between types of the same kind "
                "(e.g. bank to e-wallet)"
            )
        users = _obligations_using(c, "account_id = ? OR category_id = ?", account_id)
        if users:
            raise LedgerError(f"used by obligation(s) {', '.join(users)}; change those first")
        changes["kind"] = data["kind"]
    if "archived" in data:
        changes["archived"] = 1 if data["archived"] else 0
        if changes["archived"] and acct["kind"] in HOLDING_KINDS:
            if ledger.balances(c)["accounts"].get(account_id, 0) != 0:
                raise LedgerError("move the remaining balance out before archiving this account")
    if acct["is_system"] and ("name" in changes or changes.get("archived")):
        raise LedgerError("system accounts cannot be renamed or archived")
    if changes:
        with c:
            sets = ", ".join(f"{k} = ?" for k in changes)
            c.execute(f"UPDATE accounts SET {sets} WHERE id = ?", (*changes.values(), account_id))
            audit(c, "update", "account", account_id, {"before": dict(acct), "changes": changes})
    return jsonify(account=dict(c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()))


@bp.delete("/accounts/<int:account_id>")
def delete_account(account_id):
    c = conn()
    acct = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if acct is None:
        return jsonify(error="account not found"), 404
    if acct["is_system"]:
        raise LedgerError("system accounts cannot be deleted")
    used = _usage(c, "account_id").get(account_id, 0)
    if used:
        raise LedgerError(
            f"{acct['name']} appears in {used} transaction(s), so it stays in the ledger. Archive it instead."
        )
    users = _obligations_using(c, "account_id = ? OR category_id = ?", account_id)
    if users:
        raise LedgerError(f"used by obligation(s) {', '.join(users)}; change those first")
    with c:
        c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        audit(c, "delete", "account", account_id, {"before": dict(acct)})
    return jsonify(ok=True)


@bp.get("/accounts/<int:account_id>")
def account_detail(account_id):
    c = conn()
    acct = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if acct is None:
        return jsonify(error="account not found"), 404
    acct = dict(acct)
    bal = ledger.balances(c)
    funds = _funds_by_id()
    by_fund = [
        {"fund_id": f, "fund_name": funds[f]["name"], "color": funds[f]["color"],
         "amount": ledger.natural_balance(acct["kind"], v)}
        for (a, f), v in bal["cells"].items() if a == account_id and v != 0
    ]
    by_fund.sort(key=lambda x: -x["amount"])
    txns, _ = ledger.list_transactions(c, account_id=account_id, limit=None, ascending=True)
    running = 0
    for t in txns:
        delta = sum(p["amount"] for p in t["postings"] if p["account_id"] == account_id)
        t["delta"] = ledger.natural_balance(acct["kind"], delta)
        running += t["delta"]
        t["running"] = running
    txns.reverse()
    acct["balance"] = bal["accounts"].get(account_id, 0)
    return jsonify(account=acct, by_fund=by_fund, history=txns)


# --------------------------------------------------------------- funds

def _validate_fund(data, partial=False):
    out = {}
    if not partial or "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise LedgerError("name is required")
        out["name"] = name
    for key in ("description", "color"):
        if key in data:
            out[key] = str(data[key] or "")
    if "target" in data:
        target = data["target"]
        if target in (None, "", 0):
            out["target"] = None
        elif isinstance(target, bool) or not isinstance(target, int) or target < 0:
            raise LedgerError("target must be a positive number of centavos")
        else:
            out["target"] = target
    if "archived" in data:
        out["archived"] = 1 if data["archived"] else 0
    return out


@bp.get("/funds")
def list_funds():
    c = conn()
    bal = ledger.balances(c)
    accounts = _accounts_by_id()
    used = _usage(c, "fund_id")
    out = []
    for f in _rows("SELECT * FROM funds ORDER BY is_system DESC, archived, name"):
        f["balance"] = bal["funds"].get(f["id"], 0)
        f["txn_count"] = used.get(f["id"], 0)
        f["by_account"] = [
            {"account_id": a, "account_name": accounts[a]["name"], "amount": v}
            for (a, fid), v in bal["cells"].items()
            if fid == f["id"] and accounts[a]["kind"] in HOLDING_KINDS and v != 0
        ]
        out.append(f)
    return jsonify(funds=out)


@bp.post("/funds")
def create_fund():
    data = _validate_fund(body())
    c = conn()
    if not data.get("color"):
        data["color"] = next_fund_color(c)
    with c:
        cols = ", ".join(data)
        cur = c.execute(f"INSERT INTO funds ({cols}) VALUES ({', '.join('?' * len(data))})", tuple(data.values()))
        audit(c, "create", "fund", cur.lastrowid, data)
    return jsonify(fund=dict(c.execute("SELECT * FROM funds WHERE id = ?", (cur.lastrowid,)).fetchone())), 201


@bp.put("/funds/<int:fund_id>")
def update_fund(fund_id):
    c = conn()
    fund = c.execute("SELECT * FROM funds WHERE id = ?", (fund_id,)).fetchone()
    if fund is None:
        return jsonify(error="fund not found"), 404
    changes = _validate_fund(body(), partial=True)
    if fund["is_system"] and ("name" in changes or changes.get("archived")):
        raise LedgerError("the Unallocated fund cannot be renamed or archived")
    if changes.get("archived") and ledger.balances(c)["funds"].get(fund_id, 0) != 0:
        raise LedgerError("allocate the remaining balance elsewhere before archiving this fund")
    if changes:
        with c:
            sets = ", ".join(f"{k} = ?" for k in changes)
            c.execute(f"UPDATE funds SET {sets} WHERE id = ?", (*changes.values(), fund_id))
            audit(c, "update", "fund", fund_id, {"before": dict(fund), "changes": changes})
    return jsonify(fund=dict(c.execute("SELECT * FROM funds WHERE id = ?", (fund_id,)).fetchone()))


@bp.delete("/funds/<int:fund_id>")
def delete_fund(fund_id):
    c = conn()
    fund = c.execute("SELECT * FROM funds WHERE id = ?", (fund_id,)).fetchone()
    if fund is None:
        return jsonify(error="fund not found"), 404
    if fund["is_system"]:
        raise LedgerError("the Unallocated fund cannot be deleted")
    used = _usage(c, "fund_id").get(fund_id, 0)
    if used:
        raise LedgerError(
            f"{fund['name']} appears in {used} transaction(s), so it stays in the ledger. Archive it instead."
        )
    users = _obligations_using(c, "fund_id = ?", fund_id)
    if users:
        raise LedgerError(f"sinking fund for {', '.join(users)}; link those obligations to another fund first")
    with c:
        c.execute("DELETE FROM funds WHERE id = ?", (fund_id,))
        audit(c, "delete", "fund", fund_id, {"before": dict(fund)})
    return jsonify(ok=True)


@bp.get("/funds/<int:fund_id>")
def fund_detail(fund_id):
    c = conn()
    fund = c.execute("SELECT * FROM funds WHERE id = ?", (fund_id,)).fetchone()
    if fund is None:
        return jsonify(error="fund not found"), 404
    fund = dict(fund)
    bal = ledger.balances(c)
    accounts = _accounts_by_id()
    funds = _funds_by_id()
    fund["balance"] = bal["funds"].get(fund_id, 0)
    by_account = [
        {"account_id": a, "account_name": accounts[a]["name"], "kind": accounts[a]["kind"], "amount": v}
        for (a, f), v in bal["cells"].items()
        if f == fund_id and accounts[a]["kind"] in HOLDING_KINDS and v != 0
    ]
    by_account.sort(key=lambda x: -x["amount"])

    tr = trace.run(c)
    node = f"fund:{fund_id}"
    inflows, outflows = [], []
    for e in tr["edges"]:
        if e["to"] == node:
            inflows.append({**_node_info(e["from"], accounts, funds), "amount": e["amount"]})
        elif e["from"] == node:
            outflows.append({**_node_info(e["to"], accounts, funds), "amount": e["amount"]})
    inflows.sort(key=lambda x: -x["amount"])
    outflows.sort(key=lambda x: -x["amount"])

    txns, _ = ledger.list_transactions(c, fund_id=fund_id, limit=None, ascending=True)
    running = 0
    for t in txns:
        t["delta"] = sum(
            p["amount"] for p in t["postings"]
            if p["fund_id"] == fund_id and p["account_kind"] in HOLDING_KINDS
        )
        running += t["delta"]
        t["running"] = running
        if t["id"] in tr["txn_origins"] and t["delta"] < 0:
            t["origins"] = _labelled(tr["txn_origins"][t["id"]], accounts)
    txns.reverse()
    linked = _rows("SELECT id, name, kind FROM obligations WHERE fund_id = ? AND archived = 0", (fund_id,))
    return jsonify(
        fund=fund,
        by_account=by_account,
        composition=_labelled(tr["composition"].get(fund_id, {}), accounts),
        inflows=inflows,
        outflows=outflows,
        history=txns,
        obligations=linked,
    )


def _node_info(node, accounts, funds):
    kind, _, raw = node.partition(":")
    ident = int(raw)
    if kind == "fund":
        f = funds[ident]
        return {"node": node, "type": "fund", "id": ident, "label": f["name"], "color": f["color"]}
    a = accounts[ident]
    label = a["name"]
    if kind == "in" and a["kind"] == "expense":
        label = f"Refund: {label}"
    return {"node": node, "type": kind, "id": ident, "label": label, "account_kind": a["kind"]}


# -------------------------------------------------------- transactions

def _filters():
    args = request.args
    return dict(
        start=args.get("start") or None,
        end=args.get("end") or None,
        fund_id=args.get("fund_id", type=int),
        account_id=args.get("account_id", type=int),
        ttype=args.get("type") or None,
        q=args.get("q") or None,
        obligation_id=args.get("obligation_id", type=int),
        include_voided=args.get("include_voided") in ("1", "true", "yes"),
        limit=min(args.get("limit", 100, type=int), 1000),
        offset=args.get("offset", 0, type=int),
    )


@bp.get("/transactions")
def list_transactions():
    items, total = ledger.list_transactions(conn(), **_filters())
    return jsonify(items=items, total=total)


@bp.post("/transactions")
def create_transaction():
    c = conn()
    txn_id = ledger.create_transaction(c, body())
    return jsonify(transaction=ledger.get_transaction(c, txn_id)), 201


@bp.get("/transactions/<int:txn_id>")
def get_transaction(txn_id):
    t = ledger.get_transaction(conn(), txn_id)
    if t is None:
        return jsonify(error="transaction not found"), 404
    history = _rows(
        "SELECT * FROM audit_log WHERE entity = 'transaction' AND entity_id = ? ORDER BY id", (txn_id,)
    )
    return jsonify(transaction=t, audit=history)


@bp.put("/transactions/<int:txn_id>")
def update_transaction(txn_id):
    c = conn()
    ledger.update_transaction(c, txn_id, body())
    return jsonify(transaction=ledger.get_transaction(c, txn_id))


@bp.post("/transactions/<int:txn_id>/void")
def void_transaction(txn_id):
    c = conn()
    data = request.get_json(silent=True) or {}
    ledger.void_transaction(c, txn_id, str(data.get("reason") or ""))
    return jsonify(transaction=ledger.get_transaction(c, txn_id))


@bp.post("/transactions/<int:txn_id>/restore")
def restore_transaction(txn_id):
    c = conn()
    ledger.restore_transaction(c, txn_id)
    return jsonify(transaction=ledger.get_transaction(c, txn_id))


@bp.get("/balances")
def get_balances():
    bal = ledger.balances(conn())
    return jsonify(
        cells=[{"account_id": a, "fund_id": f, "amount": v} for (a, f), v in bal["cells"].items() if v],
        accounts=bal["accounts"],
        funds=bal["funds"],
    )


# ----------------------------------------------------------- dashboard

@bp.get("/dashboard")
def dashboard():
    c = conn()
    today = date.today()
    bal = ledger.balances(c)
    accounts = _accounts_by_id()
    funds = _funds_by_id()
    holding = [a for a in accounts.values() if a["kind"] in HOLDING_KINDS]
    month_start = today.replace(day=1).isoformat()
    month = c.execute(
        """SELECT a.kind, SUM(p.amount) AS total FROM postings p
           JOIN transactions t ON t.id = p.transaction_id JOIN accounts a ON a.id = p.account_id
           WHERE t.voided_at IS NULL AND t.date >= ? AND t.date <= ? AND a.kind IN ('income', 'expense')
           GROUP BY a.kind""",
        (month_start, today.isoformat()),
    ).fetchall()
    month_totals = {r["kind"]: ledger.natural_balance(r["kind"], r["total"]) for r in month}
    recent, _ = ledger.list_transactions(c, limit=8)
    pl = obligations.plan(c, today)
    return jsonify(
        net_worth=bal["net_worth"],
        assets=bal["assets"],
        liabilities=bal["liabilities"],
        month={"start": month_start, "income": month_totals.get("income", 0),
               "expense": month_totals.get("expense", 0)},
        funds=[
            {**f, "balance": bal["funds"].get(f["id"], 0)}
            for f in sorted(funds.values(), key=lambda f: (-f["is_system"], f["name"]))
            if not f["archived"] or bal["funds"].get(f["id"], 0)
        ],
        accounts=[
            {**a, "balance": bal["accounts"].get(a["id"], 0)}
            for a in sorted(holding, key=lambda a: (a["kind"], a["name"]))
            if not a["archived"] or bal["accounts"].get(a["id"], 0)
        ],
        cells=[
            {"account_id": a, "fund_id": f, "amount": v}
            for (a, f), v in bal["cells"].items()
            if accounts[a]["kind"] in HOLDING_KINDS and v != 0
        ],
        recent=recent,
        plan={k: pl[k] for k in ("totals", "unallocated", "free_to_spend", "today")}
        | {"upcoming": pl["upcoming"][:6]},
    )


@bp.get("/flows")
def flows():
    c = conn()
    start = request.args.get("start") or None
    end = request.args.get("end") or None
    tr = trace.run(c, start=start, end=end)
    accounts = _accounts_by_id()
    funds = _funds_by_id()
    nodes = {}
    links = []
    for e in tr["edges"]:
        for n in (e["from"], e["to"]):
            if n not in nodes:
                nodes[n] = _node_info(n, accounts, funds)
        links.append({"source": e["from"], "target": e["to"], "value": e["amount"]})
    categories = [
        {"account_id": a, "label": accounts[a]["name"], "kind": accounts[a]["kind"],
         "total": sum(comp.values()), "origins": _labelled(comp, accounts)}
        for a, comp in tr["category_origins"].items()
    ]
    categories.sort(key=lambda x: -x["total"])
    composition = [
        {"fund_id": f, "label": funds[f]["name"], "color": funds[f]["color"],
         "origins": _labelled(comp, accounts)}
        for f, comp in tr["composition"].items() if comp
    ]
    return jsonify(nodes=list(nodes.values()), links=links, categories=categories, composition=composition)


# --------------------------------------------------------- obligations

@bp.get("/plan")
def get_plan():
    raw = request.args.get("today")
    today = date.fromisoformat(raw) if raw else date.today()
    return jsonify(obligations.plan(conn(), today))


@bp.post("/obligations")
def create_obligation():
    data = body()
    c = conn()
    fields = obligations.validate(c, data)
    with c:
        if data.get("create_fund") and not fields.get("fund_id"):
            name, n = fields["name"], 1
            while c.execute("SELECT 1 FROM funds WHERE name = ?", (name,)).fetchone():
                n += 1
                name = f"{fields['name']} ({n})"
            cur = c.execute(
                "INSERT INTO funds (name, description, color) VALUES (?, ?, ?)",
                (name, f"Sinking fund for {fields['name']}", str(data.get("color") or next_fund_color(c))),
            )
            fields["fund_id"] = cur.lastrowid
            audit(c, "create", "fund", cur.lastrowid, {"name": name, "for_obligation": fields["name"]})
        cols = ", ".join(fields)
        cur = c.execute(
            f"INSERT INTO obligations ({cols}) VALUES ({', '.join('?' * len(fields))})", tuple(fields.values())
        )
        audit(c, "create", "obligation", cur.lastrowid, fields)
    return jsonify(obligation=dict(c.execute("SELECT * FROM obligations WHERE id = ?", (cur.lastrowid,)).fetchone())), 201


@bp.put("/obligations/<int:ob_id>")
def update_obligation(ob_id):
    c = conn()
    ob = c.execute("SELECT * FROM obligations WHERE id = ?", (ob_id,)).fetchone()
    if ob is None:
        return jsonify(error="obligation not found"), 404
    data = body()
    merged = {**dict(ob), **data}
    obligations.validate(c, merged)  # whole-record consistency (e.g. prior <= total)
    changes = obligations.validate(c, data, partial=True)
    if changes:
        with c:
            sets = ", ".join(f"{k} = ?" for k in changes)
            c.execute(f"UPDATE obligations SET {sets} WHERE id = ?", (*changes.values(), ob_id))
            audit(c, "update", "obligation", ob_id, {"before": dict(ob), "changes": changes})
    return jsonify(obligation=dict(c.execute("SELECT * FROM obligations WHERE id = ?", (ob_id,)).fetchone()))


@bp.delete("/obligations/<int:ob_id>")
def delete_obligation(ob_id):
    c = conn()
    ob = c.execute("SELECT * FROM obligations WHERE id = ?", (ob_id,)).fetchone()
    if ob is None:
        return jsonify(error="obligation not found"), 404
    n = c.execute("SELECT COUNT(*) FROM transactions WHERE obligation_id = ?", (ob_id,)).fetchone()[0]
    if n:
        raise LedgerError(f"{ob['name']} has {n} recorded payment(s), so it stays in the ledger. Archive it instead.")
    with c:
        c.execute("DELETE FROM obligations WHERE id = ?", (ob_id,))
        audit(c, "delete", "obligation", ob_id, {"before": dict(ob)})
    return jsonify(ok=True)


@bp.get("/obligations/<int:ob_id>")
def obligation_detail(ob_id):
    c = conn()
    raw = request.args.get("today")
    today = date.fromisoformat(raw) if raw else date.today()
    pl = obligations.plan(c, today)
    ob = next((o for o in pl["obligations"] if o["id"] == ob_id), None)
    if ob is None:
        row = c.execute("SELECT * FROM obligations WHERE id = ?", (ob_id,)).fetchone()
        if row is None:
            return jsonify(error="obligation not found"), 404
        ob = dict(row)
        ob.update(obligations._status(ob, obligations._payment_stats(c).get(ob_id, {}), today))
    payments, _ = ledger.list_transactions(c, obligation_id=ob_id, limit=None)
    return jsonify(obligation=ob, schedule=obligations.schedule(ob, ob, today), payments=payments)


# --------------------------------------------------- audit & integrity

@bp.get("/audit")
def audit_log():
    limit = min(request.args.get("limit", 200, type=int), 1000)
    offset = request.args.get("offset", 0, type=int)
    rows = _rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    total = conn().execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    return jsonify(items=rows, total=total)


@bp.get("/integrity")
def integrity():
    return jsonify(ledger.integrity_report(conn()))


@bp.get("/export/transactions.csv")
def export_csv():
    c = conn()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["transaction_id", "date", "type", "description", "reference", "account", "account_kind",
                "fund", "amount", "memo", "voided"])
    rows = c.execute(
        """SELECT t.id, t.date, t.type, t.description, t.reference, a.name AS account, a.kind,
                  f.name AS fund, p.amount, p.memo, t.voided_at
           FROM postings p JOIN transactions t ON t.id = p.transaction_id
           JOIN accounts a ON a.id = p.account_id JOIN funds f ON f.id = p.fund_id
           ORDER BY t.date, t.id, p.id"""
    )
    for r in rows:
        sign = "-" if r["amount"] < 0 else ""
        whole, frac = divmod(abs(r["amount"]), 100)
        w.writerow([r["id"], r["date"], r["type"], r["description"], r["reference"], r["account"], r["kind"],
                    r["fund"], f"{sign}{whole}.{frac:02d}", r["memo"], "yes" if r["voided_at"] else ""])
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=fundflow-{date.today().isoformat()}.csv"},
    )


@bp.get("/export/backup.json")
def export_backup():
    c = conn()
    tables = ("meta", "accounts", "funds", "obligations", "transactions", "postings", "audit_log")
    dump = {t: _rows(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}
    dump["exported_at"] = date.today().isoformat()
    dump["version"] = __version__
    resp = jsonify(dump)
    resp.headers["Content-Disposition"] = f"attachment; filename=fundflow-backup-{date.today().isoformat()}.json"
    return resp
