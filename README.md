# ERP

A modular ERP built one module at a time on a shared domain kernel, so
modules stay stitchable instead of needing rework later.

## Architecture

Single Django project (modular monolith, not microservices), PostgreSQL in
production / SQLite for local dev by default. Every module builds on the
`core` kernel instead of inventing its own version of shared concepts:

- **`apps/core`** — the kernel. Every other module builds on it rather
  than redefining "customer", "currency" or "address" for itself.
  - `Party` + `PartyRoleAssignment` — one record per real entity, with
    roles attached, so a company that both buys and sells is one row.
    Plus `Contact` (the people at that organisation), `Address`
    (structured, typed billing/shipping, with `Country`),
    `PartyBankAccount` and `PartyTag`.
  - `Currency` + `ExchangeRate` — date-effective rates quoted against
    the base currency, so a historical transaction keeps converting at
    the rate that applied on its date. A missing rate raises rather
    than silently assuming 1:1.
  - `PaymentTerms` — net days plus early-settlement discounts (2/10
    net 30), shared by AR and AP since the arithmetic is identical.
  - `DocumentSequence` — human-facing document numbers
    (`INV-2026-00001`) handed out under a row lock, with optional
    yearly reset, instead of leaking primary keys onto paperwork.
  - `Company` — singleton profile: identity, base currency, fiscal
    year start (with `fiscal_year_bounds()`).
  - `UnitOfMeasure` — with base-unit conversion factors.
- **`apps/inventory`** — `Warehouse`, `Item`, `StockMovement`. On-hand
  quantity is always derived by summing the movement ledger
  (`Item.on_hand_at`), never stored as a separate counter, so it can't
  drift out of sync with reality.
  **Valuation** is weighted average, derived the same way: each movement
  carries a `unit_cost`, and `average_cost_at()` / `stock_value_at()`
  replay the ledger rather than maintaining a running average field that
  would drift when a movement is corrected. FIFO was the alternative;
  it needs a separate mutable cost-layer table, which is exactly the
  parallel state this design avoids. The tradeoff to know: weighted
  average smooths margin when purchase prices swing, and moving to FIFO
  later is a migration, not a setting.
  `valuation.post_inventory_entry()` turns stock movements into ledger
  entries (see the perpetual flow under Accounting).
- **`apps/accounting`** — also home to **tax configuration**, which
  lives here rather than in Core because a tax is meaningless without
  the GL accounts it posts to (Odoo puts `account.tax` in its
  accounting module for the same reason). `Tax` supports percentage
  and per-unit fixed rates, tax-inclusive pricing, compound taxes
  (`include_base_amount` + `sequence`), and separate collected/paid
  accounts for the sales and purchase sides. `compute_taxes()` applies
  a set of taxes to an amount and returns the net base, per-tax
  amounts, and total. `FiscalPosition` substitutes or removes taxes
  per customer (zero-rated exports, reverse charge), and
  `PartyTaxProfile` attaches that plus outright exemption to a
  `core.Party` — again from the Accounting side, so Core stays
  independent. Sales consumes all of this; Purchasing bills are still
  untaxed until that module's pass.
  Also here: `Payment` — money actually moving, posted as Dr Bank / Cr
  Receivable (or the reverse for a disbursement), numbered from a
  sequence, immutable once posted and corrected by voiding. It is
  deliberately free of any link to invoices or bills, because
  Accounting must not import Sales or Purchasing; each of those owns
  its own allocation model pointing back here.
  Plus `Account` (chart of accounts, hierarchical,
  type-checked against its parent), `JournalEntry`/`JournalLine`
  (double-entry ledger, references `Party` from the kernel). A
  `JournalEntry` can only be posted if its debits equal its credits;
  once posted, the entry and its lines are immutable — corrections are
  made by posting a reversing entry (`JournalEntry.create_reversal()`),
  never by editing history. `JournalLine` deliberately has no
  quantity/unit fields — inventory valuation integrates via
  `inventory.valuation`, a service that creates journal entries from
  stock movements, not via schema coupling between the two modules.

  **Perpetual inventory.** Stock is an asset on the books, not an
  expense at purchase time:

      receive goods   Dr Inventory       Cr GRNI
      vendor bill     Dr GRNI            Cr Accounts Payable
      ship goods      Dr Cost of Sales   Cr Inventory

  The GRNI (goods received not invoiced) accrual in the middle is what
  stops a bill expensing goods that will be expensed again when sold.
  Accounts come from the `Item`, falling back to the `Company`
  defaults; a stocked item with neither configured refuses to post
  rather than quietly skipping the cost. Service lines expense directly
  and never touch stock.

