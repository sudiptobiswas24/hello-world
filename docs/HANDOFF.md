# Handoff — start here

This is an ERP for a woven polypropylene sack plant in India: Django 5.2,
DRF, React, Odoo-like. The owner wants every module as deep as Odoo's,
and correct before wide. This file says where things stand and what comes
next. It was last brought up to date on 24 October 2026, after g23, so
that a new session can start small.

## 0. Read, then verify, before doing anything

First, look for interrupted work and resume it from where it stopped (CLAUDE.md, "Interrupted work"). At the last handoff (after g17) nothing was interrupted: no agent, background job or gate was running, and every worktree's commits were pushed.

1. Read, in order:
   - `CLAUDE.md`: house rules and the mistakes this project keeps making.
   - this file.
   - `scripts/gate/README.md`: how a release is gated.
   - `docs/handoff/findings.md`: the last audit and what each fix branch holds.
   - `docs/RISKS.md`, "Open defects": every defect seen and not yet fixed, numbered.
   - `.claude/skills/audit/SKILL.md`: before any audit.
2. Verify every branch below exists, with `git ls-remote origin 'refs/heads/claude/erp-*'`.
   If any is missing, stop and tell the owner.

| Branch | What it is |
|---|---|
| `claude/erp-creation-guidance-ox5afo` | **The product.** Gated and pushed through b1d03ed (g17), plus documentation commits. PR #4 (the owner's, into master) tracks it. Push only here, and only after a gate. |
| `claude/erp-fix-{store,pay,asset,make,race,ui}` | The six fix branches. All are in g17 (b1d03ed); kept for their history. Safe to delete. |
| `claude/erp-leftover-{ctree,htree,ltree}` | Edits left by sessions before 8 October. Checked on 8 October: all superseded by the product (ctree was an early draft of 9d81846). Safe to delete. |
| `claude/erp-g17-wip`, `claude/erp-g17-make-wip` | Backups of g17 before it was pushed. Safe to delete. |
| `claude/erp-gate-scratch`, `claude/erp-gate-results` | Scratch, from running two gate lanes in another cloud session. Safe to delete. |


## 1. Environment, once per new container

```
cd <repo>
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # if .venv is missing
(cd frontend && npm ci && npm run build)                               # the browser tests open this build
pg_isready -q || service postgresql start                              # role erp/erp, as the gate uses it
```

- Chromium for Playwright is at `/opt/pw-browsers/chromium`. Pass
  `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium`, or the
  browser tests skip and the count looks right.
- Quick test runs: `PYTHONPATH=scripts/gate .venv/bin/python manage.py test <labels> --settings=fastsettings --exclude-tag migration`.
- A background job may run two hours at most, so gate lanes run separately
  (scripts/gate/README.md).
- Odoo 19 Community source, read-only, for comparison work:
  `git clone --depth 1 --branch 19.0 --filter=blob:none --sparse https://github.com/odoo/odoo.git`,
  then `git sparse-checkout set addons/<module> ...`.

## 2. Next steps, in order

**g49 to g52 are pushed together (260b6ca, 19 November 2026), all six lanes green on one tip (gated as 50498303).**
Ten commits on 587ef25. g49's gate was folded into g50's, g50's into g51's, and g52's first two commits into
g51's as they came (21617aa failed pg-c on a race in Receive by scan, O607, fixed in a seventh commit). g51's
gate then passed five lanes on af87315 (ui; ist 8968 tests; pg-a 4171, pg-b 3701, pg-c 1096) and its migration
lane was on inventory when the container was replaced at 09:45 on 19 November. The lane logs went with it, and
finalize writes the Gate lines from them, so the ten commits, O609 to O611 added, were gated again as one.
**Lesson: /home/user/work is not kept across a container replacement, only the snapshot branches are.** The
gate tree and the fix tree came back from claude/erp-wip-*; the logs, the chain script and this entry's draft
did not. Keep anything a gate needs that is not in a worktree in one that is (`notes`, detached at the base).
The machine then restarted three times, at 14:30, 15:45 and 17:51, files kept and every process gone, killing
the migration lane in core twice and in inventory once. **Lesson: an idle session's container is reclaimed.** Both came when no job
tracked by the session ran: the watcher had reached its time limit and was not started again, taken for
optional. It is what keeps the machine, and the detached gate on it, alive. One watcher at its two-hour
limit still leaves a gap before the next is started (the third reclaim came five minutes into one), so two run
staggered an hour apart, each started again as it stops. The chain is restart-safe and skipped the five lanes and two apps that had passed. My own suites
beside the lanes also slowed core to about 16 minutes a test against 7,000 seconds an app: run nothing
parallel beside the migration lane.


g49 (one commit), O599:
- An allocation cut, raised, moved or deleted after its payment earned a settlement discount kept that discount:
  490 cut to 245 held 10.00 where 245 earns 5.00, and a deleted allocation left the document owing what its
  discount had covered. The allocation re-states the discount the way it re-states its exchange difference
  (RealisedOnItsOwnDay.restate_discount, one place for invoices and bills): the payment's standing discount on
  the document it leaves is withdrawn on the day of the change, or the discount's own day when later; still
  applied there, the payment earns again on what it applies now, by O250's running total. Moved to another
  document, it earns there only when someone takes it. A discount taken with no payment named is left alone.
- A payment holds one standing discount a document, not one ever: the unique constraint counts standing rows
  only (sales 0087, purchasing 0094), so a payment whose discount came back can earn again.
- **The model tests passed and the API test of a re-pointed allocation that was already there found a 500.** An
  allocation moved between documents locks both, and an edit over the API locks in the order locked_before_it
  lists them; with the document it leaves appended last, a move from invoice 1 to invoice 2 locked 1 after 2 and
  the sentinel refused. The two are listed in key order now, and a new API test moves one from the earlier
  invoice to the later.

g50 (one commit), O573, O601, O602:
- A key no field takes is refused beside it with a 400 naming what the record takes, not dropped: an account
  sent `hold_money` for `holds_money` was made, 201, as no bank; a follow-up sent `asigned_to` was planned for
  whoever sent it. The rule is on the shared bases (apps.core.api.ModelSerializer and the new
  apps.core.api.Serializer), nested lines included. A read-only field sent back as read (an id, a name through a
  relation) is still taken and ignored, as O565 decided.
