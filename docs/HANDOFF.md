# Handoff — start here

This is an ERP for a woven polypropylene sack plant in India: Django 5.2,
DRF, React, Odoo-like. The owner wants every module as deep as Odoo's,
and correct before wide. This file says where things stand and what comes
next. It was written at the end of a long session on 8 October 2026, so
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

g17 is pushed (b1d03ed). It holds:
- the six fix branches, with the duplicated rules merged;
- the review of those fixes, and fixes for what the review found (two release blockers among them);
- the open-defects register.

findings.md says what each fix branch held. docs/RISKS.md, "Open
defects", is the one list of what is still open.

1. **Audit round 2 is done (9 October):** accounting with GST, sales,
   purchasing, permissions, quality and CRM. It found 91 defects, each
   proven by a failing probe, now registered as O49-O123. The probes are
   in the scratch worktrees `audit_{acc,sales,pur,perm,qc}`. If those are
   gone, each row names its probe class, which can be rewritten from the
   row.
2. **Fix wave 1, to be gated as g18, was started on 9 October.** Four
   agents worked on worktrees `fix_{sec,trade,stat,qual}` at ef7c0c3,
   under the rules in docs/handoff/fix_common.md:
   - security and roles: O83-O88, O119, O120;
   - trading-document rules: O65-O68, O71-O73, O82, O90, O92, O96,
     O106, O107;
   - statutory: O50, O55, O57-O62, O102-O104;
   - quality and production: O110-O113, O115-O118, O121, O122.

   Every ten minutes the worktrees, uncommitted edits included, are
   snapshotted to `claude/erp-wip-<worktree>` (CLAUDE.md, "Snapshots"):
   the fix_* worktrees, and the audit_* worktrees with their probes. If
   the container was lost, restore from there before anything else.

   Each agent's work is reviewed by the auditor who found the defects
   before it joins the gate. If the session stopped part-way, look for
   these worktrees and their commits first (CLAUDE.md, "Interrupted
   work").

   Fix wave 2 (g19) takes the rest: exchange and settlement, pricing,
   GRNI residues, commission, the remaining races, the inert features,
   and the older rows (O2, O42, O48, O28 and others).

   Owed by the owner: O114, a held lot's transfer. Owed by the tax
   adviser: O60 for registered bill-to-ship-to buyers.
3. **Open defects** in docs/RISKS.md, by kind: books first, then race,
   then rule. O42 (a landed-cost release does not follow goods moved
   since) is next. Each fix needs a test that fails without it, and its
   commit names the id.
4. **Review every fix before its gate**, by someone other than its
   author, with probes that try to break it. In the g17 round, fixes
   that each had a test failing without them still held two release
   blockers.
5. **A gate takes fixes for what is broken, nothing else.** Refactors,
   migrations for data that does not exist, and widened checks go to
   the register.

## 3. The owner's standing instructions

- Go deeper, not wider. Look for matured bugs, not only decimal places.
  Fix issues so they do not recur: patterns, shared rules, audit checks,
  and CLAUDE.md lessons.
- Decide; do not ask, unless a choice is truly theirs and blocking. Say
  in a line what was decided.
- **Token spend (owner, 10 October).** The session limit is the constraint.
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