- **`apps/sales`** — `SalesOrder`/`SalesOrderLine`, `Invoice`/`InvoiceLine`.
  Customers are `Party` records with the `CUSTOMER` role (no separate
  Customer model — the whole point of the kernel). This is the module
  that actually consumes the kernel: taxes, document sequences,
  payment terms, addresses and exchange rates all land here.
  - **Line arithmetic**: gross → discount → net → tax, in that order
    (tax is charged on the discounted amount). Totals are derived from
    lines, never stored.
  - **Numbering**: `confirm()` assigns `SO-2026-00001`, posting assigns
    `INV-2026-00001`, credit notes draw from their own `CN-` sequence.
  - **Payment terms** default from the customer and set `due_date` at
    posting time.
  - **Multi-currency**: the document keeps its own currency; posting
    converts to base at the rate effective on the invoice date and
    freezes that rate on the invoice. The receivable debit is set to
    the exact sum of the converted credits — rounding each line
    independently can leave the entry a cent out of balance and make a
    legitimate invoice unpostable.
  - **Order → invoice**: `SalesOrder.create_invoice()` bills whatever
    is still uninvoiced, carrying discounts and taxes across. Invoice
    lines link back to the order line, so quantities draw down
    (`quantity_invoiced()` / `quantity_uninvoiced()`) exactly like
    shipments do, and an order cannot be billed twice. A credit note
    releases the quantity again. `invoice_status()` and
    `delivery_status()` report none / partial / full.
  - **Settlement**: `InvoicePayment` applies an `accounting.Payment` to
    an invoice. The ledger entry was already made when the payment
    posted — the allocation records *which* invoices that money
    settles, which is what makes aging possible. Over-allocating either
    the payment or the invoice is refused, as is settling with another
    party's payment or a disbursement. `amount_paid()`,
    `amount_credited()` (posted credit notes count against the
    balance), `amount_due()` and `settlement_status()` follow from it.
  - **AR aging**: `ar_aging()` buckets outstanding invoices by days
    overdue (current / 1-30 / 31-60 / 61-90 / 90+).
  Posting builds a balanced `JournalEntry` via Accounting (Dr Accounts
  Receivable / Cr Revenue per line / Cr tax account per tax); Sales
  never writes ledger rows directly. A posted invoice is immutable like
  a `JournalEntry` — the only correction path is
  `Invoice.create_credit_note()`. Credits can be **partial** — pass
  `quantities={line: qty}` to give back three of ten units — and a
  line tracks `quantity_credited()` / `quantity_creditable()` so the
  same goods can't be refunded twice. A credit note posts the mirror
  of the invoice (Cr Receivable / Dr Revenue / Dr tax) at the exchange
  rate the invoice was billed at, never today's, so crediting an old
  foreign-currency invoice can't book a spurious FX gain. A credit
  that happens to cover every line in full is additionally linked as a
  reversal of the original entry.
  **Returns refund.** `Delivery.create_return()` reverses the stock and
  the cost, then credits whatever was invoiced for those goods,
  allocating the returned quantity oldest-invoice-first and raising one
  credit note per affected invoice. Pass `credit_invoices=False` for a
  replacement rather than a refund.

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

## Permissions

Built on Django's own auth system (users, groups, permissions) rather
than a bespoke framework. Two layers:

- `DjangoModelPermissions` gates standard CRUD per model
  (`add_invoice`, `change_bill`, ...). Anonymous requests are rejected
  outright.
- `ActionPermission` (`apps/core/permissions.py`) gates the actions
  that actually commit something — posting to the ledger, posting to
  stock, approving leave — behind separate custom permissions:
  `accounting.post_journalentry`, `sales.post_invoice`,
  `purchasing.post_bill`, `purchasing.post_goodsreceipt`,
  `hr.decide_leaverequest`.

