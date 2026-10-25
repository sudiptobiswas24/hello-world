# g23b brief: review_books3, an adversarial, read-only review of fix_books3

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

**Setup**
- Worktree: `git -C /home/user/hello-world worktree add --detach /home/user/work/review_books3 cd30628`
  (never under /tmp). Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only those,
  unpushed. Never `git stash`.
- The commit message ends exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`
  Name no model anywhere else.

**What you review:** fix_books3's seven commits as integrated in cd30628 (subjects naming O260, O261,
O350, O353, O349, O347, O329). The author's report: /home/user/work/reports/fix_books3.md. The brief:
docs/handoff/g23b_books.md. Known; do not re-find: O375 (0040 rows sharing a reference keep the replay).

**Lead with** what posts the books wrong, leaves a control account with a balance, or lets a document
and its ledger disagree.
1. **O260 `settlement.booked_by`.** Every reader of "what a document booked": does each ask it? A credit
   note with TCS, a give-back, a void of each. Foreign currency and rupees.
2. **O261 a note partly absorbed.** The author counts the absorbed part at the document's remainder.
   Try: the note absorbed before the payment, not after; two notes on one invoice; a note partly
   absorbed then the refund voided; the debit-note mirror. Does AR (and AP) foot to zero every time?
3. **O350 commission.** A payment that covers TCS only, an over-payment, a refund, a credit note after
   commission was earned, across two periods.
4. **O353 capitalising with a waiting draft.** The draft then deleted, then posted, then a second draft.
   Does the asset's cost and the bill line's account foot?
5. **O349.** Work the -3.17 by hand and show it is gone for standard, average and FIFO, and for a
   return voided.
6. **O347.** Mirror of the inventory-account rule: create, edit, API, admin, the import path, and an old
   item already on a wrong account posting.
7. **O329.** MigrationExecutor forward, back and forward on PostgreSQL too; the health check with three
   accounts, one surplus and two shortfalls.

**Method**
- Work every number in a plain-Python script in /home/user/work/calc_review_books3/ first.
- One probe per claim, stated as a fact, with PASS or FAIL for each check. Number the findings; for
  each give file:line, a scenario with rupees, and whether it blocks. A finding needs a probe that
  fails on the tip; a suspicion without one is listed as unproven.
- Races on PostgreSQL with `race()` (`postgres://erp:erp@localhost:5432/review_books3`; drop its test
  databases first): a refund and a note's absorption at once.
- Run only your probes and the modules they touch (fastsettings, --exclude-tag migration), plus
  audit_invariants and makemigrations --check; say what they print.

**Budget and report**
- About 80 tool calls. Report to /home/user/work/reports/review_books3.md, and send 10 lines back:
  findings by severity, blockers first.
