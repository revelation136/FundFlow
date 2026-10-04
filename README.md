# FundFlow

A personal fund ledger that knows **where every peso came from and where it went**.

FundFlow tracks money along two dimensions at once:

| Dimension | Answers | Examples |
|---|---|---|
| **Account** | *Where is the money?* | BDO Savings, GCash, Cash on hand, BPI credit card |
| **Fund** | *What is the money for?* | Fund 1, Solar Project, Emergency Fund, Car Insurance |

So one bank account can hold many funds: ₱150,000 in BDO might be ₱50,000 *Fund 1* + ₱10,000 *Solar Project* + ₱90,000 *Emergency Fund*, and FundFlow always knows the split.

Under the hood it is a strict **double-entry ledger**: every transaction is a set of postings that sum to zero, so money can never appear or disappear without a recorded origin and destination.

## What it does

- **Sources → funds → spending.** Record salary into *Fund 1*, allocate ₱50,000 of it to *Solar Project*, buy panels from *Solar Project* — and see the whole chain.
- **Provenance tracing.** Every fund shows what it is made of by *original* source (e.g. *Solar Project: 58% Freelance, 42% Salary*), and every expense shows which sources paid for it — traced pro-rata through any number of allocations.
- **Allocation matrix.** A table of accounts × funds showing exactly how each account's balance is divided by purpose.
- **Obligations planner** for amortizations, insurance premiums, subscriptions and bills (monthly, quarterly, semi-annual, annual):
  - remaining balance and installments left (counted from payments you record, plus any made before you started tracking)
  - next due date, overdue installments, full payment calendar
  - a **sinking fund** per obligation and how much to **set aside this month** — e.g. a ₱24,000 yearly premium due in 5 months needs ₱4,800/month
  - **Free to spend**: unallocated money after this month's set-asides and anything overdue
  - one click to allocate all of this month's set-asides
- **Flow diagram** (Sankey) of money from sources through funds to spending, for any period.
- **A ledger you can trust.** Amounts are integer centavos (no rounding drift). Transactions are never deleted — only edited or voided — and every change is written to an append-only audit log (enforced by database triggers). A built-in integrity check verifies that every transaction balances and that fund balances add up to the money in your accounts.
- Credit cards and loans are supported: charging a card reduces the fund immediately; paying the card is just a transfer.
- CSV export and full JSON backup. Light and dark themes. Works on phones.

## Quick start (Windows)

Requires [Python 3.10+](https://www.python.org/downloads/).

```bat
pip install -r requirements.txt
run.bat
```

`run.bat` starts FundFlow at <http://127.0.0.1:5050> and opens your browser. Your data lives in `data/fundflow.db` (a single SQLite file, never committed to git).

Want to look around first? `run-demo.bat` starts a separate demo ledger (`data/demo.db`) on port 5051, pre-filled with the example below.

On macOS/Linux:

```bash
pip install -r requirements.txt
python -m fundflow --open          # your ledger
python -m fundflow --demo --open   # demo data, separate database
```

Options: `--port 5050`, `--db path/to/file.db`, `--host 127.0.0.1` (the default keeps it reachable only from your computer).

## The example, step by step

1. **Accounts** → add *BDO Savings* (bank). Sources and categories can also be created on the fly from any dropdown.
2. **Funds** → create *Fund 1* and *Solar Project* (optionally with a ₱150,000 target).
3. **New transaction → Income**: ₱100,000 from *Salary* into *BDO Savings*, all to *Fund 1*.
4. **New transaction → Allocate**: in *BDO Savings*, from *Fund 1* to *Solar Project*, ₱50,000.
   BDO still shows ₱100,000 — now split ₱50,000 *Fund 1* / ₱50,000 *Solar Project*.
5. **New transaction → Expense**: ₱32,000 for panels, from *BDO Savings* / *Solar Project*.
   The panels are shown as *funded by Salary 100%*; *Solar Project* has ₱18,000 left, all traceable to Salary.
6. **Obligations → New obligation**: *Car Loan*, amortization, ₱18,500 monthly, 60 installments, 12 already paid → FundFlow shows ₱888,000 remaining, the next due date, and what to set aside.

## Transaction types

| Type | Use it for | Postings (simplified) |
|---|---|---|
| Income | salary, freelance, gifts — optionally split across funds | Source → Account·Fund |
| Allocate | give money a purpose; stays in the same account | Account·FundA → Account·FundB |
| Expense | spending from a fund | Account·Fund → Category |
| Pay obligation | an installment / premium; advances its schedule | Account·Fund → Category |
| Transfer | bank → e-wallet, paying a credit card, with optional fee | AccountA·Fund → AccountB·Fund |
| Opening balance | money you already had when you started | Opening Balance → Account·Fund |
| Adjustment | reconcile with your statement (+/−) | Reconciliation ↔ Account·Fund |

## How the numbers work

- **Fund balance** = sum of its postings on asset and liability accounts.
- **Provenance**: the ledger is replayed in date order; each fund keeps a composition by origin, and money leaving a fund carries a pro-rata share of it (largest-remainder rounding keeps every centavo exact). Spending more than a fund holds is marked *Unfunded*.
- **Set aside this month** for an obligation = (next payment − what its sinking fund held before this month) ÷ months left until it is due, minus what you already put in this month. Money due now but not covered is reported separately as a shortfall.
- **Free to spend** = Unallocated balance − this month's set-asides − shortfall.

## Project layout

```
fundflow/
  db.py           SQLite schema, append-only triggers, seed data
  ledger.py       double-entry rules, transaction builders, balances, integrity check
  trace.py        provenance tracing and flow edges
  obligations.py  schedules, remaining balances, sinking-fund plan
  api.py          JSON API (all amounts in integer centavos)
  demo.py         sample data
  static/         web UI (vanilla JS modules, no build step)
tests/            pytest suite
```

Run the tests with `python -m pytest`.

## Privacy

FundFlow runs entirely on your computer. There is no account, no cloud and no telemetry. The `data/` folder is git-ignored so your ledger is never pushed to GitHub — back it up yourself (Settings → Download full backup, or copy `data/fundflow.db`).

## Roadmap ideas

- Import bank statements (CSV) and match them to transactions
- Recurring income/expense templates
- Loan principal/interest split for amortizations
- Restore from JSON backup
- Multi-currency
