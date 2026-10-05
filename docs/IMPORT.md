# Bringing the old system's records in

Once, at go-live, from CSV files saved out of the old system or a
spreadsheet (UTF-8; a spreadsheet's byte-order mark is fine). Eight
kinds, brought in in this order, because each needs the one before:

| Order | Kind | What it makes |
|---|---|---|
| 1 | `parties` | Customers, vendors, with GST details, credit limit, billing address |
| 2 | `employees` | Employees with department and manager, and their logins and roles |
| 3 | `customer_reps` | Which rep each customer belongs to |
| 4 | `items` | Items with their unit, HSN and costing |
| 5 | `opening_stock` | One posted stock adjustment a warehouse |
| 6 | `open_invoices` | Posted invoices still owed, one per old invoice |
| 7 | `open_bills` | Posted bills still owing, one per old bill |
| 8 | `opening_balances` | One posted journal entry for every other balance |

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
the journal entry for everything else. Once all six are in, that account
holds the old system's equity; if it does not, something was left out.

Make, before the opening position:

- the opening-balance account;
- an adjustment reason with code `OPENING` (or name another with
  `--reason`) whose account is the opening-balance account;
- the company's default receivable and payable accounts.

## Columns

`*` is required.

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
is no one else's), `roles` (role names separated by `;`, such as
`Line Manager;Employee Self Service`; needs `username`), `sales_rep`
(`yes` makes them a sales rep, who can then carry customers).

With `--commit`, `--passwords-out <file>` is required: each new login's
first password is written there, readable only by whoever ran the
import, and never shown on the screen. Hand each out, have it changed at
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

### opening_stock — needs `--date`

`sku*`, `warehouse*` (code), `quantity*` (above nothing), `unit_cost*`
(what one stocking unit is worth), `lot` (batch code; made if new).

### open_invoices and open_bills — need `--against <account code>`

`customer*` or `vendor*` (code, of a party with that role),
`reference*` (the old document's number: refused if that party already
has one with it), `date*` (the old document's date), `amount*` (what is
still owed, tax included), `payment_terms` (code; default the party's),
`currency` (code; default the party's). The reference must be the old
invoice's number exactly as it was filed: a credit note with GST raised
on it later is reported against that number.

The due date follows from the date and the terms, as it would for a new
invoice, so aging is right from the first day. Only what is still owed
comes in: an old invoice paid in part comes in at its unpaid part. A
credit owed back to a customer is not an open invoice; record it as a
credit note after go-live.

### opening_balances — needs `--date`

`account*` (code), `debit` or `credit` (one, above nothing),
`description`. Every other balance on the old trial balance: bank,
loans, fixed assets, GST ledgers, and the opening-balance account
itself, so the file balances. The receivable, payable, inventory and
goods-received accounts are refused here: they come in as documents.

## What this does not do

- It does not update a record that exists; it only adds.
- It does not bring in history: old paid invoices, past stock movements
  or past orders. Look them up in the old system.
- Open sales and purchase orders are not imported; enter them as new
  orders.
- An opening invoice or bill is dated in a period the old system
  already filed GST for. Do not compile GSTR-1 or GSTR-3B for periods
  before go-live in this system: they would list the opening invoices
  as supplies with no tax.
