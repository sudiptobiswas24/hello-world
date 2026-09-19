# ERP

A modular ERP built one module at a time on a shared domain kernel, so
modules stay stitchable instead of needing rework later.

## Architecture

Single Django project (modular monolith, not microservices), PostgreSQL in
production / SQLite for local dev by default. Every module builds on the
`core` kernel instead of inventing its own version of shared concepts:

- **`apps/core`** — the kernel. `Party` (a single record for any
  customer/vendor/employee, roles attached via `PartyRoleAssignment` so one
  entity can hold several roles), `Currency`, `UnitOfMeasure`. Every future
  module (accounting, sales, purchasing, HR) references these instead of
  redefining "customer" or "currency" itself.
- **`apps/inventory`** — first business module. `Warehouse`, `Item`,
  `StockMovement`. On-hand quantity is always derived by summing the
  movement ledger (`Item.on_hand_at`), never stored as a separate counter,
  so it can't drift out of sync with reality.
- **`apps/accounting`** — `Account` (chart of accounts, hierarchical,
  type-checked against its parent), `JournalEntry`/`JournalLine`
  (double-entry ledger, references `Party` from the kernel). A
  `JournalEntry` can only be posted if its debits equal its credits;
  once posted, the entry and its lines are immutable — corrections are
  made by posting a reversing entry (`JournalEntry.create_reversal()`),
  never by editing history. `JournalLine` deliberately has no
  quantity/unit fields — inventory valuation will integrate via a
  service that creates journal entries from `StockMovement`, not via
  schema coupling between the two modules.

- **`apps/sales`** — `SalesOrder`/`SalesOrderLine`, `Invoice`/`InvoiceLine`.
  Customers are `Party` records with the `CUSTOMER` role (no separate
  Customer model — the whole point of the kernel). Posting an invoice
  builds a balanced `JournalEntry` via Accounting (Dr Accounts
  Receivable / Cr Revenue per line); Sales never writes ledger rows
  directly. A posted invoice is immutable like a `JournalEntry` — the
  only correction path is `Invoice.create_credit_note()`, which calls
  the original invoice's `JournalEntry.create_reversal()` rather than
  reimplementing correction logic.

- **`apps/purchasing`** — mirrors Sales for the payables side.
  `PurchaseOrder`/`PurchaseOrderLine`, `Bill`/`BillLine`. Vendors are
  `Party` records with the `VENDOR` role. Posting a bill builds a
  balanced `JournalEntry` (Dr Expense per line / Cr Accounts Payable).
  Corrections go through `Bill.create_debit_note()`, same
  immutable-then-reverse pattern as Sales' credit notes — deliberately
  not a third, different correction mechanism.
  **Receiving is now wired up**: `GoodsReceipt`/`GoodsReceiptLine` post
  real `StockMovement` rows against a `PurchaseOrderLine`, enforce that
  received quantity (net of returns) never exceeds ordered quantity,
  and track partial receipts across multiple deliveries
  (`PurchaseOrderLine.quantity_received()`). Same posted/immutable
  pattern as everything else; a bad receipt is corrected with
  `GoodsReceipt.create_return()` — a whole-receipt reversal that
  creates offsetting `StockMovement` rows, not an edit.
  What's still missing: **Bill doesn't reference `GoodsReceipt`**, so
  there's no check that a vendor bill matches what was actually
  received (the "3" in three-way match). That needs `BillLine` to gain
  a link to `PurchaseOrderLine`/`GoodsReceiptLine`, which is a real
  schema change to the already-shipped Bill model — flagged, not done.

- **`apps/hr`** — `Department`, `Employee` (backed by a `Party` with the
  `EMPLOYEE` role, same reuse pattern as customers/vendors),
  `LeaveRequest` with a pending → approved/rejected/cancelled workflow.
  **Payroll is explicitly out of scope here** — it would need its own
  ledger-posting design (like Sales/Purchasing got for AR/AP) rather
  than being bolted onto employee records.

## Module roadmap

1. ~~Inventory~~ (done)
2. ~~Accounting~~ (done) — chart of accounts + double-entry ledger
3. ~~Sales / CRM~~ (done) — orders, invoicing, credit notes
4. ~~Purchasing~~ (done) — orders, vendor bills, debit notes
5. ~~HR~~ (done) — employees, departments, leave requests (no payroll yet)

## Known gaps (not yet addressed)

- **No permissions/roles.** Any authenticated user can post journal
  entries, invoices, and bills, or approve their own leave requests.
  No segregation of duties anywhere in the system. Deferred by
  request, twice now — this is the top priority before anything here
  touches real money or real employees.
- **No User↔Employee link.** `LeaveRequest.approve/reject` take an
  explicit `decided_by` employee id rather than inferring it from the
  logged-in user, because there's no account-to-employee mapping yet.
  Same underlying gap as the permissions issue above.
- **Payroll** is not built. Employee compensation, pay runs, and the
  resulting ledger postings are a separate design effort.
- **Bill ↔ GoodsReceipt three-way match** is not wired up (see
  Purchasing above) — a bill can currently be posted for more or less
  than was actually received.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Admin UI: `/admin/`
- Inventory API: `/api/inventory/` (warehouses, items, stock-movements)
- Accounting API: `/api/accounting/` (accounts, journal-entries,
  journal-lines). Post an entry with `POST /api/accounting/journal-entries/{id}/post_entry/`,
  reverse a posted one with `POST /api/accounting/journal-entries/{id}/reverse/`.
- Sales API: `/api/sales/` (sales-orders, invoices, invoice-lines). Post an
  invoice with `POST /api/sales/invoices/{id}/post_invoice/`, correct a
  posted one with `POST /api/sales/invoices/{id}/credit_note/`.
- Purchasing API: `/api/purchasing/` (purchase-orders, bills, bill-lines,
  goods-receipts, goods-receipt-lines). Post a bill with
  `POST /api/purchasing/bills/{id}/post_bill/`, correct a posted one with
  `POST /api/purchasing/bills/{id}/debit_note/`. Post a receipt with
  `POST /api/purchasing/goods-receipts/{id}/post_receipt/`, correct one
  with `POST /api/purchasing/goods-receipts/{id}/return_receipt/`.
- HR API: `/api/hr/` (departments, employees, leave-requests). Decide a
  leave request with `POST /api/hr/leave-requests/{id}/approve/` or
  `/reject/` (body: `decided_by: <employee id>`), or `/cancel/`.

## Tests

```bash
python manage.py test
```
