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
| O152 | gst | The e-invoice `ShipDtls` carries the buyer's GSTIN and name even when the goods go to a third party (gst/einvoice.py:289) | statutory | open |
| O153 | gst | The e-way bill's `transactionType` is 2 (bill-to-ship-to) for delivery to the buyer's own other address, where NIC's meaning is a third party (gst/ewaybill.py:366). The tax adviser to decide | statutory | decide |
| O166 | gst | The e-way bill codes a sales delivery as a delivery challan ("CHL", gst/ewaybill.py near line 500), though a sales delivery travels on its tax invoice. The tax adviser to decide | statutory | decide |
| O214 | hr | The deduction cap (half the wages) applies on a leaver's final slip only; the Payment of Wages Act ceiling applies to every wage payment. The accountant is to confirm the ceiling for this plant (seen 2026-10-14) | statutory | open |
| O224 | hr | The contract-labour registers follow the Contract Labour Act, which the Occupational Safety, Health and Working Conditions Code replaced from 21 November 2025; the registers the Code asks for are to be confirmed with the adviser (seen 2026-10-14) | statutory | open |
| O247 | purchasing | A lower-deduction certificate (section 197) is one undated rate on the party's tax profile (`PartyTaxProfile.tds_rate_percent`): its validity is not kept, so a lapsed certificate goes on deducting at its rate (seen 2026-10-16, fix_purch) | statutory | minor |
| O248 | purchasing | An approval given before purchasing 0075 has no kept figures: each change to such an order is held to the order as it stood just before that change, so a second change can be refused though the approval covered it (errs on refusing) (seen 2026-10-16, fix_purch) | rule | minor |
| O249 | purchasing | `PurchaseOrderLine.save`: a line created in code with `blanket_line` on an existing order is not checked against the agreement's remaining quantity or status; the API cannot reach it (seen 2026-10-16, fix_purch) | rule | minor |
| O250 | accounting, sales, purchasing | The settlement discount is taken once per document: a second part payment inside the discount days earns none, though each settles its share of the invoice (seen 2026-10-16, fix_money) | rule | minor |
| O251 | accounting, frontend | The books' opening day and opening-balances account (BooksOpening, g22) have an admin page only; a fresh install must set it before an asset is typed in or an opening balance imported, and both refusals say so, but no office screen leads there (seen 2026-10-18, fix_stores) | minor | open |
| O255 | core | The two-factor secret (L12, g22) is stored in clear: anyone holding a copy of the database, a backup included, holds every login's second factor. Encrypt it at rest with a key kept outside the database (seen 2026-10-19, review_sec) | security | open |
| O256 | sales | Withdrawing an unearned settlement discount is dated by the payment's void day with no correction-date rule (seen 2026-10-19, review_core U2) | rule | minor |
| O240 | hr | A month's professional tax is taken once, on the month's wages, less what other runs of the month took (g21). If the earlier run of the month is voided after a later run took the month's figure and is never run again, the later run keeps what it took on wages that are now lower: 110 on 12,000 where the slab for the wages left may be less. Nothing gives the difference back (seen 2026-10-15, rereview) | rule | open; the accountant says whether a refund in the next month is the practice |
| O257 | core, manufacturing, assets, accounting | The rest of O226: eight lock shapes the sentinel is still told not to see (UNSEEN in apps/core/lock_order.py), each taking one model's rows in an order of its own: document numbering of a second type after the first (core/models.py next_value), WorkOrder.void's entries, depreciate_all's assets, costing.publish's items, the woven bag's BOMs and plans, a roll mount's void, roll_up's standards, the station clock's void. g22 fixed dispatch commit's operations; the pattern is to hold the rows in key order with `lock_rows()` first, then walk, then a race() test (seen 2026-10-19, fix_core) | race | minor, one at a time, each with a race() test |
| O258 | core | The rest of O228: `accounting.account` has no rank in LOCK_ORDER, so a raw `FOR SHARE` on it (`accounting.money.hold_money_account`) is ranked against nothing. Its place needs a survey of every pair it is taken in (`LOCK_ORDER_SURVEY` over the PostgreSQL lanes), not a guess (seen 2026-10-19, fix_core) | race | minor |
| O259 | core | The rest of O40: `per_unit_withdrawal_rates` and `shelf_read_before_lock` follow calls inside one module only. Following a call into another module needs the call resolved (which `post` or `reverse` it means); review_core's U1 named eight candidates, none proved (seen 2026-10-19, fix_core) | rule | open |
| O260 | accounting | `settlement.document_slice` reads an invoice's booked base from its own journal entry without its TCS entry, as the drawdown did before g22. Harmless only while TCS is refused in a foreign currency, since a rupee allocation has no exchange difference (seen 2026-10-16, fix_money) | books | latent |
| O261 | accounting, sales, purchasing | The rest of O209: a credit or debit note partly taken as a refund is still counted at its slice of the document's base, not at what it booked; a note wholly absorbed and each drawdown count at what they booked (seen 2026-10-16, fix_money) | books | minor |
| O262 | hr | A leaver's advance left unrecovered stays a receivable on the advance (apps/hr/advances.py): the shortfall settlement of O215 settles only the final slip's recoverable, and an advance's remainder has no receipt or write-off (seen 2026-10-19, fix_core) | books | open |
| O263 | imports | apps/imports/api.py describes the employees file as "login and roles", though the import refuses a roles cell: the other half of O132 (seen 2026-10-19, fix_core) | minor | open |
| O264 | purchasing | `PurchaseOrderLine.component_cost` (purchasing/models.py near 2930) is called by nothing and excused in audit_invariants; if it is ever used to post, it rounds quantity times a four-place rate per component. Remove it (seen 2026-10-19, fix_core) | minor | open |
| O265 | manufacturing | The tape-load check asks `on_creels_undrawn(lot)` with no warehouse (manufacturing/tape_loads.py near 110), taking creels anywhere off the station warehouse's on-hand. Unreachable while a lot is in one place (seen 2026-10-18, fix_stores) | minor | open |
| O266 | assets | `FIXED_AT_GO_LIVE` refuses an edit of a hand draft's `depreciated_before` or `opening_depreciation` after it is created, though the draft has posted nothing; a typo is corrected only by registering the asset again (seen 2026-10-18, fix_stores) | rule | minor |
| O267 | purchasing | `PurchaseOrder.confirm()` carries `@serialised("status")` twice: a nested lock and re-read, harmless (seen 2026-10-16, fix_purch) | minor | open |
| O268 | inventory | A count sheet cannot carry a cost: something never stocked that a count finds (sweepings, a found pallet) raises a gain line with no cost to take, and posting the sheet is refused. It is counted in on an adjustment, whose line now takes a cost (g22 month), but the count's refusal does not say so (seen 2026-10-19, g22 month) | rule | minor |
| O270 | hr, accounting | A former employee's write-off is held at what the books show owed on the receivable, but a payment from them posting at the same moment does not hold the employee, so it can still land just after the write-off read the books; and the books' figure is the person's whole balance on the account, advances included (O262), not the slip's (seen 2026-10-19, g22 gate) | race | minor |
| O281 | accounting | An entry dated before the books' opening day (BooksOpening) still posts. Decided: refused, except a document marked as an opening balance (open invoices and bills carry their old dates) (seen 2026-10-20, audit2_money M3; docs/handoff/reports/g23_audit2_money.md) | books | open |
| O282 | accounting | An account's currency is not frozen once posted: the Bookkeeper can turn a rupee bank account into a dollar one under its entries (seen 2026-10-20, audit2_money M2; docs/handoff/reports/g23_audit2_money.md) | books | open |
| O283 | accounting | Inert active flags: Account.is_active is read for money accounts only; CostCentre.is_active and Tax.is_active by no posting, so a retired tax still taxes new invoices; FiscalPosition.is_active and ChargeType.is_active likewise, not probed (seen 2026-10-20, audit2_money M14-M16; docs/handoff/reports/g23_audit2_money.md) | rule | open |
| O284 | accounting | A recurring entry's generate(on_date=) has no correction-date rule: an auto-posting schedule posts an entry 400 days ahead (seen 2026-10-20, audit2_money M4; docs/handoff/reports/g23_audit2_money.md) | rule | open |
| O285 | gst | GSTR-1's JSON writes every CDNR note with inv_typ R, so a note on an SEZ invoice (SEWOP) goes out wrong (seen 2026-10-20, audit2_money M6; docs/handoff/reports/g23_audit2_money.md) | statutory | open |
| O286 | gst | Table 13 counts the old system's opening invoices and their plain credits as documents issued (3 where 2 were issued) (seen 2026-10-20, audit2_money M1; docs/handoff/reports/g23_audit2_money.md) | statutory | open |
| O287 | gst | b2cl_limit is read live: a month compiled again after the limit changes moves invoices between B2CL and B2CS; keep it at the event (seen 2026-10-20, audit2_money M10; docs/handoff/reports/g23_audit2_money.md) | statutory | open |
| O288 | gst | The GSTR-2B match counts a bill matched to a line the portal marks ITC not available (itcavl N) among the matched (seen 2026-10-20, audit2_money M5; docs/handoff/reports/g23_audit2_money.md) | statutory | minor |
| O289 | gst | EwayBill.record and EInvoice.record take a time five days ahead (seen 2026-10-20, audit2_money M7-M8; docs/handoff/reports/g23_audit2_money.md) | rule | minor |
| O290 | accounting | Two bank-statement imports committed at once leave 4 lines where 2 were sent (PostgreSQL race()) (seen 2026-10-20, audit2_money M9; docs/handoff/reports/g23_audit2_money.md) | race | open |
| O291 | gst | /api/gst/gstr1/?period=99999-01 answers 500, not 400 (seen 2026-10-20, audit2_money M11; docs/handoff/reports/g23_audit2_money.md) | crash | minor |
| O294 | sales | Commission on the collected basis counts the payment with tax, the invoiced basis without: 1,000 plus 20% tax at 5% pays 60.00 where 50.00 is due (seen 2026-10-20, audit2_trade T3; docs/handoff/reports/g23_audit2_trade.md) | books | open |
| O295 | sales | run_dunning sends the highest level not yet sent: after the 60-day notice, later runs send the 30-day and then the 7-day (seen 2026-10-20, audit2_trade T2; docs/handoff/reports/g23_audit2_trade.md) | rule | open |
| O296 | sales | Two dunning runs at once end the second with a unique-constraint 500 (PostgreSQL race()) (seen 2026-10-20, audit2_trade T5; docs/handoff/reports/g23_audit2_trade.md) | race | minor |
| O297 | purchasing | Bill.create_debit_note with no date dates the note today even when the bill is dated later; sales credit notes ask correction_date (seen 2026-10-20, audit2_trade T6; docs/handoff/reports/g23_audit2_trade.md) | rule | minor |
| O298 | sales, purchasing | A credit or debit note made in code posts on another control account, before its invoice, a year ahead, or for more than the invoice; the API cannot reach it (credits/debits read-only) (seen 2026-10-20, audit2_trade T7; docs/handoff/reports/g23_audit2_trade.md) | rule | minor |
| O299 | sales | GET sales-orders/approval returns margin_percent to a Sales Rep, so cost can be worked out (not probed) (seen 2026-10-20, audit2_trade T8; docs/handoff/reports/g23_audit2_trade.md) | security | minor |
| O312 | hr | Timesheets have no route, screen, admin or role, so hourly pay cannot be fed from the office; matters only if anyone is paid by the hour (owner to say) (seen 2026-10-20, audit2_people P13; docs/handoff/reports/g23_audit2_people.md) | rule | minor |
| O315 | planning | A requisition turned into a draft purchase order falls out of supply (requisition_supply nets draft orders, purchase_supply counts confirmed ones), so MRP proposes the same 1,000 kg again (seen 2026-10-20, audit2_core C3; docs/handoff/reports/g23_audit2_core.md) | rule | open |
| O316 | planning | The Production Planner can change the quantity of a firmed planned order (1,000 to 10) and delete it (seen 2026-10-20, audit2_core C4; docs/handoff/reports/g23_audit2_core.md) | rule | open |
| O317 | core | A follow-up link of /\host passes chatter.py near 94, which refuses only a leading //, and Home renders it as a link: a rep can put an outside address on the Controller's Home (seen 2026-10-20, audit2_core C5; docs/handoff/reports/g23_audit2_core.md) | security | open |
| O318 | core, assets, inventory | Decimal(str(request...)) answers 500 to Infinity or NaN (asset dispose proceeds, a promise quantity) and at about seven more sites not probed; one shared reader that refuses what is not a finite number (seen 2026-10-20, audit2_core C6; docs/handoff/reports/g23_audit2_core.md) | crash | open |
| O319 | sales | The printed invoice drops text in angle brackets from party and address text ("Sharma <Raipur> & Sons" prints "Sharma & Sons"): reportlab reads it as markup; escape it (Rule 46 content) (seen 2026-10-20, audit2_core C7; docs/handoff/reports/g23_audit2_core.md) | statutory | open |
| O320 | core, planning | Four methods nothing calls (UnitOfMeasure.to_base_quantity, PlanningRun.fenced, PlanningRun.into_the_fence, PlannedOrder.was_fenced); audit_invariants reads module-level helpers only, so methods escape it (seen 2026-10-20, audit2_core C8; docs/handoff/reports/g23_audit2_core.md) | rule | minor |
| O329 | inventory | Migration inventory 0040 gives old rows the money replayed then, not their documents' booked figures, and its summary nets its drops so it can read 0 dropped; the health check nets by inventory account (seen 2026-10-20, audit2_stores review part 1, item 6; docs/handoff/reports/g23_audit2_stores.md) | books | minor |
| O333 | core, frontend | A leaving date still to come leaves the login reading active on the Logins screen after the date passes, though sign-in refuses it. Decided: the screen shows it ended, derived from the leaving date; no job writes it (seen 2026-10-20, fix_blockers Q2; docs/handoff/reports/g23_fix_blockers.md) | rule | minor |
| O335 | purchasing | A return by someone without post_bill drafts debit notes allocated by quantity_debited, which counts posted notes only: two receipts of 5 billed on A and B, both drafts take A and nothing is drafted against B (sales counts drafts since O292; share the rule) (seen 2026-10-20, fix_blockers D1; docs/handoff/reports/g23_fix_blockers.md) | books | open |
| O336 | sales, purchasing | A draft note from a return is not counted by quantity_creditable() / quantity_debitable(): while a store hand's draft waits, AR can credit the same line in full by hand, and posting the draft credits it twice (no ceiling at posting, O298) (seen 2026-10-20, fix_blockers D2; docs/handoff/reports/g23_fix_blockers.md) | books | open |
| O337 | sales, purchasing | approved_figures kept before O293 on a confirmed foreign-currency order hold document-currency figures and are now compared with base figures, so any edit is refused as past its approval. No effect before go-live; a data migration if live data holds such orders (seen 2026-10-20, fix_blockers D3; docs/handoff/reports/g23_fix_blockers.md) | rule | minor |
| O342 | sales | Credit exposure is built from two kinds of rate: this order at its date (O334), every other confirmed order at today's (committed_in_base). Decided: every order at its own date, through in_base (seen 2026-10-21, fix_blockers Q7; docs/handoff/reports/g23_fix_blockers.md) | books | open |
| O343 | deploy | deploy/backup.sh's nightly check builds the manifest path from BACKUP_DIR and cds into it, so a relative BACKUP_DIR breaks the check; refuse one that is not absolute (seen 2026-10-21, fix_blockers D5; docs/handoff/reports/g23_fix_blockers.md) | rule | minor |
| O344 | deploy | The nightly check unpacks the whole attachments archive beside the backups once more: the disk needs room for a second copy of the media while it runs. The RUNBOOK says so, and the check refuses to start without the room (seen 2026-10-21, fix_blockers D6; docs/handoff/reports/g23_fix_blockers.md) | rule | minor |
| O345 | manufacturing | Shift.code takes any text and is only escaped where it goes into a label. Decided: a shift code is letters, digits and hyphens, refused otherwise (seen 2026-10-21, fix_floor; docs/handoff/reports/g23_fix_floor.md) | rule | minor |
| O346 | manufacturing | tests_review_qrecall TheColumn failed once with TransactionManagementError under --parallel 2 and passed alone and in the next run: a test the machine's speed decides is a defect (seen 2026-10-21, fix_floor; docs/handoff/reports/g23_fix_floor.md) | rule | open |
| O347 | inventory, accounting | A Stores Manager can point an item's cogs_account at any account, the same hole as an inventory account set to payables. Decided: the shared rule asks it too (an expense account kept for cost of sales; changing it needs accounting) (seen 2026-10-21, fix_stores2 round 2; docs/handoff/reports/g23_fix_stores2.md) | books | open |
| O348 | inventory, manufacturing | Floor roles still read an item's standard_cost and a Quality Inspector a work order's costs. Decided: the stock-money rule (HidesStockMoney) hides them without inventory.view_stock_value (seen 2026-10-21, fix_stores2 round 2; docs/handoff/reports/g23_fix_stores2.md) | security | open |
| O349 | purchasing | A legacy standard-costed subcontract return with no recorded consumption leaves -3.17 of price variance (average and FIFO net to 0) (seen 2026-10-21, fix_stores2 round 2; docs/handoff/reports/g23_fix_stores2.md) | books | minor |

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
