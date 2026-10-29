# g22 brief: review_money: adversarial, read-only review of fix_money

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

## Setup
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/review_money d554c46`.
- Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only them, unpushed.
- Your commit ends exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`.
  Name no model anywhere else.

## What you review
fix_money's 15 commits, e73428e..d554c46 (`git log --oneline e73428e..d554c46`). The author's report is $SP/reports/fix_money.md. The brief was docs/handoff/g22_money.md. Known and not to re-find:
- the debit note on a scrap invoice collecting no TCS;
- O244's two unprobed paths;
- O209's purchasing side.

## The plant
A woven-sack plant in West Bengal: domestic and export sales, scrap with TCS, advances, job work taken in. Lead with what puts a wrong figure in the books, on a document a buyer or a GST officer reads, or in a return.

1. **O209, allocations.** Re-point, delete and re-add allocations in every order, across currencies with a rate change. Does the control account clear and the FX account hold only what each side booked, rounded (CLAUDE.md "Numbers")?
2. **O230, a bounced cheque.**
   - Deposited, matched, bounced, voided, the return line matched; then the statement closed.
   - Across two statements and two months.
   - A void dated before the deposit's statement.
   - Unmatching each line again.
   Does the reconciliation foot at every step?
3. **O243, TCS on a scrap advance.**
   - An advance taken, drawn down across two invoices, part refunded, an invoice credited, the advance voided.
   - Is TCS collected once, given back in proportion, and at the rate that stood on the receipt's day?
   - Does the TCS payable account clear when the challan pays the month?
4. **O235, the sales debit note.**
   - Check its print against CGST Rule 53, read from what it posted.
   - In GSTR-1 (CDNR, CDNUR), is it the right section and sign, in its own month and the invoice's?
   - Check the e-invoice payload (DBN) against the schema's required fields.
   - Its reverse: a credit note on a debit note.
   - A debit note on an export under LUT.
5. **O238, receipt and refund vouchers.** Rule 50 and 51 content, the GST on a job-work advance and its reverse on refund, and in GSTR-1 (advances, Table 11).
6. **O109, settlement discounts.**
   - Taken within and outside the days, on a part payment, on both sides, by whom.
   - The GST switch off and on: what posts, and does the input/output tax move only when it should?
7. **O77.** Credit limit across currencies and a rate change.
8. **O24.** A payment through an EEFC account in another currency, refused in words.
9. **O245.** The TCS return and a challan after the section is edited.
10. **Who may.** Test as the roles over the API (apps/core/tests_roles.py): Controller, AR Manager, AP Clerk, Sales Rep, and anyone else who reaches these endpoints. Then the new screens in the browser (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium, `npm run build` first).
11. **Races** with `race()` on PostgreSQL (`postgres://erp:erp@localhost:5432/review_money`; drop test_review_money first):
    - a discount taken as the payment is allocated;
    - an advance drawn down by two invoices at once;
    - a statement line matched as its payment is voided.

## Method
- One probe per claim, stated as a fact about money or a document, with PASS or FAIL per check.
- Number the findings. For each, give file:line, a scenario with names and rupees, and whether it blocks.
- A finding needs a probe that fails on the tip; list suspicions without one as unproven.
- Run audit_invariants and makemigrations --check on the tip, and say what they print.

## Budget and report
- About 80 tool calls. Run long jobs in the foreground with a long timeout.
- Report to $SP/reports/review_money.md, and send 10 lines back: findings by severity, blockers first.
