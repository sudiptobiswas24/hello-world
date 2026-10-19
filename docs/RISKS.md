# What could still bite us

Kept current with the code: anything here is either a known limit, a
decision still owed, or a place the tests do not reach. Each says what
would go wrong, how likely it is here, and what to do about it. Remove
an entry only in the commit that removes the risk.

Last reviewed: 2026-10-09.

## Before the first real day

These are not code defects. They are what no test in this repository
can prove.

1. **No real person has used it yet.** Every flow was driven by tests
   written by the people who built it, in a browser, as each role. The
   screens open without error for all 20 roles (`apps/web/tests_browser_sweep.py`),
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
6. **TDS rates are the plant's to enter, and none are entered.** The
   sections, their rates, thresholds and accounts are kept on the TDS
   sections screen because they change with every budget. *Do:* enter
   this year's 194Q, 194C, 194J and 194I with the plant's accountant,
   and each vendor's PAN and section, before the first bill is paid.

## Known limits in the code

- **An invoice posted before g21 reprints today's names** (O218): the
  details a tax invoice prints are kept at posting (`Invoice.issued_as`)
  only from g21 on; an invoice posted earlier, with no record of how it was
  issued, prints the parties' current names and addresses. No real invoice
  exists before g21, so only test and demo data are affected.
- **Unit prices keep two places** (the owner's decision 23, O208): a price
  converted from another currency is rounded to the paisa per unit and the
  line follows that rate, so 1,000 units at 10 USD / 1.1 come to 9,090.00,
  not 9,090.91. The rate on the invoice is the one charged.

- **Pay slips posted before hr migration 0022** have no kept days, marker
  or wage base (`kept_paid_days is None`): they read the working pattern,
  holiday region, leave, register and PF settings as they stand, so a later
  edit restates them. Only months worked out after g20 are frozen. Nothing
  is invented for the old ones.
- **A run counted in a unit of another chain** (pieces of a kg item) cannot
  be released ("Cannot convert pc to kg"), so `check_output`,
  `outside_earned`, `conversion_earned` and `time_variance_minutes` read a
  date-independent factor. Were such runs ever allowed, those four would
  need each entry's own day's factor (g20 review R10).

- **Approvals seeded before sales 0064 whose notes name no figures**
  (the rest of O200, closed in g20): a line cut after the seed keeps what
  it held, and total and margin stay empty. The history cannot tell what
  was approved; g20 matches what it can (a charge line by its charge's
  name).

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
15. **TDS stops at the list.** The quarter's deductions are listed as
    26Q reads them, but no return file (FVU) is produced and Form 26AS
    is matched by hand. Tax is deducted on a bill, not on an advance
    paid before one, and payments abroad (section 195) are refused. A
    year's threshold is reckoned in bill-date order: a bill entered late
    for an earlier date does not re-reckon the deductions after it.
16. **Reverse charge stops at the books and the return.** The tax is
    owed and claimed and 3B shows it, but nothing makes it paid in cash
    rather than set off against credit (table 6 is not built), and no
    self-invoice is raised for a supply from an unregistered person.
    The reverse-charge taxes are chosen on the bill line: IGST from
    another state, CGST and SGST from inside it.
17. **MSME days run from the bill's date.** The Act counts from the day
    the goods are accepted, which is usually the bill's date here and
    sometimes later; the list does not know the difference. Interest
    owed on a late payment (three times the bank rate, compounded
    monthly) is not computed, and a Udyam number is checked for its
    shape, not against the registry.
18. **A claim credit is not undone.** Like every credit note it is a
    posted correction; a claim credited in error is answered by a new
    invoice for the amount, not by reversing the note.
19. **Bonus and gratuity are pay components, set up by hand.** The
    statutory bonus (8.33% of basic and DA capped at 7,000, for those
    earning 21,000 or less) and the gratuity provision (4.81% of basic)
    accrue only once those components are made and given to each person;
    nothing makes them. The bonus is paid out by an earning that debits
    the bonus payable. Thirty days' work before a bonus is owed, and the
    actuarial valuation an auditor may want for gratuity, are not done:
    the gratuity list is the Act's own arithmetic on last wages.
20. **A sale's e-way bill takes the truck from its order's one delivery.**
    An order shipped on two trucks and invoiced once leaves the e-way
    bill's transport to be typed, because which truck the invoice went
    on is not recorded.
21. **A proof of delivery is a reference, not the paper.** The receipt
    records the date, who signed and the customer's GRN number; the
    signed copy itself is kept in the file, because nothing here stores
    a scan.
22. **Tape line settings are typed from the line, not read from it.**
    They are as good as the shift's habit of entering a change when it
    is made; a run with no setting recorded traces only to its granule
    lot, as before.
23. **The licence calendar is read, not sent.** A licence due for
    renewal shows on its list until the morning checks (22 in the plant
    review) put it in somebody's inbox; nothing mails anyone yet.
24. **Attendance is a punch file pasted in, not a reader on the wall.**
    The biometric reader's export is read as CSV (employee number, date,
    shift, in, out) and checked whole; nothing talks to the device. A
    row with one punch is listed to be entered by hand, not guessed at,
    and how late somebody was needs the row's shift code to be a shift
    the plant keeps.
25. **Kilogrammes on the daily report are what was weighed or what the
    item converts to.** Rolls and doffs carry their weight; a sack
    converts through its specification; a section whose output nothing
    converts shows its unit and no kilogrammes, and its kWh a kilo is
    blank rather than wrong. The kilogramme is the weight unit coded
    kg. The kWh is the section's meters' whole draw for the day, idle
    included, laid against its good output.
26. **Maximum demand and power factor are copied off the board's meter
    by hand.** The highest demand shown for a window is the highest any
    reading in it carried, which is the board's own figure only if
    somebody read the meter before the board reset it at the month's
    end; nothing reads the meter itself.
