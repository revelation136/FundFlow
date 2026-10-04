from datetime import date

from fundflow import ledger, obligations
from fundflow.obligations import installment_date, plan

from .conftest import P


def test_installment_dates_clamp_to_month_end():
    first = date(2026, 1, 31)
    assert installment_date(first, "monthly", 1) == date(2026, 2, 28)
    assert installment_date(first, "monthly", 2) == date(2026, 3, 31)
    assert installment_date(first, "quarterly", 1) == date(2026, 4, 30)
    assert installment_date(first, "annual", 2) == date(2028, 1, 31)
    assert installment_date(date(2026, 3, 15), "monthly", -3) == date(2025, 12, 15)


def _obligation(conn, **fields):
    data = obligations.validate(conn, fields)
    with conn:
        return conn.execute(
            f"INSERT INTO obligations ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
            tuple(data.values()),
        ).lastrowid


def _find(p, ob_id):
    return next(o for o in p["obligations"] if o["id"] == ob_id)


def test_amortization_remaining_balance_and_payments(conn, book):
    bdo = book.account("BDO", "asset")
    amort = book.account("Loan Amortization", "expense")
    car = book.fund("Car Loan")
    ob_id = _obligation(
        conn, name="Car Loan", kind="amortization", amount=18_500 * P, frequency="monthly",
        first_due="2025-08-15", total_payments=60, prior_payments=12,
        fund_id=car, account_id=bdo, category_id=amort,
    )
    today = date(2026, 10, 5)
    p = plan(conn, today)
    ob = _find(p, ob_id)
    # 12 paid before tracking; installments for Aug 2026 and Sep 2026 are overdue.
    assert ob["paid_count"] == 12
    assert ob["next_due"] == "2026-08-15"
    assert ob["overdue_count"] == 2
    assert ob["remaining_payments"] == 48
    assert ob["remaining_balance"] == 48 * 18_500 * P
    assert ob["state"] == "overdue"
    assert ob["shortfall_now"] == 2 * 18_500 * P
    assert ob["end_date"] == "2030-07-15"

    ledger.create_transaction(conn, {
        "type": "opening", "date": "2026-08-01", "account_id": bdo, "fund_id": car, "amount": 60_000 * P,
    })
    for d in ("2026-08-15", "2026-09-15"):
        ledger.create_transaction(conn, {"type": "payment", "date": d, "obligation_id": ob_id})
    ob = _find(plan(conn, today), ob_id)
    assert ob["paid_count"] == 14
    assert ob["paid_in_ledger"] == 2 * 18_500 * P
    assert ob["next_due"] == "2026-10-15"
    assert ob["overdue_count"] == 0
    assert ob["remaining_balance"] == 46 * 18_500 * P
    assert ob["reserved"] == 23_000 * P
    assert ob["state"] == "funded"
    assert ob["set_aside_this_month"] == 0
    assert ob["payments_covered"] == 1
    assert ledger.balances(conn)["funds"][car] == 23_000 * P


def test_annual_insurance_monthly_set_aside(conn, book):
    bdo = book.account("BDO", "asset")
    premiums = book.account("Insurance", "expense")
    fund = book.fund("Car Insurance")
    ob_id = _obligation(
        conn, name="Car Insurance", kind="insurance", amount=24_000 * P, frequency="annual",
        first_due="2027-03-10", fund_id=fund, account_id=bdo, category_id=premiums,
    )
    today = date(2026, 10, 5)
    ob = _find(plan(conn, today), ob_id)
    assert ob["remaining_balance"] is None           # ongoing policy
    assert ob["months_to_save"] == 5
    assert ob["set_aside_this_month"] == 4_800 * P   # 24,000 / 5 months
    assert ob["monthly_equivalent"] == 2_000 * P
    assert ob["state"] == "saving"

    ledger.create_transaction(conn, {
        "type": "opening", "date": "2026-10-01", "account_id": bdo, "fund_id": fund, "amount": 9_000 * P,
    })
    ob = _find(plan(conn, today), ob_id)
    assert ob["reserved"] == 9_000 * P
    assert ob["set_aside_this_month"] == 3_000 * P   # (24,000 - 9,000) / 5


def test_plan_free_to_spend(conn, book):
    bdo = book.account("BDO", "asset")
    premiums = book.account("Insurance", "expense")
    fund = book.fund("Life Insurance")
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-10-01", "source_id": book.income("Salary"), "account_id": bdo,
        "amount": 50_000 * P,
    })
    _obligation(
        conn, name="VUL", kind="insurance", amount=9_000 * P, frequency="quarterly",
        first_due="2026-11-20", fund_id=fund, category_id=premiums,
    )
    _obligation(
        conn, name="Streaming", kind="subscription", amount=549 * P, frequency="monthly",
        first_due="2026-10-01", category_id=premiums,
    )
    p = plan(conn, date(2026, 10, 5))
    assert p["unallocated"] == 50_000 * P
    # VUL: 9,000 over 1 month (due next month) ; Streaming: no fund, already due today-ish -> shortfall.
    assert p["totals"]["set_aside_this_month"] == 9_000 * P + 549 * P
    assert p["totals"]["shortfall_now"] == 549 * P
    assert p["free_to_spend"] == 50_000 * P - 9_000 * P - 549 * P - 549 * P
    assert p["upcoming"][0]["name"] == "Streaming"


