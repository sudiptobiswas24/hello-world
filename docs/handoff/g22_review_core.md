# g22 brief: review_core, an adversarial, read-only review of fix_core

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

**Setup**
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/review_core 5bf7ddb`. Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only those, unpushed.
- The commit message ends exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`
  Name no model anywhere else.

**What you review:** fix_core's 17 commits, 49459e6..5bf7ddb.
- The author's report: $SP/reports/fix_core.md.
- The brief: docs/handoff/g22_core.md.
- Known; do not re-find:
  - O226 not done;
  - the three instances the widened audit checks name (StockCount.post, GoodsReceipt._restore_components, WorkOrderOperation._check_only_the_assignment_moved).

**Lead with** what pays a person the wrong money, posts the books wrong, lets a correction be dated anywhere, shows a rep what they may not see, or makes the audit pass what it should report.

1. **O213, a take-back for earlier runs outside a final run.** Decided by the integrator: it stays within DEDUCTION_CAP, and the rest waits for the next run.
   - Work it out in a plain-Python scenario first, for a loom weaver on piece rates, a shift worker and a salaried employee.
   - Check:
     - PF and ESI wages, and the slip's net pay;
     - what carries to the next run, and that the next run takes it;
     - a leaver's final run;
     - a run voided after it took part, and again after it took all.
   - Does the employee receivable or the take-back foot to the slips after every step (audit shape 4)?
2. **O215, a leaver's shortfall settled.**
   - Settled by a receipt, part by receipt and part by write-off, or by write-off only. Then the receipt voided, and the write-off voided.
   - Does the receivable clear and foot? Who may write off, and is the reason required?
3. **O15, correction dates.**
   - For each step moved onto `correction_date()`, try a correction dated:
     - before the document;
     - in a closed period;
     - in the future;
     - on the same day.
   - The three left excepted: is each reason true? Read each method.
4. **O32 and its follow-up, O176, O197.**
5. **O130, `scoped()`.** Rows that name no party, as a rep. Can a rep now see rows of parties they do not carry, through a null path or a mixed queryset? Test as the Sales Rep and the AR Manager over the API (apps/core/tests_roles.py).
6. **O136.** One opportunity, one order, in the database. Test the migration's clash report with MigrationExecutor.
7. **The audit checks: O246, O40, O46, O212, O228.**
   - For each widened check:
     - plant an instance and show it is reported;
     - plant a near-miss and show it is not.
   - A check that reports nothing is no proof it sees anything.
   - O228: a savepoint rolled back inside a held transaction, then a lock in the other order. Is the false positive gone, and is a real inversion still caught? Raw SQL FOR SHARE.
8. **Races** with `race()` on PostgreSQL (`postgres://erp:erp@localhost:5432/review_core`; drop test_review_core first):
   - a shortfall receipt and a write-off at once;
   - a leave cancelled as the run posts.

**Method**
- One probe per claim, stated as a fact, with PASS or FAIL for each check.
- Number the findings. For each, give file:line, a scenario with names and rupees, and whether it blocks.
- A finding needs a probe that fails on the tip. List a suspicion without one as unproven.
- Run audit_invariants and makemigrations --check, and say what they print.

**Budget and report**
- About 80 tool calls. Run long jobs in the foreground with a long timeout; restarts are frequent here.
- Report to $SP/reports/review_core.md, and send 10 lines back: findings by severity, blockers first.
