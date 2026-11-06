# g21 brief: fix_people — go-live payroll output and master data

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names, not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_people <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Budget: about 170 tool calls. Work in the order below; at the budget,
  commit what is done and report what is left.
- Report: $SP/reports/fix_people.md.
- Background: docs/handoff/reports/odoo19_gap_analysis.md ("People") and
  its notes, odoo19_research/hr_payroll.md. The plant is in West Bengal.

Every figure the law sets (a ceiling, a slab, a contribution, a rate) is
DATA with the day it applies from, entered on a screen and kept on each
pay run as it stood (audit shape "a setting read live where a fact should
be kept"). Build the mechanism; seed nothing as if it were confirmed. Where
a default is needed, use what the code uses today and say so.

## In order

1. Employee master data the bank and the returns need: bank account
   (holder, number, IFSC), PAN, date of birth, and whatever the existing
   statutory files read and cannot find. Validated (IFSC shape, PAN shape),
   readable only by those who run payroll (test as the roles).
2. The bank payment file for a pay run: one row per payslip, net pay to
   each employee's account, in a plain bulk-upload CSV (name, account,
   IFSC, amount, narration), with the run's total; refused while any
   employee lacks bank details, naming them. Mark the run's slips as paid
   by one bulk payment that names the file, and its reverse (a returned
   transfer for one employee unpays that slip only).
3. Printed payslips (PDF, from core's document printing), and the
   employee's own view of their payslips (my payslips: their own, never
   another's; test as an employee login).
4. Salary advances and loans: given (a payment out, against an employee
   receivable), recovered in instalments by the pay runs (a deduction
   line), the balance always derived, the rest recovered at a leaver's
   final settlement within the deduction cap (decision 20, O28's rule);
   the reverses (an advance voided, a run voided after it recovered).
5. Overtime: the rate derived from the employee's ordinary rate of wages
   (the components marked as ordinary wages, per day or hour by the
   working pattern) times a multiplier that is data (default 2, as the
   Factories Act and the OSH Code set it; to confirm), not typed per
   person.
6. Professional tax: slabs that carry the state and the day they apply
   from; a pay run uses the slabs that stood for its month. West Bengal's
   schedule changed on 1 October 2026: the mechanism must hold both.
7. O222: the EPS/EPF wage ceiling as dated data, the run keeping what
   stood for its month (today's constant is 15,000; a reported rise to
   25,000 from 17 September 2026 is for the accountant to confirm).
8. Labour Welfare Fund: a contribution kind with employee and employer
   shares and its months (West Bengal: half-yearly, June and December),
   as data, with its register.
9. O223: `LeavePolicy.requires_approval` is read where leave is taken, or
   it goes.

Name each item's reverse and edit path before writing it. Statutory
outputs (the ECR, the PT and LWF registers) read the kept figures, never
today's settings.

## Shared rules

Yours: the advance ledger, the payroll bank file, the dated statutory
figures in hr. Existing, use them: `correction_date()`,
`AccountingPeriod.refuse_closed()`, `@serialised`, `lock_rows`, the pay
run's posting and its kept facts (O5, O6 in g20), `apps.core.documents`
for printing, `apps.core.mail`, `AuditableViewSetMixin` (pass your own
fields through `super().perform_create`), the roles in setup_roles.

Another agent (fix_acct) works at the same time on accounting, GST and the
invoice print; another (fix_qrecall) on quality. Keep to apps/hr and what
payroll touches.