def test_shared_fund_reserved_by_due_date(conn, book):
    bdo = book.account("BDO", "asset")
    cat = book.account("Insurance", "expense")
    shared = book.fund("Insurance Pool")
    ledger.create_transaction(conn, {
        "type": "opening", "date": "2026-10-01", "account_id": bdo, "fund_id": shared, "amount": 10_000 * P,
    })
    early = _obligation(conn, name="A", kind="insurance", amount=6_000 * P, frequency="annual",
                        first_due="2026-11-01", fund_id=shared, category_id=cat)
    late = _obligation(conn, name="B", kind="insurance", amount=6_000 * P, frequency="annual",
                       first_due="2027-02-01", fund_id=shared, category_id=cat)
    p = plan(conn, date(2026, 10, 5))
    assert _find(p, early)["reserved"] == 6_000 * P
    assert _find(p, late)["reserved"] == 4_000 * P
    assert p["totals"]["reserved"] == 10_000 * P


def test_set_aside_target_is_stable_after_funding(conn, book):
    """Funding this month's set-aside must not trigger a new, smaller request."""
    bdo = book.account("BDO", "asset")
    premiums = book.account("Insurance", "expense")
    fund = book.fund("Car Insurance")
    unalloc = book.sys["unallocated_fund"]
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-10-01", "source_id": book.income("Salary"), "account_id": bdo,
        "amount": 100_000 * P,
    })
    ob_id = _obligation(
        conn, name="Car Insurance", kind="insurance", amount=24_000 * P, frequency="annual",
        first_due="2027-03-10", fund_id=fund, account_id=bdo, category_id=premiums,
    )
    ledger.create_transaction(conn, {
        "type": "allocation", "date": "2026-10-05", "account_id": bdo,
        "from_fund_id": unalloc, "to_fund_id": fund, "amount": 4_800 * P,
    })
    ob = _find(plan(conn, date(2026, 10, 6)), ob_id)
    assert ob["contributed_this_month"] == 4_800 * P
    assert ob["set_aside_this_month"] == 0
    # Next month the remaining 19,200 is spread over the 4 months left.
    ob = _find(plan(conn, date(2026, 11, 2)), ob_id)
    assert ob["set_aside_this_month"] == 4_800 * P


def test_monthly_amortization_cycle(conn, book):
    bdo = book.account("BDO", "asset")
    amort = book.account("Loan Amortization", "expense")
    car = book.fund("Car Loan")
    unalloc = book.sys["unallocated_fund"]
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-10-01", "source_id": book.income("Salary"), "account_id": bdo,
        "amount": 100_000 * P,
    })
    ob_id = _obligation(
        conn, name="Car Loan", kind="amortization", amount=18_500 * P, frequency="monthly",
        first_due="2026-10-15", total_payments=36, fund_id=car, account_id=bdo, category_id=amort,
    )
    assert _find(plan(conn, date(2026, 10, 2)), ob_id)["set_aside_this_month"] == 18_500 * P
    ledger.create_transaction(conn, {
        "type": "allocation", "date": "2026-10-02", "account_id": bdo,
        "from_fund_id": unalloc, "to_fund_id": car, "amount": 18_500 * P,
    })
    ob = _find(plan(conn, date(2026, 10, 3)), ob_id)
    assert (ob["set_aside_this_month"], ob["state"]) == (0, "funded")
    ledger.create_transaction(conn, {"type": "payment", "date": "2026-10-15", "obligation_id": ob_id})
    ob = _find(plan(conn, date(2026, 10, 20)), ob_id)
    assert ob["set_aside_this_month"] == 0          # October's set-aside is done
    assert ob["remaining_balance"] == 35 * 18_500 * P
    assert _find(plan(conn, date(2026, 11, 2)), ob_id)["set_aside_this_month"] == 18_500 * P


def test_contribution_covers_overdue_before_next_payment(conn, book):
    bdo = book.account("BDO", "asset")
    amort = book.account("Loan Amortization", "expense")
    car = book.fund("Car Loan")
    unalloc = book.sys["unallocated_fund"]
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-10-01", "source_id": book.income("Salary"), "account_id": bdo,
        "amount": 100_000 * P,
    })
    ob_id = _obligation(
        conn, name="Car Loan", kind="amortization", amount=18_500 * P, frequency="monthly",
        first_due="2026-09-15", fund_id=car, account_id=bdo, category_id=amort,
    )
    ledger.create_transaction(conn, {
        "type": "allocation", "date": "2026-10-02", "account_id": bdo,
        "from_fund_id": unalloc, "to_fund_id": car, "amount": 18_500 * P,
    })
    ob = _find(plan(conn, date(2026, 10, 5)), ob_id)
    assert ob["overdue_count"] == 1
    assert ob["shortfall_now"] == 0                  # the September installment is covered...
    assert ob["set_aside_this_month"] == 18_500 * P  # ...October's still needs saving
