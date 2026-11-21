# review_statutory: g24 fix_statutory, d76ef6f..0cca92e

Worktree /home/user/work/review_statutory (detached at 0cca92e, probe commits on top, unpushed, 7 commits, last 30992a0).
Probes: apps/hr/tests_review_statutory.py, apps/purchasing/tests_review_statutory.py, apps/sales/tests_review_statutory.py.
Hand numbers: /home/user/work/calc_review_statutory/calc.py.
Run: fastsettings, --exclude-tag migration, --parallel 2: 21 tests, 7 failures (6 findings; p04 counts twice, min and max percent).
audit_invariants: "No invariant findings." makemigrations --check: "No changes detected."
No blocker found. The gate itself held where it was probed: only the Controller confirms over the API, in every other role 403; confirming twice 400;
a posted run's kept figures cannot be edited or deleted over the API and the run's slip and ESI file keep what they posted;
an unconfirmed PT schedule, TDS rate stops the run / the bill over the API with a 400 in words.

## Findings (each has a failing probe)

1. MEDIUM, may block go-live sign-off. Unmarking a levy walks round the unconfirmed gate.
   apps/hr/payroll.py:~340 (schedule_on gates on `self.statutory in SLAB_LEVIES`); statutory_check.py "used" test is the same flag.
   Probe p03. Payroll Officer (who holds change on paycomponent, not confirm) PATCHes the loaded PT component's `statutory` to "";
   an October run for a 25,000 WB employee then calculates PT 100.00 at a schedule nobody confirmed, and go_live_check no longer
   counts PT as used. Fix: refuse clearing/changing `statutory` on a component with an unconfirmed schedule, or gate on slab presence.

2. MEDIUM. go_live_check's Bonus line ignores the two figures the run reads for the rate.
   apps/hr/statutory_check.py:~70 asks only BONUS_FLOOR and BONUS_ELIGIBILITY; Payslip._percent (payroll.py:~1926) also reads
   BONUS_MINIMUM_PERCENT and BONUS_MAXIMUM_PERCENT. Probe p04 (both subtests): with 8.33 or 20 unconfirmed the October run is refused
   ("is not confirmed") but statutory_gaps(2026-10-26) Bonus is [] so the line prints OK. Fix: add both kinds to the Bonus line.

3. MEDIUM. TDS and TCS challans are named by the payment date, not by the deductions they pay: a 1961-Act challan reads 2025-Act.
   apps/accounting/challans.py:~44 `form = name_on(f"{tax}_challan", date)`; TCS also apps/sales/tcs.py:~505 `section.code_on(date)`
   (TDS challan section_code uses the month, so a TDS challan disagrees with itself).
   Probe p12: 194Q deductions on 10-11 March 2026 (tax paid 7 April 2026): challan section_code 194Q (1961) but form "Challan ITNS 281N";
   names.csv's own source says ITNS 281 is for 1961-Act payments. Probe p17: TCS collections of 16 March 2026, return row 206C(1),
   challan paid 7 April section_code 394(1), form 281N. Every March challan paid in April carries the wrong Act's name and, for TCS, the wrong section.
   Fix: name the challan from the month it pays (as the return rows do), one function for both.

4. LOW-MEDIUM. A schedule is confirmed as it stands, not as it was read (shape 1, mirror gap with TDS rates and statutory figures, which take `seen`).
   apps/hr/payroll_views.py SlabViewSet.confirm / PayComponentSlab.confirm_schedule (payroll.py:~455) take no body.
   Probe p02: Controller's screen showed band amount 0.00; Payroll Officer PATCHes it to 999; Controller's confirm (sending the amount it read)
   answers 200 and confirms 999. Whole-schedule confirm confirms whatever each band holds at that second.

5. LOW. TcsDeclaration.form defaults to "127" whatever the date (apps/accounting/tcs.py:121, frontend TcsDeclarationForm initial "127").
   names.csv holds tcs_declaration (27C / 127) but nothing reads it. Probe p15: a declaration received 15 Jan 2026 is stored as "127", not 27C.
   Same inert shape: tds_return_nonresident, tds_return_salary, salary_certificate are in names.csv and read by no code.
   The web scan (HARD_CODED) looks only for 26Q/27EQ/27Q/24Q/challan 281 so cannot see these.

## Unproven or for the accountant (no failing probe)
- Dated figures change on the 1st / mid-month / last day: ESI probe p06 (rate 0.75 to 1 from 1 Oct, 16 Oct, 31 Oct, 1 Nov, whole-month October run
  of 12,000, hand 120 / 120 / 120 / 90) PASSES: the figure on the period's last day governs the whole run (O222). So a rate effective 16 Oct is paid
  for all of October on a monthly run; the same shape the CLAUDE.md table calls "a rate row paid for the whole period". PF ceiling 17 Sep: September
  at 25,000 = 3,000 for a 20,000 basic against 1,800 at 15,000 (the author's open item 1). Two fortnights (p07, 6,000 each) take 45 and 60 correctly.
- PT, LWF and the minimum wage use the same last-day rule; no probe of a PT schedule dated mid-month beyond reading the code.
- setup_statutory's `confirmed_by` column confirms as a named Controller (or any superuser) with no act of theirs; whoever can edit statutory/*.csv
  and run the command can forge a confirmation. Shipped rows have none. By design per the author, flagged for the Controller.
- go_live_check tests only today: an unconfirmed row dated in the future (e.g. a PT schedule from 1 Oct checked on 26 Sep) passes today and stops the run later.
  The Minimum wage line fails for every active employee without employment and skill, even where no bonus component exists (author's own note).
- Bonus minimum of 100 rupees (CSV source text "8.33% or 100 rupees, the higher") is not in the mechanism: wages under 1,200 get under 100.
- ESI coverage ceiling is read at the current run's date while the deciding month is the block's first; with a ceiling rising mid-block a person
  becomes covered mid-block. Plausibly right in law (a rise covers at once), not probed.

## Probes that PASS (claims held)
p01 roles (Payroll Officer, HR Admin, Bookkeeper, AP Manager, Line Manager, Employee Self Service, Sales Rep all 403 on figure and minimum-wage confirm;
Controller 200; empty body 400; second confirm 400). p06, p07 ESI dates. p08 posted-run facts: PATCH value / effective_from / DELETE of a kept ESI row all 400,
still confirmed, a later row dated inside the month does not move the posted line (90.00) or esi_rows wages (12,000). p09 ESI ceiling coverage.
p10 bonus: 20,000 in January pays 791.35 (9,500 x 8.33%), 22,000 in February pays none. p11 bonus rate 25 and 8 refused. p13 2025-Act challan 393(1) / 281N.
p14 TAN malformed over the Company API 400, lowercase kept upper. p16 return without TAN 400 (TDS and TCS). p18 two fortnights of bonus 499.80 + 291.55 = 791.35.
p19 minimum wage VDA from 16 Oct pays the whole month at 10,700 (891.31). p20 PT unconfirmed over pay-runs/calculate 400. p21 deduct_tds with an unconfirmed rate 400.

## Not done
PostgreSQL race lane, Asia/Kolkata lane, browser tests, TCS invoice posting via API (existing NoConfirmedRate tests cover it), full-suite runs.
