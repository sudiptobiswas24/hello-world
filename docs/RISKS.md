# What could still bite us

Kept current with the code: anything here is either a known limit, a
decision still owed, or a place the tests do not reach. Each says what
would go wrong, how likely it is here, and what to do about it. Remove
an entry only in the commit that removes the risk.

Last reviewed: 2026-10-06.

## Before the first real day

These are not code defects. They are what no test in this repository
can prove.

1. **No real person has used it yet.** Every flow was driven by tests
   written by the people who built it, in a browser, as each role. The
   screens open without error for all 17 roles (`apps/web/tests_browser_sweep.py`),
   but whether the plant's own clerks can find their way, and whether
   the words on the screens are the plant's words, is unknown.
   *Do:* the pilot in docs/PILOT.md: a fortnight, two or three people
   per desk, the same day's work in both systems, three numbers compared
   every evening, and the pass conditions written down before it starts.
2. **The import has never seen the plant's own files.** `import_csv`
   (docs/IMPORT.md) brings in parties, items, opening stock, open
   invoices and bills and the other opening balances, dry run first,
   each file whole or not at all. It was tested on files written for the
   tests. The old system's exports will have their own column names,
   date shapes and oddities. *Do:* fill the blank files `import_csv
   <kind> <file> --template` writes, dry-run every one on a copy of the
   database weeks before go-live, then `go_live_check`, and reconcile the
   opening-balance account to the old system's equity. It adds and never
   updates, and brings in no history and no open orders.
