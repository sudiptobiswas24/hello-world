# g22 brief: review_purch, an adversarial, read-only review of fix_purch

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

## Setup
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/review_purch 674ac8a`.
- Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only those, unpushed.
- End your commit message exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`.
  Name no model anywhere else.

## What you review
fix_purch's 10 commits, e73428e..674ac8a.
- The author's report: $SP/reports/fix_purch.md.
- The brief: docs/handoff/g22_purch.md.
- Already known, so do not re-find: an unconfirmed TDS rate deducts nothing. It is being decided as a refusal at posting.

The plant buys PP granules, masterbatch and stores, some imported. Its accountant files GSTR-2B matching, TDS (Form 140 / 26Q) and the MSME return. Lead with what puts a wrong figure in the books, in TDS, or in a return.

1. **O233, dated TDS rates.**
   - A rate change on the 1st and mid-month.
   - A bill drafted before a change and posted after.
   - A rate edited or deleted after a deduction used it: what do the challan, the return and the bill say?
   - The no-PAN rate.
   - The threshold, per year and per bill, across a rate change.
   - The data migration on old rows (MigrationExecutor).
2. **O234, TCS collected by a supplier.**
   - On a bill, its debit note, a part payment, and a prepayment.
   - Does the TCS receivable foot to the bills, and does the bill total match the supplier's to the paisa?
3. **O91 and O99, the match and approval.**
   - Discount, tolerance, a re-price after approval, an agreed price changed after the order.
   - O167, now shared with sales: does every path that changes a confirmed order on either side ask the policy again? Lines added, removed, re-priced, re-discounted, by API and by admin.
4. **O95, invoice numbers.**
   - Case, spaces, `-`, `/` and `.`, Unicode look-alikes, across a financial year boundary (31 March / 1 April), two vendors, a void then re-entry.
   - The migration's collision report.
5. **O154, the supplier's credit-note number.** Set once, by whom, then changed (refused), and read by GSTR-2B matching.
6. **O188 and O189, MSME and due dates.** A part settled by a debit note, by TDS, by a payment, a delivery objected to and then accepted. Days late and the 45-day rule.
7. **O211, returns in posting order.** A return between two bills, two returns, a return voided.
8. **O190.**
9. **O227, refuted.** Is the refutation's race test sound: does it fail when it should?
10. **Who may.** Test as the roles over the API (apps/core/tests_roles.py): AP Clerk, Purchasing Clerk, Controller, Stores Manager. Then the screens in the browser.
11. **Races** with `race()` on PostgreSQL (`postgres://erp:erp@localhost:5432/review_purch`; drop test_review_purch first):
    - two bills with one invoice number at once;
    - a TDS rate confirmed as a bill posts;
    - a debit note's credit-note number set twice at once.

## Method
- One probe per claim, stated as a fact about money or a document, with PASS or FAIL per check.
- Number the findings. For each: file:line, a scenario with names and rupees, and whether it blocks.
- A finding needs a probe that fails on the tip. List a suspicion without one as unproven.
- Run audit_invariants and makemigrations --check on the tip and say what they print.

## Budget and report
- About 80 tool calls. Run long jobs in the foreground with a long timeout.
- Report to $SP/reports/review_purch.md, and send 10 lines back: findings by severity, blockers first.
