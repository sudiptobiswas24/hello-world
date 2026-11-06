# review_people2: fix_people2 (13 commits, 0152e3f..755d376)

Worktree /home/user/work/review_people2 (detached at 755d376 plus one probe commit 07a10a3, apps/hr/tests_review_people2.py).
Figures worked in the probes below; every number printed by the run, not typed.

## Blockers

1. PF-ER is booked again in full on the second run when the pension is a component of its own (O301 with O237).
   apps/hr/payroll.py `_employer_share_total_first` (about line 1578) takes `unrounded[employer] + unrounded[pension]`,
   and `unrounded` is stored before the month's rebate (line ~1357, `self.__dict__["_unrounded"]`). With the new month
   ceiling `unrounded` is already the month's figure (3.67% + 8.33% of min(16,000, 15,000) = 1,800), so the employer line
   becomes `1,800 - this run's EPS`, and what the first run took is never taken off.
   Scenario (probe Probes.test_eps_split_two_runs): basic 8,000 in each fortnight of June, PF-ER 3.67% and EPS 8.33%
   as separate employer components, ceiling 15,000. Run 1: PF 960, PF-ER 294, EPS 666. Run 2: PF 840, PF-ER 1,216,
   EPS 584. Employer PF for the month 294 + 666 + 1,216 + 584 = 2,760, right is 1,800. ECR epf_diff 1,510, right 550.
   The ledger (PF payable, employer expense) is overstated by 960 and the ECR is wrong. With one 12% PF-ER component
   (the tests' fixture) it is right, which is why the new tests are green. Fix: take the rebates off before the total
   (total = round(er + eps - rebate_er - rebate_eps)), and add this case to AMonthPaidInTwoRunsTests. BLOCKS.

## Not blockers, should be fixed

2. ECR drops a run in which the member's PF line is nil (apps/hr/statutory_files.py `_month_members`, line ~127 `if lines.get(kind)`).
   A line of 0 is never written (`amount > 0` filter), so a run that took nothing because the month's ceiling was
   already reached has no PF line and its slip is left out of the member's row: gross and NCP days of that run are missing.
   Probe Zero: basic 15,000 for the first fortnight (PF 1,800), 4,000 for the second (PF none, ESI 30). ECR gross 15,000,
   right 19,000; the second run's unpaid days (ncp_days) would be lost the same way. The ESI file of the same member
   shows 19,000, so the two files disagree. Fix: collect the month's slips of every member with a PF line in any run
   and sum gross/ncp over all of them. Does not block the ledger; the filed return is wrong. Fix before release.

3. O309 guards the create only (apps/hr/contract_labour.py:80 `self._state.adding`). Edit path: a worker is moved to an
   inactive contractor by PATCH /api/hr/contract-workers/<id>/ {"contractor": inactive}; accepted (probe
   Probes2.test_manager_hijack_and_contractor_move: "ACCEPTED"). Mirror gap, edit path of the guard. A left worker
   can also be brought back (left_on cleared) under a stopped contractor. Medium; does not block.

4. O308: the window is `max(today, period_end) + 10`, not "today when paid late" as the docstring and message say
   (apps/hr/payroll.py `_check_pay_date`, line ~728; message line ~737). June's run, with today 2026-10-23, posts with a
   pay date of 2026-11-02 (today + 10, still to come) and is refused at 11-03 (probe PayDate). The entry is booked on a
   future day, and the void of it is then allowed only on that day: the original defect, ten days wide, for every
   late run. A comment that is not a constraint (CLAUDE.md mistake 1). Either bound a late run at today or say ten days
   after today. Low; does not block. (Kolkata: `timezone.localdate()` is used, no UTC day.)

5. O307: no repair of rows the old rule let in, and the new refusal bites unrelated edits.
   (a) LeaveEntitlement.save (apps/hr/models.py:664) refuses a stored carry above the policy's limit on any later save:
   lower the limit to 2, then an entitlement with carried_over 5 cannot have its days edited ("carries at most 2")
   (probe Policy). Existing rows above the limit are neither listed nor corrected (no data step, no morning check).
   (b) A change of annual days made in October restates the whole current year (2026 goes 12 to 15 at once): the
   author's Q4, owner to answer; the earlier years are kept correctly (probe Policy: 2025 stays 12, 2026 15, 2027 18,
   then 12 in April 2027 leaves 2026 at 15, a change in 2029 keeps 2028 at 12). Low.

6. O301 ESI, known (author Q1) and now with a consequence worth stating to the owner: a member covered in the first
   fortnight (ESI 94 on 12,500) is "not covered" from the second and in every later month, because the deciding month
   is June's total (25,000). Void of the first fortnight with the second posted gives the first no ESI at all
   (history changes on a redo). Owner/adviser. Also a sequential use now refuses a post: calculate both fortnights,
   post the first, the second no longer posts until worked out again (posted_as_calculated); intended, but say so on the screen.

7. O305 toast (frontend/src/modules/payroll/PayRunForm.tsx): "no pay set up for the period" is said of everyone in
   `skipped`, also of people simply not chosen when a run is calculated for a subset (`employees=`). The wording blames
   the pay rows wrongly. Low.

8. O304: rows already imported and corrected by hand before this change keep source=import (no data step possible),
   so the first punch file after release overwrites them once. Say so in the release note. Low.

9. O311 is as written: floor roles (Production Supervisor, Quality Inspector, Maintenance), Line Manager and Employee Self
   Service get no uan/esi_number from any of 371 no-argument API routes plus employee detail, leave, payslip and the
   history route (history lists field names only). Controller does read them (it holds view_employeepaydetails), which
   the commit says "HR and payroll" for: confirm that is wanted. `?missing=uan` still tells any employee reader
   which members have no number (a yes/no, no number). Low.

## Checked and found right (no finding)

- O301 PF/ESI with three runs in a month (6,000 each: PF 720, 720, 360 = 1,800; ECR 18,000 gross, 15,000 wages, ESI file 18,000),
  middle run voided and redone (720), first voided and redone (720), total 1,800 again. Joiner, leaver and a member
  whose wages cross the ESI limit: ledger PF payable equals the posted PF lines (18,000 = 18,000), ECR equals the
  ledger split; the file is the same from either run of the month.
- O303 on the API as Line Manager: a manager patching a report's claim to themselves is refused 403, claim stays with its maker.
- O302/O306/O310 as modelled and tested; O304 sequence (import, hand change, import) kept; hand-first kept.
- O307 policy years: first change, a second in the same year, a gap year all give the figures above.
- Migration 0030: forward to 0030, back to 0029 and forward again with MigrationExecutor (default settings, SQLite): OK, table created and dropped. The author's PostgreSQL race (two fortnights posted at once, database review_people2): OK.

## Probes
apps/hr/tests_review_people2.py (commit 07a10a3). Findings 1, 2, 3, 4 each fail a stated expectation when asserted; the file only prints
(PROBE lines) so it stays green; turn them into assertions with the fixes.