3. **The Docker deployment has run once, here, not on the plant's
   server.** On 2026-10-05 the image was built from this `Dockerfile`
   (with only this build environment's proxy certificate added), the
   stack started from `docker-compose.yml` on an empty database (about
   five minutes of migrations, 502 from Caddy meanwhile), the office
   application signed in over HTTPS through Caddy, and a backup was
   taken, restored into a scratch database, and restored over the live
   one, which dropped what was made after it. The plant's server, its
   network name and its certificate are still untried. *Do:* the same
   on the server, following RUNBOOK.md, before the pilot.
4. **Speed was measured on made-up data.** The perf databases hold a
   year and five years of generated documents; every search, list and
   ledger answered in under 1.1 s there. Real data has different
   shapes (one customer with half the invoices, very long orders).
   *Do:* re-measure the lists people complain about in the pilot.
5. **Backups are only as good as the last restore.** The runbook asks
   for a monthly restore check; nothing enforces it.

## Known limits in the code

6. **Permissions are by model, not by record, except for leave and a
   rep's customers.** Anyone who may post invoices may post *any*
   invoice. Two things are scoped to the person: leave (people read their
   own and their reports' requests, and their manager chain or HR
   decides), and customers. A sales rep sees, sells to and changes only
   the customers carried by them (each customer's sales terms name its
   rep), with their orders, quotations, invoices and the sales and
   receivables reports; a customer they make is theirs. Everyone else
   holds `sales.view_every_customer` and sees all. A login that reads
   parties without that permission and is nobody's rep sees no customer:
   a custom role made outside `setup_roles` must be given it.
   `go_live_check` names reps who carry nobody and customers no rep
   carries. Nothing else is scoped to the person yet. *Likely to matter:*
   a purchasing team split by vendor.
7. **Sign-in guessing is limited by name and address together.** Ten
   wrong in fifteen minutes lock that name from that address only, so a
   colleague cannot lock someone out from their own desk; fifty from one
   address lock the address; a hundred at one name from anywhere lock
   the name, which takes a script, not a grudge. The address is read
   through Caddy (`DJANGO_TRUSTED_PROXIES=1`); add a proxy in front of
   Caddy without raising it and every sign-in shares the proxy's address.
8. **GST is compiled, not filed.** Opening invoices and bills from the
   import are marked and left out of every return and e-invoice: the old
   system reported them. A rate difference or a return on something the
   old system invoiced is a **credit note with GST** on the opening
   invoice (its own lines and tax): it reduces this month's output tax,
   is reported in GSTR-1 against the old invoice's number and date, and
   is e-invoiced against them. For an unregistered buyer the old
   invoice's full value is asked, since it decides whether the note is
   reported as a large one; for a registered buyer it is optional, and
   without it nothing stops notes crediting more than the old invoice
   was for (what was owed on it is not what it was for). The mirror on bills, a debit note with GST,
   takes the input tax back. The old system's number is the opening
   invoice's reference, so that reference must be the number exactly as
   it was filed. **Filing:** GSTR-1, GSTR-3B and ITC-04 are
   built; e-invoice and e-way bill payloads are built. Nothing is sent
   to the GST portal or NIC: someone uploads them. A payload the portal
   rejects is found out there, not here.
9. **One company.** `Company.get()` is a single row. A second legal
   entity is a second installation.
10. **Stock value as of a past date replays history.** On five years of
    generated data, the valuation report as of a date before the latest
    fold took about 1.0 s; it grows with the movements before the
    oldest usable fold. Today's value is fast (about 0.14 s).
11. **"Orders to ship" and "orders to receive" pass a list of order ids
    to the database.** On SQLite this fails past 32,766 open orders. Not
    a concern on PostgreSQL, which is what production runs.
12. **Components sent to a subcontractor and brought back by a stock
    transfer still count as sent.** `issue_components()` then sends
    nothing by default; send what is needed with explicit `quantities`.
13. **Only Chromium is tested.** The browser tests run Chromium; Firefox
    and Safari have never opened the office application.
14. **The sweep proves screens open, not that their numbers are right.**
    Numbers are proven by the model and API tests and by the documents
    being read back from the ledger; a screen that formats a right
    number wrongly is caught only where a flow test reads that cell.

## Decisions taken for the owner, confirmed on 2026-10-05

15. **A drop-ship is awaited until it is received, cancelled or closed
    short.** Its customer line is not shipped from the shelf, held in
    stock, or planned by MRP meanwhile. If goods go back to the vendor
    and will not be replaced, close the purchase line short; the shelf
    then owes the customer and holds stock for them again.
16. **A payment settles only documents booked to the same control
    account and currency.** A receipt recorded against revenue (a cash
    sale) cannot be applied to an invoice; record it against the
    receivable.
17. **Leave is decided by the employee's manager (or one above, or
    their department's), or by HR, always as themselves and never their
    own** (confirmed by the owner, 2026-10-05). It is asked and decided
    on the Leave screen (People). HR Admin holds
    `hr.decide_any_leaverequest` and may decide anyone's, a manager's
    team included; a Line Manager decides and reads only their reports'.
    HR Admin and Payroll Officer read everyone's. Logins must be linked
    to employees (the employee record's `user`, or the employees import)
    before any of this works for them: an unlinked login sees no leave.
18. **A purchase line can now be closed short**, like a sales line: the
    rest is no longer expected, planned as supply or awaited by a
    customer on a drop-ship. Refused while more is billed than received
    (raise a debit note first).
19. **GST on an advance is charged on job-work orders only**, at the
    order's tax (an order whose lines bear different taxes takes no
    single advance), and reversed when the advance is drawn down or given
    back; the last of it takes exactly what is left. An advance received
    and invoiced in the same month appears in neither 11A nor 11B. Part
    of an advance whose amount the tax rounding cannot reach to the
    paisa is refused, naming the amounts either side. Advances paid *to*
    vendors carry no tax here: credit on them waits for the vendor's
    invoice.

## Later items

Tracked here until done; each moves to "Known limits" with what it does
not cover, or is deleted with the commit that finishes it.

- ~~L1 GST on advances received for job work~~ — done; see item 19.
- ~~L2 Refunding part of a customer deposit~~ — done: a deposit is
  credited back by any amount up to what is left (and a vendor
  prepayment debited back the same way), from the invoice or bill
  screen or `{"amount": "90.00"}` on the credit or debit note action.
- ~~L3 Link a login to an employee~~ — done, for leave; see items 6 and 17.
- ~~L4 Import from files (CSV)~~ — done; see item 2 for what it has
  not been tried on.
- L5 An item's costing method and tracking are not yet fixed once its
  stock has moved, as its unit is. A costing method changed over stock
  already held replays every past movement under a method it never
  happened by, and the stock's value parts from the ledger with no
  entry to say why. Lot tracking switched on over stock already held
  strands that stock: every movement must then name a batch, and that
  stock is in none. The item screen sets both only when the item is
  made; the API still takes a change. Test fixtures under standard
  costing, valuation folds and lot trace switch them over stock and are
  reworked first, or a step that switches one revalues the stock or
  puts it into an opening batch.
- ~~L6 A second role for a party already made~~ — done: given on the
  party's page (`parties/<id>/roles/`), asked as making the party is
  asked; a rep claims no vendor, and a role a document names stays.
