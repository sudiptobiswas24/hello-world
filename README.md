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

## Module roadmap

1. ~~Inventory~~ (done)
2. Accounting (chart of accounts, ledger entries, invoices) — built against
   `Party` and `Currency` from day one
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

## Tests

```bash
python manage.py test
```
