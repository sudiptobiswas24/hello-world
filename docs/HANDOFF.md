# Handoff — start here

This is an ERP for a woven polypropylene sack plant in India: Django 5.2,
DRF, React, Odoo-like. The owner wants every module as deep as Odoo's,
and correct before wide. This file says where things stand and what comes
next. It was last brought up to date on 11 October 2026, after g18, so
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
   - **g22, next: robust. The register to zero, then proof on a month.**
     In flight from 16 October. Briefs in docs/handoff/, two agents at a time:
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
     - A second read-only audit per module (Sonnet, probes, the audit
       skill) runs against the fixed tree. The first round found 40
       defects with a green suite.
   - **g23, stores and purchasing for a polymer buyer:**
     - L16 polymer against price circulars;
     - L17 weighbridge, gross, tare and net on receipts and dispatches;
     - L18 gate register and returnable gate passes;
     - L23 landed cost by weight;
     - L28 the supplier's certificate of analysis checked against the
       incoming plan;
     - L21 import purchases;
     - L9 scanning in stores.
   - **g24, the floor and quality:**
     - L25 machine counters read automatically, and O225 the scale
       program;
     - L27 a BOM from what a run used, and runs split and merged;
     - L11 step instructions and photo checks;
     - L29 BIS test plans and ISI marking records.
   - **g25, sales and the books:**
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
     step refuses and asks for a credit note.
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
  - Model per job: Sonnet for audits, reviews and probes; Opus only for
    fixes to money, stock, locks or permissions; Sonnet for small
    mechanical fixes.
  - At most two agents at once.
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
