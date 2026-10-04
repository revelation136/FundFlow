import sqlite3

import pytest

from fundflow import ledger, trace
from fundflow.ledger import LedgerError
from fundflow.money import split_pro_rata, to_cents

from .conftest import P


def test_money_helpers():
    assert to_cents("1,234.56") == 123456
    assert to_cents("0.005") == 1
    assert to_cents(50) == 5000
    parts = split_pro_rata(1000, {"a": 1, "b": 1, "c": 1})
    assert sum(parts.values()) == 1000
    assert sorted(parts.values()) == [333, 333, 334]
    assert split_pro_rata(-10, {"a": 1, "b": 1}) == {"a": -5, "b": -5}
    assert split_pro_rata(10, {"a": 0}) == {}


def _salary_example(conn, book):
    """The motivating example: Fund 1 = 100,000 from salary; 50,000 goes to solar."""
    bdo = book.account("BDO Savings", "asset", "bank")
    salary = book.income("Salary")
    fund1 = book.fund("Fund 1")
    solar = book.fund("Solar Project")
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-09-01", "source_id": salary, "account_id": bdo,
        "amount": 100_000 * P, "splits": [{"fund_id": fund1, "amount": 100_000 * P}],
    })
    ledger.create_transaction(conn, {
        "type": "allocation", "date": "2026-09-02", "account_id": bdo,
        "from_fund_id": fund1, "to_fund_id": solar, "amount": 50_000 * P,
    })
    return bdo, salary, fund1, solar


def test_funds_split_one_bank_account(conn, book):
    bdo, _, fund1, solar = _salary_example(conn, book)
    bal = ledger.balances(conn)
    assert bal["accounts"][bdo] == 100_000 * P
    assert bal["funds"][fund1] == 50_000 * P
    assert bal["funds"][solar] == 50_000 * P
    assert bal["cells"][(bdo, solar)] == 50_000 * P
    assert bal["net_worth"] == 100_000 * P


def test_income_remainder_goes_to_unallocated(conn, book):
    bdo = book.account("BDO", "asset")
    solar = book.fund("Solar")
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-09-01", "source_id": book.income("Salary"), "account_id": bdo,
        "amount": 1000 * P, "splits": [{"fund_id": solar, "amount": 300 * P}],
    })
    bal = ledger.balances(conn)
    assert bal["funds"][solar] == 300 * P
    assert bal["funds"][book.sys["unallocated_fund"]] == 700 * P


def test_rejects_bad_transactions(conn, book):
    bdo = book.account("BDO", "asset")
    solar = book.fund("Solar")
    salary = book.income("Salary")
    with pytest.raises(LedgerError, match="do not balance"):
        ledger.create_transaction(conn, {
            "type": "journal", "date": "2026-09-01",
            "postings": [{"account_id": bdo, "fund_id": solar, "amount": 100},
                         {"account_id": salary, "fund_id": solar, "amount": -99}],
        })
    with pytest.raises(LedgerError, match="more than"):
        ledger.create_transaction(conn, {
            "type": "income", "date": "2026-09-01", "source_id": salary, "account_id": bdo,
            "amount": 100, "splits": [{"fund_id": solar, "amount": 200}],
        })
    with pytest.raises(LedgerError, match="itself"):
        ledger.create_transaction(conn, {
            "type": "allocation", "date": "2026-09-01", "account_id": bdo,
            "from_fund_id": solar, "to_fund_id": solar, "amount": 100,
        })
    with pytest.raises(LedgerError, match="centavos"):
        ledger.create_transaction(conn, {
            "type": "expense", "date": "2026-09-01", "account_id": bdo, "fund_id": solar,
            "category_id": book.account("Food", "expense"), "amount": 10.5,
        })
    with pytest.raises(LedgerError, match="date"):
        ledger.create_transaction(conn, {
            "type": "allocation", "date": "yesterday", "account_id": bdo,
            "from_fund_id": book.sys["unallocated_fund"], "to_fund_id": solar, "amount": 100,
        })


def test_transfer_with_fee_keeps_fund(conn, book):
    bdo = book.account("BDO", "asset")
    gcash = book.account("GCash", "asset")
    fees = book.income("Salary")  # wrong kind on purpose below
    personal = book.fund("Personal")
    ledger.create_transaction(conn, {
        "type": "opening", "date": "2026-09-01", "account_id": bdo, "fund_id": personal, "amount": 1000 * P,
    })
    with pytest.raises(LedgerError, match="fee category"):
        ledger.create_transaction(conn, {
            "type": "transfer", "date": "2026-09-02", "from_account_id": bdo, "to_account_id": gcash,
            "fund_id": personal, "amount": 500 * P, "fee": 15 * P, "fee_category_id": fees,
        })
    fee_cat = book.account("Fees", "expense")
    ledger.create_transaction(conn, {
        "type": "transfer", "date": "2026-09-02", "from_account_id": bdo, "to_account_id": gcash,
        "fund_id": personal, "amount": 500 * P, "fee": 15 * P, "fee_category_id": fee_cat,
    })
    bal = ledger.balances(conn)
    assert bal["accounts"][bdo] == 485 * P
    assert bal["accounts"][gcash] == 500 * P
    assert bal["accounts"][fee_cat] == 15 * P
    assert bal["funds"][personal] == 985 * P


