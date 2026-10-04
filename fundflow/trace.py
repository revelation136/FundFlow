"""Provenance tracing: where did each fund's money come from, and where did it go?

The ledger is replayed chronologically while every fund keeps a *composition*:
how many centavos of its balance trace back to each origin (an income source,
an opening balance, a refund...). When money leaves a fund - to another fund or
to an expense - it carries a pro-rata share of that composition with it.

Invariant: for every fund, ``sum(composition) == fund balance``.
"""
from collections import defaultdict

from .money import split_pro_rata

HOLDING = ("asset", "liability")
DEFICIT = "deficit"      # negative entry inside an overdrawn fund
UNFUNDED = "unfunded"    # money that left a fund that did not have it


def _add(comp: dict, origin: str, amount: int) -> None:
    hole = -comp.get(DEFICIT, 0)
    if hole > 0:
        fill = min(hole, amount)
        comp[DEFICIT] += fill
        if comp[DEFICIT] == 0:
            del comp[DEFICIT]
        amount -= fill
    if amount:
        comp[origin] = comp.get(origin, 0) + amount


def _take(comp: dict, amount: int) -> dict:
    positives = {o: c for o, c in comp.items() if c > 0}
    available = sum(positives.values())
    taken = split_pro_rata(min(amount, available), positives)
    for origin, cents in taken.items():
        comp[origin] -= cents
        if comp[origin] == 0:
            del comp[origin]
    short = amount - sum(taken.values())
    if short:
        comp[DEFICIT] = comp.get(DEFICIT, 0) - short
        taken[UNFUNDED] = taken.get(UNFUNDED, 0) + short
    return taken


def _merge(target: dict, extra: dict) -> None:
    for k, v in extra.items():
        target[k] = target.get(k, 0) + v


def run(conn, start: str | None = None, end: str | None = None) -> dict:
    """Replay the ledger up to ``end``; collect flow edges for [start, end]."""
    kinds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM accounts")}
    sql = """SELECT t.id, t.date, p.account_id, p.fund_id, p.amount
             FROM transactions t JOIN postings p ON p.transaction_id = t.id
             WHERE t.voided_at IS NULL"""
    args = []
    if end:
        sql += " AND t.date <= ?"
        args.append(end)
    sql += " ORDER BY t.date, t.id, p.id"

    grouped = defaultdict(list)
    dates = {}
    for row in conn.execute(sql, args):
        grouped[row["id"]].append(row)
        dates[row["id"]] = row["date"]

    comp = defaultdict(dict)                 # fund -> {origin: cents}
    edges = defaultdict(int)                 # (from_node, to_node) -> cents, within period
    txn_origins = {}                         # txn -> {origin: cents} of money spent
    category_origins = defaultdict(dict)     # expense account -> {origin: cents}, within period

    for txn_id, postings in grouped.items():
        in_period = start is None or dates[txn_id] >= start
        delta = defaultdict(int)
        ext_in = defaultdict(int)
        ext_out = defaultdict(int)
        inflows, outflows = [], []
        for p in postings:
            if kinds[p["account_id"]] in HOLDING:
                delta[p["fund_id"]] += p["amount"]
            elif p["amount"] < 0:
                inflows.append(p)
                ext_in[p["fund_id"]] += -p["amount"]
            else:
                outflows.append(p)
                ext_out[p["fund_id"]] += p["amount"]

        # 1. Money entering funds from outside (income, opening balances, refunds).
        for p in inflows:
            origin = f"a{p['account_id']}"
            _add(comp[p["fund_id"]], origin, -p["amount"])
            if in_period:
                edges[(f"in:{p['account_id']}", f"fund:{p['fund_id']}")] += -p["amount"]

        # 2. Money moving between funds (allocations). Match donors to
        #    recipients greedily so every centavo is accounted for exactly.
        internal = {f: delta[f] - ext_in[f] + ext_out[f] for f in set(delta) | set(ext_in) | set(ext_out)}
        donors = sorted((f, -v) for f, v in internal.items() if v < 0)
        recipients = sorted([f, v] for f, v in internal.items() if v > 0)
        ri = 0
        for donor, remaining in donors:
            while remaining > 0 and ri < len(recipients):
                recipient, need = recipients[ri]
                moved = min(remaining, need)
                for origin, cents in _take(comp[donor], moved).items():
                    _add(comp[recipient], origin, cents)
                if in_period:
                    edges[(f"fund:{donor}", f"fund:{recipient}")] += moved
                remaining -= moved
                recipients[ri][1] -= moved
                if recipients[ri][1] == 0:
                    ri += 1

        # 3. Money leaving funds (expenses, payments, fees).
        for p in outflows:
            taken = _take(comp[p["fund_id"]], p["amount"])
            _merge(txn_origins.setdefault(txn_id, {}), taken)
            if in_period:
                edges[(f"fund:{p['fund_id']}", f"out:{p['account_id']}")] += p["amount"]
                _merge(category_origins[p["account_id"]], taken)

    return {
        "composition": {f: {o: c for o, c in d.items() if c} for f, d in comp.items()},
        "edges": [{"from": a, "to": b, "amount": v} for (a, b), v in edges.items() if v],
        "txn_origins": txn_origins,
        "category_origins": dict(category_origins),
    }
