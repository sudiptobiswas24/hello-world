# Bringing the old system's records in

Once, at go-live, from CSV files saved out of the old system or a
spreadsheet (UTF-8; a spreadsheet's byte-order mark is fine). Brought in
in this order, because each needs the ones before:

| Order | Kind | What it makes |
|---|---|---|
| 0 | `accounts` | The plant's own accounts beside the seeded chart: each bank, loan, asset class and expense head on the old trial balance |
| 1 | `parties` | Customers, vendors, with GST details, credit limit, billing address |
| 2 | `employees` | Employees with department and manager, and their logins |
| 3 | `customer_reps` | Which rep each customer belongs to |
| 4 | `items` | Items with their unit, HSN and costing |
| 5 | `tape_specs`, `fabric_specs`, `bag_specs` | What the plant makes, each building its bill of materials and inspection plan (`film_specs`, `liner_specs` likewise) |
| 6 | `opening_stock` | One posted stock adjustment a warehouse |
| 7 | `open_invoices` | Posted invoices still owed, one per old invoice |
| 8 | `open_bills` | Posted bills still owing, one per old bill |
| 9 | `opening_balances` | One posted journal entry for every other balance |
| 10 | `open_sales_orders`, `open_purchase_orders` | The order book: what is still to deliver and to receive, confirmed |
| 11 | `fixed_assets` | The asset register, with what the old system had depreciated |
| 12 | `compensation` | Each employee's pay components |
| 13 | `price_lists`, `vendor_prices` | Customer price lists and what vendors charge |
| 14 | `shipment_history` | Months shipped from the old system, for the forecast |

**From the office:** Settings, *Bring old records in*. Choose the kind,
download its blank file, fill it, choose it back in (or paste its rows),
*Check*, then *Keep*. The controller holds the right (`core.import_records`);
what is kept is stamped with who brought it in. The command below does the
same from the server.

```bash
python manage.py import_csv parties parties.csv --template   # a blank file to fill in
python manage.py import_csv parties parties.csv              # dry run
python manage.py import_csv parties parties.csv --commit     # keep it
python manage.py go_live_check                               # then: is it ready?
```

`--template` writes a file holding only the header row, the columns in
order; it never writes over a file that exists. With Docker, run each
as `docker compose run --rm web python manage.py ...` with the files in
`imports/`, which the container sees as `/app/imports/`.

**A dry run is the default.** It checks every row through the same
rules as a record typed in, lists every problem as `row N, column:
message` (row 1 is the header), and keeps nothing. A file goes in whole
or not at all: one bad row and nothing from the file is kept, even with
`--commit`. Fix the file and run it again.

Lists of choices are the system's own words (`customer`, `fifo`,
`regular`). Dates are `YYYY-MM-DD` or `DD-MM-YYYY`. Yes-or-no columns
take `yes`/`no`. Columns not listed are ignored; a column left empty
takes the default.

## The opening position is documents, not bare balances

Receivables, payables and stock come in as invoices, bills and a stock
adjustment, so they age, can be paid and allocated, and reconcile to the
ledger like anything else. Each posts against one **opening-balance
account** (an equity account, say `3900 Opening balances`), and so does
the journal entry for everything else. Once the opening position is in, that account
holds the old system's equity; if it does not, something was left out.

Before the opening position, the plant needs:

- the opening-balance account;
- an adjustment reason with code `OPENING` (or name another with
  `--reason`) whose account is the opening-balance account;
- the company's default receivable and payable accounts.