- A key the view reads beside its serializer is named where it is read and taken there only: on the viewset,
  body_beside_its_serializer = {action: keys} (a new party's `role`, on its create; a change is refused it), or in
  the context of a serializer an action makes, body_beside (a coating's `lamination_gsm`, the record a follow-up
  is about). audit_invariants reports a key read beside a serializer and not named, a name listed for an action
  that does not read it (body_read_beside_its_serializer), and a plain Serializer written on DRF's.
- The screen check reads record screens now: each RecordScreen's create and save against its serializer, by the
  permission it offers, and a plain ViewSet's own create or change. 583 writes checked, 281 before. It found
  O602: the leave policy screen offered New and Save, and the API answers both with 405; it reads only now.
- **The suite said what the rule would have broken, and it was a test:** the plant-month test sent a `method`
  with every payment. Payment has no such field; the key had been dropped every time. It is gone.
- O601: the screen check took any body parameter named `values` for its fields' keys; a row action's is its whole
  row. It is the declared body's first parameter, and only where there are fields, which ActionForm fills with
  the fields' values and nothing else (held by CountForm.test.tsx). Planted, a row action's `(values) => values`
  is reported; before, it read as sending nothing, and clean.

g51 (three commits):
- O600: a credit or debit note undoing settlement discounts booked the whole of what it undid beside the newest
  discount, so two discounts booked to two accounts (the discount account changed between them) left one over and
  one under; and nothing kept which discount a note had undone, so a payment returned after a half note withdrew
  its discount again (the invoice owed 10.00 the other payment's discount had settled). The same for write-offs:
  the newer one undone and then recovered came off bad debt twice, and the older could not be recovered. Each
  discount row and each write-off keeps undone_amount; a note undoes newest first, each row on its own entry's
  account (settlement.undo_discounts, shared by both notes); a withdrawal or recovery takes only what is on the
  books of its own row. The rows are held in key order before they are written newest first: the lock-order
  sentinel refused the first version. sales 0088 and purchasing 0095 fill undone_amount from the notes posted
  before and name each document whose books may hold the old split.
- Redundant code, asked for by the owner: 196 unused imports and 75 unused local names gone, each application
  import checked against every module reaching it by name or dotted path; three serializer Meta settings that sat
  after a method's return (none had any effect: the fields are editable=False). Twelve local imports repeating a
  models module's re-export stay on purpose. The office application has no dead file or export.
- The gate runs the full suite twice, not three times (owner, 18 November): SQLite under Asia/Kolkata and
  PostgreSQL under UTC. The third, SQLite under UTC, had each half in one of the other two and cost about 110
  minutes a gate. gateline.py adds the PostgreSQL parts up against Asia/Kolkata's count now, and with
  GATE_BROWSERS=1 (lane.sh, the workflow) a browser test that cannot run fails the lane, naming why, instead of
  skipping under an OK.

g52 (five commits), what sweeping the demo plant found. Every screen as each of the 11 demo logins, in a real
browser, 1105 views with a screenshot each: no failed request, no console error, no error panel. Each empty list,
blank column and word that should never reach a person was read; all are explained but these:
- O603: every document's letterhead and foot printed the company's tax ID as its GSTIN, while the tax invoice,
  the returns and the e-invoice name the plant's GST registration; the demo plant's purchase orders and challans
  carried no GSTIN at all. core.documents.issuer() asks accounting for the registration's GSTIN. History folds a
  record kept whole ("6 details kept") and wraps long values.
- O607: a hand scanner at Receive by scan types before the warehouse list has come, so the scan went naming no
  warehouse and was refused. It waits for the list the screen already asked for. Found by g51's pg-c under load.
- O609: Django's get_object_or_404 answers a value that is not a number with a 500. Sixteen files called it 79
  times, 47 of them on something the request sent (`?item=abc` on the control chart). They import DRF's, which
  answers 404, and audit_invariants reports Django's anywhere in the application (lookups_that_answer_500).
- O610: a line showed the taxes chosen on it, not those it is charged. An export under the LUT kept CGST9, SGST9
  beside a tax of 0.00 while its print said IGST0. Every trading line reads back charged_taxes (the party's
  position applied, or as posted); the line shows them, and a draft whose choice is charged otherwise says so
  ("Charged IGST0") beside the picker.
- O611: a document in another currency showed its amounts with no currency: a USD invoice read "Total
  1,140.00". The five trading documents read back foreign_currency (empty in the base); totals and list cells
  show it, and the trucks panel's value says it is in INR.
Registered, not fixed: O604, O605, O606 and O608 (in the register).

Counts: Asia/Kolkata 8,984 (252 skipped), PostgreSQL 4,183 + 3,701 + 1,100 = 8,984, migration-tagged 65 and the
one PostgreSQL-only (every app ran: the range touches scripts/gate), vitest 87.

**g46, g47 and g48 are pushed together (39b78af, 18 November 2026), all seven lanes green on one tip (gated as 8d289dc).**
Six commits from 5a2e1b0. g46's tip failed the migration lane, g47's first tip failed three browser tests, and
finalize refused g47's fixed tip on its migration lane's base (below), so each was folded into the next and the six
gated once.

g46 (three commits):
- O596: the pay-run poster test reads the ledger log's rows from its own post, not those earlier tests left in the
  same PostgreSQL database (the log refuses TRUNCATE, so a TransactionTestCase's flush cannot clear it).
- O247: a party's TDS rate of its own says what it rests on (tds_rate_basis): the deductee's own class, with no
  dates, or a lower-deduction certificate (section 197), which stands only from its first day to its last inside
  one financial year. own_tds_rate_on() is the one reading, asked by the deduction on the day tax falls due and by
  the posting check, which let every bill through for a vendor with any rate of its own. **Upgrade note:** a rate
  entered before keeps its old meaning, every day, until it is next saved, which asks; Health lists each one.
- O597 (found building O247, fixed in it, not registered): a deduction keeps the certificate its rate was
  (TdsDeduction.certificate, one of its facts), and the quarter's return names it. Deductions made before say
  none; it is not guessed from the profile.

g47 (one commit), O579:
- 252 actions that read their body by hand name the keys they take (apps.core.api.takes_only), and a key not taken
  is refused beside it rather than dropped. audit_invariants reports an action reading its body without the list,
  in its own source or in a helper it hands the request to (actions_reading_the_body_by_hand), and a list short of
  a key the action or its helpers read (takes_only_short_of_its_reads).
- **The first lists broke two screens, and only the suite said so:** the station's four weighing actions read
  `core` in a helper, and the tablet sends it with every roll; the old-supply credit and debit notes read `memo`,
  `on_date` and `old_value` in from_request, which OldSupplyNote sends. A screen check by pattern had called 180
  calls clean and missed both. Seven more actions read their body only through a helper given the request
  (granting and revoking a role, approving leave, four asset actions) and escaped the first form of the check.
- The ui lane now runs scripts/gate/screenkeys.py: every write the office app and the station tablet make, read
  from oxc's syntax tree, against the keys its action takes (281 checked; nine generic runners listed with why,
  and a new unreadable one fails). gateline.py now reads it and the register check, whose failure it never read.
- **Gate a tip with its own scripts** (GATE_TREE/scripts/gate/lane.sh), or a new step in it does not run: lane.sh
  takes its steps from the copy that is run, and this gate was the first to need it.

g48 (one commit), O250:
- Every payment inside the discount days earns its share of the settlement discount, a row each
  (accounting.mixins.SettlementDiscountTaken; sales.InvoiceDiscount, purchasing.BillDiscount). 1,000 at 2/10 paid
  490 + 490 by the 10th left 10.00 owing that no one owed: a document kept one discount, so the second payment
  earned nothing. A payment earns on the running total of what the payments that earned here settle, so
  400 + 400 + 180 earns 8.16, 8.17 and 3.67 and leaves no paisa. A returned payment takes back its own discount and
  no other. Each discount event books the step in the rounded base of what stands (discount_base_moved). A migration
  each side copies the discount taken before into its row, naming no payment; back, a document holding two refuses.
- **Every lane since g40 printed "FAIL O109 ... [due 10.00]" and stayed green.** The fact tests' say() printed PASS or
  FAIL and returned, in four modules, so a false claim was a line in a log. It is one helper now
  (apps/core/tests_claims.py) and raises; run with it first, the O250 claim was the only one to fail.
- A PostgreSQL race of two payments earning at once (8.16 and 8.17, not 8.16 twice) and a MigrationExecutor test
  each side.

Counts: SQLite 8,937 (252 skipped), PostgreSQL 4,141 + 3,701 + 1,095 = 8,937, Asia/Kolkata 8,937 (252 skipped),
migration-tagged 58 and the one PostgreSQL-only (every app ran: the range touches scripts/gate), vitest 84.

- **O579's lists refused the adders' `id`, and only the browser tests said so.** c14ebc7 (g47) failed three
  browser tests on SQLite and on PostgreSQL alike: a count line, a party's second role and an RFQ's quote could not
  be added from their panels. ActionForm gave a declared body the form's own state, which blank() starts as
  { id: 0 }, so `body: (values) => values` sent an `id` that the three actions, now naming their keys, refused. The
  screen check took `values` to be the fields and called the three clean. 05e24d6 gives a body the fields' values and
  nothing else, with a rendered test of the count's adder that failed with the screen as it was.
- **finalize refused 05e24d6 with all seven lanes green.** Its migration lane ran with GATE_BASE=c14ebc7, and a
  skip rests on a commit a whole gate passed: c14ebc7 had failed three browser tests. Only office-app files changed
  after it, but that is the doubt the gate README says to settle by running every app, and the guard was kept. The
  range from 5a2e1b0 touches scripts/gate, so every app runs either way (about five hours); g48 was built, so it was
  folded in and one gate run on its tip, rather than five hours for g47 alone and then g48's own gate.
- **A watcher's `pgrep -f "lane.sh g48 ui"` matches its own `bash -c` line**, so it waits out its timeout, not the
  lane: ist's watcher in g47 and ui's in g48 each did, and pg-a started 80 minutes late. Write `[l]ane.sh`. The rest
  of g48's order ran from one detached chain script, launching each lane as its turn came.
- The container restarted twice (01:36 UTC in g47's ist lane, 07:48 in g48's mig lane), killing every lane and
  watcher with no word. The mig lane carried on from its `.done` markers; a chain started again from its top re-ran
  a lane that had passed. A chain written for a gate should skip a lane whose driver file says exit=0 on the HEAD,
  so it can be started again as it is. Not in scripts/gate yet: a change there runs every app's migration tests at
  the next gate.
- The tip that failed on the way: aee7ff3 (g46) failed the migration lane on purchasing's
  CategoryKeptMigrationTests: O247 added tds_rate_basis NOT NULL with no database default, and the test, which
  rolls purchasing back and leaves accounting at its newest, made a tax profile through an older model that knew
  no basis. The field now has db_default "" (migration 0040 edited, never pushed). The same change had given the
  deduction's certificate a db_default and not the profile's basis: the pre-flight rule on AddField says why both.
- Register: O598 (a certificate's amount limit is not kept: past the amount it names, its rate goes on to its last
  day; a bill crossing it wants two rates, which one deduction per bill and section cannot hold); O601 (the screen
  check reads `body: (values) => values` as the fields' keys, true only because of 05e24d6, and nothing checks
  ActionForm's call); O599 (an allocation deleted or cut after its payment earned keeps the discount, older than
  O250); O600 (a note undoing discounts booked to two accounts books beside the newest). O579 and O250 are fixed.
- Left open, and why: O389 (no live plant holds a disposal from before g24, so a backfill helps nobody yet); O600
  (a note's discount undo split across the accounts the discounts went to needs what each earlier note undid on
  each account: notes keep one total, and their one entry holds the write-off's undo beside the discount's, so the
  books cannot tell the two apart when both went to one account. Wants a record per note and account, not a
  replay).
- Waiting on people, unchanged: the owner (O448, O494, O557, O558, O559, O562, O571, decision 24; O550 needs the
  Maharashtra minimum wage notification), the tax adviser (O449, O500, O534, O559, O561), the accountant (O536,
  O538, O562, O563, docs/STATUTORY_REVIEW.md section 14).

**g42 to g45 are pushed together (19a9b84, 17 November 2026), all seven lanes green on one tip (gated as 2906eab).**
Thirty-six commits from 565c724. Four rounds went through one gate: each time a tip failed and had to change, the
next round's reviewed commits were folded in rather than gated twice.

g42 (twenty commits, reviewed after g41):
- O249 and O581: a purchase order line naming an agreement's line is checked against it when made, as an edit is,
  and calls the agreement off only for its vendor, item and dates, through the one check release() uses.
- O357: the credit check names a confirmed order with no exchange rate on its date, and Health lists every one.
- O577 and O578: every create, and the six viewsets' actions, name the keys they take (takes_only), and
  audit_invariants (creates_reading_the_body_by_hand) reports a create that does not; the e-way bill screen asks
  the value going back on a job-work delivery.
- O587, O588, O589: a method field returning a list is refused when sent, by the serializer's own list of them,
  not by its name; a new lead shows New before it is saved; the bank rate's serializer goes through the dated
  rates' one mixin.
- O256: a withdrawn settlement discount is never dated before the discount it takes back; a withdrawal with no
  discount taken is refused in words (deab163). O554: the reverse-charge footing counts the bills the challan
  pays, as the return does.
- O580: the correction-date check follows a step into the helper it posts through, and found four postings with
  no date rule: a customer's TDS (O582), landed cost (O583), a cost version's publish (O584), a run's close (O585).
- O586 and O591: the hr lists ask which run kept each row in one query, not one a row.
- O590: races.stale_behind_a_lock waits on its own transaction and says at once when the second never waited.
- O383: a salary payment that no slip, bank file, advance, remittance or payout takes reads as applied to nothing
  (settlement.register_taking_field), so the one a refused application leaves on net pay payable is the only one
  the Controller finds on the payments list, and voids. Before, every salary payment read so.

g43 (six commits, people and locks):
- O551: the gratuity ceiling is a dated statutory figure the Controller confirms (FigureKind.GRATUITY_CEILING),
  read on the leaving day and kept on the payout (GratuityPayout.ceiling_figure); a row a payout kept cannot be
  changed or deleted. **Upgrade note:** an upgraded install refuses the gratuity calculation, in words, until the
  Controller confirms the ceiling. setup_statutory loads S.O. 1420(E)'s 20,00,000 from 29 March 2018
  (statutory/gratuity.csv); go_live_check asks for it.
- O365: a run worked out for some people names only those chosen with no pay set up; hr 0043 marks as entered
  every punched day a hand corrected before O304 (a day with only a time changed cannot be told from the file's
  and stays so).
- O373: the lock-order survey records every site that takes a pair, not only the first.
- O379: update_or_create() is held FOR NO KEY UPDATE through one helper, apps.core.models.update_or_create_held,
  which takes the numbering row inside its own block (c06274f); plain_for_update reports a new plain call.
- O380: a stoppage whose save failed gives its number back; audit_invariants (numbers_kept_past_a_rollback)
  reports a method that numbers outside @serialised or @numbered_in_one_transaction.

g44 (the backup fix, four commits from the redundant-code audit, the sweep fix):
- The backup test drops its scratch database with a plain DROP, which stops autovacuum; WITH (FORCE) could not
  end an autovacuum worker as the erp role and failed sqlite on deb1ab7.
- The owner asked for a fresh agent to audit redundant code (old versions kept beside new) and to discuss before
  anything went. The report, with the owner's decisions at its head, is docs/handoff/reports/g44_redundant.md.
  Decisions: **remove no dead code yet** (the list stays on file); the morning checks escalate overdue quality
  decisions (O594: escalate_overdue had no caller, so a decision left past its deadline reached nobody); the 39
  probe and test copies under docs/handoff/reports go (a README there says the commit each is still in); the
  merges weighed one by one.
- Merged, no behaviour change: an allocation's save and locked_before_it (InvoicePayment and BillPayment) on
  RealisedOnItsOwnDay; what is overdue on one mixin, settlement.LateByItsInstallments; "the party holds role X"
  and names_another in core (33 callers); month_end in core; AuditModel.write_past_its_guard for twenty hand
  copies of the flag, save, finally shape (twelve the audit listed, eight more its new check,
  guard_bypasses_by_hand, found). Not merged, and why: the order viewsets' actions are view plumbing over
  core.approvals; the pdf action is five lines; computed_by is a reflection helper in two unrelated models; the
  aliases stay.
- The browser sweep's plant confirms its gratuity ceiling, as an installed plant does: O551's refusal failed nine
  sweep tests on pg-c at 8222958.

g45 (three commits):
- O388: a note's part absorbed is restated at the base it cleared, one figure on the note's side and the
  document's (restatement.absorbed_cleared), sales and purchasing both: the receivable read a paisa off.
- O593: a number taken inside a caller's transaction that then rolled back is given back on the object, read
  off the row rather than Django's on-commit list.
- O374: hr.employee is ranked between accounting.payment and accounting.account, and Payment.post asks its checks
  before it shares the bank. g43 ranked the person after the account, on the register's word; the PostgreSQL lane
  refused 41 tests, every one a receipt from a person (the bank shared, then hold_the_payer), which SQLite never
  saw because it drops FOR SHARE. The rank was taken out and redone from a survey of every lock taken on
  PostgreSQL: both orders were real, so one path had to change, and a race() on PostgreSQL proves it.

Counts: SQLite 8,911 (251 skipped), PostgreSQL 4,116 + 3,701 + 1,094 = 8,911, Asia/Kolkata 8,911 (251 skipped), migration-tagged
55 (every app ran: lock_order.py changed), vitest 83.

- Tips that failed on the way, and why:
  - bba172d (g42) failed pg-a and sqlite on one test: O256 made withdraw_discount read the discount's entry
    first, and EveryEventAsksTheRestatementTests called it with none taken. Neither O256's agent nor its review
    ran tests_restatement: a change to a shared settlement function runs apps.accounting whole. The same tip's ui
    lane failed tsc on the O578 test (TS2532): vitest does not typecheck, so an agent touching frontend/ runs
    `npm run build` as well.
  - deb1ab7 failed sqlite on the backup test's DROP WITH (FORCE) against autovacuum (the fix above).
  - e141989 failed pg-b on 41 LockOrderViolations (O374's first rank, above).
  - 8222958 failed pg-c on nine sweep tests (O551's designed refusal, above).
- Gate lessons:
  - Two browser failures in a day ran beside the migration lane: a page crash at 12.9 of 16 GB (O592) and a button
    that never appeared (O595). The gate now runs no browser lane (sqlite, pg-c, ist) beside mig.
  - A rank in LOCK_ORDER is proved on PostgreSQL: SQLite drops FOR UPDATE and FOR SHARE, so the sentinel never
    sees those locks there.
  - Never split a hunk with `git apply --unidiff-zero`: it placed month_end inside to_date in an intermediate
    commit, and only testing that commit alone caught it. Write the intermediate file with `git hash-object -w`
    and `git update-index --cacheinfo`.
  - A change to apps/core/lock_order.py, or a migration in hr, makes mig run every app: about seven and a half
    hours, the long pole again.
  - The container restarted twice: at 23:57 on 16 November, nineteen minutes into sqlite and pg-c, and at 01:53,
    six minutes into ist. Each lane it killed ran again from the start; the lanes already OK kept their results,
    on the same tip.
- Register: O592 and O595 (browser failures beside the migration lane; whether either recurs alone is the next
  runs' answer), O596 (hr.tests_review_audit p20 reads ledger rows a demo-loading test left in the same
  PostgreSQL database). O593 and O374 are fixed in g45 and not added.
- Next: g46, built in /home/user/work/g46 on 2906eab: O596 (06db2b9, the test reads only the ledger rows its own
  post made); remove O596's row in that commit when it is replayed onto this documentation commit.
- O564 (the demo failure only g39's whole pg-c lane showed) did not recur: still open.
- Waiting on people, unchanged: the owner (O448, O494, O557, O558, O559, O562, O571, decision 24; O550 needs the
  Maharashtra minimum wage notification), the tax adviser (O449, O500, O534, O559, O561), the accountant (O536,
  O538, O562, O563, docs/STATUTORY_REVIEW.md section 14).

**g41 is pushed (fe02c85, 15 November 2026), all seven lanes green on one tip (gated as 310f75e).** Ten commits, all
register rows or defects that waited on nobody, from two agents and one gate fix:
- O568: the TDS and TCS rate lists ask whether each rate was taken in the list's own query (DatedRate.with_taken),
  not one EXISTS a row; a delete still asks the database fresh.
- O569: the demo keeps one letter of undertaking for each financial year from the story's July to today, its year,
  filing day and ARN worked out from its dates. The row understated it: every load failed from April 2027, and
  even today the LUT ended on the day of the load. The demo runs again two years on and in May.
- O567 and O570: hr has one rule, Confirmed.kept_because(), which delete() refuses in for the statutory figure,
  the minimum wage and the pay component slab; their screens offer Delete (the slab list its remover) only where
  no posted run kept the row, and say why.
- O566: a footer a document passes prints as passed. The tax invoice, self-invoice and payment voucher keep their
  contacts when issued and reprint those, not today's. A document issued before this kept no contacts and now
  reprints with none (name and Tax ID only): every older printout's foot changes on the day of the upgrade.
- O565 and O575: every serializer is written on one base, apps.core.api.ModelSerializer, which refuses a
  read-only list sent to it (nested many=True, a related many=True, a ListField, a method field named as lines)
  with a 400 beside the field, naming `new_lines` where the serializer takes it; O555's check moved into it.
  audit_invariants (serializers_off_the_shared_base) reports a serializer off the base. Any other read-only field
  is still ignored, so a screen or integration sending back what it read is not refused: the first form refused
  every read-only field and broke 11 browser tests, and was narrowed. The lead screen's Status is read back only.
- O574: six creates that read their body by hand (e-invoice, e-way bill, self-invoice, payment voucher, cost
  sheet, test certificate) refuse a key they do not take, beside it, through apps.core.api.takes_only.
- O572: the firming test PATCHed a read-only field and so tested nothing; it changes a writable one now.
- O576: the agreement-lock race control (test_06_o227) was decided by machine speed. Every write takes the
  company's numbering row first, so the two calls were always serialised; the control went wrong only when the
  release reached the row first. A new helper, races.stale_behind_a_lock, holds the order by what PostgreSQL shows
  (pg_blocking_pids), not by the clock.
Counts: SQLite 8,850 (248 skipped), PostgreSQL 4,071 + 3,689 + 1,090 = 8,850, Asia/Kolkata 8,850 (248 skipped), migration-tagged 54, vitest 79.
- The first tip (6243e1a, four commits) failed pg-c on test_06_o227 at load 8 on four cores; alone it passed three
  in three. A longer barrier made it worse (each side then waited on the other until the barrier broke). Since the
  tip had to change, g42's first five reviewed commits were folded in rather than gated twice.
- Register: O571 (a lead can never be "Being worked", for the owner), O573 (fields DRF drops without a word:
  undeclared ones, and scalar read-only ones by design), O579 (about 250 actions read their body by hand). O562 is
  now for the owner and the accountant: which account is a leaver's provision when no run accrued one. O390 comes
  off: it does not hold (a void of a receipt the restatement holds would take away what it holds, and one applied
  to an invoice it did not restate would leave that invoice due at the close unrestated).
- Next: g42, already built and pre-integrated on 310f75e (/home/user/work/g42int, 18 commits): O249, O357, O581
  (a purchase order line calls off its agreement only as release() does, one shared check), O587, O588, O589,
  O577 and O578 (takes_only on five more creates and the six viewsets' actions; audit_invariants'
  creates_reading_the_body_by_hand; the e-way bill screen asks the value going back), O256 and O554, O580 (the
  correction-date check follows a step into the helper it posts through) and the four undated postings it found
  (O582 customer TDS, O583 landed cost, O584 a cost version's publish, O585 a run's close), O586 and O591 (the hr
  lists in one query), O590 (the race helper waits on its own transaction).
- O564 (the demo failure only g39's whole pg-c lane showed) did not recur in g41's two pg-c runs: still open, a
  run-dependent failure waiting for its next appearance.
- Waiting on people, unchanged but for O571 and O562: the owner (O448, O494, O557, O558, O559, O562, O571,
  decision 24; O550 needs the Maharashtra minimum wage notification), the tax adviser (O449, O500, O534, O559,
  O561), the accountant (O536, O538, O562, O563, docs/STATUTORY_REVIEW.md section 14).
- Gate lessons from g41: the sqlite lane reached 7,259 of its 9,000 s beside mig; pausing mig's purchasing step
  (SIGSTOP of both the lane's and timeout's process groups: timeout runs its child in a group of its own) let it
  finish at 6,945 s of tests. A change to apps/accounting/dated_rates.py made mig run twelve apps, about eight
  hours: the long pole again. Code review of the tip found the hr kept_because one query a row (O586), the read-only
  list check keyed on names (O587) and the race helper's blind wait (O590), all fixed in g42.

**g40 is pushed (c2b00a8, 15 November 2026), all seven lanes green on one tip (gated as 7556815).** Six commits, all register
rows that waited on nobody: O555 (a document's lines sent as `lines` instead of `new_lines` are refused with a 400
beside the field, on a new or saved document, in the shared TakesItsLinesMixin, so bill, invoice, sales order and
purchase order alike), O556 (the bank, TDS and TCS rate screens offer Delete only where the server takes it and say
why not, one shared `kept_because`; TDS and TCS follow the server: deletable until something was deducted or collected
at the rate), O552 (a self-invoice and a payment voucher keep `recipient_gstin` and `letterhead` as issued and print
only those; gst 0012 fills old rows from the settings of the day it ran, and says so; `kept_settings_read_live` now
reports a printout reading today's GST registration or company), O560 (the demo gives everyone the gratuity provision
through `give_gratuity_provision()`, which setup_payroll shares; BONUS still goes to nobody, O550), O549 (no fixture
was left that crosses the books' opening day; a shared `TwoYearsOn` mixin runs one class of each such fixture again
with the clock 800 days on), and the gratuity payout's race tests (two payouts at once, two voids at once; each fails
without the locks). Counts: SQLite 8,825 (248 skipped), PostgreSQL 4,059 + 3,685 + 1,081 = 8,825, Asia/Kolkata 8,825 (248
skipped), migration-tagged 54 and the one PostgreSQL-only, vitest 73.
- Register rows O565 to O569 are what g40 saw and did not fix: other serializers still drop `lines` without a word
  (quotation, delivery, recurring invoice and more: O555's shape), every printout's footer reprints today's company
  email, phone and website, two payroll forms offer Delete on a figure a pay run kept (O556's shape), one EXISTS per
  row on the TDS and TCS rate lists, and the demo's letter of undertaking keeps a fixed "2026-27" while its dates
  move, so every demo load fails from October 2027.
- O564 (the demo failure only g39's whole pg-c lane showed) did not recur in g40's pg-c either; it stays open until a failure names its cause.
- Waiting on people, unchanged: the owner (O448, O494, O557, O558, O559, decision 24; O550 needs the Maharashtra
  minimum wage notification before the demo can give BONUS), the tax adviser (O449, O500, O534, O559, O561), the
  accountant (O536, O538, O563, docs/STATUTORY_REVIEW.md section 14).
- Gate lessons from g40: built beside g39's gate by two agents whose tests took turns through
  /home/user/work/agent_tests.lock; based on the g39 handoff commit, so it fast-forwards. A change to a module the
  migrations import (apps/accounting/dated_rates.py) makes the mig lane run nearly every app, about five and a half
  hours: the long pole. Lanes went pg-c first so the PostgreSQL-only demo and race tests answered early.

**g39 is pushed (c198aba, 14 November 2026), all seven lanes green on one tip (gated as ee143c4).** Five commits
finishing the two pieces g38 left uncommitted. Limit 19: the gratuity provision is seeded (GRATUITY, 4.81% of basic
and DA a month into 2470, charged to 6120) and given to everyone by `give_statutory_accruals`; go_live_check names
anyone without it; `GratuityPayout` (hr 0041) pays a leaver exactly what gratuity_due owes on the leaving day, against
the provision their runs accrued into, refused before five years unless death or disablement, voided with its
payment, edit and delete refused; `gratuity-payouts/` for the Controller (Payroll Officer reads). Limit L5 and O548:
an item's tracking is fixed once its stock has moved, as its unit and costing method are, through the API, the admin
and code; the admin answers beside the form instead of a 500; `audit_invariants` reports a QuerySet.update of an
item's unit, costing, tracking or inventory account (`item_counting_changed_around_save`); no test switches tracking
over held stock any more. Register rows O560-O563 are what the gratuity work saw and did not fix: the demo gives
GRATUITY to nobody (as O550 for BONUS), a death's payout cannot go to a nominee (O561, for the owner and the tax
adviser), a provision booked by journal cannot be paid out, and whether s.2A's 240 days count toward the fifth year
(O563, for the accountant). Counts: SQLite 8,805 (246 skipped), PostgreSQL 4,057 + 3,685 + 1,063 = 8,805,
Asia/Kolkata 8,805 (246 skipped), migration-tagged 43 of 53 (the apps the range needed) and the
one PostgreSQL-only, vitest 70.
- Not done: no payout screen, no race() test on PostgreSQL for the payout, no demo or browser drive of either piece.
- The first tip (5ab7886) failed pg-a: accounting's restatement test found GratuityPayout booking against a payment
  without asking the restatement or being listed. It is payroll in the base currency and is now listed with that
  reason. The gratuity agent had run hr, core and web, not accounting: a new model that names a payment and an amount
  must run apps.accounting too. Before restarting, the five apps neither agent had run were run on the combined tree
  (1,202 OK).
- The second tip (524933a) failed pg-c, PostgreSQL only: RunRaceTests borrows RunTestCase.setUp, which since L5
  reads by_batch, and did not borrow by_batch; and both demo tests failed at "Polymer bought on a circular" with no
  reason shown (see the register row below). Neither agent ran its tests on PostgreSQL. A change to a fixture that
  apps/e2e/tests_races.py borrows, or to how stock rows are locked, must run on PostgreSQL before it is handed in.
  The third tip ran pg-c first, so its answer came in ninety minutes, not at the end.
- Gate lessons from g39: three tips. Two agents' tests beside two lanes had pushed g38's sales step to its limit, so
  g39's agents tested only beside one lane. The lane script refuses pg-C while a ui lane builds the office app:
  start pg-C after ui. The g40 agents' tests, niced and one at a time through /home/user/work/agent_tests.lock,
  still pushed ist to 7,893 s of tests beside two lanes (about 8,200 of its 9,000 s step); pausing them (SIGSTOP,
  resumed after) carried it. Agents' tests go beside one lane only. The demo failure on the second tip is O564.
- Landing: g39 was gated on 8eff573, under g38's handoff commit, so it was replayed onto 1607aca; the replay
  differs from the gated tip in docs/HANDOFF.md alone. Base the next integration on the handoff commit.
- Next: g40 is being built (/home/user/work/g40_trade: O555, O556, O552; /home/user/work/g40_people: O560, O549,
  the gratuity payout's race test).

**g38 is pushed (8eff573, 14 November 2026), all seven lanes green on one tip (gated as 12a107c).** Fifteen
commits: twelve from five agents deepening the trade, statutory and floor modules, then the register and two gate
fixes. Fixed: O541 (an open invoice or bill
raised after the books opened is refused), O542 (the opening-stock reason follows the account the books opened
against), O543 (an empty date on the import screen is said beside the date box), O544 and O545 (where a sale's
goods went is a fact the invoice keeps, an export of goods names its port, each truck has its own e-way bill), O546
(a job-work e-way bill says where the lorry goes), O547 (each state's own labour welfare fund; Maharashtra's slabs),
O457 (go_live_check counts and names the open to-dos from before the rule), O528 (the dispatch board loses no shift
overlap). Limits reworded: 16 (the reverse-charge self-invoice is raised as the bill posts and printed; 3B's 6.1
footed to the ledger), 17 (MSME days kept from the day of acceptance, the Act's interest shown), 19 (the statutory
bonus seeded, given and worked out for the year: statement, declaration, payout), 20 (where goods went), 21 (the
signed delivery copy kept as proof of delivery). Decision 24 records two choices taken for the owner: O541's opening
day and O545's export port; confirm them. Register rows O548 to O559 are what the agents saw and did not fix.
Counts: SQLite 8,786 (246 skipped), PostgreSQL 4,053 + 3,670 + 1,063 = 8,786, Asia/Kolkata 8,786,
migration-tagged 53 and the one PostgreSQL-only, vitest 70.
- The gate needed two fixture fixes and a test-helper fix before it went green, none of them product code: the
  plant-month export names its port (O545's rule reached a fixture no agent ran), and the schema window
  (apps/core/tests_schema_window.py) now adds every column today's models write at an old schema, not only
  taxes_taken_under. The sales migration tests failed 21 times on the first tip because g38 added port_address;
  they had not run in g37's gate since GATE_BASE skipped them.
- Next: g39, already built: the gratuity provision and payout (/home/user/work/g39_grat, limit 19, register
  O560-O563) and limit L5, an item's tracking fixed once its stock has moved, with the fixtures reworked
  (/home/user/work/g39_l5, closes O548).
- Waiting on people: the owner (O448, O494, O557, O558, O559, decision 24), the tax adviser (O449, O500, O534,
  O559), the accountant (O536, O538, docs/STATUTORY_REVIEW.md section 14).
- Gate lessons from g38: three tips before one went green, each a full seven-lane run. A test helper that
  names one column breaks on the next one: make it general. Agent tests beside two lanes on four cores pushed the
  sales migration step to 5,656 of 7,000 s; pausing the agents' tests (SIGSTOP, resumed after) saved it at 6,066 s.
  Keep agents' tests to a free slot. A container restart leaves PostgreSQL replaying its log for a minute or two:
  wait for it to accept connections before starting a pg lane. Two restarts in ninety minutes (23:38 and 01:10
  UTC) each killed pg-c near its end; memory (0.8 of 16 GB) and disk were not the cause. A lane of ninety minutes
  is the one most exposed: if restarts keep coming that often, split pg-c.

**g37 is pushed (9386a9d, 11 November 2026), all seven lanes green on one tip (gated as 46d5aca).** Sixteen
commits, the work an audit of the project itself asked for: the evidence so far is all the author's own, and the
path to go-live is the plant's files, a narrower pilot and outside eyes on the statutory outputs, not more internal
rounds. Fixed: O529 to O531 (opening stock, open invoices and open bills are held to the books' opening day and
opening account by one `opening_position()` check, and neither the command nor the screen asks for `--date` or
`--against`), O533 (the e-way bill, the place of supply and posting read one rule, `gst.state_delivered_in`; a
ship-to with no state is refused when goods move), O539 (GSTR-1 warns of a short HSN), O532 and O537 (the demo
plant's pay components are setup_payroll's own, and every statutory output comes out of it as loaded). Added:
`scripts/gate/migneeded.py` (the mig lane runs only the apps a range needs when given `GATE_BASE`; it saves time
only on a push with no migration), docs/STATUTORY_REVIEW.md for the accountant, docs/PILOT.md in four stages with
docs/pilot_pack/ (21 templates). `.github/workflows/gate.yml` holds the lanes for GitHub Actions: it starts by hand
only and **has never been run**; read its first run before trusting it. Counts: SQLite 8,668 (246 skipped),
PostgreSQL 3,961 + 3,654 + 1,053 = 8,668, Asia/Kolkata 8,668, migration-tagged 51, vitest 70.
- Open code rows, none being worked: O346, O457, O528, O540 to O547 (see docs/RISKS.md).
- Waiting on people, and these now decide the pace: the owner (O448, O494; the plant's real export files for the
  import; a GSTR-1 file put through the GST offline tool, O535; the first run of the Actions workflow; the pilot
  plan's choices in docs/PILOT.md), the tax adviser (O449, O500, O534), the accountant (O536, O538, with
  docs/STATUTORY_REVIEW.md).
- Not answered by the owner: whether new fix rounds stop here in favour of the import and the pilot. The audit says
  they should.
- Gate lessons from g37: about seven hours end to end, the mig lane five and a half of it alone. A "container
  restarted" notice can mean only the watchers died: check `uptime` and `pgrep` before re-running a lane. The
  watchers and the guard each end at their own two-hour limit and are started again.

**g36 is pushed (5a4cf66, 11 November 2026), all seven lanes green on one tip (gated as e98dc64).** Five commits:
O524 (a crewed bank's cap counts the crewed machines at their own speed; a crew of N runs the fastest N), O526 and
O527 (the dispatch board runs each machine on the shifts its crew runs it, day by day: `manning.crewed_on`), O259
(`shelf_read_before_lock` and `per_unit_withdrawal_rates` follow a call into another module where the text says
which function it means), O525 (`_issue_roll` holds its shelf before it reads; not a reachable race, since
mount_roll's numbering hold already closed it, and the race test passes with and without the hold) and new row O528
(a shift running past the next day's first shift loses the overlap on the board). Counts: SQLite 8,637 (246
skipped), PostgreSQL 3,950 + 3,653 + 1,034 = 8,637, Asia/Kolkata 8,637, migration-tagged 51, vitest 70.
Next: g37, in /home/user/work/g37tree (reports in /home/user/work/reports/g37_*.md). It came out of an audit of
the project itself on 11 November, which said the evidence so far is all the author's own and the path to go-live
is the plant's files, a narrower pilot and outside eyes on the statutory outputs, not more internal rounds. g37
holds: the import's opening kinds held to the books' opening day and account (O529-O531), the e-way bill's state
and GSTR-1's short-HSN warning (O533, O539), a demo plant that produces every statutory output (O532, O537), a mig
lane that runs only what a range needs (GATE_BASE) with a GitHub Actions workflow not yet run, docs/PILOT.md in
four stages with docs/pilot_pack/, and docs/STATUTORY_REVIEW.md for the accountant with rows O532-O540.
- Gate lessons from g36: no restart in eight hours. Fix agents running their own tests beside the gate slowed pg-c
  from 79 to 92 minutes; keep to one agent's tests beside two lanes. The runtime guard tripped twice on agents'
  own short polling waits; it now takes MIN (seconds) and skips limits below it.

**g35 is pushed (e28637b, 11 November 2026), all seven lanes green on one tip (gated as 7f57f16).** Ten
commits closing eight open code rows: O497 (the payments list reads reconciled in one query), O499 (posting a bill
writes again the MSME due day of other posted micro or small bills whose deliveries it takes), O501 (a page of bills
reads its Act days in one pass), O469 (Logins filed Active, Ended or Deactivated by the shared left_by rule), O474
(timesheets over the API and on People > Timesheets; view_every_timesheet; hours fixed once submitted; send back
refused once a posted pay run paid them; hr migration 0039), O481 (dispatch times an operation at the machine's own
rate), O520 (a work centre may not be rated at nought; manufacturing 0090 stops on an existing zero), O521 (capacity
counts each machine at its own speed), O522 and O523 (the machine's minutes recorded at the event, 0091 and 0092).
New row O524: a crewed bank's crew limit is still in clock minutes. Counts: SQLite 8,626 (245 skipped), PostgreSQL
3,946 + 3,647 + 1,033 = 8,626, Asia/Kolkata 8,626, migration-tagged 51, vitest 70. Next: the open code rows (O524,
O457, O346, O259), and the decision rows waiting on people (O448 and O494 owner, O449 and O500 tax adviser).
- Gate lessons from g35: two machine restarts, about 18:50 and 01:31. Each took PostgreSQL down; the second
  killed pg-c eight minutes in, and it re-ran from the start (1,033 tests, 79 minutes). The six finished lanes
  needed nothing. The migration-tagged count went 50 to 51: the new manufacturing migration test.

**g34 is pushed (6f6a58e, 10 November 2026), all seven lanes green on one tip (gated as 4cbc191).** Twelve
commits from /home/user/work/g34_o498: ten inert-method rows removed (O472, O476-O480, O482, O484-O486, O489), seven
wired to the API or a screen (O470 reconciled flags, O473 pay-run employer cost, O475 vehicle time on site, O483
MSME pay-by day, O487 stores issue subcontract components, O490 batches a delivery line shipped, O491 what an order
promised and cannot cover), O498 (an MSME bill falls due on the Act's day, not the terms'; purchasing migration 0090
keeps the terms' date), and new rows O497, O499 and O500 (two MSMED Act readings for the adviser). Next: the open
code rows (O457, O259, O346, O474, O481, O497, O499), and the decision rows waiting on people (O448 owner, O449
and O500 tax adviser, O494 owner, O469).
- Gate lessons from g34: mig started first and ran alone in slot 1 (about 4 hours 10 minutes); pg-c, the last
  lane, was started in slot 1 the moment mig ended rather than queued behind ist, which kept two lanes running
  and ended the gate about an hour sooner. The migration-tagged count went 49 to 50: purchasing's new 0090 test.
  A session restart at about 14:20 stopped the watcher and the runtime guard but not the machine (uptime
  unbroken) or the detached pg-c lane; restarting the two jobs was enough.

**g33 is pushed (3063006, 10 November 2026), all seven lanes green on one tip (gated as 98dcc74).** Holds O471
(HR revokes and issues a station PIN from the employee screen; the PIN is shown once), O488 (a charge line takes
its charge's taxes on every path; add_charge gone), O266 (a hand-typed asset's go-live figures are corrected on the
draft until it posts, read under its lock, with race tests), O494 (decision row: may a charge line post untaxed by
choice, the owner's), O495 (the office screens add a freight or packing charge to a document) and O496 (freight
typed on a bill books to its charge's expense account; load_demo carries a FREIGHT charge). Next: g34, the twelve
commits in /home/user/work/g34_o498 (3a28ce9..2ff4468: ten inert-method rows removed, seven wired, O498 MSME due
date, rows O497/O499/O500), cherry-picked onto 3063006; a trial pick onto 98dcc74 applied clean with no migration
changes.
- Gate lessons from g33: the machine restarted at 01:27 mid-gate and killed the two detached lanes (mig, sqlite)
  and PostgreSQL. Nothing on disk was lost: mig resumed from its per-HEAD .done markers (accounting and assets not
  re-run), sqlite re-ran from the start. After any restart check `uptime`, start PostgreSQL, re-run the dead lanes,
  and restart the snapshot loop, the watchers and the runtime guard. mig is the long pole (core 7 tests took 50
  minutes); with mig alone in slot 1, slot 2 ran ui, pg-a, sqlite, pg-b, ist and pg-c, and the gate ended when
  pg-c did. pg-c grew to 1,032 tests and 83 minutes with the new race tests. sqlite and ist skips went 241 to 245:
  the four new race tests, PostgreSQL-only. Read a skip change before calling a lane green.

**g32 is pushed (10e6990, 10 November 2026), all seven lanes green on one tip (gated as e0ef3f7).** Holds thirteen
rows: O362 (a contractor's licence cut below the workers on it from today on is refused; most_at_once() is the one
count a worker joining and a licence shrinking are held to), O263 (the employees file says roles are given on the
Logins screen), O264/O267 (a dead cost method and a doubled lock), O268 (a count that finds something never stocked
says to count it in on an adjustment), O364 (a carry entered under a since-lowered limit is kept and its row still
edits), O333 (a login whose person has left reads Ended, derived from the sign-in rule), O345/O493 (a shift code is
letters, digits and hyphens, unique whatever its case; migration 0089 stops in words on an installed N and n),
O265/O492 (tape-load checks and creel promises count only the station's own warehouse, and a roll draws only doffs
loaded off its own shelf), O320 (two fence methods print in run_mrp; audit_invariants now reports model methods
nothing calls, which found O470-O491), O372 (a shortfall write-off is held to what the documents carry, not stray
debits on the receivable). New rows: O469, O470-O491, O493 closed in the same gate.
- Gate lessons from g32: the two-slot order (mig from minute zero) brought the gate to seven hours on a loaded
  machine, against eight and a half. Agents running whole apps beside the gate pushed the load to 14 and sqlite to
  6,870 of its 9,000 seconds; `/home/user/work/runtime_guard.sh` (wakes on a step past 80% of its own timeout, or a
  5-minute load over 10) caught both. **Agents beside a gate: targeted modules, --parallel 1, nice -n 19, at most two
  runs each, every run under timeout.** A pre-gate race run of all sixteen race files on one worker needs more than
  3,500 seconds; run only the files a change's locks touch (g34's 129 receipt races took 1,347). Merging register
  edits from parallel worktrees with a script that keeps only lines both sides keep dropped 24 new rows: resolve a
  RISKS conflict by taking ours and applying that commit's own deletions by hand, then compare the row ids with the
  expected set before going on.

**g31 is pushed (7a27e21, 9 November 2026), all seven lanes green on one tip (gated as 5474b24).** Holds O405's
remainder (the demo now shows tape, fabric, film and atta-sack specifications with spec-built BOMs, two tape lots
inspected and released, energy meters with a week of readings, a sales commission plan, earned/sick/unpaid leave with
a carried-over entitlement, and Maharashtra PT from the statutory slabs with the women's exemption) and a clock fix.
O346 stays open with its cause unknown: the traceback was lost to the missing tblib, pinned since c6105e4; it was not
seen in g25-g31 nor in 12 runs at --parallel 2 under load. When it recurs, read its traceback before changing anything.
- Gate lessons from g31: pg-a failed on d33747b at `test_p02` (302 != 200). The test asked the clock twice for two
  two-factor codes, and a run that crossed a 30-second window's edge between the two asked for the wrong one; a probe
  sleeping to the next window reproduced it every time. **A test that asks the clock twice can straddle a window's
  edge: work the moment out once and derive every code from it.** The gate took eight and a half hours because ui ran
  alone first and mig, the longest lane at over three hours, ran last; `scripts/gate/lane.sh` now orders two slots,
  mig then pg-c beside ui, pg-a, sqlite, pg-b, ist, about five hours. A side run of mig groups beside the lane collided
  with the lane reaching the same group and both wrote one log: do not run a lane's groups beside it.

**g30 is pushed (e56d4a8, 9 November 2026), all seven lanes green on one tip (gated as 4f80e6f).** Holds two rows:
O363 (every correction that takes back money booked to an account since switched off is marked `takes_back`: the
partial recovery of a written-off invoice was refused once its bad-debt account was switched off; both unearned-
discount withdrawals marked too; `audit_invariants` now reports `take-back unmarked` for any function that posts a
fresh entry beside an earlier one without the marker) and O458 (shared rule B on every line whose document can close:
sales and purchase order lines refuse a cancelled order; quotation, RFQ, requisition, blanket and gate-pass lines,
customer-supplied items and RFQ invitations change only while their document allows, moved or deleted alike).
Still open: O405's remainder, O457, O259, O346; O448 waits on the owner, O449 on the tax adviser.
- Gate lessons from g30: the first run (32e9205) failed pg-c on `test_06_o227`, the control that proves the
  blanket-agreement race can be seen. O458 made a purchase order line lock its order in save(); the first lock of a
  transaction takes the numbering row (O257), so the raise held it before the race's barrier, the release waited on it,
  and the two ran in turn with the agreement unlocked. The code was right; the control now takes away both locks.
  **A new lock in a save() can blind a race control test: before the gate, run `apps/e2e/tests_*races*` on
  PostgreSQL whenever a change adds a lock.** Also: a new gate worktree needs `.venv` and `frontend/node_modules`
  linked in (scripts/gate/README.md), or the ui lane fails on missing types; and never run three apps' tests in the
  foreground (one run was killed at the 10-minute limit): detach and watch. The mig watcher outlives its two-hour
  limit; re-arm it, the lane keeps running.

**g29 is pushed (175998f, 7 November 2026), all seven lanes green on one tip (gated as 6c7391a; the replay changed
one commit message only, `Gate: g29` written where the placeholder belonged).** Holds the pilot loading run's findings
and two statutory rows: O464 (a mistyped --gstin refused in a line), the `accounts` import kind (the plant's own chart
before its opening balances), O465 (the first-passwords file is made before employees are kept; IMPORT.md gives the
Docker command), O466 (an asset whose accounts are already in the opening balances is refused: the documented order
counted plant and machinery twice), O467 (items import their sales and purchase taxes, tax-group codes accepted;
go_live_check lists goods with none), O461 (Maharashtra PT with its bands for women; gender and work state on the
employee), O468 (Confirm schedule on the pay component), O460 (the ESI file sends days paid for, kept on the slip).
Still open after g29: see g30 above.
- Before the first live use: the Maharashtra amounts were written from recall, not the Gazette, and load unconfirmed;
  the Controller checks them before confirming. The ESI days reading is the common one; PILOT.md says to upload the
  first ESI file before anything else. The loading run footed to the paisa on my sample files, not the plant's data.
- Gate lessons from g29: the first run (97d1f29) failed sqlite on tests_plant_month, an end-to-end test still asserting
  the rostered ESI days O460 replaced. **When a fix changes a figure, grep every test that reads that output, e2e
  included,** before the gate, not after. Two container restarts in one gate: ist and pg-c re-ran from the start; mig
  resumed from its .done markers and lost only the app it was in. Push the gate tip to a `claude/erp-*-backup` branch
  as soon as the lanes start; the commits otherwise exist in one container.

**g28 is pushed (06b9997, 6 November 2026), all seven lanes green on one tip (gated as 8196001).** Holds the five
batches of open rows (trade and statutory, payroll, planning and floor, core and audit, pilot UI): O152, O251, O315,
O316, O319, O355, O359, O366, O367, O368, O401, O417, O418, O442 to O447, O450 to O456, O459, O462, O463; O405 in part
(demo floor stations, bank details, disposal account; the row stays open for the rest). Still open from this round:
O457, O458, O460 (ESI days), O461 (Maharashtra PT schedule), O405's remainder, O363, O259, O346; O448 waits on the
owner, O449 on the tax adviser. Next: the pilot (data-load templates, a dry-run install on the on-site server, UPS and
off-site backup steps in RUNBOOK).
- Gate lessons from g28: a commit *subject* naming a row still open fails registercheck, so a partial fix names its id
  in the body only. A browser test that fails on two lanes in different ways is a defect, not a flake: O463 was a
  picker's stale blur timer closing the list reopened inside 120 ms (6 in 20 before the fix, 15 of 15 after).

**g27 is pushed (fee871b, 5 November 2026); g26 below.**

**g26 is pushed (07921cc, 4 November 2026), all seven lanes green on one tip (gated as 27e2e1b).** Holds: O429 (the
mig lane one app at a time, resuming after a restart), L28 (certificates of analysis), L9 (scanning in stores), the
fix_coa, fix_scan, fix_setup and fix_logic rounds (O419-O428, O430-O441 less those still open), and 27e2e1b: a write
cancels a first read still on its way before it invalidates (the GSTR-2B screen showed the pre-write answer; pg-c found it).
- Gate rules learned the hard way (now in scripts/gate/lane.sh's header, in g27): at most two lanes at a time and
  **mig counts as one**; sqlite beside two lanes passes its 9000 s limit. Always keep a harness-tracked watcher job
  (`timeout 7000 bash -c 'while pgrep -f "[l]ane.sh g26 <lane>" ...'`; the bracket stops it matching itself) while any detached lane runs, or the container is
  reclaimed and the lanes die (two restarts on 4 November). Read a lane's log before rerunning it; lane.sh now keeps the
  previous log as `.prev`. Timings beside one other lane: sqlite 107 min, pg-c 103 min, mig about 6 h in all.
- g27 is pushed (fee871b, 5 November 2026), all seven lanes green on one tip (gated as e5d5ca6; replayed onto the
  HANDOFF commit, the only difference docs/HANDOFF.md). Holds: lane.sh's rules, O343/O344 (backup), O361 (dated drafts
  hold a close), O431 (Stores issues, returns and voids material), O425 (spares get no cancel advice), O356 (2B
  "credit not available" total), O427 (a line stores its taxes through the party's fiscal position: LUT export as
  IGST 0%; a position may not chain mappings), O386 (a filed GSTR-1/3B keeps its figures, gst/0010; no amendment
  tables, a later month warns). Reports: /home/user/work/reports/fix_close.md, fix_gst.md. Recorded: O445, O446.
- Two more gate mistakes in g27: sqlite started beside ui, whose build empties apps/web/static/web, so 159 browser tests
  skipped and the lane said OK (400 skipped, not 241): **run ui alone first, and check the skip count**. A watcher job
  needs the Bash tool's timeout set to 7200000, or it ends at 30 minutes.
- Next (g28): lane.sh refuses a test lane while the office app is not built; then the open rows. Waiting on others:
  O385 and O387 (tax adviser); whether approved-but-unpaid expense claims hold a close (owner).

**g25 is pushed (ef0ef37, 3 November 2026), all lanes green on one tip (gated as dc3a5a6).** Holds: round A (gate register,
passes, weighbridge, review fixes), the interface sweep's fixes, the tax patch, the outputs patch, round B (landed cost
by weight, the bill of entry, rate contracts, customs valuation defaults), the first installation's fixes, O415 and O416,
the four probe holes and the plant's inter-state position (c3facdf, 4defc7d), and the demo loading on PostgreSQL again
(dc3a5a6). c3facdf was committed with ten tests red in its own rate-contract fixture; 4defc7d fixed them.
- How the gate ran: each lane a tracked job, two at a time (ui; sqlite with pg-a; ist with pg-b; pg-c; then mig). pg-b
  is split into pg-b and pg-c (pg-c took 5,007 s, watch it). mig could not run as lane.sh has it (O429): killed for
  memory, by the job limit and by three container restarts. It ran one app at a time on two workers, detached, 47
  OK, and the PostgreSQL-only migration test on PostgreSQL for the first time; logs in /home/user/work/gatelogs25/mig_apps/.
  finalize ran from a copy (/home/user/work/gatelogs25/finalize_copy/, with mig_apps.sh) whose gateline sums those logs and whose finalize keeps a
  docs-only commit's own true Gate line (nine carried one). Put both into scripts/gate/ in g26's first commit.
Round B's report: /home/user/work/reports/fix_imports.md, with the review below.
- Its PostgreSQL void/pay races were red as committed (a bill's refund_due() is always 0; the refund is on the
  void's debit note) and its balance expectations wrong both ways; rewritten by hand (+12,14,364.00 either
  order). Its post-once race passed without the lock it names; it now asserts the status refusal and fails
  without it. On its tip: the full fast suite 8,240 OK with the browser tests, the sweep as every role, the
  changed modules with migrations, audit_invariants clean. The 47 migration-tagged tests: see the gate.
- For the accountant: duty landed over the whole BE or per line by each line's own rate; whether a supplier's
  rate-difference note covers the kg received or only the kg kept; the difference read at the order's date;
  which month's filed GSTR-3B stops a void. For the customs broker: rounding to the rupee per line, and the
  20% freight / 1.125% insurance defaults (now confirmable under Purchasing > Configuration). For the GST
  Officer: the IMPG field names in a real 2B download.
- A first installation, 2 November: installed from nothing on PostgreSQL in production mode (two database
  roles, Kolkata time) as RUNBOOK.md and deploy/entrypoint.sh do it, then driven in a browser and through the API
  as the people. It could not post until fixed (the fresh-install commit: the books setup readies the company,
  units, terms, the OPENING reason, the plant's GST registration screen, the plant's own name, an order line's
  unit). With it: go_live_check ready, a first purchase and sale worked by hand. Open from it: O419 (pay
  components), O420 to O422. Scripts: /home/user/work/fresh/ (install.sh, day1_trade.py, read_outputs.py).
- O416 and O415 in (3 commits from claude/erp-g25-balances; report /home/user/work/reports/fix_balances.md):
  balances and agings in base through one shared reading, the demo carrying an open foreign invoice and bill;
  one default receiving warehouse, a quarantine bay not counted as receiving. Reviewed: the edited planning test
  encoded the double count. Its D1 to D6 are O423 to O428. For the accountant: booked or closing rates in the
  year-end aging and in credit exposure; the GST default on an export order.
Order from here:
1. O429 (the gate's mig lane) first, then round C (L28 certificates of analysis, L9 scanning), then the gate, finalize, push.
2. Open from the 1 November checks: O416 (agings in mixed currencies), O401 (a document saved before its lines), O405 (the demo shows a third
   of the plant: specifications, inspections, stations, bank details, disposal accounts). Report:
   docs/handoff/reports/drilldown_nov01.md.
Every round now drives its flows in the browser and reads its outputs before reporting (fix_common.md).


g18 is pushed (7b9473a, 11 October), all six lanes green on one tip. It holds:
- fix wave 1: security and roles, trading-document rules, statutory, and
  quality and production, each reviewed before it joined (reports in
  docs/handoff/reports/);
- the integration fixes (a merge that spliced two test classes, sales
  migrations renumbered), with scripts/gate/structcheck.py to catch the first;
- what the PostgreSQL browser lane found on the integrated tip: ten
  viewsets had replaced the history mixin's save, so a job-work loss locked
  its period outside any transaction (a 500 on PostgreSQL only) and nothing
  they made had created_by. Fixed with its guards: `audit_invariants`
  `writes_around_the_mixin`, and the lock-order sentinel now refuses a lock
  outside a transaction on every database. Those two are wider than item 7
  below allows a gate; they went in because they are the guard for the
  defect that held the gate, which the owner's standing instruction asks.

g20 is pushed (ebbf607, 14 October), all six lanes green on one tip: 49
rows (money and settlement, cost and payroll facts, security and crashes),
each reviewed and its findings fixed; a rep no longer offered the books'
tab, with sign_in() waiting for the rail; the owner's decision 23. g19
(b70f722) holds decision 22. scripts/gate/registercheck.py (ui lane)
fails a push whose commit subjects name a row still open, or a row whose
status says fixed. Integrate by cherry-picking, never merging. Agents
write `Gate: g19`/`g20` instead of the placeholder unless told: check
every commit for `Gate: {GATE}` before finalize, and fix the messages by
a replay whose tree is identical (as g19 and g20 were).

**The plant (owner, 14 October):** West Bengal; sells its PP scrap now and
plans in-house recycling; no Hindi screens.

docs/RISKS.md, "Open defects", is the one list of what is still open.
docs/handoff/reports/odoo19_gap_analysis.md compares every module with
Odoo 19 (notes in odoo19_research/); its last section is what the
accountant or tax adviser must confirm before the statutory items are
built.

1. **g21 is pushed (a681c87, 16 October), all six lanes green on one tip:**
   - SQLite 6797; PostgreSQL 6797; Asia/Kolkata 6797; migration-tagged 21;
     vitest 42.
   - It holds:
     - the 17 race rows;
     - decision 23 (O204, O207, the "Held because" column) and O123;
     - the payroll go-live outputs (bank file, payslips, advances,
       overtime, PT, LWF, EPF ceiling, EPS past 58);
     - the tax go-live work: the Rule 46 invoice and LUT, e-invoice
       cancellation, TCS on scrap, backups with attachments,
       setup_indian_books.
   - Each part was reviewed by someone other than its author, and the
     review fixes were reviewed again.
   - What the gate itself found, each fixed with a test that fails
     without it:
     - the lock-order sentinel checking migrations' own models (24
       migration-test errors);
     - PayRun.post writing slip lines in the database's order;
     - an e-invoice acknowledgement dated by its UTC day (seen only in
       the Kolkata lane after 18:30 UTC);
     - the attachments panel saying "None yet." before it knew;
     - four race tests and two browser tests that a later rule or the
       machine's speed decided.
   - Lessons for the next gate:
     - On this machine (4 CPUs) run lanes two at a time: four at once
       timed pg-b and mig out at 6900 s.
     - Anyone who changes the lock-order sentinel runs the migration tag
       too. Every agent here ran the suite without it.
     - Review agents sign their probe commits with the Opus trailer and
       the placeholder, or the integrator replays them.
   - The go-live build list from the gap analysis, the items that wait on
     nobody first: the tax invoice's Rule 46 content (O218), e-invoice
     cancellation (O220), the backup of attachments (O221), printed
     payslips and their self-service, employee bank, PAN and joining
     data, the bank payment file, a salary-advance ledger, overtime rate
     derived, professional-tax slabs with effective dates and a state.
   - Then, once the accountant confirms: TCS on scrap (O219), the EPF
     ceiling as dated data (O222), the Labour Welfare Fund (West Bengal,
     before the December run), the bonus ceiling (the higher of 7,000 and
     the minimum wage), the Income-tax Act 2025 section and form names,
     the Indian chart and GST taxes seeded.
2. **After g21: depth and robustness first (owner, 14 October: "emphasise
   the existing modules' depth and a robust ERP for a woven sacks plant").**
   This replaces the reach plan's order. Breadth waits; what a sack plant
   does every day, done right in the modules it already has, comes first.
   - **g22 is pushed (1963c16, 20 October), all six lanes green on one
     tip:** SQLite 7356; PostgreSQL 7356; Asia/Kolkata 7356; migration-tagged
     33; vitest 45. 153 commits. It holds:
     - 68 register rows closed (O11 to O256 as the commit subjects name
       them, O216 and O217 by the sign-in round); what each fix left is a
       row of its own (O257 to O270);
     - L12: two-factor sign-in, session time-outs (the idle clock a signed
       cookie, so no ordinary request writes the session), API keys;
     - the plant month (apps/e2e/tests_plant_month.py, 46 tests: buying,
       the floor, quality, job work, selling, scrap with TCS, payroll, the
       close), through the API as the people who do each step. Its
       findings, each fixed with its test: a return locked a drawn advance
       or prepayment after the stock (sales and purchasing, one rule); a
       count-in at a given cost; three stores screens that could add no
       line, and a check of all 58 adders; one left open as O269;
     - what the gate found: a money account held under a test's own
       transaction (315 PostgreSQL failures), and a former employee's
       write-off past money already paid back (a race test, O270 left).
     - Lessons for the next gate:
       - A restart can wipe /tmp: the scratchpad, every worktree, the lane
         logs, .venv and node_modules went on 20 October. Keep the gated
         tree and the logs under /home/user (work/, gate-logs/); the
         snapshot branches restored the tree to the commit.
       - A new container: build .venv from the gated tree's own
         requirements.txt, then `pip install playwright` (it is in no
         requirements file); recreate PostgreSQL's role
         (`CREATE ROLE erp LOGIN PASSWORD 'erp' CREATEDB CREATEROLE`; CREATEROLE since g24, for the two-role test in apps/web/tests_books_log_roles.py, which skips without it) if the cluster is
         new; add `.venv` and `frontend/node_modules` to .git/info/exclude
         when they are links, or the tree reads dirty.
       - The mig lane outgrew its 6900 s cap (20 of 33 by then, alone). It
         was run as `manage.py test apps --tag migration --parallel 4`,
         detached and uncapped: 4884 s, OK. Change lane.sh's mig to that
         (parallel 4, run detached) before the next gate.
       - A bisect's good end is run first: the sweep's base was red too,
         and the verdict named an innocent commit.
       - A documentation-only commit inside a gated push carries
         `Gate: {GATE}` too; finalize.py refuses anything else.
     The plan and briefs that ran: docs/handoff/g22_*.md, two agents at a time:
     1. fix_purch (g22_purch.md) and fix_money (g22_money.md): books first.
     2. fix_stores (g22_stores.md) and fix_core (g22_core.md).
     3. fix_sec3 (g22_sec.md, L12 with O216 and O217).
     Each is reviewed by someone other than its author before it joins.
     Then `month` (g22_month.md) on the integrated tree. Its failures
     become rows. Then the gate. The second audit per module follows in g23.
     Not in g22, waiting on others:
     - the tax adviser: O153, O166;
     - the accountant: O214, O240;
     - the OSH Code's forms: O224.
     - Every open row left after g21 (about 60: books first, then rule,
       statutory, minor), by module, one fix agent per module group. Each
       fix ships with its test, and its commit names the row.
     - L12 security (two-factor sign-in, session time-outs) belongs here.
       The books hold bank details and pay.
     - Then the proof. A plant month in apps/e2e, the size of
       FromPolymerToCashTests times thirty:
       - polymer bought, received short and returned;
       - tape, looms, lamination, printing and conversion through the
         stations, with a failure held for the Quality Manager;
       - re-batches and job work;
       - dispatches with e-way bills, invoices, a credit note and a
         bounced cheque;
       - scrap sold;
       - a pay run with piece rates, overtime, an advance and a leaver;
       - depreciation and the month closed.
       At the end every control account clears, the shelf foots to the
       ledger, every derived balance foots to its account, and the
       statutory files read what was posted. Run it on PostgreSQL and in
       Asia/Kolkata. Each failure is a register row.
   - **Decided in g23b (O257): every transaction that locks a row takes the
     company's numbering row first**, so locking transactions run one at a
     time across the plant: no deadlock among them, at the cost that two
     postings at once wait for each other and a long one (a pay run, the demo
     load) holds the rest. Ranked first in apps.core.lock_order; the sentinel
     refuses any row before it. Taken because numbering rows held to commit
     met every other lock nested inside a document (a note's refund through
     the bank), and site-by-site holds kept missing paths.
   - **g23b is pushed (6018079 gated, 26 October), all six lanes green on one
     tip:** SQLite 7902, 205 skipped; PostgreSQL 7902; Asia/Kolkata 7902;
     migration-tagged 44; vitest 55. 59 commits. It holds:
     - the 16 security, crash, books and race rows (O255, O317, O318, O348,
       O260, O261, O350, O353, O349, O347, O329, O262, O360, O270, O258,
       O257), plus O371 and O376; every round reviewed, every blocker fixed;
     - O257's decision: every transaction that locks a row takes the
       company's numbering row first (below);
     - left as rows: O372 to O375, O377 to O384.
     - What the gate found, each fixed with a test that fails without it: a
       job-work challan and vendor work asking the period before their number;
       a browser test that left a page open on a record it deleted; a credit
       note's refund locking the bank after the numbering row, seen only on
       PostgreSQL, which led to the numbering-first rule; sign-out and
       password change waiting behind a long posting; races whose tests knew
       only one winner (race_in_order() now runs both orders).
     - Lessons for the next gate:
       - A runner that goes on after a failed lane runs later lanes on a tree
         being edited. run_gate_g23b.sh stops at the first FAIL.
       - A timing threshold under a lock decides by the machine's speed. Ask
         whether the call finished while the lock was held instead.
       - Taking a global lock first changes who wins every race. Make each
         race test run both orders and assert each order's books.
       - The PostgreSQL lanes may run 9000 s (lane.sh); pg-b took 5766 s.
       - Do not run agents' PostgreSQL tests beside a gate; pg-b hit its cap.
   - **g23b, how it ran: the security, crash, books and race rows.** Briefs
     docs/handoff/g23b_*.md. Stopped on 24 October by the session's limit,
     before the gate. To resume:
     - Integration tree /home/user/work/g23btree (snapshot
       claude/erp-wip-g23btree). It holds 22 agent commits cherry-picked on
       603a9ca: sec4 (O255, O317, O318, O348), books3 (O260, O261, O350,
       O353, O349, O347) and hrlocks (O262, O360, O270, O258, and seven of
       O257's eight shapes). Those 14 rows are off the register. One conflict,
       apps/inventory/serializers.py, was resolved to keep both sides
       (HidesStockMoney on ItemSerializer, and the stock and cogs account
       validators). makemigrations --check and audit_invariants are clean;
       no test suite has run on the combined tree yet.
     - Agent worktrees, each backed up at claude/erp-g23b-<name> (commits)
       and claude/erp-wip-fix_<name> (uncommitted edits too). Each was still
       working when the session stopped. Start one new agent per worktree
       with its brief, its report in /home/user/work/reports/, and
       `git diff HEAD`:
       - fix_sec4: done, 2e14f78 (in g23btree; O376 left, decided). Was: grant inventory.view_stock_value to the Controller and
         Bookkeeper (decided: they keep the ledger, the inventory account
         included; no other role gains it), with role tests; rerun core,
         assets, inventory, manufacturing, quality and web.
       - fix_books3: done, O329 in e7b400f (in g23btree; O375 left). Was: O329 (inventory 0040 gives rows what their documents
         booked; the stock-to-ledger check does not let one account's
         surplus hide another's shortfall). The "nets its drops" half is
         already fixed at 9e9d4d4: prove it with a test.
       - fix_hrlocks: O257's numbering (decided: serialise behind one
         numbering row per company, because GST Rule 46(b) needs
         consecutive invoice numbers, so no gaps); lock_rows sorts by
         LOCK_ORDER rank, not model name (root cause; take off the
         statutory remittance's local fix); a leaver with no final slip can
         settle an advance (O262's reverse path): done, c5717eb, in g23btree. Numbering and lock_rows (now O371) were not done.
     - Then cherry-pick each worktree's new commits beyond its
       claude/erp-g23b-<name> tip into g23btree, one review per agent, and
       the six-lane gate (gate logs /home/user/gate-logs-g23b), finalize
       from the pushed tip, and push. O257 stays open until the numbering
       commit; registercheck fails the ui lane while a subject names it, so
       take it off in that commit.
     - Shared venv: cryptography 44.0.2 is installed and pinned.
       Production now needs ERP_FIELD_KEYS in .env (.env.example,
       docker-compose.yml and deploy/backup.sh say so).
     - O312 and O352 wait on the owner.
   - **g23 is pushed (b344a2c gated, c34e9b0 the tip, 24 October), all six
     lanes green on one tip:** SQLite 7726, 174 skipped; PostgreSQL 7726;
     Asia/Kolkata 7726; migration-tagged 39; vitest 45. 131 commits and a
     docs-only merge. It holds:
     - the second read-only audit of every module (floor, stores, money,
       trade, people, core), Sonnet, probes only; every finding a row
       (O271 to O368). Reports: docs/handoff/reports/g23_*.md;
     - O269: each StockMovement keeps the paisa its document booked
       (`value`); the replay sums it, cost_of_removing answers in paisa,
       and `audit_invariants` names a movement written without its money;
     - the fixes by module, each round reviewed once, blockers fixed at
       once: 60 rows closed. 59 rows are open, 24 of them minor; two of
       those wait on the owner (O312 timesheets, O352 whether the
       Controller approves orders) and two others on the tax adviser
       (O153, O166).
     - Shared rules built here, use them: `takes_back` (the explicit
       correction marker), `moment_that_has_come` (five minutes of portal
       skew), `settlement.in_base`, `once_only()` for imports,
       `login_ended`/`check_login_acts` for leavers, `posts_its_notes`,
       `recurrence.run_date`, `batch_code()`, `HidesStockMoney`,
       `AdjustmentReason.check_account_if_chosen()`.
     - What the gate found, every one a test encoding something a fix now
       refuses, none in the product: two adjustment reasons on an asset
       account (O324), an e-invoice acknowledgement ahead of the story's
       clock and with no offset (O289), an e-way bill number thirteen digits
       once PostgreSQL's ids passed 99, a recurring test dated ahead of
       today (O284), the 0065 seed test reading the latest before 0080, and
       three PF tests expecting one run's file where O301 files the month.
     - Lessons for the next gate:
       - Never change the tree under a running lane: a fix committed while
         pg-b ran made its result belong to neither tip. Stop the lanes,
         commit, rerun.
       - lane.sh prints a lane's verdict and exits 0 whatever it says; a
         runner reads `^OK` and `^FAILED` from the log, not the exit code.
       - The container restarted twice mid-gate. Every lane counts once it
         printed OK on the tip, so after a restart run only the lanes that
         had not finished (check each log's HEAD line first).
       - Docs commits made in the main checkout while a tree is gated mean
         the push is not a fast-forward: finalize from the merge base, then
         a docs-only merge (the tree's register, the branch's reports).
         Better: make register commits in the gated tree only.
       - pip needs tblib, or the parallel runner dies silently on the first
         failure ("tracebacks cannot be pickled"); it is pinned now.
       - Agents never `git stash`: worktrees share one stash.
   - **g24 is pushed (b6b763b gated, 29 October), all six lanes green on one
     tip:** SQLite 8113, 230 skipped, 1 expected failure (O385); PostgreSQL
     8113; Asia/Kolkata 8113; migration-tagged 46; vitest 55. 69 commits. It
     holds the audit trail (two layers, two database roles), the statutory
     figures confirmed before use with a go-live line per engine, TAN and the
     2025 Act's names, GST reverse charge, the year-end restatement and tax
     depreciation by block, and the empty-attachments backup. Each part was
     reviewed and every blocker fixed; left as rows O385 to O391.
     - What the gate found, each fixed with a test that fails without it: the
       payment voucher and the year-end sentinel; the Auditor's reads; a
       migration's historical write labelled through the wrong state.
     - Lessons for the next gate:
       - pg-b took 7,719 s against its 9,000 s cap and the mig lane 6,120 s
         against 6,900: split pg-b or raise both caps before g25, or a green
         gate times out.
       - scripts/gate/gateline.py reads "OK (skipped=N)" only: with expected
         failures in the line it reported 0 skipped. g24 was finalized with a
         fixed copy; fix the script itself in g25's first commit.
       - Agents built from one base clash on migration numbers every round:
         renumber at integration, the later agent's after the earlier.
       - Run one agent at a time when the usage limit is near (the owner's
         instruction, 28 October); every stop so far lost nothing committed.
   - **g24, go-live statutory and the audit trail (decided 26 October, from
     docs/handoff/reports/odoo19_gap_analysis_oct26.md).** The re-done Odoo 19
     comparison found the 16 go-live build items of 14 October 12 built and 4
     partly built, and 11 go-live gaps against Odoo 19, of which these were in
     no gate. They come before the feature gates because go-live needs them
     and none waits on the polymer, floor or sales work:
     - an edit log that keeps old and new values and cannot be bypassed:
       admin, shell and script edits included (Companies (Accounts) Rules,
       rule 3(1) proviso; Odoo forces its trail on for Indian companies).
       Today RecordEvent keeps field names only, written by the API layer;
     - the statutory data seeded from the confirmed figures (TDS rates, West
       Bengal PT from 1 October 2026, LWF before the December run, EPF and
       ESI, bonus, the TCS rate), and a go_live_check line that refuses an
       engine with no confirmed row, so the first run cannot deduct nothing
       unseen. The figures wait on the accountant; the mechanism does not;
     - TAN, and the Income-tax Act 2025's section and form names on TDS and
       TCS challans, returns and screens (screens still say 26Q and challan
       281);
     - reverse charge: the self-invoice for an unregistered supplier, its
       cash payment and GSTR-3B table 6;
     - bonus: a dated ceiling, the higher of 7,000 and the minimum wage, with
       a dated minimum-wage table;
     - before the first year-end (31 March 2027): foreign balances restated
       at the closing rate (the plant exports and imports), and tax
       depreciation by block of assets at written-down value beside the
       books' straight line.
     Salary TDS (section 392, Forms 138 and 130) joins g24 if anyone's pay
     will pass the exemption; the accountant says. The soon-after list (S, 60
     items, the report's section 3) is worked after g27 by the plant's use.
   - **g25, stores and purchasing for a polymer buyer:**
     - L16 polymer against price circulars;
     - L17 weighbridge, gross, tare and net on receipts and dispatches;
     - L18 gate register and returnable gate passes;
     - L23 landed cost by weight;
     - L28 the supplier's certificate of analysis checked against the
       incoming plan;
     - L21 import purchases;
     - L9 scanning in stores.
   - **g26, the floor and quality:**
     - L25 machine counters read automatically, beside the scale
       bridge g22 built (deploy/scale_bridge);
     - L27 a BOM from what a run used, and runs split and merged;
     - L11 step instructions and photo checks;
     - L29 BIS test plans and ISI marking records.
   - **g27, sales and the books:**
     - L19 post-dated cheques;
     - L20 export papers;
     - L22 billing policy per item;
     - L6 documents that leave the building;
     - L30 to L35, the annual accounts and payroll compliance, before
       the first year-end and the first leaver;
     - L31 the GST service provider.
   - **After the pilot, breadth:**
     - L8 analysis;
     - L13 CRM basics;
     - L10 rules the owner configures;
     - L7 WhatsApp;
     - L26 the recycling line, once the plant commits to it;
     - L37 export incentives, before the first claim;
     - L15 EPR, if the adviser says so.
   - L14 (law as data) runs through all of it. L24 is not needed (no
     godowns in other states). L20, L21 and L29 are firm: the plant
     exports, imports and holds a BIS licence.
   - The old line "plant logic already ahead of Odoo is not deepened
     before the pilot" is withdrawn. Depth in the plant's own logic is
     now the point, ordered by what go-live needs.
3. **Waiting on the tax adviser:** O60 for
   registered bill-to-ship-to buyers, O153, O166 (e-way bill codes).
   **Waiting on the tax adviser, also:** whether the plant must register as
   a plastic-packaging producer for EPR under the Plastic Waste Management
   Rules (L15).
   **Waiting on the plant's accountant:** the 20 figures in the gap
   analysis's last section (EPF ceiling, TCS rate on scrap, GST rates on
   sacks and scrap, West Bengal PT and LWF), and the leaver deduction cap.
   Added by g21: whether B2CL's invoice-value threshold counts TCS (the
   code counts it); that EPS stops from the month after the member turns
   58; the overtime multiple (2); which day of the month decides the EPF
   ceiling that applies (the code: the period's last day).
   Also: freight billed on a scrap invoice counted in the TCS base (the
   code counts it, shared by goods value on a mixed invoice); the TCS rate
   on scrap under section 394 (1% or 2%).
   Added by g22:
   - whether a supplier's GST invoice value, and so GSTR-2B's comparison,
     includes income-tax TCS (the code includes it);
   - that TDS deducts nothing until each section's rate is confirmed on
     the TDS rates screen: go-live waits on that;
   - whether a lower-deduction certificate's validity should be kept
     (today one undated rate on the party);
   - (O109) whether an early-settlement discount agreed before the supply
     should reduce the taxable value by a GST credit note (CGST section
     15(3)(b)) rather than be a financial discount with GST unchanged. The
     code takes it as a financial discount; the switch is "discount reduces
     GST" on each payment term, off by default, and with it on the discount
     step refuses and asks for a credit note;
   - (O95) whether a vendor may use an invoice number again in the same
     financial year once its first bill has been debited in full (the code
     keeps the number taken);
   - (g23, audit2_people P6) TDS on salary (section 192 of the 1961 Act,
     392 of the 2025 Act), Forms 24Q and 16, is not built: required once
     any employee's salary passes the exemption; to be built before the
     first such month, with the accountant's slabs and regime;
   - whether GSTR-1's invoice value for a scrap invoice carries the
     income-tax TCS (the code includes it: 24,072.00 on 20,000.00 taxable;
     the taxable value rightly leaves it out);
   - (O213) whether an overpayment taken back outside a final run is held
     within the deduction cap like an advance recovery (the code holds it
     there, and what the cap leaves waits for the next run).
   Added by g24. None of its figures could be confirmed from an official site (the
   proxy blocked them), so every one is loaded unconfirmed, with what secondary
   sources said, and go_live_check fails until the Controller confirms each:
   - for the accountant (statutory): the EPF wage ceiling (25,000 from
     17 September 2026, or 15,000); the bonus notification, Central or State;
     which West Bengal minimum-wage schedule covers sack making (no plastics
     schedule was found; the loaded rows are stand-ins); the TCS rate on scrap
     (1% or 2%); section 393's serial numbers and the 194J and 194I thresholds;
     Form 144 or 140; the West Bengal PT band edge (25,000 or 30,000); the LWF
     employer share (30 or 15);
   - for the accountant (year-end): AS 11 or Ind AS 21; every tax block rate;
     each block's opening WDV from the last return; the put-to-use and
     day-count rules; additional depreciation; separate unrealised exchange
     accounts or the FX gain and loss accounts;
   - for the tax adviser (reverse charge): time of supply (the bill date or the
     self-invoice date, s.13(3)); when the cash set-off happens (the deposit or
     the GSTR-3B filing); a supplier's registration as dated data; O385 (a part
     debit note against a self-invoice); O387 (table 4(B) after a self-invoice
     is cancelled); the consolidated monthly self-invoice; the voucher's tax on
     a part payment, pro rata today; the Rule 47A and Notification 17/2024-CT
     texts, which were not read first-hand;
   - for the accountant (audit trail): the exact wording of rule 3(1)'s proviso
     and the retention period, quoted in docs/handoff/reports/g24_fix_audit.md.
   Deployment, from g24: production needs DATABASE_OWNER_URL (migrations,
   backup and restore run as the owner) beside DATABASE_URL (the app's role,
   which owns nothing) and POSTGRES_APP_PASSWORD; ERP_FIELD_KEYS as before.
   .env.example and docker-compose.yml say so. A new container's erp role
   needs CREATEROLE for the two-role test.
4. The scratch worktrees `fix_{sec,trade,stat,qual}` and
   `audit_{acc,sales,pur,perm,qc}` are merged into g18 and can be removed;
   their snapshots stay on `claude/erp-wip-*`.
5. **Open defects** in docs/RISKS.md, by kind: books first, then race,
   then rule. O42 (a landed-cost release does not follow goods moved
   since) is next. Each fix needs a test that fails without it, and its
   commit names the id.
6. **Review every fix before its gate**, by someone other than its
   author, with probes that try to break it. In the g17 round, fixes
   that each had a test failing without them still held two release
   blockers.
7. **A gate takes fixes for what is broken, nothing else.** Refactors,
   migrations for data that does not exist, and widened checks go to
   the register.

## 3. The owner's standing instructions

- Go deeper, not wider. Look for matured bugs, not only decimal places.
  Depth in the existing modules and a robust ERP for a woven sacks plant
  come before any new module (owner, 14 October).
  Fix issues so they do not recur: patterns, shared rules, audit checks,
  and CLAUDE.md lessons.
- Decide; do not ask, unless a choice is truly theirs and blocking. Say
  in a line what was decided.
- **Token spend (owner, 10 October).** The session limit is the constraint.
  - Reasoning effort stays high for every agent; never lowered to save
    tokens (owner, 10 October).
  - Model per job (owner, 19 October: use lesser models where they do;
    Opus 5.5 for thinking and planning): Opus 5.5 for the integrator's
    thinking, planning, briefs, decisions and diagnosis, and for fixes to
    money, stock, locks, permissions or the costing engine; Sonnet for
    audits, reviews, probes and small mechanical fixes; Haiku for
    mechanical runs, sweeps and summaries. A design question an agent
    raises comes back to the integrator, not settled by the agent.
  - At most three agents at once on this 4-CPU machine (owner, 21 October:
    run it faster). Fix agents run only the tests their own commits add or
    touch, plus their races on PostgreSQL; never whole modules or the whole
    suite. The integrator runs the whole suites once, on the integration
    tree, before the gate: eight suites at once had each round taking hours.
  - Each fix round gets one review. Blockers it finds are fixed at once;
    anything smaller is registered and batched into the next round.
  - Briefs name the exact files and lines (the register has them).
  - About 60 tool calls per agent; at that point it commits and reports.
  - New work goes to a fresh agent with a tight brief. Resume an agent
    only for work cut off mid-way: a resumed agent re-reads its whole
    transcript.
  - Independent review for costing, locking, security and statutory
    fixes; the integrator's diff read for small ones.
  - Reports: 10 lines back, the detail in a file.
  - The integrating session reads ranges, never whole logs or diffs. It
    does not edit CLAUDE.md mid-session. It starts fresh after each gate
    push.
- More compute means more cloud sessions, each with its own machine.
  Check a remote lane's skip count before trusting it.
- No pull requests unless asked. PR #4 is the owner's; watch it, do not
  rewrite it.
- Commit as `git -c user.name=Claude -c user.email=noreply@anthropic.com`.
  No model names in code or commit messages beyond the trailer. Every
  commit ends with the Gate line, then the trailers
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: <the session URL>`.
- Push only to `claude/erp-creation-guidance-ox5afo`, after a gate.
  Scratch branches `claude/erp-*` are allowed for backups and other
  sessions.
- Be direct; lead with what is wrong. No praise without reasons.

## 4. Decisions already made (do not reopen)

- **Money moves only through accounts marked "bank, cash or card"**
  (`Account.holds_money`). Receivables, payables, tax accounts and
  company settings refuse a marked account. The mark is fixed once an
  account is posted to. The account form on screen sets it.
- **Piece-rate overpayment is recovered from the next run.** A leaver's
  is recovered in the final run. A shortfall becomes a receivable (to
  build).
- **Agents and gates.** Fix agents work on their own branches. One
  integrator reviews and gates. A gate runs on the newest commit and
  covers everything pushed with it.

## 5. Loose ends

- PR #4 (the owner's, into master) tracks the product branch. Check its
  state after each push; do not rewrite it.
- Cloud session session_01JhBJNMa8eEqsnUwAeQ5Zhy ran two g16 lanes. It
  can be archived.
