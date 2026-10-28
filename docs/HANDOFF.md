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
   - **g24, in flight (started 26 October):** three agents, briefs
     docs/handoff/g24_{audit,statutory,rcm}.md, base d76ef6f, worktrees
     /home/user/work/fix_{audit,statutory,rcm}, integration tree
     /home/user/work/g24tree, gate logs /home/user/gate-logs-g24. The year-end
     items (closing-rate restatement, tax depreciation at written-down value)
     follow as a second round, once the audit trail is in. Register and
     handoff commits are made in the integration tree only.
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