27. **Imported shipment history is as good as the old system's
    register.** A month is one net figure an item and a warehouse,
    with no deliveries behind it to trace; the forecast reads it as it
    reads its own months. A month this system shipped in is refused,
    so the two cannot overlap, but a figure typed for the wrong item
    forecasts the wrong item.
28. **The bank statement groups stock by the class set on each item.**
    An item nobody classed shows on its own "unclassified" line rather
    than in a class, so the statement is honest and incomplete until
    the stores set them. Debtors are cut at ninety days, the aging's
    own buckets; a bank that cuts at sixty reads the aging. The margins
    are typed each time, not kept, and the statement is read, never
    stored: last month's is reproduced by asking for its date, and a
    posting dated back since then changes it.
29. **Packing is expensed as the bale is pressed, not added to the
    sacks' value.** The cover, straps and label leave stores under the
    packing reason's account the moment a bale is sealed; a bale broken
    afterwards keeps them consumed, because they were cut. A sack with
    no recipe packs with nothing drawn, which the bale shows.
30. **Finding a record searches what is written on it, five of a kind
    at a time.** Numbers, codes, names and lorry registrations; not
    amounts, dates or the text of a note. A rep finds only their own
    customers' documents, as their lists show.
31. **A CSV is the screen's columns, every page of the current
    filter.** Values go out as the server sends them (exact money and
    quantities, ISO dates); a column the screen renders from several
    fields leaves as its raw value. It is built in the browser, so a
    list of a hundred thousand rows is a wait.
32. **A spare's life is the gap between placements at the same
    position, as the fitters recorded them.** An issue not told the
    position leaves no gap to read. The critical list counts the spare
    on every shelf, not the maintenance store alone.
33. **The morning checks know eighteen questions.** Maintenance and
    calibration due, licences, MSME days, attendance, deliveries
    unsigned, freight unbilled, invoices and bills past due, four kinds
    of draft over two days, stock under level, meters unread, complaint
    actions overdue, a rep's follow-ups due. Anything else still waits to be asked. The mail
    needs a mail server named (RUNBOOK.md) and a cron line; the home
    page needs neither.

## Open defects

Each one has been seen in the code and not yet fixed. The 8 October
audit and its fix agents found them. A row comes off only in the commit
that fixes it, and that commit's message names its id; the fix has a
test that fails without it. Severity is the integrator's judgement:
**crash** (an action fails for everyone), **books** (money or stock
comes out wrong), **race** (two people at once), **rule** (a check
missing), **minor**.

