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
6. **TDS rates are the plant's to enter, and none are entered.** The
   sections, their rates, thresholds and accounts are kept on the TDS
   sections screen because they change with every budget. *Do:* enter
   this year's 194Q, 194C, 194J and 194I with the plant's accountant,
   and each vendor's PAN and section, before the first bill is paid.

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
| O1 | sales | `DeliveryAllocation.delete` reads `self.delivery`, which the model does not have: deleting any allocation fails | crash | fixed in g17 |
| O2 | purchasing | Landed cost on the same bill (`Bill._record_landed_cost`, `_split_across_warehouses`) still lands on the line's destination and always debits inventory: the mirror of stores finding 6 | books | open |
| O3 | inventory | A write-down given an explicit unit cost books that cost, not what the shelf loses (`cost_of_removing`) | books | open |
| O4 | inventory | The importer reads 8-place opening costs into a 4-place field | books | open |
| O5 | payroll | Editing a working pattern or holiday region restates posted slips' day counts: a frozen fact read live | books | open |
| O6 | payroll | The EPFO ECR reads the PF wage ceiling live, not as it stood for the month | books | open |
| O7 | manufacturing | Release merges components by item and unit: the same item in two units freezes two rows that each count all its issues | books | open |
| O8 | manufacturing | The WIP tile (apps/web/bank.py) reads the account in settings, not each run's recorded WIP account | books | open |
| O10 | inventory, quality | A posted stock adjustment, stock count or quality inspection can be deleted (named in `DELETABLE_REPORTED`) | rule | open |
| O11 | inventory | Cancelling a put-away transfer leaves its receipt route move standing | rule | open |
| O12 | assets | `place_in_service` accepts an in-service date still to come | rule | open |
| O13 | assets | `depreciated_before` is not enforced on a draft made by hand | rule | open |
| O14 | assets | The admin lets an asset's status be edited directly | rule | open |
| O15 | all | 17 correction steps outside hr and assets are not yet on `correction_date()` (named in `DATED_ELSEWHERE`) | rule | open, one module at a time |
| O16 | accounting | Unmarking `Account.holds_money` takes no lock and can race a payment posted through the account | race | open, needs a `race()` proof |
| O17 | core, gst | Nine viewsets do not use `AuditableViewSetMixin`, so their edits take no lock: countries, party tags, saved filters, notes, follow-ups, attachments, and the e-invoice, e-way bill and GSTR-2B deletes | race | open |
| O18 | hr | The punch-file import reads an attendance day (and whether it was entered by hand) before taking the employee's lock | race | open |
| O20 | payroll | Whether posting a run under the employee locks also closes the run-post against leave and attendance race is unproven | race | open, needs a `race()` proof |
| O21 | sales | Deleting a line of an approved order locks the order before the line; closing short locks the line first | race | open, check against 8f31e5a. Purchasing has the same order (seen in code by the purchasing audit) |
| O22 | all | Admin edits read their row without a lock | race | minor |
| O23 | inventory | The adjustment post endpoint answers with a stale `total_value` (lines prefetched before posting) | minor | open |
| O24 | accounting | `Account.currency` is read by nothing | minor | open |
| O25 | frontend | RecordScreen's afterCreate comment says it decides where to go after a delete; the code does not (mistake 1) | minor | open |
| O26 | assets | Capitalising a bill line has no API action | minor | open |
| O27 | inventory | Migrations 0030 and 0037 have no migration test | rule | open |
| O28 | payroll | A leaver's final pay too small for what is taken back: the shortfall becomes a receivable from the former employee; PF/ESI wages drop by what is taken back. A roll voided after the final slip was paid: the same receivable, and the void is not refused (quality corrections must stay possible). Confirmed by the owner on 9 October. What the final run takes back must stay within the legal limit on deductions from one wage payment (believed to be half the wages; the plant's accountant to confirm); the rest is the receivable. Not built | books | to build |
| O29 | accounting, assets | A period closes while its depreciation is uncharged. Decided: refuse it, through a period-close check that accounting keeps and assets registers with. Not built | rule | to build |
| O31 | payroll | A leaver's piece-work recovery happens only if the leaving date is set before the final run is calculated. Set after that run posts, no later run includes the person, and the overpayment is never recovered, with nothing said | books | open |
| O32 | hr | `LeaveRequest.cancel(on_date)` takes any date: called in code with a day before the leave, it cancels a holiday already taken and restores the balance. The API passes no date | rule | open |
| O34 | assets | The fixed-asset register is not footed against the ledger in the daily health checks | rule | open |
| O35 | core | `locked_before_it()` is read from the instance as it stood before the lock: an allocation re-pointed to another payment at the same moment locks the old payment | race | minor |
| O36 | sales | A line's check against its call-offs runs under the line's lock only on a confirmed order whose line drops in value; a cut on a draft order, or on a zero-price line, made through model code reads the call-offs unlocked (the API path is locked) | race | minor |
| O37 | hr, purchasing, sales, manufacturing | These save() methods lock a related row without declaring it in `locked_before_it()`, so an API edit holds the row first and the related row second: hr/timesheets.py:342, hr/payroll.py:1472, hr/contract_labour.py:75, purchasing/models.py:615, :2478, :2523, sales/models.py:1393, manufacturing/maintenance.py:346 (line numbers at 7f726a5). No opposite-order path traced yet. One pair is known: sales `reopen()` locks the line then the customer, while a line saved from code that grows the order locks the customer then the line (sales/models.py:1406 and :1474) One pair is known: sales `reopen()` locks the line then the customer, while a line saved from code that grows the order locks the customer then the line (sales/models.py:1406 and :1474) | race | open, unproven |
| O38 | purchasing | `_restore_components` puts components back at the consumed unit cost times the quantity, with no remainder carried (under a paisa at eight places) | books | minor |
| O39 | inventory | reservations.py:75 reads `held_at` before the lock, so it can lock the wrong second shelf | race | open |
| O40 | core | Audit gaps: `per_unit_withdrawal_rates` skips inventory/models.py and `average_cost_at`; `shelf_read_before_lock` sees a read and a lock only inside one function | rule | open |
| O41 | manufacturing | `_booked` converts each entry at today's unit factor; a conversion that depends on the date makes `quantity_produced` drift from what went on the shelf | books | minor |
| O42 | purchasing | A landed-cost release takes off each shelf it landed on what that shelf still holds of it, and the rest out of cost of sales. Goods moved to another shelf after the landing (a put-away from the bay) carried their freight with them, but the release does not follow them: landed on the bay, put away, released, the bay gives up 0, cost of sales is credited 80, and the stock room keeps the 80. Shelf and ledger agree; the release does nothing. `value_still_held` must follow a transfer's value to the shelf it went to | books | open, next |
| O45 | manufacturing, core | Three admin mixins refuse the same thing (`PostedNotDeletedMixin`, `PostedImmutableAdminMixin`, `PostedDocumentAdminMixin`): fold into one (mistake 5) | rule | open |
| O46 | core | `unlocked_open_runs` sees only `.is_open()`; a run's status compared to RELEASED by a function that does not hold the run goes unreported (station clock and job work were found by reading) | rule | open |
| O47 | manufacturing | `TapeLoad.kg` keeps 3 places but `load_tape` accepts 4: 48.9796 kg is stored as 48.980, 0.0004 kg more than the shelf held | books | minor |
| O48 | purchasing, manufacturing | Landed cost on goods no longer on a shelf goes to cost of sales. The owner decided on 9 October that the share on goods issued to production goes to production cost instead (the run they went into), and only the share on goods sold or written off goes to cost of sales. Not built. O42 touches the same code | books | to build |
| O49 | accounting | `settlement_difference` (settlement.py:30) books round(amount × rate difference), not each side's booked base subtracted, which CLAUDE.md already requires: 100.09 EUR at 1.12345 paid at 1.09876 books 2.47, not 2.48, leaving 0.01 on receivables (and on payables, the mirror) | books | open; FxResidueProbe |
| O51 | accounting | `post_drawdown` rounds each drawdown at the held rate and the last does not take what is left: 100 EUR held at 112.35, drawn 50 + 50, leaves -0.01 on deposits | books | open; FxResidueProbe |
| O52 | accounting | Bank reconciliation for a past statement reads today's facts: `unpresented()` drops a payment matched on any later statement or voided on any later date. September's cheque of 400 presented on 2 October shows September unpresented 0, difference -400 | books | open; StatementProbe |
| O53 | purchasing | `refund_due` on a debit note counts a vendor refund whose payment was voided (bounced); the sales mirror skips voided payments. A bounced 300 refund reads 0 due while payables hold 300 | books | open; DebitNoteRefundProbe |
| O54 | sales, purchasing | Re-pointing an allocation to another document checks it against the old document's ceiling: 1,000 moved from X (1,000) to Y (500) leaves Y at -500 due; the API answers 200 | books | open; RepointProbe |
| O56 | purchasing | A debit note's lines drop the bill line's cost centre (and the prepayment version too): loom shed 1,000 less 300 reads LOOM 1,000 and unallocated -300 | books | minor; CostCentreProbe |
| O63 | accounting | Race: a statement closes while one of its lines is unmatched (`close()` locks the statement; the line's save() reads `closed` unlocked): closed with a line unexplained. Shown on PostgreSQL | race | open; AccountingRaceProbe |
| O64 | purchasing | Race: a TDS deduction reversed while a challan pays it (`TdsChallan.pay` reads what is owed unlocked, then updates): reversed and paid, TDS payable +700 paid for nothing. Shown on PostgreSQL | race | open; AccountingRaceProbe |
| O70 | sales | A credit note refunded at another rate books its exchange difference the wrong way (every allocation passed as a receivable): receivables end at 40.00, not 0. Purchasing's mirror always passes the other side; a refunded debit note may share it (not probed) | books | open; ForeignDepositCycleProbe |
| O74 | sales | Commission on collection counts a voided (bounced) receipt | books | open; CommissionProbe |
| O75 | sales | Commission on collection leaves out money taken as a deposit: 300 deposit plus 700 paid counts 700 | books | open; CommissionProbe |
| O76 | sales | Commission is worked in the invoice's currency, not the base: 1,000 EUR at 1.1 counts 1,000, not 1,100 | books | open; CommissionProbe |
| O77 | sales | The credit limit adds currencies at face value: 600 USD plus 380 EUR reads 980 against 1,000; the true figure is 1,018.00 | rule | open; CreditLimitProbe |
| O78 | sales | An order in another currency takes the item's base price at face value: a EUR line priced 10 from 10 USD | books | open; PricingProbe |
| O79 | sales | The price ignores the line's unit, and volume breaks compare quantities in it: 2 boxes of 12 at 10 each priced 10 a box (20.00, not 240.00). Purchasing's price resolution has no unit either (not probed) | books | open; PricingProbe |
| O80 | sales | A write-off partly undone by a credit note cannot be recovered: recovery takes the whole write-off or nothing, and no receipt can be applied | books | open; WriteOffRecoveryProbe |
| O81 | sales | The dunning mail asks for the whole balance and the last due date, not what the notice records as overdue (mistake 1) | minor | open; DunningProbe |
| O89 | purchasing | A bill posted before any of its goods arrive leaves goods-received-not-invoiced uncleared for good: billed 10 @ 5, then received, GRNI -50 and purchases 50 (expected 0 and 0). A drop-ship billed before receipt has the same posting (code only) | books | open; audit_pur probes |
| O91 | purchasing | The match's price check ignores the agreed discount: 10 @ 100 less 10% agreed, billed at 100 with tolerance 0, posts with 100 of price variance | rule | open; audit_pur probes |
| O93 | purchasing | Billing one receipt in parts leaves a paisa on GRNI: each bill's accrual is rounded on its own | books | minor; audit_pur probes |
| O94 | purchasing | A return is debited to the oldest bill, not the one that paid for the goods returned: 15.00 debited and 4.00 variance kept, expected 16.20 and 2.80 | books | minor; audit_pur probes |
| O95 | purchasing | The same vendor invoice number in another case is accepted: INV-77 and inv-77 both post | rule | minor; audit_pur probes |
| O97 | purchasing | A blanket release drops the agreed discount: 10 @ 100 less 5% released at 1,000.00, expected 950.00 | books | open; audit_pur probes |
| O98 | purchasing | A vendor price has no unit: 100 a kg agreed, 2 t ordered with no price typed came out at 100.00 a tonne. The mirror of O79 in sales | books | open; audit_pur probes |
| O99 | purchasing | A hand-typed price passes the approval rule whenever an agreed price exists, used or not: agreed 5.00, typed 50.00, confirmed with no approval reasons. `price_against_agreement` exists and nothing calls it | rule | open; audit_pur probes |
| O100 | purchasing | `preferred_vendor` compares prices across currencies at face value | rule | minor; audit_pur probes |
| O101 | purchasing, assets | Capitalising a received stock line takes its cost out of GRNI and leaves it in stock too: GRNI -12,000, inventory 12,000, plant 12,000 | books | open; audit_pur probes |
| O105 | purchasing, sales | A prepayment or deposit drawdown credits whatever account is set today, not the one it was held in: after the setting moved, 1400 keeps +15 and 1401 goes to -15 | books | minor; audit_pur probes |
| O108 | purchasing | Race: an RFQ award and a direct order on one requisition both go through (each holds a different lock): 20 ordered against a request for 10. Shown on PostgreSQL | race | open; audit_pur races |
| O109 | purchasing, sales | Built and never called (shape 2): settlement discounts on both sides (`Bill.take_settlement_discount`, `Invoice.apply_settlement_discount`), `Bill.match_report`, `PurchaseRequisition.suggested_vendors`, `BlanketOrderLine.is_fully_released`. Each should be reached from its screen or action, or removed | rule | open |
| O123 | purchasing, quality | `GoodsReceipt.accept` never asks the lot's release status, so a lot quality rejected can be cleared onto a pickable shelf (downstream gates still hold it). Seen in code, not probed | rule | verify |
| O124 | inventory, manufacturing | The O82 and O106 shape outside sales and purchasing: a line's save() asks only the document it joins, without its lock. inventory/adjustments.py:603 and :853, manufacturing/inward.py:117, manufacturing/orders.py:2692 and :3240, manufacturing/scrap.py:90. Named in `LINES_REPORTED` (audit_invariants) until converted to `answer_to_its_document()` | security | open |
| O125 | accounting | `JournalLine._on_a_posted_entry` asks both entries but without the entry's lock, so a line can be added as the entry posts | race | open, unproven |
| O126 | core | 28 read-only API routes are bound to the shared viewset mixin's `update` and `destroy`, whose `super()` has neither (e.g. PayslipViewSet, ToolUsageViewSet): likely a 500 where a 405 belongs. Read, not run | crash | minor, verify |
| O127 | manufacturing | `FROZEN_AT_RELEASE` on work orders is a separate copy of the frozen-once-moved rule (`refuse_changing_what_moved`); one rule should serve both (mistake 5). The no-order-line branch of `BillLine.accrual()` can no longer be reached | minor | open |
| O129 | inventory | `Warehouse.held_for` names a customer to every rep (listed as reported in the rep-scope check) | security | minor |
| O130 | core | `scoped()` filters `path__in`, so rows whose party path is NULL drop out of a rep's view | rule | minor |
| O131 | core, hr | A deleted leave request's history is readable by anyone holding view_leaverequest | security | minor |
| O132 | docs, frontend | docs/IMPORT.md still documents the employees import's roles column, which the import no longer reads; the leave screen does not offer Cancel to a manager the server allows | minor | open |
| O133 | inventory, manufacturing | `allocate()` does not subtract tape loaded on a backflushed run's creels and not yet drawn: a pick naming no lot can choose a doff whose 100 kg is all on a creel | rule | open |
| O134 | inventory | tracking.py's shortfall message ("Only ...") is not normalised, so PostgreSQL shows 300.0000 | minor | open |
| O135 | purchasing, quality | A lot quality holds after it was sent to a subcontractor makes the subcontract receipt refuse ("held by quality"), with no way out short of moving the lot. The owner decides whether a receipt of goods already sent is allowed | rule | decide |
| O136 | sales (CRM) | `Opportunity.sales_order` has no unique constraint in the database; only the lock in `win()` keeps one order to one opportunity | minor | open |
| O145 | quality | In the fix-wave-1 quality commits: O113 half closed. A re-inspection dated the SAME day as the standing verdict is ranked by id: a rejection drafted first and posted last stands while the lot reads released | rule | queued, wave 1 follow-up; SameDayReinspectionProbe |
| O146 | quality | In the fix-wave-1 quality commits: a split gets round the date rule. PP-A rejected 1 June, split into PP-A2; an inspection of PP-A2 dated 25 May passes and releases it. The rule reads only the lot's own inspections, not its parent's | minor | queued; SplitBackdatedProbe |
| O147 | purchasing, quality | Subcontract components can leave a quarantine warehouse: `issue_components` never gives the release gate a warehouse (predates the fix; the fix's claim that every way out asks the gate does not hold). Also `pack` passes no warehouse, so a quarantine bay's bundles can be baled (the delivery still refuses them) | rule | queued; SubcontractQuarantineProbe |
| O148 | purchasing, quality | In the fix-wave-1 quality commits: a batch released when sent and rejected afterwards blocks receipt of the assemblies made from it ("held by quality"). The same question as O135 | rule | queued; decide with O135; SubcontractHeldAfterSendingProbe |
| O149 | purchasing | In the fix-wave-1 quality commits: a subcontract return gives each batch its proportional share rounded to 2 places: returned one at a time, F-1 0.99 and F-2 2.01 come back (F-2 gains 0.01 it never had), and a 3-of-10 return puts back fractional frames | books | queued; SubcontractReturnByBatchProbe |
| O150 | manufacturing | The O118 lock was not applied to a complaint's batches: a reject and a batch added at once both finish (a rejected complaint names a batch; shown on PostgreSQL); removing the only batch as it closes has the same unlocked read | race | queued; ComplaintLotRaceProbe |
| O151 | hr | An expense claim paid, unpaid and paid again overwrites `journal_entry` and `voided_entry`: after a second unpay, the first payment and its reversal belong to no claim and `claims_paid` cannot find them (hr/expenses.py:144-185) | books | open |
| O152 | gst | The e-invoice `ShipDtls` carries the buyer's GSTIN and name even when the goods go to a third party (gst/einvoice.py:289) | statutory | open |
| O153 | gst | The e-way bill's `transactionType` is 2 (bill-to-ship-to) for delivery to the buyer's own other address, where NIC's meaning is a third party (gst/ewaybill.py:366). The tax adviser to decide | statutory | decide |
| O154 | purchasing, gst | Debit notes raised by a receipt return (`create_return(debit_bills=True)`) post at once, so the supplier's credit-note number can never be recorded on them; GSTR-2B pairs them only on date and value. O57's fix covers hand-made debit notes only | statutory | open |
| O156 | frontend | The TDS return screen has no "reverses" column; the bank statement screen can match a line only to a payment, so a line for a direct entry (a TDS challan, an expense claim) is matched only through the API or auto-match | minor | open |
| O166 | gst | The e-way bill codes a sales delivery as a delivery challan ("CHL", gst/ewaybill.py near line 500), though a sales delivery travels on its tax invoice. The tax adviser to decide | statutory | decide |
| O167 | purchasing | Purchasing has the O72/O141 shape: a confirmed purchase order's approval is withdrawn on any re-price, and only the budget is asked again, never the order value, line value or agreed-price policy (`PurchaseOrderLine.save`) | rule | open |
| O168 | core | In the fix-wave-1 trading commits: a document's orders are read just before its lock is taken, so an order line added in that instant has its order locked after the document, against the written lock order. Posting re-locks it. The new deposit and return lock lists read their rows before locking them, the same shape | race | minor, unproven |
| O176 | hr | Paying an expense claim may be dated ahead (refused only before the claim); an existing test relies on paying ahead, so `ExpenseClaim.pay` stays exempted from the correction-date check. Refusing a day still to come would match the rule that nothing is posted for a day that has not come | rule | minor |
| O182 | core, inventory | The lock-order sentinel (apps/core/lock_order.py) leaves 54 models unranked; `inventory.stockposition` is taken both before and after orders, receipts and bill lines, so those paths are unchecked for deadlock. Rank it and the six named in UNRANKED Shown on PostgreSQL: two `BillLine.allocate_landed_cost` calls onto the same two receipt lines in opposite order deadlock on stockposition (purchasing/models.py:4425, :4512; predates wave 1) (docs/handoff/reports/review_trade3.md #2) | race | open, wave 2 |
| O184 | core | The lock-order sentinel cannot see locks taken by update(), save() or delete(), joined tables under select_related, a values_list without pk, or any unranked model (which also skips the key-order check) (docs/handoff/reports/review_trade3.md #1) | race | open, wave 2 |
| O187 | gst | In the fix-wave-1 statutory commits: ITC-04 reads a job-worker value receipt by its own date and leaves voided ones out whenever the void happened, so voiding a closed month's receipt (now allowed, by a reversal in an open month) rewrites that closed month's return. The void belongs in the period it happens | statutory | open, wave 2 first |
| O188 | purchasing | MSME report: a part settled wholly by a debit note or TDS (no payment) has no paid-on day, so days late runs to the report date: unpaid 0.00, days late 168 (docs/handoff/reports/fix_stat.md, seen #1) | statutory | open |
| O189 | purchasing | `Bill.pay_by()` and the bill's shown due date are still the whole bill's when one delivery is objected to (the payment run is right) (docs/handoff/reports/fix_stat.md, seen #2) | statutory | minor |
| O190 | purchasing | A debit note left unkeyed by migration 0063 (a collision), edited as a draft, is re-keyed by clean() and refused by the constraint with a generic message (docs/handoff/reports/fix_stat.md, seen #3) | minor | open |
| O191 | core | An action reading `request.data.get` answers 500 when the JSON body is a list, not an object (supplier_note and objection seen; likely every such action) (docs/handoff/reports/fix_stat.md, seen #4) | crash | minor |
| O192 | core | In the fix-wave-1 security commits: a role proposal outlives its proposer: HR demoted and switched off, a holder still confirms it and the role is given (docs/handoff/reports/review_sec2.md F2) | security | queued, wave 1 |
| O193 | core | In the fix-wave-1 security commits: a proposal for a deactivated login stays in the holders' queue and confirms, so a leaver ends up holding the role (docs/handoff/reports/review_sec2.md F3) | security | queued, wave 1 |
| O194 | core, hr | In the fix-wave-1 security commits: `refused_link` reads a login's current groups, not its pending proposals, so HR can link a login to an employee and then have a Controller role confirmed for it (docs/handoff/reports/review_sec2.md F4) | security | queued, wave 1 |
| O195 | core | The two-person rule's "nobody acts on their own login" compares logins only: HR makes a login holding HR Admin (legitimately), and as it proposes Bookkeeper for HR's own login; a real Bookkeeper confirms, seeing "proposed by puppet". Show who made the proposer's login, or refuse a proposal for the maker of the proposer (docs/handoff/reports/fix_sec.md, seen #2) | security | open, wave 2 |
| O196 | core, hr | O194 in reverse: HR links an empty login to an employee, then proposes Controller for it, and a Controller confirms: a Controller login linked to an employee, which the link rule would refuse (docs/handoff/reports/fix_sec.md, seen #1) | security | open |
| O197 | core | `UserViewSet.perform_create` and `perform_update` run without a transaction: a create refused while giving roles answers 400 but leaves the login made, with no roles (docs/handoff/reports/fix_sec.md, seen #3) | rule | open |
| O198 | core | A keeper-set password lapses only when a role is given: a permission of its own or is_staff given in the admin does not lapse it. `KeptCredential.value` keeps a copy of the keeper-set password hash (exposed nowhere). Credentials set before the lapse rule carry no record of who set them | security | minor |
| O199 | accounting | The per-unit note cap (`priced_to_give_back`): for a fractional quantity, a share the paisa does not divide leaves under one paisa per line on the receivable, and the last unit's rounding can post up to half a paisa over the unrounded cap (docs/handoff/reports/fix_trade.md, seen #2) | books | minor |
| O200 | sales | Migration sales 0064: an approval whose note names no figures leaves a line cut after it seeded at what it holds; charge lines are never matched by label; total and margin are seeded empty unless the note names them (docs/handoff/reports/fix_trade.md, seen #3) | books | minor |
| O201 | accounting | A typed note line may credit any revenue account, not the one its original line used (docs/handoff/reports/fix_trade.md, seen #4) | books | open |

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
