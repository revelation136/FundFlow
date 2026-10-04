from datetime import date

from fundflow import db, demo, ledger

from .conftest import P


def test_end_to_end_over_http(client):
    meta = client.get("/api/meta").get_json()
    unallocated = meta["system"]["unallocated_fund"]
    assert meta["settings"]["currency_symbol"] == "₱"

    bdo = client.post("/api/accounts", json={"name": "BDO", "kind": "asset", "subtype": "bank"}).get_json()["account"]
    salary = next(a for a in client.get("/api/accounts").get_json()["accounts"] if a["name"] == "Salary")
    fund1 = client.post("/api/funds", json={"name": "Fund 1"}).get_json()["fund"]
    solar = client.post("/api/funds", json={"name": "Solar", "target": 150_000 * P}).get_json()["fund"]

    r = client.post("/api/transactions", json={
        "type": "income", "date": "2026-09-01", "source_id": salary["id"], "account_id": bdo["id"],
        "amount": 100_000 * P, "splits": [{"fund_id": fund1["id"], "amount": 100_000 * P}],
    })
    assert r.status_code == 201
    r = client.post("/api/transactions", json={
        "type": "allocation", "date": "2026-09-02", "account_id": bdo["id"],
        "from_fund_id": fund1["id"], "to_fund_id": solar["id"], "amount": 50_000 * P,
    })
    assert r.status_code == 201

    bad = client.post("/api/transactions", json={"type": "allocation", "date": "2026-09-02"})
    assert bad.status_code == 400 and "error" in bad.get_json()

    detail = client.get(f"/api/funds/{solar['id']}").get_json()
    assert detail["fund"]["balance"] == 50_000 * P
    assert detail["composition"][0]["label"] == "Salary"
    assert detail["inflows"][0]["label"] == "Fund 1"

    acct = client.get(f"/api/accounts/{bdo['id']}").get_json()
    assert {f["fund_name"]: f["amount"] for f in acct["by_fund"]} == {"Fund 1": 50_000 * P, "Solar": 50_000 * P}
    assert acct["history"][0]["running"] == 100_000 * P

    flows = client.get("/api/flows").get_json()
    assert {(l["source"], l["target"]) for l in flows["links"]} == {
        (f"in:{salary['id']}", f"fund:{fund1['id']}"), (f"fund:{fund1['id']}", f"fund:{solar['id']}"),
    }

    dash = client.get("/api/dashboard").get_json()
    assert dash["net_worth"] == 100_000 * P
    assert unallocated in {f["id"] for f in dash["funds"]}

    ob = client.post("/api/obligations", json={
        "name": "Car Insurance", "kind": "insurance", "amount": 24_000 * P, "frequency": "annual",
        "first_due": "2027-03-10", "create_fund": True,
    })
    assert ob.status_code == 201
    assert ob.get_json()["obligation"]["fund_id"] is not None
    assert client.get("/api/plan?today=2026-10-05").get_json()["totals"]["set_aside_this_month"] == 4_800 * P

    assert client.get("/api/integrity").get_json()["ok"] is True
    csv = client.get("/api/export/transactions.csv").get_data(as_text=True)
    assert "Solar" in csv and "50000.00" in csv
    assert client.get("/").status_code == 200


def test_archive_rules(client):
    meta = client.get("/api/meta").get_json()
    r = client.put(f"/api/funds/{meta['system']['unallocated_fund']}", json={"archived": True})
    assert r.status_code == 400
    r = client.post("/api/funds", json={"name": "Dup"})
    assert client.post("/api/funds", json={"name": "Dup"}).status_code == 400


def test_demo_data_is_consistent(conn):
    demo.seed(conn, date(2026, 10, 5))
    report = ledger.integrity_report(conn)
    assert report["ok"], report["problems"]
    assert report["overdrawn"] == []
    assert report["transactions"] > 10
    # Seeding twice does nothing.
    demo.seed(conn, date(2026, 10, 5))
    assert ledger.integrity_report(conn)["transactions"] == report["transactions"]
    assert db.system_ids(conn)["unallocated_fund"]


def _setup(client):
    bdo = client.post("/api/accounts", json={"name": "BDO", "kind": "asset", "subtype": "bank"}).get_json()["account"]
    salary = next(a for a in client.get("/api/accounts").get_json()["accounts"] if a["name"] == "Salary")
    fund = client.post("/api/funds", json={"name": "Solar"}).get_json()["fund"]
    txn = client.post("/api/transactions", json={
        "type": "income", "date": "2026-09-01", "source_id": salary["id"], "account_id": bdo["id"],
        "amount": 1000 * P, "splits": [{"fund_id": fund["id"], "amount": 1000 * P}],
    }).get_json()["transaction"]
    return bdo, salary, fund, txn


