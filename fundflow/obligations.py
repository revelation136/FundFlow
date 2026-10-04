"""Obligations: amortizations, insurance premiums and other recurring payments.

For each obligation we derive - from the ledger, never from hand-kept counters -
how many installments are paid, what is still owed, when the next one is due,
and how much must be set aside each month so the money is there on time
(the "sinking fund" approach). The plan then tells you what is safe to spend.
"""
from calendar import monthrange
from datetime import date, timedelta

from .db import system_ids
from .ledger import LedgerError, balances

KINDS = ("amortization", "insurance", "subscription", "bill", "other")
FREQUENCIES = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
MAX_SCHEDULE = 600  # 50 years of monthly payments


def installment_date(first_due: date, frequency: str, index: int) -> date:
    """Due date of installment ``index`` (0-based), anchored to first_due's day."""
    months = first_due.month - 1 + index * FREQUENCIES[frequency]
    year, month = first_due.year + months // 12, months % 12 + 1
    return date(year, month, min(first_due.day, monthrange(year, month)[1]))


def months_until(today: date, due: date) -> int:
    return (due.year - today.year) * 12 + (due.month - today.month)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def validate(conn, data: dict, partial=False) -> dict:
    """Validate obligation fields coming from the API."""
    out = {}

    def need(key):
        return not partial or key in data

    if need("name"):
        name = str(data.get("name") or "").strip()
        if not name:
            raise LedgerError("name is required")
        out["name"] = name
    if need("kind"):
        if data.get("kind") not in KINDS:
            raise LedgerError(f"kind must be one of: {', '.join(KINDS)}")
        out["kind"] = data["kind"]
    if need("amount"):
        amount = data.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise LedgerError("amount must be a positive number of centavos")
        out["amount"] = amount
    if need("frequency"):
        if data.get("frequency") not in FREQUENCIES:
            raise LedgerError(f"frequency must be one of: {', '.join(FREQUENCIES)}")
        out["frequency"] = data["frequency"]
    if need("first_due"):
        try:
            out["first_due"] = date.fromisoformat(str(data.get("first_due"))).isoformat()
        except ValueError as exc:
            raise LedgerError("first due date must be YYYY-MM-DD") from exc
    if "total_payments" in data:
        tp = data.get("total_payments")
        if tp in (None, "", 0):
            out["total_payments"] = None
        else:
            try:
                tp = int(tp)
            except (TypeError, ValueError) as exc:
                raise LedgerError("total payments must be a whole number") from exc
            if tp <= 0:
                raise LedgerError("total payments must be positive")
            out["total_payments"] = tp
    if "prior_payments" in data:
        try:
            pp = int(data.get("prior_payments") or 0)
        except (TypeError, ValueError) as exc:
            raise LedgerError("payments already made must be a whole number") from exc
        if pp < 0:
            raise LedgerError("payments already made cannot be negative")
        out["prior_payments"] = pp
    for key, table, kinds in (
        ("fund_id", "funds", None),
        ("account_id", "accounts", ("asset", "liability")),
        ("category_id", "accounts", ("expense",)),
    ):
        if key in data:
            value = data.get(key)
            if value in (None, ""):
                out[key] = None
                continue
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(value),)).fetchone()
            if row is None:
                raise LedgerError(f"{key.replace('_id', '')} does not exist")
            if kinds and row["kind"] not in kinds:
                raise LedgerError(f"{key.replace('_id', '')} must be one of: {', '.join(kinds)}")
            out[key] = row["id"]
    if "notes" in data:
        out["notes"] = str(data.get("notes") or "")
    if "archived" in data:
        out["archived"] = 1 if data.get("archived") else 0
    total = out.get("total_payments")
    if total is not None and out.get("prior_payments", 0) > total:
        raise LedgerError("payments already made cannot exceed total payments")
    return out


def _payment_stats(conn) -> dict:
    stats = {}
    rows = conn.execute(
        """SELECT t.obligation_id, SUM(t.installments) AS n, MAX(t.date) AS last_date,
                  SUM((SELECT COALESCE(SUM(p.amount), 0) FROM postings p
                       JOIN accounts a ON a.id = p.account_id
                       WHERE p.transaction_id = t.id AND a.kind = 'expense')) AS paid
           FROM transactions t
           WHERE t.voided_at IS NULL AND t.obligation_id IS NOT NULL
           GROUP BY t.obligation_id"""
    )
    for r in rows:
        stats[r["obligation_id"]] = {"count": r["n"] or 0, "paid": r["paid"] or 0, "last_date": r["last_date"]}
    return stats


