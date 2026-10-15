Integrator here. Your payroll work has had its independent review: $SP/reports/review_people.md, with 12 findings. Five of them block the gate. The probes are commit 3b75e17 in $SP/review_people (based on fbfc49d). Read the report first. Three of the five blockers are reverse paths, mistake 2: a void, a return, a re-payment.

The tree has moved on. Work in a NEW worktree from the integrated tip:
  git -C /home/user/hello-world worktree add --detach $SP/fix_people2 b0d8234
Link .venv and node_modules, then `git cherry-pick 3b75e17` so the probes are in. Your commits are in that tip already, rehashed.

Commits:
- `Gate: {GATE}` (literal) and the two trailers.
- Each commit names the row (O222 for the dated figures; otherwise "g21 payroll") and the finding ("review RP1").
- Fix O236 and O237 as well (already registered: the past-58 pension line's account, and the 1,801 rounding). Name them in their commits so the rows come off.

Decided by the integrator, so not for the owner:
- **RP5 and A7.** PT and LWF are owed once per employee per period, however many runs pay them: PT per month, LWF per its months (June and December in West Bengal).
  - A second run in the same month deducts only the balance due on the month's total wages, less what an earlier run of the month already took.
  - An advance's instalment is likewise per month, not per run.
  - Each run keeps, on its lines, what earlier runs of the period took, so its register and its void read kept figures.
  - A void of the earlier run re-opens the balance; say what the later run then shows.
  - Test two runs in June (LWF and PT), a run voided between them, and a leaver's final run mid-month.

Fix in this order, each with its probe passing and a regression test named after what it protects:
1. **RP1.** An advance's payment voided while the run that recovers it posts. The void and the recovery must decide under the same locks: the advance, then the employee, as `hold_the_employee` and the lock order say, re-read after the lock. Prove the forced order on PostgreSQL (`postgres://erp:erp@localhost:5432/fix_people2`; drop test_fix_people2 first). It must fail without the fix and pass with it.
2. **RP2.** The bank file's payment voided while a returned transfer's receipt stands: refuse it in words, or unwind both in one entry. Net pay payable must foot to the slips after every order of void, return and re-pay (audit shape 4).
3. **RP3.** A return receipt voided after the slip was paid again pays twice. Refuse the void while the slip is paid by anything after it, or make the void re-open exactly what it closed. Foot net pay payable.
4. **RP4.** Advance recovery posts to the account the advance was given against, kept on the advance at giving (audit shape "a setting read live"). Re-pointing the component must change nothing posted or derived.
5. **RP5 and A7**, as decided above.
6. **O236 and O237.**
7. **RP6.** The bank file screen reads "Paid" and offers "Return a transfer" after its payment is voided. The screen reads the file's real state.
8. **RP7.** `hours_a_day` of 0 or less is refused at save and over the API, and a run never dies on a bare DivisionByZero.
9. **RP8 to RP12**, if the budget allows. Otherwise list them, and the integrator registers them.

Before reporting:
- Run hr, core, accounting, e2e, apps.web (the whole app, not only apps.web.tests: the demo loader lives there) and the browser tests you touched (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium; `npm run build` first; vitest).
- Then audit_invariants and makemigrations --check.
- Read each result before committing.

Budget: about 100 tool calls. Append a "Review fixes" section to your report, and send me 10 lines back.