def test_credit_card_spending_reduces_fund(conn, book):
    bdo = book.account("BDO", "asset")
    card = book.account("Visa", "liability")
    cat = book.account("Install", "expense")
    solar = book.fund("Solar")
    ledger.create_transaction(conn, {
        "type": "opening", "date": "2026-09-01", "account_id": bdo, "fund_id": solar, "amount": 1000 * P,
    })
    ledger.create_transaction(conn, {
        "type": "expense", "date": "2026-09-02", "account_id": card, "fund_id": solar,
        "category_id": cat, "amount": 300 * P,
    })
    bal = ledger.balances(conn)
    assert bal["funds"][solar] == 700 * P
    assert bal["accounts"][card] == 300 * P          # owed
    assert bal["liabilities"] == 300 * P
    ledger.create_transaction(conn, {
        "type": "transfer", "date": "2026-09-03", "from_account_id": bdo, "to_account_id": card,
        "fund_id": solar, "amount": 300 * P,
    })
    bal = ledger.balances(conn)
    assert bal["funds"][solar] == 700 * P
    assert bal["accounts"][card] == 0
    assert bal["accounts"][bdo] == 700 * P


def test_void_edit_and_audit(conn, book):
    bdo, salary, fund1, solar = _salary_example(conn, book)
    txns, total = ledger.list_transactions(conn, fund_id=solar)
    assert total == 1
    alloc_id = txns[0]["id"]

    ledger.update_transaction(conn, alloc_id, {
        "type": "allocation", "date": "2026-09-02", "account_id": bdo,
        "from_fund_id": fund1, "to_fund_id": solar, "amount": 60_000 * P, "description": "bigger",
    })
    assert ledger.balances(conn)["funds"][solar] == 60_000 * P

    ledger.void_transaction(conn, alloc_id, "mistake")
    assert ledger.balances(conn)["funds"].get(solar, 0) == 0
    with pytest.raises(LedgerError):
        ledger.update_transaction(conn, alloc_id, {"type": "allocation"})

    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit_log WHERE entity = 'transaction' AND entity_id = ? ORDER BY id", (alloc_id,)
    )]
    assert actions == ["create", "update", "void"]

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM transactions WHERE id = ?", (alloc_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM audit_log")
    assert ledger.integrity_report(conn)["ok"]


def test_trace_follows_money_to_its_source(conn, book):
    bdo, salary, fund1, solar = _salary_example(conn, book)
    panels = book.account("Solar Equipment", "expense")
    expense_id = ledger.create_transaction(conn, {
        "type": "expense", "date": "2026-09-03", "account_id": bdo, "fund_id": solar,
        "category_id": panels, "amount": 30_000 * P,
    })
    tr = trace.run(conn)
    assert tr["composition"][solar] == {f"a{salary}": 20_000 * P}
    assert tr["txn_origins"][expense_id] == {f"a{salary}": 30_000 * P}
    edges = {(e["from"], e["to"]): e["amount"] for e in tr["edges"]}
    assert edges[(f"in:{salary}", f"fund:{fund1}")] == 100_000 * P
    assert edges[(f"fund:{fund1}", f"fund:{solar}")] == 50_000 * P
    assert edges[(f"fund:{solar}", f"out:{panels}")] == 30_000 * P


def test_trace_mixed_sources_pro_rata(conn, book):
    bdo = book.account("BDO", "asset")
    salary = book.income("Salary")
    bonus = book.account("Bonus", "income")
    pool = book.fund("Pool")
    solar = book.fund("Solar")
    for src, amt in ((salary, 75_000), (bonus, 25_000)):
        ledger.create_transaction(conn, {
            "type": "income", "date": "2026-09-01", "source_id": src, "account_id": bdo,
            "amount": amt * P, "splits": [{"fund_id": pool, "amount": amt * P}],
        })
    ledger.create_transaction(conn, {
        "type": "allocation", "date": "2026-09-02", "account_id": bdo,
        "from_fund_id": pool, "to_fund_id": solar, "amount": 40_000 * P,
    })
    tr = trace.run(conn)
    assert tr["composition"][solar] == {f"a{salary}": 30_000 * P, f"a{bonus}": 10_000 * P}
    assert tr["composition"][pool] == {f"a{salary}": 45_000 * P, f"a{bonus}": 15_000 * P}
    # Invariant: composition always sums to the fund balance.
    bal = ledger.balances(conn)
    for fund_id, comp in tr["composition"].items():
        assert sum(comp.values()) == bal["funds"].get(fund_id, 0)


def test_trace_overspending_is_marked_unfunded(conn, book):
    bdo = book.account("BDO", "asset")
    salary = book.income("Salary")
    food = book.account("Food", "expense")
    fun = book.fund("Fun")
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-09-01", "source_id": salary, "account_id": bdo,
        "amount": 100 * P, "splits": [{"fund_id": fun, "amount": 100 * P}],
    })
    spend = ledger.create_transaction(conn, {
        "type": "expense", "date": "2026-09-02", "account_id": bdo, "fund_id": fun,
        "category_id": food, "amount": 150 * P,
    })
    tr = trace.run(conn)
    assert tr["txn_origins"][spend] == {f"a{salary}": 100 * P, trace.UNFUNDED: 50 * P}
    assert tr["composition"][fun] == {trace.DEFICIT: -50 * P}
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-09-03", "source_id": salary, "account_id": bdo,
        "amount": 80 * P, "splits": [{"fund_id": fun, "amount": 80 * P}],
    })
    assert trace.run(conn)["composition"][fun] == {f"a{salary}": 30 * P}