def _contributions(conn, start: str, end: str) -> dict:
    """Money added to each fund between start and end (opening balances excluded)."""
    out = {}
    rows = conn.execute(
        """SELECT p.fund_id, SUM(p.amount) AS delta
           FROM postings p
           JOIN transactions t ON t.id = p.transaction_id
           JOIN accounts a ON a.id = p.account_id
           WHERE t.voided_at IS NULL AND a.kind IN ('asset', 'liability')
             AND t.type NOT IN ('opening', 'adjustment') AND t.date >= ? AND t.date <= ?
           GROUP BY t.id, p.fund_id
           HAVING delta > 0""",
        (start, end),
    )
    for r in rows:
        out[r["fund_id"]] = out.get(r["fund_id"], 0) + r["delta"]
    return out


def _status(ob: dict, stats: dict, today: date) -> dict:
    first_due = date.fromisoformat(ob["first_due"])
    per_year = 12 // FREQUENCIES[ob["frequency"]]
    total = ob["total_payments"]
    paid_count = ob["prior_payments"] + stats.get("count", 0)
    remaining = None if total is None else max(0, total - paid_count)
    completed = remaining == 0

    due_now = 0           # unpaid installments due on or before today
    upcoming = None       # first installment due after today
    next_due = None
    if not completed:
        next_due = installment_date(first_due, ob["frequency"], paid_count)
        k = paid_count
        while (total is None or k < total) and k - paid_count < MAX_SCHEDULE:
            d = installment_date(first_due, ob["frequency"], k)
            if d > today:
                upcoming = d
                break
            due_now += 1
            k += 1
    overdue = sum(
        1 for i in range(due_now)
        if installment_date(first_due, ob["frequency"], paid_count + i) < today
    )
    return {
        "paid_count": paid_count,
        "paid_in_ledger": stats.get("paid", 0),
        "last_paid": stats.get("last_date"),
        "remaining_payments": remaining,
        "remaining_balance": None if remaining is None else remaining * ob["amount"],
        "contract_total": None if total is None else total * ob["amount"],
        "completed": completed,
        "next_due": next_due.isoformat() if next_due else None,
        "upcoming_due": upcoming.isoformat() if upcoming else None,
        "due_now_count": due_now,
        "overdue_count": overdue,
        "monthly_equivalent": (ob["amount"] * per_year + 6) // 12,
        "annual_cost": ob["amount"] * per_year,
        "end_date": None if total is None else installment_date(first_due, ob["frequency"], total - 1).isoformat(),
    }


def schedule(ob: dict, status: dict, today: date, horizon_days: int | None = None) -> list:
    """Remaining installments (all of them for finite contracts, else the next year)."""
    if status["completed"]:
        return []
    first_due = date.fromisoformat(ob["first_due"])
    total = ob["total_payments"]
    limit = today + timedelta(days=horizon_days or 366)
    out = []
    k = status["paid_count"]
    while (total is None or k < total) and len(out) < MAX_SCHEDULE:
        d = installment_date(first_due, ob["frequency"], k)
        if (total is None or horizon_days) and d > limit:
            break
        out.append({
            "number": k + 1,
            "of": total,
            "date": d.isoformat(),
            "amount": ob["amount"],
            "overdue": d < today,
            "remaining_after": None if total is None else (total - k - 1) * ob["amount"],
        })
        k += 1
    return out


