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

1. **g21, next.** Its tree is built from g20's pushed tip.
   - fix_race's fifteen commits (`$SP/fix_race`, from d4f1827): 15 of 17
     race rows, the sentinel seeing update/save/delete and joins, the whole
     suite clean with it raising; O39 closed as not a defect (every outcome
     matches a serial order), say so in its row. Its review is still to do.
   - fix_qrecall (brief docs/handoff/g21_qrecall.md): decision 23 (O204,
     O207, the "held because" column), and O123.
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
2. **After g21: the reach plan** (docs/RISKS.md "Later items", L6 to L15,
   decided by the owner on 14 October). g22: documents that leave the
   building (L6) and security (L12); g23: analysis (L8) and CRM basics
   (L13); g24: scanning in stores (L9) and the shop floor (L11); g25:
   rules the owner configures (L10) and WhatsApp (L7); L14 runs through
   all of them. Remaining rule, minor and statutory rows ride along with
   whichever round touches their module. Plant logic already ahead of
   Odoo is not deepened before the pilot: defects only.
   The plant's own practices, L16 to L36 (second pass, 14 October), join
   by module: L30 to L35 with the go-live statutory work (the annual
   accounts and payroll compliance before the first year-end and the
   first leaver), L16 to L19 and L22 to L23 with g22, L25 to L28 with
   g24, L31 with L7. The plant exports, imports and holds a BIS licence
   (owner, 14 October): L20, L21 and L29 are firm. L24 is not needed (no
   godowns in other states). L37 (RoDTEP, drawback, Advance Authorisation,
   EPCG) is not used yet but soon: build it after go-live, before the
   first claim.
3. **Waiting on the tax adviser:** O60 for
   registered bill-to-ship-to buyers, O153, O166 (e-way bill codes).
   **Waiting on the tax adviser, also:** whether the plant must register as
   a plastic-packaging producer for EPR under the Plastic Waste Management
   Rules (L15).
   **Waiting on the plant's accountant:** the 20 figures in the gap
   analysis's last section (EPF ceiling, TCS rate on scrap, GST rates on
   sacks and scrap, West Bengal PT and LWF), and the leaver deduction cap.
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
