# fix_people2 (base 03873b6, worktree /home/user/work/fix_people2)

Probes cherry-picked: 10a3a22, b61129d (apps/hr/tests_audit2_people.py, calc_audit2_people/). M8 and M18 (O300)
pass on the base and are left alone, not counted. The probes for my rows are kept in that file as regression
tests; each row also has a test in the module's normal test file.
Calc scripts: /home/user/work/calc_fix_people2/o301.py.

## Commits (one per row, row taken off docs/RISKS.md in it)
- O301: PF and ESI ceilings are the month's across its runs; ECR and ESI files one row a member a month
- O303: an expense line is charged to an active expense account; an edit keeps the claim its maker's
- O305: a run names who was employed in its period and left off it (API `skipped`, the run screen's toast)
- O302: terminated with no leaving date is refused, and a run will not pay such a row
- O308: a pay run posts only for a pay date near its period
- O310: a period closes with the pay runs paid in it posted (hr registers a close check)
- O304: a punched day changed by hand survives the next punch file
- O307: carry-over limit enforced where the carry is entered; a change of annual days restates no past year
  (migration 0030_leave_policy_years: CreateModel LeavePolicyYear only, read and checked)
- O306: leave is approved only within the employment as it stands when decided
- O309: an inactive contractor brings no new worker onto the register
- O311: UAN and ESI numbers are served only to HR and payroll
- Register: the owner is asked who files and approves timesheets (O312 left open, question in its row)
- O301 (second commit): the PostgreSQL race of two fortnights posted at once (apps.e2e.tests_races.PayrollRaceTests)

## Tests that failed without each fix
- O301 apps.hr.tests_statutory.AMonthPaidInTwoRunsTests:
  `Tuples differ: (Decimal('960.00'), Decimal('960.00')) != (Decimal('960.00'), Decimal('840.00'))`;
  ECR `{'gross': 8000, 'epf_wages': 8000, ...} != {'gross': 16000, 'epf_wages': 15000, ...}`; ESI file `8000 != 16000`;
  coverage `Decimal('90.00') is not None`. The void-and-redo test (960 again) passes without the fix too: it guards
  the shape chosen (month's figure less what was taken), which a "room left under the ceiling" fix would fail (1,680).
- O303 tests_audit2_people.TheExpenseClaims: `201 != 400 : 2300 - Net pay payable`; `200 == 200` (claim now E2's).
- O305 tests_pay_outputs.WhoARunLeftOffTests: `KeyError: 'skipped'`; probe: `'ZED9' not found`.
- O302 tests_audit2_people.WhoIsStillOnTheRoll: `200 != 400`; `ValidationError not raised` (the run).
- O308 tests_pay_outputs.APayDateNearItsPeriodTests (3 of 4) and the probe: `ValidationError not raised`.
- O310 tests_pay_outputs.APeriodClosesWithItsPayRunsPostedTests: `ValidationError not raised`.
- O304 tests_attendance.AHandCorrectionOfAPunchedDayTests: `('import', 'present', 0) != (MANUAL, HALF_DAY, 1)`;
  probe `'present' != AttendanceStatus.HALF_DAY`.
- O307 tests_leave.WhatAPolicyChangeLeavesAloneTests: `ValidationError not raised`;
  `(28.00, 28.00, 28.00) != (25.00, 25.00, 28.00)`; probe `Decimal('15.00') != Decimal('12.00')`.
- O306 tests_leave.LeaveAskedBeforeALeavingDateTests: `ValidationError not raised`.
- O309 tests_contract_labour.AStoppedContractorTests: `ValidationError not raised`.
- O311 tests_pay_outputs.WhoReadsStatutoryNumbersTests: `'100000000001' unexpectedly found ... : Quality Inspector`.

## Probes changed, and why (mistake 7 worked out)
- M9b (terminated, then the run): its PATCH is now refused (M9), so the person is rightly paid; it sets the stored
  row directly (a row from before the rule), which the run now refuses by name.
- M3 (carry-over): the carry above the limit is refused where it is entered rather than capped on read (a fact at
  the event, not a setting read live); the probe expects the refusal and 25 from a carry of 5.
- M17 (policy change): mocks the day of the edit its docstring names (2 January 2027); unmocked it ran on today.
- The existing tests_leave carry test set no limit (so by the field's own words nothing carries); it sets 5.
- The attendance fixture's mark(source=import) now says it stands for an imported day, so the O18 race keeps its sense.

## Runs
- Targeted fastsettings runs, --parallel 1, of every touched test module (statutory, statutory_files, review_cost,
  review_people, payroll, pay_outputs, payroll_api, pay_after_the_review, people, people_screens, tests, audit,
  attendance, leave, leave_and_the_day, review_core_leave, contract_labour, manufacturing.tests_station,
  e2e.tests_plant_month, e2e.tests_review_people_races): OK. Not run: whole apps (the integrator's).
- PostgreSQL (database fix_people2): PayrollRaceTests, LeaveRaceTests, PunchFileRaceTests: the 6 existing pass; the new
  test_two_fortnights_posted_at_once_take_the_months_pf_once passes, and without the O301 payroll change fails
  `Decimal('1920.00') != Decimal('1800.00')`. The second post is refused by posted_as_calculated, then worked out again.
- `--tag migration` apps.hr (default settings): Ran 1 test, OK.
- `makemigrations --check`: No changes detected.
- `audit_invariants`: 2 findings, both in inventory and in no file I touched (inventory/models.py
  StockMovement._settle_money:916 "named draw past the promise"; inventory.AdjustmentReason.clean() "rule only the
  admin runs"). Present on the base as far as I can tell (static checks of untouched code); the integrator to confirm.
- Frontend: `tsc --noEmit` clean after the PayRunForm change; no browser run.

## Not done
- O312 left open: who files and approves timesheets is the owner's design (question written into the row).
- Probe M15 (licence headcount edited below those working) not fixed: may be the true record of an amended licence.

## Questions for the owner / accountant
1. O301 ESI: a fortnightly plant's first fortnight cannot know its month. Fortnights of 12,000: the first is
   covered (90 taken, month so far 12,000), the second is not (month 24,000 > 21,000), and from then the
   contribution period is decided as not covered. Should the first fortnight's ESI be refunded on the second run,
   or coverage be read from the contracted monthly rate instead of wages paid? Adviser.
2. O301 PF at a rate change between the month's runs: the month's figure is taken at this run's rate less what
   earlier runs took; a mid-month change of the PF percentage itself (not wages) is not split. Adviser to confirm.
3. O308: the window is the Payment of Wages Act's outer ten days after the period, or today when paid late. Owner
   may want seven (under 1,000 workers) or a different rule for arrears runs.
4. O307: a change of annual days applies from the year it is made in (earlier years keep theirs). Should a change
   made in October apply to the current year or only from next January?
5. O303: claims are charged to expense accounts only; a tool bought on a claim and capitalised would need an asset
   account. Accountant to say whether an asset (non-money, non-control) account is allowed.
6. O310: the close check holds a period for runs not posted; it does not check that a month with employees has a
   run at all, nor that statutory dues were remitted (paid the next month). Owner.
7. M15: may a licence's headcount be lowered below the workers working (amended licence), or refused?

## Seen and not fixed (for the register)
- apps/hr/payroll.py PayRun.skipped(): derived each time; the run list does not show it, only the calculate answer
  and the screen's toast. A run calculated over the API without the screen shows it once. Kind: silent skip (minor).
- apps/hr/models.py Employee: rows already stored as terminated with no date are now refused by the run, but no
  morning check lists them. Kind: rule (minor); a check in web/checks.py would name them.
- apps/hr/statutory_files.py: ECR/ESI are now the month's, built from whichever posted runs exist; downloading the
  file from the first fortnight before the second is posted gives a part month. Kind: workflow; the file could say
  which runs it holds, or refuse while a run of the month is unposted. Owner.

# Round 2: the review (/home/user/work/reports/review_people2.md, probes 07a10a3 cherry-picked)

Figures: /home/user/work/calc_fix_people2/review1.py (plain Python).

## Commits
- 688a3d6 O301: employer PF total is the month's less what its other runs took of both shares (review 1, BLOCKS).
  PF-ER 3.67% + EPS 8.33% apart, fortnights of 8,000: PF-ER 294 + 256, EPS 666 + 584 = 1,800; ECR eps 1,250, diff 550.
  Both configurations tested: AMonthPaidInTwoRunsTests (one 12%) and AMonthPaidInTwoRunsWithThePensionApartTests (void
  and redo included). Failed without: `(Decimal('1216.00'), Decimal('584.00'))` for `(256, 584)`; `(1800, 1250, 1510) != (1800, 1250, 550)`.
- 0310baa O301: every run of the month counts in the member's ECR/ESI row, one with no line included (review 2, BLOCKS).
  15,000 then 4,000: ECR gross 19,000, EPF wages 15,000, PF 1,800. Failed without: `(15000, 15000, 1800) != (19000, 15000, 1800)`.
- 4dbcda3 O308: a late run's pay date ends at today (review 3). latest = max(end + 10, today). Failed without:
  `ValidationError not raised` (June posted 23 October for 2 November).
- 170702a O308: tests_payroll VoidingTests' fixture posted last month's run for today + 3, late under review 3 from the
  11th of any month, so the calendar decided it (failed on 23 October). Its period now ends yesterday. Test-only.
- 73ce631 O309: no worker joins a stopped contractor by an edit either (review 4): a move to it, a leaver brought back.
  Failed without: `ValidationError not raised`.
- eb6177e O358: ESI coverage of a month paid in runs shorter than itself is decided from the monthly rate at its first
  run: fixed period amounts of the deciding components on that run's first day employed x month days / run days.
  Fortnights of 8,000 = 16,000 (ESI 60, 60 again after a void and redo); 12,000 = 24,000 (neither covered). A whole-month
  run is decided by its wages as before. Failed without: `(Decimal('90.00'), None) != (None, None)`.
  The O358 row is not in my base (03873b6): the integrator takes it off, or registercheck will see its id in a subject.

## Runs
- fastsettings, --parallel 1: apps.hr.tests_statutory, tests_statutory_files, tests_review_cost, tests_review_people,
  tests_payroll, tests_pay_outputs, the audit2 plant month, tests_contract_labour: OK after the fixture fix.
- PostgreSQL PayrollRaceTests (5, the O301 race included): OK. makemigrations --check: No changes detected.
  audit_invariants: the same 2 inventory findings as round 1, none in hr.

## Left, for the owner or adviser
- O358 for a run period not aligned to the calendar month (say 26 May to 25 June) is treated as part of a month and
  scaled by days; a plant on such cycles should say whether that is meant.
- Rate-based coverage reads only fixed earnings; a piece-rate or hourly member of a fortnightly plant has a rate of 0
  under it (covered). Adviser.
- Review 5 (O307 rows above a lowered limit refused on any later save, no repair), 6 (posted-as-calculated refusal of the
  second fortnight needs words on the screen), 7 (toast wording for a subset run), 8 (O304 rows imported before release
  overwritten once), 9 (Controller reads UAN; `?missing=uan` yes/no): not in this brief, not done.
