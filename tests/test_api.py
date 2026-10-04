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