**The point is segregation of duties**: being able to create a journal
entry, invoice, or bill does not imply being able to post it. Run
`python manage.py setup_roles` to create the default role groups
(Bookkeeper vs Controller, Sales Rep vs AR Manager, Purchasing Clerk
vs AP Manager, Warehouse Staff, HR Admin, Employee Self Service) —
the "clerk" roles deliberately lack the matching `post_*` permission.
The command is idempotent, so rerun it after changing the role map.

Note that Django superusers bypass every check above by design. Keep
that to as few accounts as possible.

## Core: remaining work toward Odoo/ERPNext parity

Core is being deepened module-first; this is what a mature ERP's kernel
has that this one still doesn't.

- **UoM categories as a model.** Odoo models categories with a reference
  unit and rounding precision per unit; here `category` is still a
  plain choice field. Changing it touches `Item`, so it's its own pass.
- ~~Tax configuration~~ — **done**, built in `apps/accounting` rather
  than Core (see above). Sales consumes it; **Purchasing bills are
  still untaxed** until that module's pass.
- **Chatter / activities / attachments** — the message thread,
  follower list, scheduled activities and file attachments Odoo puts on
  every record. `AuditModel` records who and when, but there's no
  discussion or document trail.
- **Multi-company** — deliberately single-company; see below.
- ~~Number sequence coverage~~ — Sales now uses `DocumentSequence`
  (`SO-`/`INV-`/`CN-`). Purchasing and Inventory documents still carry
  hand-typed references.

## Known gaps (not yet addressed)

- **Permissions are model-level, not object-level.** A user with
  `hr.decide_leaverequest` can approve *anyone's* leave, not just
  their reports'; a user with `sales.post_invoice` can post *any*
  invoice. Row-level rules ("only your own manager approves your
  leave") need the User↔Employee link below.
- **No User↔Employee link.** `LeaveRequest.approve/reject` take an
  explicit `decided_by` employee id rather than inferring it from the
  logged-in user, because there's no account-to-employee mapping yet.
  This is the prerequisite for object-level permissions.
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
- Core API: `/api/core/` (parties, contacts, addresses, bank-accounts,
  countries, currencies, exchange-rates, units-of-measure, payment-terms,
  company). Effective rate lookup:
  `GET /api/core/currencies/{id}/rate/?on=YYYY-MM-DD`.
- Inventory API: `/api/inventory/` (warehouses, items, stock-movements)
- Accounting API: `/api/accounting/` (accounts, journal-entries,
  journal-lines, taxes, tax-groups, fiscal-positions,
  fiscal-position-tax-mappings, party-tax-profiles). Post an entry with
  `POST /api/accounting/journal-entries/{id}/post_entry/`,
  reverse a posted one with `POST /api/accounting/journal-entries/{id}/reverse/`.
  Try a tax calculation without creating a document:
  `POST /api/accounting/taxes/preview/` with
  `{"amount": "100.00", "tax_ids": [1], "party_id": 5}` — the party's
  fiscal position and exemption are applied.
- Sales API: `/api/sales/` (sales-orders, invoices, invoice-lines,
  deliveries, delivery-lines). Ship with
  `POST /api/sales/deliveries/{id}/post_delivery/`, take goods back with
  `POST /api/sales/deliveries/{id}/customer_return/`.
  Confirm an order with `POST /api/sales/sales-orders/{id}/confirm/`,
  turn it into an invoice with
  `POST /api/sales/sales-orders/{id}/create_invoice/`
  (`{"receivable_account": 1}`). Post an invoice with
  `POST /api/sales/invoices/{id}/post_invoice/`, correct a posted one
  with `POST /api/sales/invoices/{id}/credit_note/`. Apply money via
  `POST /api/sales/invoice-payments/`
  (`{"invoice": 1, "payment": 1, "amount": "250.00"}`) and read
  `GET /api/sales/invoices/aging/?as_of=YYYY-MM-DD`.
  Payments themselves live at `/api/accounting/payments/`
  (`post_payment/`, `void/`).
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
