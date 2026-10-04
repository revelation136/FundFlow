import sqlite3

import pytest

from fundflow import db, ledger, maintenance, obligations
from fundflow.ledger import LedgerError

from .conftest import P


def _setup(conn, book):
    bdo = book.account("BDO", "asset")
    gcash = book.account("GCash", "asset")
    salary = book.income("Salary")
    food = book.account("Food", "expense")
    solar = book.fund("Solar")
    unalloc = book.sys["unallocated_fund"]
    ledger.create_transaction(conn, {
        "type": "income", "date": "2026-09-01", "source_id": salary, "account_id": bdo, "amount": 1000 * P,
    })
    alloc = ledger.create_transaction(conn, {
        "type": "allocation", "date": "2026-09-02", "account_id": bdo,
        "from_fund_id": unalloc, "to_fund_id": solar, "amount": 400 * P,
    })
    transfer = ledger.create_transaction(conn, {
        "type": "transfer", "date": "2026-09-03", "from_account_id": bdo, "to_account_id": gcash,
        "fund_id": solar, "amount": 100 * P,
    })
    spend = ledger.create_transaction(conn, {
        "type": "expense", "date": "2026-09-04", "account_id": gcash, "fund_id": solar,
        "category_id": food, "amount": 30 * P,
    })
    return dict(bdo=bdo, gcash=gcash, salary=salary, food=food, solar=solar, unalloc=unalloc,
                alloc=alloc, transfer=transfer, spend=spend)


def test_merge_account_keeps_history(conn, book):
    x = _setup(conn, book)
    before = ledger.balances(conn)
    assert maintenance.usage(conn, "account", x["gcash"])["transactions"] == 2

    result = maintenance.delete_account(conn, x["gcash"], "merge", x["bdo"])
    assert result["merged_into"] == "BDO"
    after = ledger.balances(conn)
    assert after["accounts"][x["bdo"]] == before["accounts"][x["bdo"]] + before["accounts"][x["gcash"]]
    assert after["funds"] == before["funds"]
    # BDO -> GCash transfer became BDO -> BDO: voided automatically as a no-op.
    assert ledger.get_transaction(conn, x["transfer"])["voided_at"] is not None
    # The expense still edits cleanly because its saved form now points at BDO.
    ledger.update_transaction(conn, x["spend"], {**ledger.get_transaction(conn, x["spend"])["form"],
                                                 "type": "expense", "date": "2026-09-04"})
    assert ledger.integrity_report(conn)["ok"]
    assert conn.execute("SELECT action FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "update"

    with pytest.raises(LedgerError, match="another account"):
        maintenance.delete_account(conn, x["bdo"], "merge", x["food"])


def test_merge_fund_into_unallocated(conn, book):
    x = _setup(conn, book)
    total = sum(ledger.balances(conn)["funds"].values())
    maintenance.delete_fund(conn, x["solar"], "merge", x["unalloc"])
    bal = ledger.balances(conn)
    assert x["solar"] not in bal["funds"]
    assert bal["funds"][x["unalloc"]] == total
    assert ledger.get_transaction(conn, x["alloc"])["voided_at"] is not None   # Unallocated -> Unallocated
    assert ledger.integrity_report(conn)["ok"]


def test_delete_account_with_its_transactions(conn, book):
    x = _setup(conn, book)
    maintenance.delete_account(conn, x["gcash"], "delete")
    remaining = {t["id"] for t in ledger.list_transactions(conn, include_voided=True, limit=None)[0]}
    assert x["transfer"] not in remaining and x["spend"] not in remaining
    assert ledger.integrity_report(conn)["ok"]
    entry = conn.execute("SELECT detail FROM audit_log WHERE action = 'delete' AND entity = 'account'").fetchone()
    assert '"deleted_transactions"' in entry[0]
    # Ordinary deletes are still blocked afterwards.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM transactions WHERE id = ?", (x["alloc"],))
    assert conn.execute("SELECT COUNT(*) FROM meta WHERE key = 'purge_authorized'").fetchone()[0] == 0


def test_delete_obligation_keep_or_delete_payments(conn, book):
    x = _setup(conn, book)
    loan = book.account("Loan Amortization", "expense")
    fields = obligations.validate(conn, {
        "name": "Car Loan", "kind": "amortization", "amount": 50 * P, "frequency": "monthly",
        "first_due": "2026-09-15", "fund_id": x["solar"], "account_id": x["bdo"], "category_id": loan,
    })
    def make():
        with conn:
            ob = conn.execute(
                f"INSERT INTO obligations ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                tuple(fields.values()),
            ).lastrowid
        pay = ledger.create_transaction(conn, {"type": "payment", "date": "2026-09-15", "obligation_id": ob})
        return ob, pay

    ob, pay = make()
    before = ledger.balances(conn)["funds"]
    maintenance.delete_obligation(conn, ob, "keep")
    t = ledger.get_transaction(conn, pay)
    assert (t["type"], t["obligation_id"]) == ("expense", None)
    assert ledger.balances(conn)["funds"] == before
    ledger.update_transaction(conn, pay, {**t["form"], "type": "expense", "date": "2026-09-16"})

    ob, pay = make()
    solar_before = ledger.balances(conn)["funds"][x["solar"]]
    maintenance.delete_obligation(conn, ob, "delete")
    assert ledger.get_transaction(conn, pay) is None
    assert ledger.balances(conn)["funds"][x["solar"]] == solar_before + 50 * P
    assert ledger.integrity_report(conn)["ok"]


def test_old_databases_get_the_new_trigger(tmp_path):
    path = str(tmp_path / "old.db")
    conn = db.connect(path)
    db.init_db(conn)
    with conn:  # simulate a v1 database
        conn.execute("DROP TRIGGER transactions_no_delete")
        conn.execute("""CREATE TRIGGER transactions_no_delete BEFORE DELETE ON transactions
                        BEGIN SELECT RAISE(ABORT, 'no'); END""")
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    db.init_db(conn)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'transactions_no_delete'").fetchone()[0]
    assert "purge_authorized" in sql
    assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "2"
    conn.close()


def test_purge_impact_preview(conn, book):
    x = _setup(conn, book)
    impact = {(i["type"], i["name"]): i for i in maintenance.usage(conn, "fund", x["solar"])["impact"]}
    # Undoing Unallocated -> Solar (400) returns 400 to Unallocated; the transfer and the
    # expense come back out of GCash and into BDO.
    assert impact[("fund", "Unallocated")]["delta"] == 400 * P
    assert impact[("account", "BDO")]["delta"] == 100 * P
    assert impact[("account", "GCash")]["delta"] == -70 * P
    assert impact[("account", "GCash")]["after"] == 0
    assert ("fund", "Solar") not in impact
    assert not any(i["goes_negative"] for i in impact.values())
