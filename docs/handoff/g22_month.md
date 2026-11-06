# g22 brief: month: a plant month replayed end to end

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it, and .claude/skills/audit/SKILL.md.

## Setup
- Base: the commit your prompt names.
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/month <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 150 tool calls.
- Report: $SP/reports/month.md.

## The job

**You write tests, not product code.** apps/e2e/tests.py FromPolymerToCashTests runs one cycle. Write `apps/e2e/tests_plant_month.py`: one month of a woven-sack plant in West Bengal, through the API as the people who do each step (apps/core/tests_roles.py), not as a superuser and not by model calls. Use model calls only where no screen exists, and list each one.

**Work the month out first.** Put it in a plain-Python script in calc_month/: every quantity, cost, tax and balance, with the refusals listed before the happy path. The test's expected numbers come from that script, never from the output.

## The month (October 2026; the plant's day in Asia/Kolkata)

1. **Polymer bought.**
   - PP granules from a Gujarat supplier (IGST) and masterbatch from a West Bengal one (CGST+SGST), against price circulars where the code has them.
   - One receipt short, one rejected at inspection and returned, landed freight spread over the receipts.
   - Bills matched, TDS where it applies, one bill paid by NEFT, MSME days counted.
2. **Tape, looms, lamination, printing, conversion.**
   - Through the stations, with weights booked at the scale endpoint, piece work for weavers, and scrap and regrind.
   - One failure held for the Quality Manager and decided.
   - One recall of a tape lot after fabric was woven from it.
3. **Job work and re-batches.** One job-work challan out and back, one re-batch.
4. **Selling.**
   - Dispatches with e-way bills, invoices with IRNs, one export under LUT in USD.
   - A credit note for a return.
   - A customer's cheque deposited, matched on the statement, bounced, voided, and its return matched.
   - One advance on an order, drawn down.
5. **Scrap sold** with TCS, the month's TCS paid by challan.
6. **People.**
   - A pay run with monthly staff, daily-wage shift workers and piece-rate weavers.
   - Overtime, an advance recovered, PT and LWF where the month asks, EPF with one member past 58.
   - One leaver's final settlement.
   - The bank file written, paid, one transfer returned.
7. **Close.**
   - Depreciation charged.
   - The GST returns (GSTR-1, 3B) and the TDS/TCS returns built.
   - The month closed.

## What must be true at the end

Each item is its own assertion, named for the fact it protects.
- Every control account is at the balance the month leaves on it, worked out in the script:
  - GRNI;
  - customer deposits;
  - net pay payable;
  - TDS and TCS payable;
  - GST output and input;
  - FX;
  - suspense;
  - work in progress.
  An account whose purpose is to clear (GRNI after receipt and bill, a returned cheque) is at zero.
- The shelf foots to the inventory account, item by item and in total.
- Every derived balance foots to its account:
  - receivables and payables by party;
  - aging;
  - the asset register;
  - the advance ledger.
- Each statutory file reads what was posted, not today's settings: the ECR, the PT and LWF registers, GSTR-1/3B, the TDS and TCS returns.
- The trial balance balances, and the P&L's profit equals the movement in equity.

**Run it** on SQLite, on PostgreSQL (`postgres://erp:erp@localhost:5432/month`) and with `DJANGO_TIME_ZONE=Asia/Kolkata`.

**A failure is a finding.** Do not fix the product.
- Each failure goes in your report as a register row: id-less, with file:line, the scenario with its numbers, and its kind.
- Mark the test `expectedFailure` with the row's words, so the gate stays green and the defect stays visible.
- Never adjust an expected number to match output (mistake 7). Work out which side is wrong, and say how you know.