def test_delete_requires_choice_when_used(client):
    bdo, salary, fund, _ = _setup(client)
    spare_fund = client.post("/api/funds", json={"name": "Typo fund"}).get_json()["fund"]
    spare_acct = client.post("/api/accounts", json={"name": "Typo", "kind": "expense"}).get_json()["account"]

    funds = {f["name"]: f for f in client.get("/api/funds").get_json()["funds"]}
    assert funds["Solar"]["txn_count"] == 1 and funds["Typo fund"]["txn_count"] == 0

    assert client.delete(f"/api/funds/{spare_fund['id']}").status_code == 200
    assert client.delete(f"/api/accounts/{spare_acct['id']}").status_code == 200
    # Records with history need an explicit choice (merge or delete-with-transactions).
    r = client.delete(f"/api/funds/{fund['id']}")
    assert r.status_code == 400 and "mode=merge" in r.get_json()["error"]
    assert client.delete(f"/api/accounts/{bdo['id']}").status_code == 400
    assert client.delete(f"/api/accounts/{salary['id']}").status_code == 400

    audit = client.get("/api/audit").get_json()["items"]
    assert {(a["action"], a["entity"]) for a in audit} >= {("delete", "fund"), ("delete", "account")}


def test_change_account_type(client):
    bdo, _, _, _ = _setup(client)
    # Used accounts can change subtype but not kind.
    assert client.put(f"/api/accounts/{bdo['id']}", json={"subtype": "ewallet"}).get_json()["account"]["subtype"] == "ewallet"
    assert client.put(f"/api/accounts/{bdo['id']}", json={"kind": "liability"}).status_code == 400
    fresh = client.post("/api/accounts", json={"name": "Visa", "kind": "asset"}).get_json()["account"]
    r = client.put(f"/api/accounts/{fresh['id']}", json={"kind": "liability", "subtype": "credit_card"})
    assert r.get_json()["account"]["kind"] == "liability"


def test_void_and_restore(client):
    _, _, fund, txn = _setup(client)
    client.post(f"/api/transactions/{txn['id']}/void", json={"reason": "oops"})
    assert client.get(f"/api/funds/{fund['id']}").get_json()["fund"]["balance"] == 0
    r = client.post(f"/api/transactions/{txn['id']}/restore")
    assert r.status_code == 200 and r.get_json()["transaction"]["voided_at"] is None
    assert client.get(f"/api/funds/{fund['id']}").get_json()["fund"]["balance"] == 1000 * P
    assert client.post(f"/api/transactions/{txn['id']}/restore").status_code == 400
    actions = [a["action"] for a in client.get(f"/api/transactions/{txn['id']}").get_json()["audit"]]
    assert actions == ["create", "void", "restore"]


def test_obligation_archive_restore_delete(client):
    bdo, _, fund, _ = _setup(client)
    cat = client.post("/api/accounts", json={"name": "Premiums", "kind": "expense"}).get_json()["account"]
    ob = client.post("/api/obligations", json={
        "name": "VUL", "kind": "insurance", "amount": 100 * P, "frequency": "monthly",
        "first_due": "2026-09-15", "fund_id": fund["id"], "account_id": bdo["id"], "category_id": cat["id"],
    }).get_json()["obligation"]
    client.put(f"/api/obligations/{ob['id']}", json={"archived": True})
    plan = client.get("/api/plan?today=2026-10-05").get_json()
    assert plan["obligations"] == [] and plan["archived"][0]["name"] == "VUL"
    client.put(f"/api/obligations/{ob['id']}", json={"archived": False})
    client.post("/api/transactions", json={"type": "payment", "date": "2026-09-15", "obligation_id": ob["id"]})
    assert client.delete(f"/api/obligations/{ob['id']}").status_code == 400
    # A fund linked to an obligation cannot be deleted either.
    assert client.delete(f"/api/funds/{fund['id']}").status_code == 400
    spare = client.post("/api/obligations", json={
        "name": "Typo", "kind": "bill", "amount": 1 * P, "frequency": "monthly", "first_due": "2026-10-01",
    }).get_json()["obligation"]
    assert client.delete(f"/api/obligations/{spare['id']}").status_code == 200
