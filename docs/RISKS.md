# What could still bite us

Kept current with the code: anything here is either a known limit, a
decision still owed, or a place the tests do not reach. Each says what
would go wrong, how likely it is here, and what to do about it. Remove
an entry only in the commit that removes the risk.

Last reviewed: 2026-10-05.

## Before the first real day

These are not code defects. They are what no test in this repository
can prove.

1. **No real person has used it yet.** Every flow was driven by tests
   written by the people who built it, in a browser, as each role. The
   screens open without error for all 16 roles (`apps/web/tests_browser_sweep.py`),
   but whether the plant's own clerks can find their way, and whether
   the words on the screens are the plant's words, is unknown.
   *Do:* a pilot with two or three people per desk on a copy of real
   data, for a fortnight, before anything is switched off.
2. **There is no import from the old system.** Opening balances,
   open orders, item masters, parties and stock on hand would be keyed
   by hand or loaded through the API. *Do:* see "Later items", L4.
3. **The Docker image has never been built here.** Docker Hub was not
   reachable from this environment, so `Dockerfile` and
   `docker-compose.yml` are unverified. *Do:* build and start them once
   on the server, then restore a backup into them (RUNBOOK.md, "Proving a
   backup restores") before go-live.
4. **Speed was measured on made-up data.** The perf databases hold a
   year and five years of generated documents; every search, list and
   ledger answered in under 1.1 s there. Real data has different
   shapes (one customer with half the invoices, very long orders).
   *Do:* re-measure the lists people complain about in the pilot.
5. **Backups are only as good as the last restore.** The runbook asks
   for a monthly restore check; nothing enforces it.

## Known limits in the code

6. **Permissions are by model, not by record, except for leave.** Anyone
   who may post invoices may post *any* invoice, and a sales rep reads
   every customer. Leave is the exception: a login is linked to its
   employee, people read their own and their reports' requests, and only
   the employee's manager (or one above, or their department's) decides.
   Nothing else is scoped to the person yet. *Likely to matter:* once
   more than one team shares a role.
7. **Login lockout is per user name.** Ten wrong passwords in fifteen
   minutes lock that name, from anywhere: a person who knows a
   colleague's user name can lock them out. There is no per-address
   throttle. *Do:* put the server behind a proxy that rate-limits
   `/accounts/login/` by address.
8. **GST is compiled, not filed.** GSTR-1, GSTR-3B and ITC-04 are
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

## Decisions taken without the user, to confirm

15. **A drop-ship is awaited until it is received, cancelled or closed
    short.** Its customer line is not shipped from the shelf, held in
    stock, or planned by MRP meanwhile. If goods go back to the vendor
    and will not be replaced, close the purchase line short; the shelf
    then owes the customer and holds stock for them again.
16. **A payment settles only documents booked to the same control
    account and currency.** A receipt recorded against revenue (a cash
    sale) cannot be applied to an invoice; record it against the
    receivable.
17. **Leave is decided only by a manager, as themselves.** Someone with
    no manager and no department manager cannot have leave decided until
    HR sets one. A new "Line Manager" role decides leave and reads only
    their reports'; HR Admin and Payroll Officer read everyone's. An
    administrator may still name the decider, and the audit trail records
    who did. Logins must be linked to employees (the employee record's
    `user`) before any of this works for them: an unlinked login sees no
    leave at all.
18. **A purchase line can now be closed short**, like a sales line: the
    rest is no longer expected, planned as supply or awaited by a
    customer on a drop-ship. Refused while more is billed than received
    (raise a debit note first).

## Later items

Tracked here until done; each moves to "Known limits" with what it does
not cover, or is deleted with the commit that finishes it.

- **L1 GST on advances received for job work.** Not built: an advance
  against a job-work order carries no GST liability today.
- ~~L2 Refunding part of a customer deposit~~ — done: a deposit is
  credited back by any amount up to what is left (and a vendor
  prepayment debited back the same way), from the invoice or bill
  screen or `{"amount": "90.00"}` on the credit or debit note action.
- ~~L3 Link a login to an employee~~ — done, for leave; see items 6 and 17.
- **L4 Import from files (CSV).** Not built; see item 2.
