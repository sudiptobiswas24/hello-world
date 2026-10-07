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