`setup_indian_books` and then `setup_stores` (RUNBOOK.md, "First
installation") make all three: `3900 Opening balances`, the `OPENING`
reason posting to it, and the company's defaults. A plant whose chart
already has its own opening-balance account names its reason with
`--reason`.

## Columns

`*` is required.

### accounts

`code*`, `name*`, `account_type*` (`asset`, `liability`, `equity`,
`income`, `expense`), `parent` (code of an account of the same type,
above in this file or already in), `currency` (code; default the base),
`holds_money` (`yes` for a bank, cash box, card or overdraft: what
payments are made from or into).

`setup_indian_books` has already made the standard chart (RUNBOOK.md);
this adds the rest of the old trial balance, so `opening_balances` has an
account for every line. A code already in the system is refused, not
updated: pick codes the seeded chart does not use (`python manage.py
shell -c "from apps.accounting.models import Account; [print(a) for a in
Account.objects.all()]"` lists them, as does Accounts, Chart of accounts).

### parties

`code*`, `name*`, `roles*` (`customer`, `vendor`, `employee`, `other`;
several separated by `;`), `legal_name`, `email`, `phone`, `currency`
(code), `payment_terms` (code), `gstin`, `gst_state` (two-digit state
code), `gst_registration` (`regular`, `composition`, `sez`,
`unregistered`, `overseas`), `credit_limit` (customers only),
`address_line1`, `address_line2`, `city` (required with an address),
`state`, `postal_code`, `country` (two-letter code).

A code already in the system is refused, not updated.

### employees

`employee_number*`, `name*`, `hire_date*`, `party_code` (default the
employee number; a party that exists is given the employee role),
`department` (code, made beforehand in the admin), `manager` (the
manager's employee number, in this file or already in), `job_title`,
`email`, `username` (the login: made if new, linked if it exists and
is no one else's), `sales_rep`
(`yes` makes them a sales rep, who can then carry customers), `uan`
(provident fund, twelve digits) and `esi_number` (the ESI IP number,
ten digits): the monthly PF and ESI files name each person by these. `work_state` (`MH`, `WB`: whose professional tax
and welfare fund schedules apply; needed once two states' schedules are
loaded) and `gender` (`female`, `male`, `other`: Maharashtra's
professional tax exempts a woman's salary up to 25,000, a man's up to
7,500, and someone whose gender is not said is taxed as a man;
`go_live_check` names them).

No roles column: a login's roles are given on the Logins screen, by
someone who holds them, and a file with a `roles` cell filled is refused
on that row.

With `--commit`, `--passwords-out <file>` is required: each new login's
first password is written there, readable only by whoever ran the
import, and never shown on the screen. The file is made before anything
is kept, and a name that exists or a folder that cannot be written to
refuses the import. Under Docker the application's own user cannot
write to `imports/`, so this one command runs as root, and the file is
then root's to read (`sudo cat imports/first_passwords.csv`):

```bash
docker compose run --rm --user root web python manage.py import_csv employees /app/imports/employees.csv --commit --passwords-out /app/imports/first_passwords.csv
```

 Hand each out, have it changed at
the first sign-in, and delete the file. Someone with no manager and no
department manager has their leave decided by HR, who may decide anyone's.

### customer_reps

`customer*` (code), `rep*` (the rep's employee number, made a rep with
`sales_rep` yes in the employees file). A rep sees their own customers
and nobody else's, so a customer left out here is seen by no rep until
the AR Manager sets one on the customer's sales terms; `go_live_check`
counts them. Run again to move a customer to another rep.

### items

`sku*`, `name*`, `uom*` (unit code), `hsn_code`, `item_type` (`goods`,
`service`), `track_inventory` (default yes), `costing_method`,
`tracking`, `sale_price`, `standard_cost`.

`stock_class` (raw_material, packing, consumable, semi_finished or finished) groups the item on the bank's stock statement; left blank it shows there as unclassified until the controller sets it.

`sales_taxes` and `purchase_taxes` (codes separated by `;`: a tax, such
as `CGST9;SGST9`, or a tax group, such as `GST18`, meaning every tax in
it) are what a line of the item takes when it names none. Give each
taxed good its intra-state pair: the party's fiscal position turns it
into IGST across a state line. **An item left without them is read as
bearing no tax**, and its orders, invoices and bills go out with no GST;
`go_live_check` lists goods that declare none.

### opening_stock — dated the day the books opened

`sku*`, `warehouse*` (code), `quantity*` (above nothing), `unit_cost*`
(what one stocking unit is worth), `lot` (batch code; made if new).

The stock is what was counted on the day the books opened (Settings,
Books opening), and the adjustment is dated that day, so the trial
balance at the opening day holds it beside the balances and the
register. Until the day is set the file is refused: `Set the day the
books opened (Settings, Books opening) first: opening stock is what was
counted on it.` A `--date` given must be that day; any other is refused:
`2026-04-10 is not the day the books opened (2026-04-01); the books
setting says when, and the import follows it.` A count taken on a later
day is worked back to the opening day before the file is filled; what
moved since is entered as it happened.

### open_invoices and open_bills — against the opening-balance account

`customer*` or `vendor*` (code, of a party with that role),
`reference*` (the old document's number: refused if that party already
has one with it), `date*` (the old document's date), `amount*` (what is
still owed, tax included), `payment_terms` (code; default the party's),
`currency` (code; default the party's). The reference must be the old
invoice's number exactly as it was filed: a credit note with GST raised
on it later is reported against that number.

Both wait for the books' opening day (Settings, Books opening): until
it is set every row is refused, as the stock and the balances are. Each
document stands against the opening-balance account set there, so that
account is nothing again once the stock, the documents and the opening
balances are all in. An `--against` given must be that account; any
other is refused: `3000 is not the opening-balance account (3900) the
books opened against (Settings, Books opening): leave --against out, or
give that one.` Opening stock is held to it too: a `--reason` that
posts to another account is refused.

The due date follows from the date and the terms, as it would for a new
invoice, so aging is right from the first day. Only what is still owed
comes in: an old invoice paid in part comes in at its unpaid part. A
credit owed back to a customer is not an open invoice; record it as a
credit note after go-live.

### opening_balances — needs `--date`

`account*` (code), `debit` or `credit` (one, above nothing),
`description`. Every other balance on the old trial balance: bank,
loans, deposits, GST ledgers, and the opening-balance account itself,
so the file balances. The receivable, payable, inventory and
goods-received accounts are refused here: they come in as documents.
So are the cost and accumulated-depreciation accounts of every asset
category: the asset register (`fixed_assets`) posts them, each asset
its own.

### tape_specs, fabric_specs, bag_specs, film_specs, liner_specs

The columns are the specification's own fields, in the template's order;
`code*` and the items and specifications it names are required, the rest
take the model's defaults. An item is named by its `sku`, a tape or
fabric specification by its `code`, a routing by its `code`. Choices are
the system's words (`tubular`, `valve`); yes-or-no columns take yes or no.
Each specification builds its bill of materials and inspection plan as it
is kept, so tapes come before the fabrics woven from them and fabrics
before the bags cut from them. A weighed item must be stocked in the base
weight unit (kg); a bag item in a count.

### open_sales_orders and open_purchase_orders

One row per line; rows with the same `customer*` (or `vendor*`) and
`reference*` (the old order's number) make one order, dated `date*`, in
`currency` (default the party's) and, for sales, on `payment_terms`
(default the customer's). Each line: `sku*`, `description`, `quantity*`
(what is still to deliver or receive, above nothing), `uom` (default the
item's), `unit_price*`, `delivery_date` / `expected_date`, `warehouse`
(code), `taxes` (codes separated by `;`, a tax or a tax group; blank takes the item's, `none` says none). An order is confirmed once all
its rows are in, under the same rules as one typed in — an approval the
policy would ask for is reported as a problem, not skipped. Deliveries
already made in the old system are not brought in: the quantity is what
is left.

### fixed_assets — needs `--date`

`name*`, `category*` (code), `vendor` (code), `acquisition_date*`,
`in_service_date` (default the acquisition date; on or before the
go-live date), `cost*`, `salvage_value` (default 0), `life_months`
(default the category's), `depreciated_to_date` (what the old system
had charged by the go-live date). The asset goes into service with that
figure recorded against it and posts its own opening entry on the day
the books opened: its cost to the category's asset account, what was
depreciated to its accumulated account, the rest against the
opening-balance account. Leave those two accounts out of
`opening_balances`, which refuses them. This system charges only the
months after the go-live date. Cut over at a month end, so no month is split between the
two systems.

### compensation

`employee_number*`, `component*` (pay component code), `amount*`,
`effective_from*`, `effective_to`, `note`.

### price_lists and vendor_prices

`price_lists`: `price_list*` (code; made on first sight with
`price_list_name` and `currency`), `sku*`, `min_quantity` (the volume
break, default 1), `unit_price*`. `vendor_prices`: `vendor*` (code),
`sku*`, `unit_price*`, `currency` (default the vendor's), `min_quantity`,
`vendor_item_code`, `lead_time_days`, `valid_from`, `valid_to`,
`is_preferred`.

### shipment_history

`sku*`, `warehouse*` (code), `month*` (`2025-04` or `2025-04-01`),
`quantity*` (shipped less returned that month, in the item's stock unit,
nothing or more), `note` (where the figure came from). Two or three
years of it, so the seasonal forecast has a past to read from the first
month; without it the forecast proposes nothing until a year of
deliveries has gone through here. Only months this system did not ship
in itself: a month with posted deliveries is refused, because it is
counted already. Run again to correct a month; the figure is replaced.
The planner also sees and keeps it under Planning, Shipment history.

## What this does not do

- It does not update a record that exists; it only adds.
- It does not bring in history: old paid invoices, past stock movements
  or past orders. Look them up in the old system.
- Deliveries and receipts already made against an open order are not
  brought in; the order comes in at what is left of it.
- An opening invoice or bill is dated in a period the old system
  already filed GST for. Do not compile GSTR-1 or GSTR-3B for periods
  before go-live in this system: they would list the opening invoices
  as supplies with no tax.
