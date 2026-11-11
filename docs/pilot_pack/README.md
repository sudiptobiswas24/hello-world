# The pack the plant fills

For the plant's accountant and whoever runs the server. This is Stage 0
of the pilot (docs/PILOT.md): the old system's records, saved out as
CSV, put through the import on a **copy** of the database until every
file is clean. docs/IMPORT.md gives every column; this page gives the
order, the owner, what each file most often gets wrong, and the command.

`templates/` holds one blank file per kind: the header row only, the
columns in order, written by the import itself
(`python manage.py import_csv <kind> <file> --template`). Copy a blank
file into `imports/`, fill it, and leave the header as it is. A column
the header does not have is ignored without a word, so a misspelt
heading reads as an empty column.

## Every file

- CSV, UTF-8. Dates `YYYY-MM-DD` or `DD-MM-YYYY`. Yes-or-no columns
  take `yes` or `no`. Lists of choices are the system's own words
  (`customer`, `regular`), not the old system's.
- The first command below is the dry run. It keeps nothing, and lists
  every problem as `row N, column: message` (row 1 is the header). A
  clean file answers `N row(s) are clean. Dry run: nothing was kept;
  run again with --commit.`
- A file goes in whole or not at all. Fix the file and run it again.
- The import adds and never updates: a code that is already in is
  refused (`'C001' already exists.`). On the copy, start from a fresh
  database rather than editing what an earlier run kept.
- A dry run keeps nothing, so the next file cannot find this one's
  records until this one has been run with `--commit` on the copy.
- Under Docker each command is
  `docker compose run --rm web python manage.py ...`, and `imports/` is
  `/app/imports/` (docs/IMPORT.md).

Before the first file: `setup_indian_books` and `setup_stores` have
been run (RUNBOOK.md, "First installation"), and the day the books
opened is set (Settings, Books opening). `opening_balances` and
`fixed_assets` are dated that day whatever the command says, and are
refused until it is set: `Set the day the books opened (Settings, Books
opening) first`. `<day>` below is that day as `YYYY-MM-DD`. For
`opening_balances` the command asks for `--date` and it must be that
day; for `fixed_assets` `--date` may be left out, and if given must be
that day. Any other day is refused: `... is not the day the books
opened (...); the books setting says when, and the import follows it.`
`3900` is the opening-balance account those two commands make.

## The files, in order

Each needs the ones above it.

| # | File | Owner | Dry run |
|---|---|---|---|
| 0 | `accounts.csv` | Controller | `python manage.py import_csv accounts imports/accounts.csv` |
| 1 | `parties.csv` | Accounts with Sales and Purchasing | `python manage.py import_csv parties imports/parties.csv` |
| 2 | `employees.csv` | HR Admin | `python manage.py import_csv employees imports/employees.csv` |
| 3 | `customer_reps.csv` | Sales | `python manage.py import_csv customer_reps imports/customer_reps.csv` |
| 4 | `items.csv` | Stores with the Process Engineer | `python manage.py import_csv items imports/items.csv` |
| 5 | `tape_specs.csv`, then `fabric_specs.csv`, then `bag_specs.csv`; `film_specs.csv`, `liner_specs.csv` | Process Engineer | `python manage.py import_csv tape_specs imports/tape_specs.csv` and the same for each |
| 6 | `opening_stock.csv` | Stores Manager | `python manage.py import_csv opening_stock imports/opening_stock.csv --date <day>` |
| 7 | `open_invoices.csv` | AR Manager | `python manage.py import_csv open_invoices imports/open_invoices.csv --against 3900` |
| 8 | `open_bills.csv` | AP Manager | `python manage.py import_csv open_bills imports/open_bills.csv --against 3900` |
| 9 | `opening_balances.csv` | Controller | `python manage.py import_csv opening_balances imports/opening_balances.csv --date <day>` |
| 10 | `open_sales_orders.csv`, `open_purchase_orders.csv` | Sales, Purchasing | `python manage.py import_csv open_sales_orders imports/open_sales_orders.csv` and the same for purchase |
| 11 | `fixed_assets.csv` | Controller | `python manage.py import_csv fixed_assets imports/fixed_assets.csv --date <day>` |
| 12 | `compensation.csv` | Payroll Officer | `python manage.py import_csv compensation imports/compensation.csv` |
| 13 | `price_lists.csv`, `vendor_prices.csv` | Sales, Purchasing | `python manage.py import_csv price_lists imports/price_lists.csv` and the same for vendor prices |
| 14 | `shipment_history.csv` | Production Planner | `python manage.py import_csv shipment_history imports/shipment_history.csv` |

To keep a clean file, on the copy, the same command with `--commit`.
`employees` with `--commit` also needs
`--passwords-out imports/first_passwords.csv`: a new file, never one
that exists. Then:

```bash
python manage.py go_live_check
```

until it reports no failure.

## What each file most often gets wrong

The words in quotes are what the import answers.

**accounts**
1. A code the seeded chart already uses: `'1100' already exists.` Pick
   codes it does not use.
