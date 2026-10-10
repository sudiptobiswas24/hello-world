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
| O50 | sales, purchasing | A foreign receipt or payment from a closed month can never be allocated: its exchange-difference entry is dated on the payment's day, inside the closed month. Should be realised in the open month | books | open, blocks real use; FxResidueProbe |
| O51 | accounting | `post_drawdown` rounds each drawdown at the held rate and the last does not take what is left: 100 EUR held at 112.35, drawn 50 + 50, leaves -0.01 on deposits | books | open; FxResidueProbe |
| O52 | accounting | Bank reconciliation for a past statement reads today's facts: `unpresented()` drops a payment matched on any later statement or voided on any later date. September's cheque of 400 presented on 2 October shows September unpresented 0, difference -400 | books | open; StatementProbe |
| O53 | purchasing | `refund_due` on a debit note counts a vendor refund whose payment was voided (bounced); the sales mirror skips voided payments. A bounced 300 refund reads 0 due while payables hold 300 | books | open; DebitNoteRefundProbe |
| O54 | sales, purchasing | Re-pointing an allocation to another document checks it against the old document's ceiling: 1,000 moved from X (1,000) to Y (500) leaves Y at -500 due; the API answers 200 | books | open; RepointProbe |
| O55 | sales | A claim (`credit_claim`) or a write-off can be dated before its invoice, through the API too: a write-off on 20 August of a 10 September invoice leaves receivables at -1,000 on 31 August. `corrections_dated_without_the_rule` cannot see it: it looks only for reversals | rule | open; DatedAnywhereProbe |
| O56 | purchasing | A debit note's lines drop the bill line's cost centre (and the prepayment version too): loom shed 1,000 less 300 reads LOOM 1,000 and unallocated -300 | books | minor; CostCentreProbe |
| O57 | purchasing, gst | A debit note copies its bill's reference and posts at once, so the supplier's credit-note number can never be recorded, and GSTR-2B pairing works only when the dates happen to match | statutory | open; Gstr2bNoteProbe |
| O58 | purchasing | The TDS return lists only deductions that stand today: 700 deducted 10 June and reversed 5 July drops out of the April-June return while the ledger held 700 that quarter | statutory | open; TdsReturnProbe |
| O59 | gst | GSTR-1 table 13 leaves out down-payment invoices, which take INV numbers: INV-1 to INV-3 reported as 2 issued, 0 cancelled | statutory | open; DocumentsIssuedProbe |
| O60 | accounting, gst | Place of supply uses the party's state only, not where the goods go: an unregistered Maharashtra buyer taking delivery in Karnataka is charged CGST and SGST at 27, not IGST at 29; the e-way bill already says 29. Decided 9 October: goods delivered to the buyer at any of its own addresses take the ship-to (IGST Act s.10(1)(a)); goods delivered to a third party on the buyer's direction take the buyer's principal place of business (s.10(1)(b)). The tax adviser to confirm this reading | statutory | open, in fix wave 1; PlaceOfSupplyProbe |
| O61 | manufacturing, gst | A job-work challan can be issued into a closed month: it posts no entry, so nothing asks the period, and the closed half-year's ITC-04 changes | statutory | open; ClosedMonthChallanProbe |
| O62 | purchasing, accounting | A TDS challan paid from the bank is a direct entry, not a payment, so its bank statement line cannot be matched; posting it books it twice (TDS payable +700, difference -700). HR expense claims paid from a bank have the same shape (not probed) | books | open, blocks real use; ChallanOnTheStatementProbe |
| O63 | accounting | Race: a statement closes while one of its lines is unmatched (`close()` locks the statement; the line's save() reads `closed` unlocked): closed with a line unexplained. Shown on PostgreSQL | race | open; AccountingRaceProbe |
| O64 | purchasing | Race: a TDS deduction reversed while a challan pays it (`TdsChallan.pay` reads what is owed unlocked, then updates): reversed and paid, TDS payable +700 paid for nothing. Shown on PostgreSQL | race | open; AccountingRaceProbe |
| O65 | sales | A shipped order line's item can be changed (PATCH answered 200), and a return then invents stock: 5 widgets shipped, item changed to gadget, the return puts 5 gadgets on the shelf | books | open, blocks real use; EditAfterShippingProbe |
| O66 | sales | A posted delivery's line can be deleted over the API (204 to Warehouse Staff): the order line reads 0 shipped while cost of sales and the shelf stay. `DeliveryLine` has no delete guard, and the deletable-posted check asks only documents, not their lines | books | open, blocks real use; ApiProbe |
| O67 | sales | On a bill-on-delivery order one shipment can be invoiced twice: two drafts made before the first posts both post (post() checks invoice_limit(), not what shipped): 10 invoiced against 5 shipped | books | open, blocks real use; DeliveredPolicyProbe |
| O68 | sales | A shipped order's customer and currency can be changed: the rest is then billed to the new customer (on credit hold) in another currency | books | open; EditAfterShippingProbe |
| O69 | sales | A claim plus a quantity credit gives back more than was invoiced: invoice 1,000, claim 300, then a full credit or return credits 1,300 and leaves receivables at -300 | books | open; ClaimThenCreditProbe |
| O70 | sales | A credit note refunded at another rate books its exchange difference the wrong way (every allocation passed as a receivable): receivables end at 40.00, not 0. Purchasing's mirror always passes the other side; a refunded debit note may share it (not probed) | books | open; ForeignDepositCycleProbe |
| O71 | sales | An invoice line may bill any order line, another customer's included (the delivery line has this check, the invoice line not): Acme's line reads invoiced while another customer is billed | rule | open; ApiProbe, InvoiceLineOrderLineProbe |
| O72 | sales | The discount policy is not asked again on a confirmed order: a line raised to 60% against a 15% policy ships and invoices; a 60% line added stands | rule | open; ConfirmedOrderPolicyProbe |
| O73 | sales | Reopening a line closed short skips the credit limit: exposure 1,800 against a limit of 1,000 | rule | open; ReopenProbe |
| O74 | sales | Commission on collection counts a voided (bounced) receipt | books | open; CommissionProbe |
| O75 | sales | Commission on collection leaves out money taken as a deposit: 300 deposit plus 700 paid counts 700 | books | open; CommissionProbe |
| O76 | sales | Commission is worked in the invoice's currency, not the base: 1,000 EUR at 1.1 counts 1,000, not 1,100 | books | open; CommissionProbe |
| O77 | sales | The credit limit adds currencies at face value: 600 USD plus 380 EUR reads 980 against 1,000; the true figure is 1,018.00 | rule | open; CreditLimitProbe |
| O78 | sales | An order in another currency takes the item's base price at face value: a EUR line priced 10 from 10 USD | books | open; PricingProbe |
| O79 | sales | The price ignores the line's unit, and volume breaks compare quantities in it: 2 boxes of 12 at 10 each priced 10 a box (20.00, not 240.00). Purchasing's price resolution has no unit either (not probed) | books | open; PricingProbe |
| O80 | sales | A write-off partly undone by a credit note cannot be recovered: recovery takes the whole write-off or nothing, and no receipt can be applied | books | open; WriteOffRecoveryProbe |
| O81 | sales | The dunning mail asks for the whole balance and the last due date, not what the notice records as overdue (mistake 1) | minor | open; DunningProbe |
| O82 | sales, quality, hr, purchasing, manufacturing | A line's save() checks only the document it moves to, not the one it leaves (JournalLine was fixed for this; the others copied the old check, mistake 5). (a) A Sales Rep moved the only line off a posted invoice by PATCH: invoice reads 0 lines, its entry still 100. (b) A reading moved off a posted inspection. (c) Self Service moved a line off its own approved expense claim. Same code, not probed: delivery line, bill line, goods receipt line, job-work challan line | security | open; audit_perm probes A |
| O83 | core and 15 actions | A custom POST action missing from `action_permission_map` needs only `add_<model>`. 15 such actions act on one record: calibration post and void, leave cancel, operation-report and re-batch void, master-schedule commit and withdraw, third-party-release void, quotation decline, send, revise and mark_sent, invoice send, delivery backorder, stock-count add. Shown: an Inspector voided the failed calibration that had made their own inspection suspect; a manager cancelled a report's approved leave with no decide right | security | open; AddAloneActsOnARecordTests |
| O84 | sales, accounting, manufacturing, web | A rep's customers leak through side doors (RISKS item 6's exception fails): the claims report, the bad-debt report (which also passes over the missing view right), the history of a deleted order, party tax profiles (PAN) and the notes, files and history on them, an activity written on another rep's customer, the lot recall trace (customer names to a rep with no customers), and the inbox's overdue count | security | open; RepSideDoorTests, RepSeesWhoHoldsABatchTests |
| O85 | core, sales, purchasing, quality | No approval checks who raised the document (approvals.py promises a second pair of eyes): an AR Manager approved their own order at 40% against a 15% policy; an AP Manager approved their own requisition; an Inspector posted a concession naming someone else as `decided_by` (typed, not the login). Purchase orders share the mixin (not probed) | rule | open; SelfApproved*Tests, ConcessionNamesSomeoneElseTests |
| O86 | core, hr, admin | One person can let themselves in: an HR Admin made a login with the Controller role and set a real Controller's password; a Controller holding only the import right added HR Admin to their own login through the employees import; a staff HR Admin set is_superuser on themselves in Django's user admin | security | open, blocks real use; HrAdminLetsThemselvesInTests, ImportGivesRolesTests, AdminUserFormTests |
| O87 | admin | The admin's "delete selected" skips each model's delete(): a staff HR Admin deleted approved leave the API refuses. Same gap by scan on fixed assets, requisitions, blanket orders, RFQs and quotes, bank statements, maintenance jobs, downtime and specifications (wider than O10 and O22) | rule | open; AdminBulkDeleteTests |
| O88 | hr | A person's own records leak or take writes from others: a colleague's sick leave read through /employees/{id}/leave/ (asks only view_employee); Self Service wrote a 5,000 line onto a colleague's draft claim; a Line Manager appraised a peer who does not report to them | security | open; LeaveReadThroughTheEmployeeTests, ExpenseLinesTests, AppraisalOfSomeoneNotTheirsTests |
| O89 | purchasing | A bill posted before any of its goods arrive leaves goods-received-not-invoiced uncleared for good: billed 10 @ 5, then received, GRNI -50 and purchases 50 (expected 0 and 0). A drop-ship billed before receipt has the same posting (code only) | books | open; audit_pur probes |
| O90 | purchasing | A hand-typed bill line is never checked against the order line it names (the receipt line has that check): one vendor billed another's order line; a EUR bill on a USD order posted 4,450 as exchange loss; a bill for WDG-2 cleared WDG-1's accrual; a bill naming no order billed a cancelled one | rule | open; audit_pur probes |
| O91 | purchasing | The match's price check ignores the agreed discount: 10 @ 100 less 10% agreed, billed at 100 with tolerance 0, posts with 100 of price variance | rule | open; audit_pur probes |
| O92 | purchasing | A hand bill naming no order line and a generated bill both pay one receipt: payable -100 for 50 received | books | open; audit_pur probes |
| O93 | purchasing | Billing one receipt in parts leaves a paisa on GRNI: each bill's accrual is rounded on its own | books | minor; audit_pur probes |
| O94 | purchasing | A return is debited to the oldest bill, not the one that paid for the goods returned: 15.00 debited and 4.00 variance kept, expected 16.20 and 2.80 | books | minor; audit_pur probes |
| O95 | purchasing | The same vendor invoice number in another case is accepted: INV-77 and inv-77 both post | rule | minor; audit_pur probes |
| O96 | purchasing | A received or billed purchase order can still be edited: its line's item and discount, and the order's vendor and currency. The mirror of O65 and O68 in sales; one shared rule should close both | books | open; audit_pur probes |
| O97 | purchasing | A blanket release drops the agreed discount: 10 @ 100 less 5% released at 1,000.00, expected 950.00 | books | open; audit_pur probes |
| O98 | purchasing | A vendor price has no unit: 100 a kg agreed, 2 t ordered with no price typed came out at 100.00 a tonne. The mirror of O79 in sales | books | open; audit_pur probes |
| O99 | purchasing | A hand-typed price passes the approval rule whenever an agreed price exists, used or not: agreed 5.00, typed 50.00, confirmed with no approval reasons. `price_against_agreement` exists and nothing calls it | rule | open; audit_pur probes |
| O100 | purchasing | `preferred_vendor` compares prices across currencies at face value | rule | minor; audit_pur probes |
| O101 | purchasing, assets | Capitalising a received stock line takes its cost out of GRNI and leaves it in stock too: GRNI -12,000, inventory 12,000, plant 12,000 | books | open; audit_pur probes |
| O102 | purchasing | Under 194C, one bill over 30,000 draws the year's earlier untaxed bills into the deduction: 20,000 then 35,000 taxes 55,000 (1,100), where s.194C(5) taxes 35,000 (700) | statutory | open; audit_pur probes |
| O103 | purchasing | The MSME 45-day limit drives only the MSME report, not the due date or the payment run: a micro vendor on net 60 is legally due on day 45 and the run misses it | statutory | open; audit_pur probes |
| O104 | purchasing | The MSME category is read from the vendor as it stands today: a micro vendor's unpaid bill drops off the 43B(h) list once the vendor is reclassified | statutory | minor; audit_pur probes |
| O105 | purchasing, sales | A prepayment or deposit drawdown credits whatever account is set today, not the one it was held in: after the setting moved, 1400 keeps +15 and 1401 goes to -15 | books | minor; audit_pur probes |
| O106 | purchasing | Race: a line added to a bill or a goods receipt as it posts. The line's save() reads "posted" without the document's lock: a bill shows 100.00 while payables hold 50.00; an order line reads 20 received while 10 moved. Shown on PostgreSQL | race | open; audit_pur races |
| O107 | purchasing | Race: two hand-typed bills naming no order both bill the last of a line (post() locks only the bill's own order): payable -100 for 50 received. Shown on PostgreSQL | race | open; audit_pur races |
| O108 | purchasing | Race: an RFQ award and a direct order on one requisition both go through (each holds a different lock): 20 ordered against a request for 10. Shown on PostgreSQL | race | open; audit_pur races |
| O109 | purchasing, sales | Built and never called (shape 2): settlement discounts on both sides (`Bill.take_settlement_discount`, `Invoice.apply_settlement_discount`), `Bill.match_report`, `PurchaseRequisition.suggested_vendors`, `BlanketOrderLine.is_fully_released`. Each should be reached from its screen or action, or removed | rule | open |
| O110 | manufacturing, quality | Production can draw from a quarantine warehouse: deliveries and transfers refuse it, a material issue does not. 40 kg issued, the QC shelf 100 to 60 | rule | open; QuarantineProbe |
| O111 | quality | A rejected lot is released once its inspection plan is retired or made advisory: `check_released` returns early on today's plan while the lot still reads held. MRP has the same gap | rule | open; RetiredPlanProbe |
| O112 | manufacturing, quality | A held doff can be loaded on a backflushed loom (no issue is made, so nothing checks); the roll woven from it can then never be booked | rule | open; TapeLoadProbe |
| O113 | quality | A re-inspection dated earlier than the standing verdict is outranked without a word: passed 1 June, a rejection dated 30 May posted afterwards, the lot stays released | rule | open; ReinspectionProbe |
| O115 | purchasing | A component kept by batch cannot be sent to a subcontractor at all: `move_stock` is called with no lot (and `_consume_components` likewise). Everything a mandatory inspection plan covers is kept by batch | crash | open, blocks real use; BatchKeptComponentsToASubcontractorProbe |
| O116 | manufacturing | A bag count cannot be voided once its inspection was voided (`void_bags` does not skip a voided inspection as `void_gauged` does): 500 bags stay on the shelf | crash | open; BagCountVoidProbe |
| O117 | manufacturing | A complaint can be rejected after it was settled (`settle()` refuses a rejected one, `reject()` not the reverse): the rejected complaint still costs 500.00 | rule | open; ComplaintSettledThenRejectedProbe |
| O118 | manufacturing | Race: an action added while its complaint closes (the action reads the complaint unlocked): closed with an action not done. Shown on PostgreSQL | race | open; ComplaintRaceProbe |
| O119 | sales (CRM) | A rep who converts an unowned lead cannot see the opportunity it makes: its owner stays empty, so the rep reads 404 and it is missing from the pipeline | rule | open; RepScopeProbe |
| O120 | sales (CRM) | A rep can log activities on another rep's lead or opportunity (201, naming it), raising that lead's score. One root with O84's activity side door | security | open; RepScopeProbe |
| O121 | sales (CRM) | One sales order wins two opportunities: won value 350,000.00 where 250,000.00 was won | books | open; WonTwiceProbe |
| O122 | sales (CRM) | The lead score counts notes as "calls or visits" | minor | open; ScoreProbe |
| O123 | purchasing, quality | `GoodsReceipt.accept` never asks the lot's release status, so a lot quality rejected can be cleared onto a pickable shelf (downstream gates still hold it). Seen in code, not probed | rule | verify |
| O124 | inventory, manufacturing | The O82 and O106 shape outside sales and purchasing: a line's save() asks only the document it joins, without its lock. inventory/adjustments.py:603 and :853, manufacturing/inward.py:117, manufacturing/orders.py:2692 and :3240, manufacturing/scrap.py:90. Named in `LINES_REPORTED` (audit_invariants) until converted to `answer_to_its_document()` | security | open |
| O125 | accounting | `JournalLine._on_a_posted_entry` asks both entries but without the entry's lock, so a line can be added as the entry posts | race | open, unproven |
| O126 | core | 28 read-only API routes are bound to the shared viewset mixin's `update` and `destroy`, whose `super()` has neither (e.g. PayslipViewSet, ToolUsageViewSet): likely a 500 where a 405 belongs. Read, not run | crash | minor, verify |
| O127 | manufacturing | `FROZEN_AT_RELEASE` on work orders is a separate copy of the frozen-once-moved rule (`refuse_changing_what_moved`); one rule should serve both (mistake 5). The no-order-line branch of `BillLine.accrual()` can no longer be reached | minor | open |
| O128 | purchasing | TDS reverse, TDS challan void and goods-receipt reject are explicitly mapped to add_ permissions (purchasing/tds_api.py:44 and :79, purchasing/views.py:367), so anyone who may record one may undo one | security | open |
| O129 | inventory | `Warehouse.held_for` names a customer to every rep (listed as reported in the rep-scope check) | security | minor |
| O130 | core | `scoped()` filters `path__in`, so rows whose party path is NULL drop out of a rep's view | rule | minor |
| O131 | core, hr | A deleted leave request's history is readable by anyone holding view_leaverequest | security | minor |
| O132 | docs, frontend | docs/IMPORT.md still documents the employees import's roles column, which the import no longer reads; the leave screen does not offer Cancel to a manager the server allows | minor | open |
| O133 | inventory, manufacturing | `allocate()` does not subtract tape loaded on a backflushed run's creels and not yet drawn: a pick naming no lot can choose a doff whose 100 kg is all on a creel | rule | open |
| O134 | inventory | tracking.py's shortfall message ("Only ...") is not normalised, so PostgreSQL shows 300.0000 | minor | open |
| O135 | purchasing, quality | A lot quality holds after it was sent to a subcontractor makes the subcontract receipt refuse ("held by quality"), with no way out short of moving the lot. The owner decides whether a receipt of goods already sent is allowed | rule | decide |
| O136 | sales (CRM) | `Opportunity.sales_order` has no unique constraint in the database; only the lock in `win()` keeps one order to one opportunity | minor | open |
| O137 | sales, purchasing | In the fix-wave-1 trading commits (not yet in the product): Invoice.post now locks the orders its lines bill, a credit note's included, while a customer return locks the order before the invoice. A return and a credit note on one invoice at once deadlock ("deadlock detected", shown on PostgreSQL); a return to vendor and a debit note on one bill likewise | race | being fixed in wave 1 |
| O138 | sales, purchasing | In the fix-wave-1 trading commits: rule A freezes only once something has moved, so a confirmed order's customer can change, or a line move to another confirmed order, with none of confirm()'s checks asked: exposure 1,100 against a limit of 500; an order left worth 0 holding an 800 deposit. Purchase order lines have the same shape | rule | being fixed in wave 1 |
| O139 | sales, purchasing | In the fix-wave-1 trading commits: a drop-ship on its way does not count as moved, so the sales line's item can change before the vendor delivers; `PurchaseOrderLine.sales_order_line` is not frozen, so a received drop-ship can be re-pointed and its return reverses nothing | books | being fixed in wave 1 |
| O140 | sales, purchasing | In the fix-wave-1 trading commits: rule B does not check `InvoiceLine.credits_line` (nor, likely, `BillLine.debits_line`): another customer's invoice credited Acme's invoice line, which then had nothing left to credit | books | being fixed in wave 1 |
| O141 | sales | In the fix-wave-1 trading commits: O72's fix refuses every change to an approved order, within policy too (one more undiscounted line on an order approved for a 20% line), and asks nothing of cuts and deletes, which can take an order under the margin floor | rule | being fixed in wave 1 |
| O142 | core, hr | In the fix-wave-1 security commits: O86's fix overreaches. An HR Admin cannot deactivate a departing rep or give a new login the Bookkeeper role; every role but HR's own needs a superuser. Taking access away is refused too. Decided 10 October: a two-person rule (HR proposes a role it does not hold; a holder of that role, or a superuser, confirms; nobody acts on their own login) | security | being fixed in wave 1 |
| O143 | sales, core | In the fix-wave-1 security commits: self-approval through a quote revision. `create_revision` copies lines with no stamps or history and `authors()` does not follow `revision_of`, so the writer of a 40% quote revises it and approves the revision (200) | rule | being fixed in wave 1 |
| O144 | purchasing, sales | In the fix-wave-1 security commits: requisition cancel still takes `change_purchaserequisition` though `decide_purchaserequisition` exists (Self Service cancelled a colleague's approved requisition); a converted lead stays unowned, so another rep lists it naming the new customer; the employee screen links any login to any employee | security | being fixed in wave 1 |
| O145 | quality | In the fix-wave-1 quality commits: O113 half closed. A re-inspection dated the SAME day as the standing verdict is ranked by id: a rejection drafted first and posted last stands while the lot reads released | rule | queued, wave 1 follow-up; SameDayReinspectionProbe |
| O146 | quality | In the fix-wave-1 quality commits: a split gets round the date rule. PP-A rejected 1 June, split into PP-A2; an inspection of PP-A2 dated 25 May passes and releases it. The rule reads only the lot's own inspections, not its parent's | minor | queued; SplitBackdatedProbe |
| O147 | purchasing, quality | Subcontract components can leave a quarantine warehouse: `issue_components` never gives the release gate a warehouse (predates the fix; the fix's claim that every way out asks the gate does not hold). Also `pack` passes no warehouse, so a quarantine bay's bundles can be baled (the delivery still refuses them) | rule | queued; SubcontractQuarantineProbe |
| O148 | purchasing, quality | In the fix-wave-1 quality commits: a batch released when sent and rejected afterwards blocks receipt of the assemblies made from it ("held by quality"). The same question as O135 | rule | queued; decide with O135; SubcontractHeldAfterSendingProbe |
| O149 | purchasing | In the fix-wave-1 quality commits: a subcontract return gives each batch its proportional share rounded to 2 places: returned one at a time, F-1 0.99 and F-2 2.01 come back (F-2 gains 0.01 it never had), and a 3-of-10 return puts back fractional frames | books | queued; SubcontractReturnByBatchProbe |
| O150 | manufacturing | The O118 lock was not applied to a complaint's batches: a reject and a batch added at once both finish (a rejected complaint names a batch; shown on PostgreSQL); removing the only batch as it closes has the same unlocked read | race | queued; ComplaintLotRaceProbe |

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
