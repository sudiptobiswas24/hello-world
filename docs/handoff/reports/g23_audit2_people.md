# audit2_people: hr whole (base 0a6778f)

Probes: /home/user/work/audit2_people, commits 10a3a22 and b61129d (unpushed), apps/hr/tests_audit2_people.py;
expected figures from calc_audit2_people/figures.py (plain Python, no Django; june.json is its output).
Result: 17 probes fail on the base (SQLite, fastsettings); the same ones fail on PostgreSQL (22 probes run there, 16
failures, the plant month and rate-change probes pass on both). The 496 existing hr tests pass under
DJANGO_TIME_ZONE=Asia/Kolkata (the only failures in apps.hr there are these probes).
`audit_invariants`: "No invariant findings." `makemigrations --check --dry-run`: "No changes detected."

## Findings (M-number = probe)

### Security
**P1 (M18, M8). A leaver's login, roles and approvals live on.** Employee.terminate (apps/hr/models.py:314) sets a date and a
status and nothing else; nothing in core or web reads termination_date (grep: no hits). E1, a Stores Manager login, left on
30 September; on 20 October `user.is_active` is still True. A Line Manager who left on 1 June still approves a report's leave
(`check_approver`, models.py:782, never asks `is_working_on`, which only the floor modules call); expense claims the same
(expenses.py `_check_decider`). Shape: a guard (is_working_on) built for the floor, absent on the HR approvals; setting read
live. Go-live: yes, or put "deactivate the login" on the leaver checklist and add a morning check for leavers with live logins.

**P2 (M13). UAN and ESI numbers are served to the floor.** GET /api/hr/employees/ as Quality Inspector, Maintenance,
Production Supervisor, Process Engineer, Quality Manager and Line Manager (the matrix below) returns every employee's `uan`
and `esi_number` (EmployeeSerializer, apps/hr/serializers.py). Those roles need names. Statutory identifiers are the
payroll/HR offices' (pay-details already hides PAN, bank and birth date for this reason). Not a blocker; fix with a narrower
serializer for roles without view_employeepaydetails.

### Books
**P3 (M5). An expense line may be charged to any account.** ExpenseLine.save (expenses.py:219-) and ExpenseLineSerializer
accept net pay payable (a liability) and the bank. Employee Self Service posts a 500.00 taxi line to 2300 Net pay payable;
once approved and paid, `pay()` debits it: net pay payable is 500.00 lower than the slips owe (shape 3/4, control account
that stops footing). Refuse accounts that are not active expense-type, non-money, non-control accounts at line save.
Not a blocker for a trusted office; high for a self-service login.

**P4 (M6). A draft claim can be handed to a colleague by an edit.** perform_create checks the claimant
(people_api.py:107) but ExpenseClaimViewSet has no perform_update: PATCH {"employee": colleague} by the employee's own
login returns 200 and the claim is now the colleague's, to be decided by the colleague's manager. Mirror gap (create checked,
edit not). Minor.

### Statutory
**P5 (M1). Wage ceilings are applied per run, not per month.** `_amount_for` caps the base per run (payroll.py:1754); the
figures are named "a month's wages" (statutory.py FigureKind). Two fortnight runs of June at 8,000 each take PF 960 + 960 =
1,920; 12% of min(16,000, 15,000) is 1,800 (employer share the same, so 240 over-contributed). ESI's coverage test reads one
run's wages against 21,000 the same way, and ECR/ESI files are per run so a member appears twice for the month (statutory_files
`ecr_rows`, `esi_rows`). Slabs (PT, LWF) were fixed for this in g21 (`_month_before`); PF/ESI ceilings and files were not.
Go-live blocker if the plant pays more often than monthly (the pilot doc says a fortnight; piece workers often are).
Decision for the owner: monthly runs only, or month-aware ceilings and a per-month ECR.

**P6 (not built, not in RISKS). No tax on salary** (TDS under section 192, now 392 of the Income-tax Act 2025; Form 24Q, Form
16). grep of apps/hr: none; `TAX` exists only as a percent component in the tests. Plant staff above the rebate limit need
it. Owner/adviser decision before go-live; list it in RISKS limitations.

### Rule
**P7 (M9, M9b). employment_status=terminated with no termination_date is accepted** (Employee.clean, models.py:242 checks
only the other direction). The person stays "employed" for every run (`calculate` filters on dates only), register and
approval, and keeps getting paid; the station PIN stops (by_pin reads status). PATCH by HR Admin, 200. Refuse it, or derive
status from the date. Go-live: easy to hit, fix.

**P8 (M2). A punched day corrected by hand is overwritten by the next punch file.** `_punch` (attendance.py:292) protects
only source=manual; a day created by import keeps source=import when a supervisor sets it to half_day over the API (source is
read-only and never set to manual), so re-reading the reader's file puts it back to present. Overpays the half day; overtime
stays. Reproduced end to end. Fix: any API/admin save of an imported day sets source to manual.

**P9 (M19). An employee with no pay set up is silently left off the run.** `_eligible_employees` (payroll.py:689) takes only
people with a compensation row in force; the calculate response names nobody who was left out, and the only check
(web/checks.py:_wages_not_posted) asks whether any run is posted. A joiner whose pay row was forgotten gets no slip and no
warning (silent-skip shape). Return the people employed in the period with no pay in a `skipped` list, or refuse.