2. `account_type` in the old system's words: it `is not one of asset,
   liability, equity, income, expense`.
3. A `parent` that comes later in the file, or is of another type. The
   parent is above its children, or already in.

**parties**
1. `roles` in the old system's words (Debtor, Supplier): `is not one of
   customer, employee, other, vendor`. Several are separated by `;`.
2. A GSTIN that does not check, or whose first two digits are not its
   `gst_state`: refused on `gstin`. Check each on the portal first.
3. A `credit_limit` on a vendor: `is a customer's; this party is not
   one.` And an address line with no `city`: `is required.`

**employees**
1. A `roles` column filled in: `are given on the Logins screen, by
   someone who holds them; an import gives none.` Leave it out.
2. A `manager` who is in neither this file nor the system: `no employee
   'E014', here or already in.` A `department` code not made beforehand
   is refused the same way.
3. One `username` on two rows, or one already someone's: `is already
   another employee's login.`

**customer_reps**
1. A rep whose row in the employees file did not say `sales_rep` yes:
   `is not a sales rep (sales_rep yes in the employees file).`
2. A `customer` code that is not in, or is a party without the customer
   role.
3. A customer left out. It is not refused: no rep sees that customer,
   and `go_live_check` counts them.

**items**
1. `sales_taxes` and `purchase_taxes` left empty. It is not refused:
   the item is read as bearing no tax and its invoices go out with no
   GST. `go_live_check` lists such goods.
2. A tax or unit by the old system's name: `no tax or tax group
   GST@18.` The codes are this system's (`GST18`, or `CGST9;SGST9`),
   and `uom` is a unit code that exists.
3. `stock_class` left empty: the item shows as unclassified on the
   bank's stock statement until the controller sets it.

**tape_specs, fabric_specs, bag_specs, film_specs, liner_specs**
1. Out of order: a fabric naming a tape specification not yet in, or a
   bag naming a fabric not yet in. Tapes, then fabrics, then bags.
2. A choice in the plant's words: `is not one of` the system's
   (`tubular`, `valve`).
3. A weighed item stocked in something other than kg, or a bag item not
   stocked in a count.

**opening_stock**
1. Rows with nothing on the shelf: `quantity` `must be above nothing.`
   Leave them out.
2. `unit_cost` given for the purchase unit (a 25 kg bag) and not for
   one stocking unit (a kg).
3. A second run after one was kept: `already came in as ...; a second
   run would count it twice. Void that adjustment to bring a corrected
   figure in.`

**open_invoices, open_bills**
1. The whole invoice amount and not what is still owed. `amount` is the
   unpaid part, tax included.
2. A credit or an advance as a negative row: `must be above nothing;
   enter a credit as a credit note.`
3. A `reference` that is not the old document's number exactly as
   filed, or the same number twice for one party: the second is refused
   as already in.

**opening_balances**
1. The receivable, payable, inventory or goods-received account in the
   file: `1200 is brought in as open invoices, not as a balance.` The
   fixed-asset cost and accumulated-depreciation accounts likewise.
2. The file does not balance: `debits come to ... and credits to ...;
   the opening balances must balance.` The opening-balance account's
   own line is part of the file.
3. Both a debit and a credit on a row, or neither: `give one of debit
   or credit, above nothing.`

Before any row is read: the books' opening day not set, or a `--date`
that is not it (above).

**open_sales_orders, open_purchase_orders**
1. The ordered quantity and not what is left: `quantity` is `what is
   still to come`, above nothing. Lines fully delivered are left out.
2. `taxes` by the old system's names: `no tax or tax group ...`. Blank
   takes the item's; `none` says none.
3. An order the approval policy would ask about. It is confirmed under
   the same rules as one typed in, so the approval is reported as a
   problem, not skipped.

**fixed_assets**
1. `depreciated_to_date` above cost less salvage: `is more than the ...
   there is to depreciate.`
2. An asset put into service after the go-live day: `is after the
   go-live date; register it as new instead.`
3. The same name twice in one category: `already has an asset ...`.

On every row, if the books' opening day is not set or `--date` is not
it: the refusals above. The asset is in service on or before that day,
not the day the file is run.

**compensation**
1. A `component` code that does not exist here.
2. The same employee, component and `effective_from` twice: `already
   has BASIC from 01-04-2026.`
3. An `amount` below nothing: `cannot be below nothing.`

**price_lists, vendor_prices**
1. The same item and `min_quantity` twice in one price list: `already
   prices ... from ...`.
2. A vendor price run a second time: refused as already in; change it
   on the vendor prices screen instead.
3. Prices with a currency symbol or text: `is not a number.` Commas
   between thousands are read; `Rs.` and `/kg` are not.

**shipment_history**
1. `month` as a name (Apr-25): `is not a month; give it as YYYY-MM.`
2. A month net of returns below nothing: `Shipped less returned is
   nothing or more.`
3. A month this system has posted deliveries in: refused, because it is
   counted already.
