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

## Module roadmap

1. ~~Inventory~~ (done)
2. ~~Accounting~~ (done) — chart of accounts + double-entry ledger
3. Sales / CRM
4. Purchasing
5. HR

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

## Tests

```bash
python manage.py test
```