**P10 (M4). Leave asked before a leaving date is approved after it.** `terminate` and `_check_nothing_dangling` look at
approved leave only (models.py:286-); `approve` re-checks balance, register and runs but not employment (models.py:812). Left
10 June, pending leave 15-19 June approved afterwards (5 days) with a manager's yes. Check at the point it can first be known:
approve.

**P11 (M3, M17). Two leave-policy settings do not do what they say.** `carry_over_limit` ("anything above it lapses") is read
nowhere (models.py:437; carried_over is typed in by hand): 20 days, limit 5, 10 carried gives 30 not 25. And `entitlement_for`
(models.py:460) reads `annual_days` live for every year, though the class docstring says changing next year is "a policy
change rather than an edit to everybody's history": raising 12 to 15 turns the 2026 entitlement from 12 into 15. Comment not a
constraint (mistake 1). Policies are admin-edited only. Minor.

**P12 (M12). A run posts with any pay date.** PayRun.post (payroll.py:701) has no bound between pay_date and the period: June
2026 with a pay date of 30 June 2062 posts wages 36 years ahead (a typed year); the void is then allowed only on that day.
Same shape as the money audit's O284. Minor.

**P13 (M7). Hours cannot be entered or approved in the office.** Timesheets (apps/hr/timesheets.py) have no API route, no
admin registration, no screen, and no role holds hr.decide_timesheet; a per-hour component reads only approved timesheets or
hours handed to calculate. Inert for the office. Go-live blocker only if anyone is paid by the hour.

**P14 (M14, M15). Contract labour register.** An inactive contractor still takes new workers (ContractWorker.save,
contract_labour.py:73 reads only the licence date and the headcount); the licence headcount can be edited below the number
working. M15 may be a true record of an amended licence; M14 is plain. Minor.

**P15 (no probe, structural). Payroll registers no period-close check.** Only assets does (apps/assets/apps.py); a period
closes with a calculated run not posted, wages unposted, or statutory dues unremitted, and the June run then needs a July pay date.
Minor.

## Probed and held
- A plant month: 28 people (24 full month, a joiner on the 16th, a leaver on the 15th, 3 days unpaid leave, 2 absences): every
  person's PF, ESI and PT equals the hand-worked figure; net 601,012.18; ECR EPF 45,207 and EPS+difference 45,207 equal the
  employer share; PF payable 90,414, ESI payable 8,508, PT payable 5,600 and net pay payable 601,012.18 foot to the slips.
- A rate change on the 1st, mid-month, on the last day (20,181.82) and for a joiner on the 16th with a rise on the 23rd
  (11,090.91): paid for the days of each rate.
- Reverse and edit paths read, with the guard on each: advance (void, payment void, delete, recovered), shortfall settlement
  (void, run void, write-off past the books), bank file (mark_paid, return_transfer, void under a returned transfer, formula
  cells), pay run (edit, delete, void with paid slips, remittance, settlement), remittance (edit, delete, over-payment),
  attendance and leave under a posted run (hold_the_employee), compensation edit/delete under a posted run, statutory figures
  and slabs kept.
- Roles over the API (matrix of 200s for every hr route and every role): payroll, bank, advance and settlement routes are
  Controller/Payroll Officer only; Self Service sees its own leave, claims, payslips; post/void/pay need hr.post_payrun,
  which the Payroll Officer deliberately lacks; write-off needs hr.write_off_shortfall.
- Kolkata: no `now().date()` in apps/hr; existing hr suite green under Asia/Kolkata.
- Unordered loops that write or number: none found in apps/hr (employees, slips, entries are ordered or locked in key order).

## Role matrix (GET 200), apps/hr routes
Bookkeeper: expense claims/lines. Controller: employees, components, compensation, runs, payslips, slabs, remittances,
liabilities, pay-details, figures, advances, settlements, salary files, claims. Floor roles (Production Supervisor,
Maintenance, Process Engineer, Quality Inspector/Manager): employees (Supervisor also attendance). HR Admin: employees,
leave, contract labour, attendance, claims, appraisals, recruitment, departments. Payroll Officer: as Controller less
settlements, plus leave and attendance. Line Manager: employees, leave, claims, appraisals, recruitment, departments.
Employee Self Service: leave, own payslips, claims, appraisals. Nobody else reads any hr route.

## Unproven (what would prove it)
- A piece-rate roll the Quality Manager rejects is still paid per metre (piecework.py counts every roll that stands, entry not
  voided); needs a rejected lot fixture to show; a decision for the owner.
- Overtime has no Factories Act cap (hours per quarter, nine a day): AttendanceDay allows up to 24; statutory, an owner decision.
- EPS membership: every PF member is given 8.33% pension on up to the ceiling; a member who joined above the ceiling after
  1 September 2014 is not an EPS member (no field); adviser to confirm. ESI daily-wage exemption (up to 176 a day) not modelled.
- Payroll Officer holds change_employeepaydetails and add_salarytransferfile: one login can change an account number and write
  the bank file (second factor and history exist; no hold or flag on a changed account before the next file). Separation question.
- A full-and-final (leave encashment, gratuity payout, notice pay) is not built (L34).

## Not reached
New race() probes (the existing PayrollRace/Advance/PunchFile/RunPosted-as-register-changes tests cover pay, advances, files and
the register; I read the other paths' lock order and found them consistent); browser drive of the People screens as each role;
payslip PDF; appraisals and recruitment; overtime ordinary-rate arithmetic beyond the existing tests; LWF and ESI block
transitions in October/April; EPS at the 58th birthday boundary; 30 runs of one kind through a closed period.
