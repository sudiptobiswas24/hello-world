You are rereview: a second adversarial, read-only review for gate g21 of a Django 5.2 + DRF ERP for a woven polypropylene sack plant in West Bengal. Repository: /home/user/hello-world. Read its CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

Make a worktree of the tip: `git -C /home/user/hello-world worktree add --detach $SP/rereview 257c44e`. Link `.venv` and `frontend/node_modules` from /home/user/hello-world. Never edit product code. Write probes and commit only them, in your worktree, unpushed. End your commit message with exactly `Gate: {GATE}` (literal), a blank line, then these two lines:
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK
Name no model anywhere else.

**What you review.** Only the review fixes, which are new code written to make an earlier review's probes pass. They are not re-checked yet. They are on money and stock paths.
- **Recall review fixes:** 8c5db57, d7242d8, a550ec6, d2d336c. The earlier review is $SP/reports/review_qrecall.md. The fixes are described in $SP/reports/fix_qrecall.md, under "Review fixes".
- **Payroll review fixes:** 2852756, e015f7f, 750df5d, 5c087d9, d2969f9, 11d1c12, 257c44e. The earlier review is $SP/reports/review_people.md. The fixes are described in $SP/reports/fix_people.md, under "Review fixes".

Do not re-find what the earlier reviews found. Ask what the fix broke or left.

**1. Regressions.** Did a fix close a door by locking out a legitimate path? Check that:
- an Inspector can still correct a typed pass;
- the Quality Manager can still void a mistaken reject;
- a returned transfer can still be re-paid;
- a run voided and posted again still works;
- a leaver's final run still recovers an advance.

**2. The new rules' own mirrors (audit shape 1).**
- *"Only the Quality Manager lifts a hold".* Test every path that lifts one: void, a new pass, a concession, a re-batch of a held lot, a split, the admin, a bulk action.
- *"PT, LWF and an instalment once a month".* Test:
  - a run in one month paying a slip dated in another;
  - an off-cycle or final run;
  - a run voided after a later run took the balance (what does the later run's register say, and does the month foot?);
  - two runs posted at once on PostgreSQL.
- *"A recovery credited to the posted advance payment's account".* Test a payment through a bank later unmarked as money (O16), and a payment voided and re-made.

**3. The deadlock the author flagged and did not prove.** fix_people.md "Review fixes" item 10 says `Payment.void` locks the payment, then the bank-file void check locks the file or slip. Meanwhile `pay` and `mark_paid` take file, then run, then slips, then payment. Prove it or refute it with `race()` on PostgreSQL. Use `DATABASE_URL=postgres://erp:erp@localhost:5432/rereview`, and drop test_rereview first. Only a deadlock or a 500 counts as a finding: a worded refusal does not.

**4. The recall's lift under the post's locks (a550ec6).** Lock order between a lift running `on_commit` and a new verdict on another source of the same lot. Run it on PostgreSQL. Is the lift still kept out of the verdict's transaction, and is the key order kept?

**5. One walk shared by the gate and trace (a550ec6).** Check that trace's recall report (`manufacturing/trace.recall`) gives the same lots as before on a plain tree, and stops honestly ("not followed") on a loop.

**Method.**
- One probe per claim, stated as a fact about money or stock, with PASS or FAIL for each.
- Number the findings. For each, give file:line, a scenario with names and figures, and whether it blocks the gate.
- A finding needs a probe that fails on the tip. List suspicions without one separately, as unproven.
- Run audit_invariants and makemigrations --check, and say what they print.

**Budget and report.** About 60 tool calls. Report to $SP/reports/rereview.md, and send 10 lines back: findings by severity, blockers first.
