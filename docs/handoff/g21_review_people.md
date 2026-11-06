You are review_people: an adversarial, read-only review for gate g21 of a Django 5.2 + DRF ERP for a woven polypropylene sack plant in West Bengal. Repository /home/user/hello-world; read its CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

Make a worktree of the integrated tip: `git -C /home/user/hello-world worktree add --detach $SP/review_people fbfc49d`. Link `.venv` and `frontend/node_modules` from /home/user/hello-world. Work only there. Never edit product code; write probes (tests that assert what SHOULD be true) and commit only them, in that worktree, unpushed.

What you review: payroll's go-live outputs as fix_people built them. The commits, oldest first, are:
- c756b0a: pay details, the bank file, payslip PDF and my-payslips, advances and loans, overtime, dated PT slabs, LWF, the dated wage ceilings (O222);
- baf239a (O223);
- d8a8367 (the PT and LWF registers screen);
- b8fa0b4 (the bank file and a slip on one payment);
- 1bb6061 (is_paid);
- 7410e6c (the overtime rate);
- 0d95de4 (EPS past 58);
- 75245c4 (screens);
- f6bc5f6 (my payslips).
The author's report is $SP/reports/fix_people.md. The brief is docs/handoff/g21_people.md. O236 and O237 in docs/RISKS.md are already known; confirm them, don't re-find them.

The plant pays three kinds of people: loom weavers on piece rates, shift workers on daily wages, and staff on monthly salaries. Check every rule for all three.

Lead with what pays the wrong money, pays it twice, posts the books wrong, or lets the wrong person read pay. In particular:
1. **The bank file.**
   - Can a slip be paid twice: two files for one run, a file and a single payment, a file written after one slip was paid by hand?
   - Can a voided run's file still be marked paid?
   - Does a returned transfer unpay exactly one slip and book the money back?
   - Is the CSV's total the posted net pay to the paisa?
   - Is an employee without bank details named, and refused?
   - Do the account and IFSC in the file come from what stood when the file was written, not from a later edit?
2. **Advances and loans.** Work it from a plain-Python scenario first. The balance is derived from the runs.
   - Check recovery: inside the deduction cap, and the rest at a leaver's final settlement (decision 20 / O28).
   - Check the reverses: an advance voided after a run recovered part of it; a run voided after it recovered.
   - Check two runs in one month, and a run posted then voided then posted again.
   - Does the employee receivable foot to the derived balance after every one of these (audit shape 4)?
   - Is an advance a pay input that holds the employee first (`hold_the_employee`)?
3. **The law's figures as dated data, kept on the run** (the EPF/EPS ceiling, PT slabs by state and date, LWF shares and months, the overtime multiple).
   - Check a change on the 1st, mid-month and the last day of a month.
   - Check a run drafted before a change and posted after it.
   - Check a row edited or deleted after a run used it: does the posted run, its ECR, its PT/LWF register and its payslip PDF still read what it posted (audit shape "a setting read live")?
   - Check West Bengal PT's two schedules either side of 1 October 2026.
   - Check LWF only in June and December.
   - Check a PT slab for a state the employee is not in.
4. **EPS past 58.** Born on the 1st, the 15th and the last day of a month. No date of birth. A date of birth corrected after a run.
   - What do the ECR, the posting and the payslip each say?
   - Do the books and the ECR foot by account?
5. **Overtime** at the agreed rate for each of the three kinds of pay. Can a weaver on piece rates be paid overtime at all, and on what base?
6. **Who may.** Test as the roles over the API (apps/core/tests_roles.py): Payroll Officer, Controller, Line Manager, HR, Employee Self Service, and a rep.
   - Bank details, PAN and date of birth: who can read them, in which endpoints, in which exports, payslip PDFs and history (the chatter or audit history of a changed bank account)?
   - Can an employee reach another's payslip through my-payslips, its PDF, a filter or an id?
   - Can the Payroll Officer pay, mark paid or void what the Controller holds?
7. **Races** on PostgreSQL with `race()` (apps/e2e/tests_races.py; `DATABASE_URL=postgres://erp:erp@localhost:5432/review_people`, drop test_review_people first):
   - an advance given as a run posts;
   - a bank file written as a slip is paid;
   - pay details edited as a file is written.
   SQLite proves nothing about races.
8. **Screens:** drive one flow in the browser as the Payroll Officer and one as the Controller (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium, `npm run build` in frontend first). Money must be shown with src/lib/format.ts, never Number().

Method:
- One probe per claim, stated as a fact about money or state, with PASS or FAIL per check.
- Number the findings. For each, give file:line, a concrete scenario with names and rupees, and whether it blocks the gate.
- A finding needs a probe that fails on the tip. List a suspicion without one separately, as unproven.
- Run audit_invariants and makemigrations --check on the tip and say what they print.

Budget: about 80 tool calls. Report to $SP/reports/review_people.md, and 10 lines back: findings by severity, blockers first.
