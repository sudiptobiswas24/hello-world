# fix_statutory (g24): statutory seeds, go-live lines, TAN, the 2025 Act's names, bonus

Worktree /home/user/work/fix_statutory, base d76ef6f. Not pushed. Calc script:
/home/user/work/calc_fix_statutory/calc.py. Revert proofs: /home/user/work/calc_fix_statutory/reverts.py
(output reverts.log).

## Commits

Three commits on d76ef6f, tip f76a843 (no register ids: the brief's items are build items, not RISKS rows):
- 8674d96 TAN on the company, and the Income-tax Act 2025's names on TDS and TCS (decisions 3, 4)
- 4617d21 Statutory figures loaded from dated files, confirmed before use; go-live lines; the bonus rule
  (decisions 1, 2, 5)
- f76a843 New blank columns carry a database default (the migration lane found MoneyAlreadyMoved
  MigrationTests failing "NOT NULL constraint failed: core_company.tan"; default="" and db_default="" on
  every new blank CharField, folded into the migrations that add them)

## What is built

1. **setup_statutory** (apps/hr/management/commands/setup_statutory.py) loads `statutory/*.csv` at the
   repository's root, one file per kind, each row with its day, state, value and source:
   tds_rates, tcs_rates, provident_fund, esi, professional_tax, labour_welfare_fund, bonus,
   minimum_wages (plus names.csv for decision 4). The TDS/TCS half is apps/accounting/statutory_load.py,
   shared with setup_indian_books, which now seeds the scrap TCS rates from the same file (one home).
   Every row loads unconfirmed unless its `confirmed_by` column names a user holding the confirm
   permission; all rows shipped are unconfirmed. Re-run adds only what is missing. PT and LWF load into
   the one active component marked as that levy; with none, the command says so and loads nothing.
2. **Confirmation**: StatutoryFigure, MinimumWage and PT/LWF bands carry confirmed_by/confirmed_on
   (statutory.Confirmed, same rule as DatedRate.confirm: vouches for the figures read, refused if moved).
   figure_as_of, minimum_wage_on and PayComponent.schedule_on refuse an unconfirmed row in words naming
   the figure and "the Controller". A changed figure is unconfirmed again. Permissions
   hr.confirm_statutoryfigure and hr.confirm_minimumwage, granted to the Controller only; the Payroll
   Officer loads and types. API: POST /api/hr/statutory-figures/{id}/confirm/,
   /api/hr/minimum-wages/{id}/confirm/, /api/hr/pay-component-slabs/{id}/confirm/ (whole schedule).
   Screens: Confirm on the statutory figure form, new Minimum wages list and form.
3. **go_live_check**: one line per engine the plant uses (TDS, TCS, PF, ESI, PT, LWF, Bonus, Minimum
   wage), FAIL naming every figure no confirmed row covers today (apps/hr/statutory_check.py, which asks
   the engines' own readers). Tested with a planted gap (EPF ceiling unconfirmed: only PF fails).
4. **TAN** on Company (validated in save(); company screen). challans.pay_over refuses without it and
   keeps tan and the challan's name on TdsChallan/TcsChallan (TdsChallan also keeps the section of the
   Act). Return endpoints return {tan, act, form, challan_form, rows}, refused without a TAN; the
   return and challan screens show them.
5. **2025 Act names**: statutory/names.csv + accounting/statutory_names.py. TdsSection/TcsSection carry
   section_1961 and section_2025; the return rows, the TDS challan and TcsSection.code_on name the one
   standing on the day. apps/web/tests_statutory_names.py fails on 26Q/27EQ/27Q/24Q/"challan 281" in any
   screen (frontend/src) or print (documents.py, templates). Search keywords that held "26q", "27eq" and
   "challan 281" in registry.ts were replaced.
6. **Bonus**: PayComponent.ceiling_figure "bonus_ceiling": base capped at max(dated bonus_floor,
   minimum wage for the person's work_state, scheduled employment, zone and skill on the period's last
   day), paid only while the month's wages are within the dated bonus_eligibility figure. Kept on the line
   (base_ceiling, kept_terms bonus_floor, minimum_wage, bonus_eligibility); a minimum-wage row a posted run
   kept cannot be edited. EmployeePayDetails gains scheduled_employment, wage_zone, skill (API and
   screen). The flat base_ceiling still works for components not moved; a run posted at it keeps 583.10 /
   7,000 after the component moves to the rule (test).

Which law: the four Labour Codes are in force from 21 November 2025 and the Payment of Bonus Act 1965
is repealed; bonus is Chapter IV of the Code on Wages 2019. The 7,000/minimum-wage and 21,000 figures
are from the Central Government's notifications of 25 August 2026 (S.O. 4711(E) and its companion),
reported for the Central sphere; a West Bengal factory is normally the State's sphere and no State
notification was found. Loaded unconfirmed and listed below.

## Numbers (worked in calc.py before the tests)
- Bonus 8.33%: 12,000 at a minimum wage of 10,558 -> 879.48; 9,000 -> 749.70; 21,000 -> 879.48;
  21,000.01 -> none; minimum wage 6,500 -> floor 7,000 -> 583.10.
- WB PT on 25,000: September 2026 130, October 2026 100. LWF: 3 and 30 in June and December.
- PF 12% of 20,000 at the 25,000 ceiling once confirmed: 2,400.00.

## Tests that failed without each fix (reverts.py)
- figure refusal (statutory.figure_as_of): SetupStatutoryTests.test_a_loaded_ceiling_is_paid_at_by_no_run_
  until_the_controller_confirms_it -> "AssertionError: ValidationError not raised"
- slab schedule refusal (PayComponent.schedule_on): test_a_loaded_schedule_is_paid_at_by_no_run_until_
  the_controller_confirms_it -> "AssertionError: ValidationError not raised"
- bonus rule (Payslip._ceiling): BonusRuleTests -> 4 errors
- go-live lines: GoLiveStatutoryTests -> "'FAIL  Statutory: TDS: No confirmed figure covers today' not
  found"
- TAN (challans.company_tan): test_no_challan_and_no_return_without_a_tan -> "ValidationError not raised"
- names (statutory_names.section_on): test_the_return_names_the_form_of_its_period ->
  "[('194Q', '1000.00')] != [('393(1)', '1000.00')]"
- scan: TdsReturn titled "(26Q)" -> the line listed.

## Test runs
At the tip f76a843 (after the session restart), fastsettings, --exclude-tag migration, --parallel 2:
core OK (599, 4 skipped), accounting OK (356), purchasing OK (1341), sales OK (1051), hr OK (570),
gst OK (239), imports OK (43), e2e OK (318, 199 skipped: PostgreSQL races), web OK (198, browser,
PLAYWRIGHT_CHROMIUM_EXECUTABLE set, after npm run build).
Earlier, on the working tree before the commits:
hr OK (570), purchasing OK (1341), sales OK (1051), accounting OK, gst OK, core OK (4 skipped),
imports OK, e2e OK (318, 199 skipped: the PostgreSQL race lanes, not run), web: 198 run, the 9
sweep failures (returns answering 400 without a TAN) fixed by giving the sweep a TAN, then
apps.web.tests_browser_sweep OK (3, browser, after npm run build) and tests_statutory_names OK.
audit_invariants: no findings. makemigrations --check: no changes (at the tip).
The restart killed one chain (exit 144 mid-sales); everything was rerun at the tip after it.
Migration lane (--tag migration, default settings): at the tip: accounting OK (2), core OK (7), hr OK (1); purchasing and sales
were still running when this was written (detached: /home/user/work/calc_fix_statutory/miglane.sh, results
appended to /home/user/work/calc_fix_statutory/mig_summary.txt). Before f76a843 accounting's lane failed
(the NOT NULL above).
Not run: the PostgreSQL race tests (no lock order changed; pay_over now reads the TAN before its
transaction) and the suite under DJANGO_TIME_ZONE=Asia/Kolkata.

## Figures left for the accountant (all loaded unconfirmed; none could be read at its official source)
The proxy refused every official domain (incometaxindia.gov.in, egazette.gov.in, indiacode.nic.in,
labour.wb.gov.in: DNS failure or CONNECT 403), and WebFetch failed on every secondary site too; only
search-result summaries were readable. So no row is confirmed, and each row's source column says what
was seen. The ones where sources disagree or nothing was found:
1. EPF/EPS ceiling 25,000 from 17 September 2026 (S.O. 5109(E)); and how September is split (the ERP
   reads the ceiling on the period's last day, so all of September at 25,000 unless entered otherwise).
2. Bonus: whether the Central notifications (7,000 or minimum wage; 21,000) or a State notification
   govern a West Bengal factory; and whether "minimum wage" there is the Central or the State's.
3. Which WB minimum-wage schedule covers woven polypropylene sacks, zone and skill; the July 2026
   figures loaded are factoHR's "Factories not covered" and "Bottling and packaging" totals (basic+VDA
   undivided, vda loaded 0). The plastic-industry schedule was not found. January 2027 VDA not out.
4. TCS on scrap: 2% (loaded) or 1% from 1 April 2026; base with or without GST (form_code 6CE kept).
5. TDS 393(1) table serial numbers (not loaded: "393(1)" only); 194J threshold 50,000 vs 30,000;
   194I threshold (50,000 a month, 2.4 lakh or 6 lakh a year) and the ERP's per-bill single threshold
   cannot express "a month"; 194Q 0.1% over 50 lakh under the 2025 Act; 194H 2% / 20,000.
6. Form 144 for 27Q (one outlet says 140); Form 130 for Form 16; challan ITNS 281N (portal FAQ summary).
7. WB PT band edge 25,000 or 30,000 for the 100-rupee band; the notification number 1607-F.T.; the old
   schedule's start date (loaded as 1 April 2008, a placeholder for "before 1 October 2026").
8. WB LWF employer 30 (loaded) or 15; supervisory exclusion ceiling 10,000 (2023 Amendment Act).
9. ESI 0.75% / 3.25% and 21,000 under the Code on Social Security (unchanged per secondary sources).

## What I could not do / limits
- ESI rates and the bonus percentages are loaded and checked by go_live_check but no engine reads them:
  ESI and bonus components still carry their percentage on EmployeeCompensation. A component rate that
  differs from the confirmed figure is not caught.
- PT/LWF schedules can be confirmed over the API only; the pay component screen has no Confirm button.
- The migration leaves every existing StatutoryFigure and PT/LWF band unconfirmed: an installed plant's
  next run is refused until the Controller confirms them. That is the decided rule; say it at upgrade.
- Challans paid before this have blank tan and form (not known then); the serializer falls back to the
  section's code for section_code.
- TdsRate/TcsRate.authority is 128 characters; long sources are cut there (the full text is in the CSV
  and the SeededDefault label, 255).
- apps.imports was also run (go_live_check lives there); the brief's list did not name it.
- audit_invariants: added "hr.statutory_gaps" to CALLED_FROM_OUTSIDE (its only caller is go_live_check,
  a management command the check does not read).

## Defects seen and not fixed
- apps/accounting/tds.py:62 TdsSection thresholds are undated fields, so a threshold change (194J
  30,000 -> 50,000 on 1 April 2025) restates every earlier year's catch-up. Kind: a setting read live
  where a fact should be kept. Scenario: 194J bills of 40,000 in May 2024 under a 30,000 threshold,
  threshold edited to 50,000 in 2025: the 2024 year's catch-up is now judged at 50,000.
- apps/accounting/tds.py:113 194I's threshold is "50,000 a month" in law; the ERP's single threshold is
  per bill: two 30,000 bills in one month (60,000) deduct nothing. Kind: rule the model cannot express.
- apps/hr/statutory_check.py minimum-wage line fails for every active employee without a scheduled
  employment and skill, managers included: the law exempts no one by pay, but the plant may want
  managerial staff out of scope; for the accountant.

## Second round (coordinator's points 1 and 2)

Commit 0cca92e "ESI and the bonus percentages read their dated figures: they were inert" (tip).
- What was inert: ESI_EMPLOYEE_RATE, ESI_EMPLOYER_RATE, ESI_WAGE_CEILING, BONUS_MINIMUM_PERCENT and
  BONUS_MAXIMUM_PERCENT were loaded and confirmable and read by nothing (audit shape 2).
- Now: a component marked ESI/ESI employer is taken at the dated rate of the slip's period and covered up
  to the dated ceiling (Payslip._percent, _coverage_ceiling), refused while unconfirmed, kept on the line
  (kept_terms and the line's rate). One source: an ESI component refuses its own coverage ceiling and a
  person's ESI row must be 1 ("insured"); migration hr 0034 moves existing data. A bonus rate outside the
  dated least/most is refused, both kept on the line.
- audit_invariants: unread_figures() widens the unread-settings check to every FigureKind (a mention only in
  the go-live check is not a read), with a planted test. Against the previous payroll.py it reports 5
  inert figures; at the tip none.
- Test that failed without it: EsiReadsTheDatedRatesTests.test_a_run_before_the_change_and_a_run_after_it_
  each_read_their_own_rate: "[(120.00, 1.0000), (120.00, 1.0000)] != [(90.00, 0.7500), (390.00, 3.2500)]".
  Mid-year change: 0.75% to 1% from 1 October 2026 on 12,000: September 90, October 120 (rupee up);
  employer 3.25%: 390 both.
- Fixtures changed to the one source: StatutoryTestCase.mark(), plant month (figures entered by the
  Payroll Officer and confirmed by the Controller over the API), manufacturing tests_piecework.

Runs: hr OK (575), accounting OK (356), e2e OK (318, 199 skipped), manufacturing.tests_piecework OK (27),
fastsettings --exclude-tag migration. PostgreSQL (database fix_statutory) payroll races: tests_races
PayrollRaceTests, OneDecisionAtATimeRaceTests, RunPostedAsTheRegisterChangesRaceTests,
PayrollOutputRaceTests, AnAdvancesPaymentAndItsRecoveryRaceTests and tests_races_recall_and_payroll_locks
ThePayrollLocks, TheMonthsFiguresAtOnce: OK (42). --tag migration: purchasing OK (10; the summary line
is blank because its log has NUL bytes from a duplicate run I started and killed, the log ends "Ran 10
tests ... OK"), sales OK (10). audit_invariants no findings; makemigrations --check no changes.
After the restart, at 0cca92e: purchasing --tag migration rerun clean, OK (10); hr --tag migration OK (1,
with migration 0034); audit_invariants no findings; makemigrations --check no changes. Everything is in
/home/user/work/calc_fix_statutory/mig_summary.txt.

## Review round (review_statutory.md, five findings)

The reviewer's probe commits are cherry-picked on top of 0cca92e: 71914c3, e7df918, 737945f, ddd9d99, 6c721fa, b01043f. That is 0cca92e..30992a0, which holds six commits, not the seven the brief mentioned. Each message already ended with `Gate: {GATE}` and the two trailer lines, so none was reworded. All 21 probes pass at the tip.

| Commit | Finding | Probe that failed without it |
|---|---|---|
| 5c86fca (+ a3c7a5a, the screen's duplicate hint that broke `npm run build`) | 1: `statutory` link is the Controller's (serializer refuses a change; POST pay-components/{id}/relink/, hr.relink_paycomponent, Controller only, recorded "statutory pt to none"; migration hr 0035); go-live decides PT/LWF use from employees' work states and the states the levy files name | p03 |
| 1ba12ee | 2: ENGINE_FIGURES per engine; the run reads through read_figure(engine, kind), refusing a kind its engine does not list; go-live lines built from the same list; Overtime line + statutory/overtime.csv; every FigureKind asserted to belong to an engine | p04 (both subtests) |
| 2935ef8 | 3: statutory_names.period_names(tax, day, section), the one place a period is named; challans named by the month they pay (tan, form, section kept); return heading, rows and TCS collections ask it | p12, p17 |
| 4f87364 | 4: confirm_schedule(user, seen) takes every band as read ({"bands": [...]}) and refuses a missing or changed band | p02 |
| 4d2ff9e | 5: TcsDeclaration.form set from its received day through names.csv (read-only; migration accounting 0033 renames stored rows by date); 27Q/144, 24Q/138, Form 16/130 keys removed (no such document is built); the screen scan builds its pattern from every number in names.csv, with a test that each key is seen; "Form 127"/"27C" removed from the TCS screens and keywords | p15 |

Runs at a3c7a5a, fastsettings with --exclude-tag migration and --parallel 2. Browser tests ran after npm run build.
- **Fast tests:** hr OK (592), accounting OK (356), sales OK (1052), purchasing OK (1347), gst OK (239), core OK (599, 4 skipped), e2e OK (318, 199 skipped), manufacturing.tests_piecework OK (27), imports OK (43), web OK (199).
- **PostgreSQL** (database fix_statutory): the seven pay-run race classes passed, OK (42).
- **--tag migration:** hr OK (1). The accounting result, the audit and makemigrations --check are in /home/user/work/calc_fix_statutory/mig_summary.txt (lane `reviewlane.sh`), under "review round".
- **Finished:** --tag migration accounting OK (2). audit_invariants: no findings. makemigrations --check: no changes.