| Id | Area | Defect | Kind | State |
|---|---|---|---|---|
| O11 | inventory | Cancelling a put-away transfer leaves its receipt route move standing | rule | open |
| O12 | assets | `place_in_service` accepts an in-service date still to come | rule | open |
| O13 | assets | `depreciated_before` is not enforced on a draft made by hand | rule | open |
| O14 | assets | The admin lets an asset's status be edited directly | rule | open |
| O15 | all | 17 correction steps outside hr and assets are not yet on `correction_date()` (named in `DATED_ELSEWHERE`) | rule | open, one module at a time |
| O23 | inventory | The adjustment post endpoint answers with a stale `total_value` (lines prefetched before posting) | minor | open |
| O24 | accounting | `Account.currency` is read by nothing | minor | open |
| O25 | frontend | RecordScreen's afterCreate comment says it decides where to go after a delete; the code does not (mistake 1) | minor | open |
| O26 | assets | Capitalising a bill line has no API action | minor | open |
| O27 | inventory | Migrations 0030 and 0037 have no migration test | rule | open |
| O29 | accounting, assets | A period closes while its depreciation is uncharged. Decided: refuse it, through a period-close check that accounting keeps and assets registers with. Not built | rule | to build |
| O32 | hr | `LeaveRequest.cancel(on_date)` takes any date: called in code with a day before the leave, it cancels a holiday already taken and restores the balance. The API passes no date | rule | open |
| O34 | assets | The fixed-asset register is not footed against the ledger in the daily health checks | rule | open |
| O40 | core | Audit gaps: `per_unit_withdrawal_rates` skips inventory/models.py and `average_cost_at`; `shelf_read_before_lock` sees a read and a lock only inside one function | rule | open |
| O45 | manufacturing, core | Three admin mixins refuse the same thing (`PostedNotDeletedMixin`, `PostedImmutableAdminMixin`, `PostedDocumentAdminMixin`): fold into one (mistake 5) | rule | open |
| O46 | core | `unlocked_open_runs` sees only `.is_open()`; a run's status compared to RELEASED by a function that does not hold the run goes unreported (station clock and job work were found by reading) | rule | open |
| O77 | sales | The credit limit adds currencies at face value: 600 USD plus 380 EUR reads 980 against 1,000; the true figure is 1,018.00 | rule | open; CreditLimitProbe |
| O81 | sales | The dunning mail asks for the whole balance and the last due date, not what the notice records as overdue (mistake 1) | minor | open; DunningProbe |
| O91 | purchasing | The match's price check ignores the agreed discount: 10 @ 100 less 10% agreed, billed at 100 with tolerance 0, posts with 100 of price variance | rule | open; audit_pur probes |
| O95 | purchasing | The same vendor invoice number in another case is accepted: INV-77 and inv-77 both post | rule | minor; audit_pur probes |
| O99 | purchasing | A hand-typed price passes the approval rule whenever an agreed price exists, used or not: agreed 5.00, typed 50.00, confirmed with no approval reasons. `price_against_agreement` exists and nothing calls it | rule | open; audit_pur probes |
| O109 | purchasing, sales | Built and never called (shape 2): settlement discounts on both sides (`Bill.take_settlement_discount`, `Invoice.apply_settlement_discount`), `Bill.match_report`, `PurchaseRequisition.suggested_vendors`, `BlanketOrderLine.is_fully_released`. Each should be reached from its screen or action, or removed | rule | open |
| O127 | manufacturing | `FROZEN_AT_RELEASE` on work orders is a separate copy of the frozen-once-moved rule (`refuse_changing_what_moved`); one rule should serve both (mistake 5). The no-order-line branch of `BillLine.accrual()` can no longer be reached | minor | open |
| O130 | core | `scoped()` filters `path__in`, so rows whose party path is NULL drop out of a rep's view | rule | minor |
| O132 | docs, frontend | docs/IMPORT.md still documents the employees import's roles column, which the import no longer reads; the leave screen does not offer Cancel to a manager the server allows | minor | open |
| O133 | inventory, manufacturing | `allocate()` does not subtract tape loaded on a backflushed run's creels and not yet drawn: a pick naming no lot can choose a doff whose 100 kg is all on a creel | rule | open |
| O134 | inventory | tracking.py's shortfall message ("Only ...") is not normalised, so PostgreSQL shows 300.0000 | minor | open |
| O136 | sales (CRM) | `Opportunity.sales_order` has no unique constraint in the database; only the lock in `win()` keeps one order to one opportunity | minor | open |
| O152 | gst | The e-invoice `ShipDtls` carries the buyer's GSTIN and name even when the goods go to a third party (gst/einvoice.py:289) | statutory | open |
| O153 | gst | The e-way bill's `transactionType` is 2 (bill-to-ship-to) for delivery to the buyer's own other address, where NIC's meaning is a third party (gst/ewaybill.py:366). The tax adviser to decide | statutory | decide |
| O154 | purchasing, gst | Debit notes raised by a receipt return (`create_return(debit_bills=True)`) post at once, so the supplier's credit-note number can never be recorded on them; GSTR-2B pairs them only on date and value. O57's fix covers hand-made debit notes only | statutory | open |
| O156 | frontend | The TDS return screen has no "reverses" column; the bank statement screen can match a line only to a payment, so a line for a direct entry (a TDS challan, an expense claim) is matched only through the API or auto-match | minor | open |
| O166 | gst | The e-way bill codes a sales delivery as a delivery challan ("CHL", gst/ewaybill.py near line 500), though a sales delivery travels on its tax invoice. The tax adviser to decide | statutory | decide |
| O167 | purchasing | Purchasing has the O72/O141 shape: a confirmed purchase order's approval is withdrawn on any re-price, and only the budget is asked again, never the order value, line value or agreed-price policy (`PurchaseOrderLine.save`) | rule | open |
| O176 | hr | Paying an expense claim may be dated ahead (refused only before the claim); an existing test relies on paying ahead, so `ExpenseClaim.pay` stays exempted from the correction-date check. Refusing a day still to come would match the rule that nothing is posted for a day that has not come | rule | minor |
| O188 | purchasing | MSME report: a part settled wholly by a debit note or TDS (no payment) has no paid-on day, so days late runs to the report date: unpaid 0.00, days late 168 (docs/handoff/reports/fix_stat.md, seen #1) | statutory | open |
| O189 | purchasing | `Bill.pay_by()` and the bill's shown due date are still the whole bill's when one delivery is objected to (the payment run is right) (docs/handoff/reports/fix_stat.md, seen #2) | statutory | minor |
| O190 | purchasing | A debit note left unkeyed by migration 0063 (a collision), edited as a draft, is re-keyed by clean() and refused by the constraint with a generic message (docs/handoff/reports/fix_stat.md, seen #3) | minor | open |
| O197 | core | `UserViewSet.perform_create` and `perform_update` run without a transaction: a create refused while giving roles answers 400 but leaves the login made, with no roles (docs/handoff/reports/fix_sec.md, seen #3) | rule | open |
| O202 | manufacturing, sales | A cost sheet, a test certificate and a price-variation bill made over the API carry no created_by: quoting.cost(), certificates.issue() and bill_variation() take no maker. Their history line names who; the stamp on the record is blank (seen 2026-10-11, exempted in audit_invariants WRITES_AROUND_THE_MIXIN_ON_PURPOSE) | audit trail | open |
| O203 | quality | The Quality Manager's escalation counts Monday to Friday and never sees a plant holiday: quality may not import hr, whose calendar knows them. A failure posted the day before Diwali goes to the administrator on Diwali. A calendar hook in core, as lot holds have, would let hr answer (seen 2026-10-14) | minor | open |
| O209 | accounting | After g20's settlement slices: deleting or re-pointing a middle allocation re-states only its own slice, so a paisa may reappear on the control account; credit notes on an invoice still book their own lines' bases, not a slice of the invoice's. Not probed (docs/handoff/reports/fix_money.md, "Review fixes") (seen 2026-10-14) | books | open, unproven |
| O210 | inventory | `follow_value` (inventory/costing.py) follows transfer steps only: goods cleared out of inspection are not followed, so the share of landed freight on goods since gone is classed as cost of sales whatever they went into (the shelf share is right). docs/handoff/reports/fix_cost.md, seen #4 (seen 2026-10-14) | books | open |
| O211 | purchasing | O94's ranges compare gross receipt quantities with bills in posting order: a return posted between two bills shifts which bill a later return finds first (`_bill_lines_that_paid_for`). fix_cost.md seen #5 (seen 2026-10-14) | books | minor |
| O212 | core | `reported_without_the_period` (audit_invariants) maps every word of a followed function, docstrings included, to a model: "made" in a docstring reported ProcessRoll. Two docstrings were reworded to pass; the check should read code, not words. fix_cost.md seen #6 (seen 2026-10-14) | minor | open |
| O213 | hr | Outside a final run, a take-back for earlier runs is netted into the period's piece-work earning (payroll.py `work_out`, PER_UNIT), so PF/ESI wages drop by it. Decided by the integrator on the owner's rule for leavers (item 20): PF/ESI wages are never reduced by a recovery, on any run (seen 2026-10-14) | books | open |
| O214 | hr | The deduction cap (half the wages) applies on a leaver's final slip only; the Payment of Wages Act ceiling applies to every wage payment. The accountant is to confirm the ceiling for this plant (seen 2026-10-14) | statutory | open |
| O215 | hr, accounting | A leaver's shortfall becomes a receivable from the former employee (O28), but nothing settles it: a receipt against it is unbuilt (seen 2026-10-14) | rule | to build |
| O216 | core | A keeper that reset its own password on a login it made, and then lost its right or left, still knows that password: the lapse ends what the keeper set for others, not what it set as the login. docs/handoff/reports/fix_sec2.md, "Review fixes" (seen 2026-10-14) | security | open |
| O217 | core | A password reset through a link sent to an email HR set is recorded as set by nobody, so HR, which can read that mailbox, is not counted as knowing it. fix_sec2.md, "Review fixes" (seen 2026-10-14) | security | open |
| O224 | hr | The contract-labour registers follow the Contract Labour Act, which the Occupational Safety, Health and Working Conditions Code replaced from 21 November 2025; the registers the Code asks for are to be confirmed with the adviser (seen 2026-10-14) | statutory | open |
| O225 | manufacturing | The station's scale bridge has its endpoint, but the program that reads the scale and posts to it is not in the repository: at go-live a weight is typed (seen 2026-10-14) | rule | open |
| O226 | core, manufacturing, assets, inventory, accounting | Nine lock shapes the sentinel is told not to see (UNSEEN in apps/core/lock_order.py), each taking rows of one model in an order of its own: document numbering of a second type after the first (core/models.py next_value), WorkOrder.void's entries (manufacturing/orders.py near 3249), depreciate_all's assets (assets/views.py near 150), costing.publish's items (manufacturing/costing.py near 343), the woven bag's BOMs and plans (manufacturing/woven.py near 241, 363), dispatch commit's operations (manufacturing/dispatch.py near 295), a roll mount's void (manufacturing/process_rolls.py near 425), roll_up's standards (manufacturing/costing.py near 180), the station clock's void (manufacturing/station_clock.py near 176). Two at once can deadlock on PostgreSQL (seen 2026-10-14, fix_race) | race | minor, one at a time, each with a race() test |
| O227 | purchasing | `PurchaseOrderLine.save()` takes the blanket agreement after the order and the line when more is called off (purchasing/models.py near 2629), and does not name it in `locked_before_it()`: a blanket release onto an existing order takes blanket, then order (seen 2026-10-14, fix_race) | race | open, unproven |
| O228 | core | The lock-order sentinel keeps a row held after a savepoint that rolled back (a false positive), and does not see raw SQL `FOR SHARE` (`accounting.money.hold_money_account`; accounting.account is unranked, so nothing is missed today) (seen 2026-10-14, review_trade3 S6) | minor | open |
| O229 | core | apps/core/models.py's lock-order comment still says "deliveries and receipts, then invoices and bills"; LOCK_ORDER ranks receipt, delivery, bill, invoice, then payment, stock and period (mistake 1) (seen 2026-10-14) | minor | open |
| O230 | accounting | A payment can be voided while a bank statement line is matched to it: the line then reads as matched to a voided payment and the reconciliation is wrong. Decide the rule (refuse the void until the line is unmatched, or unmatch it in the void and say so) and how a bounced cheque's two statement lines are matched (seen 2026-10-14, review_race F10; serial, not a race) | rule | open |
| O231 | inventory, quality | The "Held because" column is N+1: `LotSerializer.held_because` asks the gate once per row, about 10 queries a lot (providers, plan, latest verdict, on hand), 248 for a page of 25 batches; `why_held` over three levels of lineage takes 26. It needs a page-wide form of the gate, each provider asked once for the page (seen 2026-10-14, fix_qrecall) | minor | open |
| O232 | manufacturing | One coating check off its weight holds every bag bundle of its run counted while the check stands, because nothing ties a disc check to the roll it was taken on: 2,100 m coated with one disc pair off and 20 bundles wait for the Quality Manager. Safe, but coarse (conversion.py record_bags, coating `made_from`) (seen 2026-10-14, fix_qrecall) | rule | open |
| O233 | purchasing, accounting | TDS rates are a table of today's figures, not dated rows kept on the deduction as they stood (TCS, built in g21, is dated): a rate change re-rates nothing posted, but a deduction drafted before the change and posted after it takes the new rate with no record of which applied (L14) (seen 2026-10-14, fix_acct) | statutory | open |
| O234 | purchasing, accounting | TCS a supplier collects from the plant (section 206C, e.g. on a vehicle bought over ten lakh) is not recorded: the bill has no TCS line, the plant's TCS receivable (credit in its own return) is never booked, and the bill's total disagrees with the supplier's (seen 2026-10-14, fix_acct) | statutory | minor |
| O235 | sales | There is no sales debit note (a supplementary invoice for an amount short-billed, CGST section 34(3)), so neither its posting nor its print exists; a price rise after invoicing is billed as a new invoice (seen 2026-10-14, fix_acct) | statutory | open |
| O238 | sales, gst | An advance received for job work (GST is due on it, being a service) prints as an "Invoice", not as a receipt voucher with CGST Rule 50's content; its refund has no Rule 51 refund voucher (seen 2026-10-14, fix_acct) | statutory | open |
| O239 | accounting, frontend | The Letter of Undertaking (ARN, year, valid from and to), which every export invoice under LUT now needs in force, is entered in the admin only: there is no office screen for it; nor for the list of what `setup_indian_books` seeded for the accountant to review (seen 2026-10-14, fix_acct) | minor | open |
| O243 | sales, accounting | TCS on an advance received for scrap is not collected: section 206C(1) (394 from April 2026) collects at the debit or the receipt, whichever is earlier, so an advance against a scrap order is due TCS when received. Since g21 a down payment on a scrap order is refused instead, so no advance escapes it, but the plant cannot take one (seen 2026-10-15, review_acct U1) | statutory | open |
| O244 | gst, sales, core | Tax paths reviewed but not driven by a probe: a payment landing between an IRN cancellation's request and the portal's answer; a TDS and a TCS challan for one month on one bank; an attachment added while a backup runs; an IRN cancelled in a closed period (seen 2026-10-15, review_acct U4, U7 to U9) | rule | open, probe each |
| O245 | sales, accounting | Two TCS outputs still read their section as it stands rather than as it was: the quarter's return rows take the collection code from `section.form_code` (sales/tcs.py `tcs_return`), and a challan shows `section.code`; neither is kept on the row or the challan. Editing a section's form code re-codes every past quarter (seen 2026-10-15, fix_mig) | statutory | minor |
| O246 | core | `audit_invariants` finds `timezone.now().date()` but not `.date()` of an aware timestamp handed in (a portal's acknowledgement, an API's datetime), which is the UTC day too: gst/einvoice.py had one from 28 September until g21's Kolkata lane met it. The check should report `.date()` on any value that may be an aware datetime, or the code should take `to_date()` everywhere a moment becomes a day (seen 2026-10-15) | rule | open |
| O247 | purchasing | A lower-deduction certificate (section 197) is one undated rate on the party's tax profile (`PartyTaxProfile.tds_rate_percent`): its validity is not kept, so a lapsed certificate goes on deducting at its rate (seen 2026-10-16, fix_purch) | statutory | minor |
| O248 | purchasing | An approval given before purchasing 0075 has no kept figures: each change to such an order is held to the order as it stood just before that change, so a second change can be refused though the approval covered it (errs on refusing) (seen 2026-10-16, fix_purch) | rule | minor |
| O249 | purchasing | `PurchaseOrderLine.save`: a line created in code with `blanket_line` on an existing order is not checked against the agreement's remaining quantity or status; the API cannot reach it (seen 2026-10-16, fix_purch) | rule | minor |
| O250 | accounting, sales, purchasing | The settlement discount is taken once per document: a second part payment inside the discount days earns none, though each settles its share of the invoice (seen 2026-10-16, fix_money) | rule | minor |
| O251 | accounting, frontend | The books' opening day and opening-balances account (BooksOpening, g22) have an admin page only; a fresh install must set it before an asset is typed in or an opening balance imported, and both refusals say so, but no office screen leads there (seen 2026-10-18, fix_stores) | minor | open |
| O252 | inventory | `StockCount.post` checks the count is still current before it holds the shelves it posts to: a movement landing between the check and the lock is counted over (seen 2026-10-18, fix_core's widened shelf_read_before_lock, which now names it) | race | open |
| O253 | purchasing | `GoodsReceipt._restore_components` books a component back at a four-place unit rate times quantity, a per-unit rate multiplied back up (CLAUDE.md "Numbers"): the shelf and the ledger part by the rounding (seen 2026-10-18, fix_core's widened per_unit_withdrawal_rates, which now names it) | books | open |
| O254 | manufacturing | `WorkOrderOperation._check_only_the_assignment_moved` reads the order's status RELEASED without holding the order (seen 2026-10-18, fix_core's widened unlocked_open_runs, which now names it) | race | minor |
| O255 | core | The two-factor secret (L12, g22) is stored in clear: anyone holding a copy of the database, a backup included, holds every login's second factor. Encrypt it at rest with a key kept outside the database (seen 2026-10-19, review_sec) | security | open |
| O256 | sales | Withdrawing an unearned settlement discount is dated by the payment's void day with no correction-date rule (seen 2026-10-19, review_core U2) | rule | minor |
| O240 | hr | A month's professional tax is taken once, on the month's wages, less what other runs of the month took (g21). If the earlier run of the month is voided after a later run took the month's figure and is never run again, the later run keeps what it took on wages that are now lower: 110 on 12,000 where the slab for the wages left may be less. Nothing gives the difference back (seen 2026-10-15, rereview) | rule | open; the accountant says whether a refund in the next month is the practice |
| O241 | quality, frontend | The inspection screen still offers Concession, and may offer a releasing re-post, to a Quality Inspector, though since g21 the API refuses both to anyone without `quality.decide_inspection`: the button leads to a refusal (seen 2026-10-15, fix_rereview) | minor | open |
| O242 | quality | `Inspection.post` locks the draft before its lots, where `void` and `escalate` now lock the lot first: a void of a draft racing its own post could deadlock on PostgreSQL (the void would be refused anyway); no race written (seen 2026-10-15, fix_rereview) | race | minor |

Upgrade notes, true of data made before the 8 October fixes:
- Landed cost allocated before the stores fix releases to cost of sales, and old inventory differences stay where they were.
- A pay run calculated with handed-in hours before hr migration 0020 is refused at post until it is calculated again.
- A void made before manufacturing migration 0079 keeps its gap between shelf and ledger, and nothing records it.
- A material issue line posted before manufacturing migration 0080 has no `posted_value`; its return and void work from its rate times its quantity, as it was booked then.
- A landed-cost allocation made before purchasing migration 0059 releases through its one `stock_movement`; a clearance out of inspection made before 0059 recorded no movement, so under FIFO freight landed on its goods goes to cost of sales.

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

20. **Decided by the owner on 9 October 2026:**
   - A pay run may be posted ahead of its pay date. It is voided on its
     own pay date, or on any day after it that has come
     (`correction_date`).
   - Freight on goods issued to production goes to production cost (O48).
   - A leaver's overpayment beyond what the final run may take back is a
     receivable from the former employee (O28).
   - A partial return across route steps takes goods from the earliest
     step first.
   - Disposing of an asset reverses a closed month's depreciation charge
     on the disposal date: intended.
   - A dated correction stamps its stock movement when it is made and
     dates only its journal entry. The replays price movements in the
     order they were written, so a stock value asked for a day between
     the two differs from the ledger by the correction.
   - A held lot (on hold, rejected or quarantined) may be transferred,
     for example to a rejects store. Release checks guard what consumes
     or ships a lot, not where it is kept (was O114).
   - Place of supply for goods: the ship-to when the goods are delivered
     to the buyer at one of its own addresses; the buyer's principal
     place of business when delivered to a third party on the buyer's
     direction (O60). The tax adviser to confirm.
21. **Decided by the owner on 10 October 2026:**
   - Giving a login a role its keeper does not hold takes two people:
     the keeper proposes, and a holder of that role (or a superuser)
     confirms. Taking access away takes one. Nobody changes their own
     login's roles (O142).
   - Whoever made or changed a document cannot approve it, including
     someone who keyed in a requisition for a person without a login
     (O85).
22. **Decided by the owner on 13 and 14 October 2026:**
   - Assemblies a subcontractor sends back, made from a batch quality
     held after it was sent, are received and held (O135, O148): the
     receipt is recorded, the assemblies take the batch's hold, and
     quality decides (rework, scrap, return, or accept on concession).
     The processing charge is owed either way.
   - A failed reading under an advisory plan is not rejected by the
     system. The batch waits as "awaiting decision", with a stronger
     warning. The Quality Manager owns the decision; the Production
     Supervisor and the Stores Manager are told. Left undecided past the
     next working day, it goes to the administrator. The first decision
     stands; whoever recorded the reading cannot make it. The choices:
     use as is (a concession they sign), rework, or reject. Each is told
     by a to-do on their list, and by mail where mail is set up.

23. **Decided by the owner on 14 October 2026:**
   - A failure under a mandatory plan at the machines (doff, film roll,
     bag bundle) waits for the Quality Manager too, as decision 22 has an
     advisory one wait: no supervisor concedes it on the spot (O204;
     done in g21, lamination, print and coating included; the PIN and
     reason are kept as the floor's note).
   - A recall: a batch rejected after lots were made from it holds what is
     still in stock of those lots, and the Quality Manager is told. Stock
     lists keep showing them, with a column saying why each is held (O207;
     done in g21: the recall follows re-batches, subcontract assemblies
     and production runs, four levels deep, and names what it did not
     follow).
   - Unit prices keep two places; a price converted from another currency
     is rounded to the paisa per unit (O208). Not to change.
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
34. **The CRM is the rep's own notebook, kept here.** Nothing reads
    mail or WhatsApp; a call is logged by hand. A lead converts to a
    customer only by the rep's hand, and a lead is one company, so two
    reps writing the same enquiry down make two leads. An
    opportunity's chance is its stage's figure (10, 30, 60) unless the
    rep types one, and the pipeline's weighted value is only as honest
    as those. A rep sees their own and the leads nobody owns; the
    manager sees all.
35. **A record's history is the API's.** Every page shows who made the
    record, which fields were changed, each action taken (posted,
    voided, sent to whom) and when, as the office application did them.
    A change made in the Django admin or by a script leaves only
    `updated_by` and `updated_at` on the record and no line in its
    history; a mail shown as sent went to the mail server, which is not
    the same as being read.
36. **A cost centre is stamped when a line posts, and only where
    something named it.** A bill line's centre, a department's on its
    wages, a hand journal's as typed. Stock postings, the work order's
    conversion, depreciation and tax carry none and read as
    unallocated on the costs-by-centre report rather than being spread
    by a rule nobody agreed. The report foots to the profit and loss
    account's expenses, so the unallocated figure is the honest size of
    what the plant has not yet said who spent.
37. **A budget line without a centre is the account's whole, and a
    recurring journal runs only when something runs it.** The budget
    report counts centred spend inside an uncentred line, so a budget
    with both an account line and a centre line of the same account
    reads the centre twice; it lists what posted under no line as
    unbudgeted rather than spreading it. Recurring entries are taken by
    `generate_recurring` (cron, with the morning checks) or by hand from
    the screen; until it runs, the inbox counts them as due, and a
    schedule that cannot run (unbalanced, dated into a closed month) is
    named in the command's output and the screen's, and skipped, not
    forced. A bank file whose rows match lines already on the statement
    is passed over row by row, so a file re-exported after the bank
    reworded a narration adds that row again: check the difference the
    import reports against the closing balance.
38. **An import is one person's and one file's.** Everything made while a
    file is kept is stamped as made by whoever kept it, through a save
    signal that holds for the length of the run, so a second file kept
    in the same server process at the same instant would carry the same
    name (gunicorn's workers take one request at a time, so it does not
    happen there). An asset brought in carries the old system's
    depreciation as an opening figure on the asset, with no journal of
    its own: the opening balances hold that side, and if they do not,
    the register and the ledger disagree by exactly that figure. Cut
    over at a month end, or the go-live month is charged in full here.
39. **The PF and ESI files read the posted slip, and what they cannot
    name stops them.** A component is a statutory line only when it is
    marked as one; an unmarked PF component leaves everyone out of the
    ECR with no complaint, so check the first month's file against the
    old system's. Where the plant keeps one employer PF component, the
    pension share is 8.33% of pension wages (capped at 15,000) and the
    EPF share is the rest; a plant that splits them keeps a named EPS
    component instead. The ECR layout is EPFO's eleven-field text file
    and the ESI file the portal's six-column template; a change on either
    portal is a change here.
40. **The health checks prove the base currency and say so for the
    rest.** A control account is matched against its open documents at
    the ledger's own figures, which is exact for documents booked at a
    rate of one. An open invoice or payment in another currency clears
    its account at its own rate, allocation by allocation, each rounded,
    so the account cannot be re-derived from the documents to the paisa;
    such an account reads "not proved" rather than "agrees" or "does
    not", and is checked by hand at the month end. The stock and trial
    balance probes replay the whole ledger: on a year of data they take
    seconds, so the inbox keeps their answer for ten minutes and the
    Health screen's "Check again" runs them live. A failure the
    middleware records goes into the database that may itself be what
    failed; then the log line carries "not recorded" and the mail (with
    `DJANGO_ADMINS`) still goes, the reference shown to the person
    saying so. That mail is Django's own error report: the traceback
    with the request's headers and cookies, passwords and tokens
    blanked, the session cookie not. It goes over the company's mail
    server to the people who keep the system, and to nobody else.
41. **The 2B match reads numbers the way a clerk types them.** A
    supplier's INV/2026-27/0012 and the clerk's INV-2026-27-12 are one
    number here (letters and digit runs, separators and leading zeros
    dropped); GSTN compares exactly, so a bill matched here can still be
    the portal's mismatch, and the figures beside it are what decide.
    A number that matches nothing falls back to the same supplier's
    document of the same day for the same value. Composition and SEZ
    suppliers' bills are outside the 2B's supplier tables and are said
    to be, not matched; ISD, import and e-commerce tables of the file
    are counted and not read. Nothing here changes GSTR-3B: 4(A)(5)
    stays what the books booked, and the waiting figure is what the
    officer holds back by hand until the supplier files.
42. **A custom field is a column on the record and nothing more.** Its
    values live in the record's own `extra` and no posting, ledger,
    stock figure, report, filter or printed paper reads them, which is
    what keeps a keeper's field from moving a number; it also means a
    field that should drive something (a customer's credit hold, an
    item's tax) is a developer's field, not a custom one. "Required"
    holds for what the office types; the system's own saves (an import,
    a posting, a renumbering) never fail on a custom field, so a record
    the system made can be missing one until somebody opens it. A field
    switched off keeps its values unseen; a key never changes, so a
    misspelt one is switched off and made again. Lists export a record's
    own columns, not yet its custom ones.
43. **A target is read against the rep an invoice carries, and a
    team against its reps as they stand.** An invoice takes the
    customer's rep when it is made and keeps it, so moving a customer to
    another rep moves no past sale; but a rep moved to another team
    takes every past invoice to the new team's number, since a team's
    actual is the sum of its members' today. Set the team before the
    period starts, or read the month's report before the move. A target
    reads each invoice in rupees at the rate it posted at; the Sales
    report still reads each invoice's own figures, so a dollar
    customer's row there is in dollars and its total mixes the two.
44. **A request for quotation holds the need while it is out, and the
    scorecard counts every return.** A requisition line on a draft or
    issued request is not asked about again and is not on the To quote
    list until the request is awarded or cancelled: a draft nobody sends
    hides the need. And a return counts against the vendor whatever its
    reason, a wrong order of ours included.
45. **A pick list is planned one delivery at a time against the whole
    shelf.** The day's list merges what two deliveries take off one
    shelf and says when the sum is more than is there, but the lots and
    bins it names for the second delivery were chosen as if the first had
    not been picked. Where that matters (one batch nearly used up), post
    the first delivery before reading the route for the second.
46. **A change order applied is not undone.** It closes the old
    version's window the day before and makes the new one the default
    from its first day, and moves draft runs due from that day across;
    runs already released keep what they froze. A wrong change is put
    right by another change order, not by editing the versions back.
    And the run board reads what a run has done, so a run with time
    booked by mistake reads as running until the booking is corrected.
47. **A quality alert may close with no action; a complaint may not.**
    A roll scrapped on the spot needs a root cause written down and
    nothing else, so the alert does not insist on a corrective action
    the way a complaint does. The cost is that an alert closed on a root
    cause alone leaves nothing to verify later; the health board counts
    alerts open a fortnight, not alerts closed too easily.
48. **An expense claim is paid by a journal, not through payables.**
    The payment debits each line's expense account and credits the cash
    or bank account chosen, on the claim; nothing sits in a creditors
    control account, so a claim does not age and is not part of a
    payment run. Paid from the wrong account, it is reversed and paid
    again. And hiring an applicant makes a party whose code is the
    employee number given: pick it as the office numbers its people.
49. **A lead's score is a rule of thumb, read from its facts.** Where it
    came from, whether it can be reached, whether it said what it wants,
    what has been done about it, how long it has sat: points added and
    capped at 100, never stored, with the reasons beside the number. It
    orders a morning's calls; it does not know the customer. The points
    are in `Lead.score_reasons` to change as the plant learns what warms.
50. **Notes and follow-ups sit beside the CRM's calls and visits, not in
    place of them.** A lead or an opportunity keeps its own activities
    (sales.Activity: an owner who is a rep, read by lead scoring); every
    other record has notes and follow-ups (apps/core/chatter.py). A call
    logged on the customer's page is a follow-up there, not an activity
    the pipeline counts. *Do:* if the two are to be one, move the CRM's
    onto follow-ups and have scoring read those, in one change.
51. **Who may read a record is asked of its own screens' lists.** History,
    files, notes and follow-ups are shown only where the record is among
    those a screen serving its kind shows the login (apps/core/endpoints.py).
    A kind no screen serves is limited by its view permission alone, and
    so is the history of a record since deleted: a rep may read what
    happened to another rep's deleted order. *Do:* nothing unless a
    deleted record's history turns out to say more than its kind's.

The reach plan (owner, 14 October, from the Odoo 19 gap analysis): the
ERP is deep in what the plant decides and thin in what carries it to
people. After g21's go-live items, in this order:

- **L6 Documents that leave the building.** Every outgoing document
  prints (RFQ, purchase order, delivery challan, debit and credit notes,
  remittance advice), mails from its own screen, and keeps the mail in
  its history. Inbound mail lands on the record it answers (a reply to an
  order on the order; mail to a sales address as a lead).
- **L7 WhatsApp.** Invoices, reminders and order confirmations sent on
  WhatsApp through a business provider (the owner chooses which), the
  message kept on the record.
- **L8 Analysis.** Every list grouped, totalled and charted (pivot and
  graph), exported to Excel; an owner's dashboard of production, sales,
  cash, stock and quality, each figure opening the records behind it.
- **L9 Scanning in stores.** Item and bin barcodes (and GTIN where a
  customer asks), and receive, pick, issue, count and transfer by scan on
  a handheld, offline-tolerant.
- **L10 Rules the owner configures.** Automation rules (when something
  happens and a condition holds: notify, set, create a follow-up) and
  approvals in stages, set up on screens, not in code; BOM change orders
  in stages.
- **L11 The shop floor.** Step instructions and photo checks on the
  station screens, maintenance requests raised by operators, and an
  equipment register (vendor, serial, warranty, documents).
- **L12 Security.** Two-factor sign-in, session time-outs, and API keys
  for anything that integrates.
- **L13 CRM basics.** Quotation templates, section and note lines, lead
  merge, mail-to-lead, activity planning.
- **L14 Law as data everywhere.** One dated-parameter mechanism for every
  rate, ceiling, slab and threshold the law sets (g21 starts it in
  payroll and TCS); the next budget or notification is a data entry.
- **L15 Plastic-waste EPR,** if the tax adviser confirms the plant must
  register as a plastic-packaging producer under the Plastic Waste
  Management Rules: what it places on the market, what it recovers and
  recycles, and the returns on the CPCB portal; with recycled-content
  tracking for the recycling line.

Until the pilot, plant logic already ahead of Odoo (scheduling, quality
statistics, maintenance, costing) is not deepened further: defects only.

The plant's own practices (second pass, 14 October): what a woven-sack
plant in India runs on that neither Odoo nor this ERP has yet. (?) waits
on the owner's answer.

- **L16 Polymer bought against price circulars:** purchase contracts
  linked to the polymer makers' circulars, rate-difference notes from
  suppliers, the mirror of the sales price indices.
- **L17 Weighbridge:** receipts and dispatches by truck weight (gross,
  tare, net), shortage against the invoice weight, claims on the supplier
  or transporter.
- **L18 Gate register and gate passes:** vehicles in and out; returnable
  passes for material out for repair, non-returnable passes.
- **L19 Post-dated cheques:** a register of cheques held, deposit
  reminders, bounce handling.
- **L20 Export papers** (the plant exports; owner, 14 October). There
  already: export supply types on the e-invoice (EXPWP/EXPWOP), the LUT
  as a fiscal position, exports zero-rated in the returns. Missing: the
  shipping bill number and date and the port code on the invoice and in
  GSTR-1 table 6A, a packing list, the certificate of origin, export
  proceeds tracked to realisation (the eBRC, within the FEMA period), and
  an IGST refund claim where exports go with payment.
- **L21 Import purchases** (the plant imports; owner, 14 October). There
  already: duty and the customs broker's bills landed on the goods
  (LandedCostApplication). Missing: the bill of entry as a document (its
  number, date, port, assessable value at the customs rate, basic customs
  duty, social welfare surcharge, IGST paid), the import IGST credit
  matched with GSTR-2B's IMPG table (read today but not matched), and
  the payable to the overseas supplier at its rate.
- **L22 Billing and shipping policy per item:** ordered or delivered
  quantity billed, ship complete or in parts.
- **L23 Landed cost by weight,** for freight on polymer.
- ~~L24 Branch transfers~~ — not needed: the plant has no godowns in
  other states (owner, 14 October).
- **L25 Machine counters** read automatically (loom picks, extruder kg),
  and the scale-bridge client (O225).
- **L26 The recycling line:** granule lots with melt-flow and
  contamination tests, recycled content carried into tape recipes, the
  virgin-only customer rules extended to recycled input.
- **L27 A BOM from what a run used;** runs split and merged.
- **L28 Incoming certificates of analysis** for polymer, compared with the
  specification before release.
- **L29 BIS test plans and ISI marking records** (the plant holds a BIS
  licence; owner, 14 October): the tests and frequencies its standard
  asks, the marking register, and the records a BIS inspection reads.
- **L30 The annual accounts:** Schedule III balance sheet and P&L, cash
  flow statement, day book, GSTR-9 and 9C, past-year credit notes
  flagged after 30 November.
- **L31 E-invoice and e-way bill sent through a GST service provider,**
  not uploaded by hand.
- **L32 Year-end closing checklist and the tax-return lock.**
- **L33 Minimum wages** by schedule, zone and skill, with the
  twice-yearly dearness revision, checked against every wage.
- **L34 Full-and-final settlement and leave encashment** (due within two
  working days of leaving under the Labour Codes).
- **L35 The statutory registers** (muster roll, wages, overtime) from
  payroll; India's sandwich leave, comp-off for overtime, biometric
  device import.
- **L36 Attachments in offsite storage,** with the backup (O221).
- **L37 Export incentives and duty-free imports** (not used yet; the
  owner plans to start soon, 14 October: build before the first claim): RoDTEP or duty drawback receivable on each shipping bill, and
  Advance Authorisation or EPCG licences with their export obligation
  tracked against the imports made under them.