def plan(conn, today: date | None = None) -> dict:
    """Everything needed to answer: what must I save, and what can I spend?"""
    today = today or date.today()
    stats = _payment_stats(conn)
    bal = balances(conn)
    obs = [dict(r) for r in conn.execute(
        """SELECT o.*, f.name AS fund_name, f.color AS fund_color
           FROM obligations o LEFT JOIN funds f ON f.id = o.fund_id
           WHERE o.archived = 0 ORDER BY o.name"""
    )]
    for ob in obs:
        ob.update(_status(ob, stats.get(ob["id"], {}), today))

    # Money already put into each sinking fund this month counts toward this
    # month's set-aside, so the target does not move after you fund it.
    contributed = _contributions(conn, today.replace(day=1).isoformat(), today.isoformat())

    # Distribute each fund's balance across the obligations that draw on it,
    # earliest due first, so shared sinking funds are not double counted.
    by_fund = {}
    for ob in obs:
        ob["reserved"] = 0
        ob["_base"] = 0
        ob["shared_fund"] = False
        if ob["fund_id"] and not ob["completed"]:
            by_fund.setdefault(ob["fund_id"], []).append(ob)
    for fund_id, group in by_fund.items():
        group.sort(key=lambda o: o["next_due"] or "9999")
        now = bal["funds"].get(fund_id, 0)
        for key, available in (("reserved", now), ("_base", now - contributed.get(fund_id, 0))):
            available = max(0, available)
            for ob in group:
                need = ob["amount"] * (ob["due_now_count"] + (1 if ob["upcoming_due"] else 0))
                give = min(available, need)
                ob[key] = give
                available -= give
            group[0][key] += available
        for ob in group:
            ob["shared_fund"] = len(group) > 1

    # This month's target per obligation: spread what the next payment still
    # needs (from the balance before this month's contributions) evenly over
    # the months left until it is due.
    for ob in obs:
        ob["monthly_target"], ob["months_to_save"] = 0, None
        if ob["completed"] or not ob["upcoming_due"]:
            continue
        leftover_base = max(0, ob["_base"] - ob["amount"] * ob["due_now_count"])
        ob["months_to_save"] = max(1, months_until(today, date.fromisoformat(ob["upcoming_due"])))
        ob["monthly_target"] = _ceil_div(max(0, ob["amount"] - leftover_base), ob["months_to_save"])
    for ob in obs:
        ob["contributed_this_month"] = 0
    for fund_id, group in by_fund.items():
        # Contributions first cover anything already due, then monthly targets.
        due_gap = max(0, sum(o["amount"] * o["due_now_count"] for o in group) - sum(o["_base"] for o in group))
        remaining = max(0, contributed.get(fund_id, 0) - due_gap)
        for ob in group:
            covered = min(ob["monthly_target"], remaining)
            ob["contributed_this_month"] = covered
            remaining -= covered

    horizon = today + timedelta(days=30)
    totals = {k: 0 for k in (
        "monthly_commitment", "remaining_balance", "due_next_30_days", "set_aside_this_month",
        "shortfall_now", "reserved", "annual_cost",
    )}
    upcoming = []
    for ob in obs:
        del ob["_base"]
        if ob["completed"]:
            ob.update(set_aside_this_month=0, shortfall_now=0, state="completed", payments_covered=0)
            continue
        due_now_amount = ob["amount"] * ob["due_now_count"]
        shortfall = max(0, due_now_amount - ob["reserved"])
        leftover = max(0, ob["reserved"] - due_now_amount)
        set_aside = max(0, ob["monthly_target"] - ob["contributed_this_month"])
        ob["shortfall_now"] = shortfall
        ob["set_aside_this_month"] = set_aside
        ob["payments_covered"] = ob["reserved"] // ob["amount"]
        if ob["overdue_count"]:
            ob["state"] = "overdue"
        elif ob["due_now_count"]:
            ob["state"] = "due_today"
        elif ob["fund_id"] is None:
            ob["state"] = "no_fund"
        elif leftover >= ob["amount"]:
            ob["state"] = "funded"
        else:
            ob["state"] = "saving"

        totals["monthly_commitment"] += ob["monthly_equivalent"]
        totals["annual_cost"] += ob["annual_cost"]
        totals["remaining_balance"] += ob["remaining_balance"] or 0
        totals["set_aside_this_month"] += set_aside
        totals["shortfall_now"] += shortfall
        totals["reserved"] += ob["reserved"]
        for item in schedule(ob, ob, today, horizon_days=366):
            if date.fromisoformat(item["date"]) <= horizon:
                totals["due_next_30_days"] += item["amount"]
            upcoming.append({**item, "obligation_id": ob["id"], "name": ob["name"], "kind": ob["kind"]})

    upcoming.sort(key=lambda i: (i["date"], i["name"]))
    unallocated = bal["funds"].get(system_ids(conn)["unallocated_fund"], 0)
    return {
        "today": today.isoformat(),
        "obligations": obs,
        "totals": totals,
        "upcoming": upcoming[:60],
        "unallocated": unallocated,
        "free_to_spend": unallocated - totals["set_aside_this_month"] - totals["shortfall_now"],
    }
