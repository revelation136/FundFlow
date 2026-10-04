"""Sample data so you can explore FundFlow without touching your real ledger."""
from datetime import date

from .ledger import create_transaction
from .obligations import installment_date

P = 100  # centavos per peso


def _months_ago(today: date, months: int, day: int) -> str:
    return installment_date(date(today.year, today.month, day), "monthly", -months).isoformat()


def seed(conn, today: date | None = None) -> None:
    today = today or date.today()
    if conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]:
        return

    def account(name, kind, subtype=""):
        row = conn.execute("SELECT id FROM accounts WHERE name = ? AND kind = ?", (name, kind)).fetchone()
        if row:
            return row["id"]
        return conn.execute(
            "INSERT INTO accounts (name, kind, subtype) VALUES (?, ?, ?)", (name, kind, subtype)
        ).lastrowid

    def fund(name, color, target=None, description=""):
        return conn.execute(
            "INSERT INTO funds (name, color, target, description) VALUES (?, ?, ?, ?)",
            (name, color, target, description),
        ).lastrowid

    with conn:
        bdo = account("BDO Savings", "asset", "bank")
        gcash = account("GCash", "asset", "ewallet")
        account("Cash on Hand", "asset", "cash")
        card = account("BPI Credit Card", "liability", "credit_card")
        salary = account("Salary", "income")
        freelance = account("Freelance", "income")
        panels = account("Solar Equipment", "expense")
        install = account("Installation & Labor", "expense")
        groceries = account("Groceries", "expense")
        account("Utilities", "expense")
        premiums = account("Insurance Premiums", "expense")
        amort = account("Loan Amortization", "expense")
        fees = account("Bank & Transfer Fees", "expense")

        fund1 = fund("Fund 1", "#2a78d6", None, "Salary pool, distributed to projects and needs.")
        solar = fund("Solar Project", "#eda100", 150_000 * P, "Rooftop solar for the house.")
        emergency = fund("Emergency Fund", "#1baf7a", 300_000 * P, "Six months of expenses.")
        personal = fund("Personal Spending", "#e87ba4")
        car_loan = fund("Car Loan", "#4a3aa7", None, "Sinking fund for the monthly amortization.")
        car_ins = fund("Car Insurance", "#eb6834", None, "Sinking fund for the annual premium.")

        loan_id = conn.execute(
            """INSERT INTO obligations (name, kind, amount, frequency, first_due, total_payments,
                   prior_payments, fund_id, account_id, category_id, notes)
               VALUES (?, 'amortization', ?, 'monthly', ?, 60, 12, ?, ?, ?, ?)""",
            ("Car Loan", 18_500 * P, _months_ago(today, 14, 15), car_loan, bdo, amort,
             "5-year auto loan; 12 payments were made before using FundFlow."),
        ).lastrowid
        next_march = date(today.year + (1 if today.month >= 3 else 0), 3, 10)
        conn.execute(
            """INSERT INTO obligations (name, kind, amount, frequency, first_due, fund_id, account_id,
                   category_id, notes)
               VALUES (?, 'insurance', ?, 'annual', ?, ?, ?, ?, ?)""",
            ("Car Insurance Premium", 24_000 * P, next_march.isoformat(), car_ins, bdo, premiums,
             "Comprehensive cover, renews every March."),
        )
        life = fund("Life Insurance", "#008300")
        conn.execute(
            """INSERT INTO obligations (name, kind, amount, frequency, first_due, fund_id, account_id,
                   category_id)
               VALUES (?, 'insurance', ?, 'quarterly', ?, ?, ?, ?)""",
            ("Life Insurance (VUL)", 9_000 * P, _months_ago(today, -1, 20), life, bdo, premiums),
        )

    def txn(**payload):
        payload["date"] = min(payload["date"], today.isoformat())
        create_transaction(conn, payload)

    txn(type="opening", date=_months_ago(today, 3, 1), account_id=bdo, fund_id=emergency, amount=80_000 * P,
        description="Starting balance")
    txn(type="income", date=_months_ago(today, 2, 5), source_id=salary, account_id=bdo, amount=100_000 * P,
        splits=[{"fund_id": fund1, "amount": 100_000 * P}], description="Salary")
    txn(type="allocation", date=_months_ago(today, 2, 5), account_id=bdo, from_fund_id=fund1,
        splits=[{"fund_id": solar, "amount": 50_000 * P}, {"fund_id": personal, "amount": 15_000 * P},
                {"fund_id": car_loan, "amount": 18_500 * P}, {"fund_id": car_ins, "amount": 4_000 * P}],
        description="Distribute Fund 1")
    txn(type="transfer", date=_months_ago(today, 2, 6), from_account_id=bdo, to_account_id=gcash,
        fund_id=personal, amount=5_000 * P, fee=15 * P, fee_category_id=fees, description="Cash-in to GCash")
    txn(type="expense", date=_months_ago(today, 2, 9), account_id=gcash, fund_id=personal,
        category_id=groceries, amount=3_250 * P, description="Weekly groceries")
    txn(type="expense", date=_months_ago(today, 2, 12), account_id=bdo, fund_id=solar, category_id=panels,
        amount=32_000 * P, description="6 x 550W solar panels")
    txn(type="payment", date=_months_ago(today, 2, 15), obligation_id=loan_id, description="Car loan")
    txn(type="income", date=_months_ago(today, 1, 5), source_id=salary, account_id=bdo, amount=100_000 * P,
        splits=[{"fund_id": fund1, "amount": 60_000 * P}], description="Salary")
    txn(type="allocation", date=_months_ago(today, 1, 5), account_id=bdo, from_fund_id=fund1,
        splits=[{"fund_id": car_loan, "amount": 18_500 * P}, {"fund_id": car_ins, "amount": 4_000 * P},
                {"fund_id": emergency, "amount": 10_000 * P}],
        description="Monthly set-asides")
    txn(type="income", date=_months_ago(today, 1, 10), source_id=freelance, account_id=gcash,
        amount=25_000 * P, splits=[{"fund_id": solar, "amount": 25_000 * P}], description="Website project")
    txn(type="expense", date=_months_ago(today, 1, 14), account_id=card, fund_id=solar, category_id=install,
        amount=8_000 * P, description="Mounting rails & installation (credit card)")
    txn(type="payment", date=_months_ago(today, 1, 15), obligation_id=loan_id, description="Car loan")
    txn(type="transfer", date=_months_ago(today, 0, 2), from_account_id=bdo, to_account_id=card, fund_id=solar,
        amount=8_000 * P, description="Pay credit card")
    txn(type="expense", date=_months_ago(today, 0, 3), account_id=gcash, fund_id=personal,
        category_id=groceries, amount=1_450 * P, description="Groceries")
