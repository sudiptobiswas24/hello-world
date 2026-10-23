# fix_trade2: O336, O335, O298, O294, O295, O296, O297, O299, O337

Worktree /home/user/work/fix_trade2, from d4ae073 with the trade audit's probes (d1d3afd, here 50293dd)
cherry-picked. Not pushed. Worked figures: /home/user/work/calc_fix_trade2/notes.py and o337.py.

## Commits

| commit | ids | what |
|---|---|---|
| 0bdbac7 | O336, O335 | `accounting.mixins.given_back_or_waiting()`: posted notes and drafts waiting both count in quantity_creditable / quantity_debitable, both returns' allocations, and every note line's save (`refuse_holding_past_what_is_left`) |
| 6d56816 | O298 | `refuse_a_note_off_its_original()` (control account, correction_date) at save and post; `refuse_a_line_past_the_document()` at line save and `refuse_giving_back_past_the_document()` at posting for notes with typed lines |
| d9da5e5 | O294 | collected-basis commission counts the net sale's share of the money (money x subtotal / total) |
| c12d93b | O295 | `_dunning_level_due()`: the highest level reached, only if above every level sent |
| ab01c53 | O296 | run_dunning holds the invoice (lock_rows) and re-reads its notices before writing one |
| 9e849f2 | O297 | Bill.create_debit_note always asks correction_date |
| 801d37f | O299 | `SalesOrder.sees_its_cost(user)` / `reasons_told_to(user)`: approval GET and the confirm refusal give a non-approver no margin figure |
| 6d0ceac | O337 | data migrations sales 0078, purchasing 0081: kept approval figures of foreign orders put in the base |

Each row came off docs/RISKS.md in its commit. Every message ends with the literal `Gate: {GATE}` and the two attribution lines.

## Each fix and the test that failed without it

- O336/O335: apps.sales.tests_partial_returns.AReturnWaitingAsADraftHoldsItsInvoiceLineTests (4) and
  apps.purchasing.tests_lifecycle.AReturnWaitingAsADraftHoldsItsBillLineTests (3). Without:
  `AssertionError: Decimal('10.0000') != Decimal('6')` (AR credits 6 of 10 while a draft of 4 waits),
  `Tuples differ: (1, Decimal('25.00')) != (2, Decimal('25.00'))` (with AP's draft on bill A, a store hand's return drafted on A again, not B),
  `Decimal('5.0000') != Decimal('0')`.
  The ceiling at posting already existed for lines naming a line (refuse_correcting_past_what_it_holds, posted notes): a draft could not post past a hand credit; what was missing was the room offered. The typed-line ceiling is O298.
  Two tests asserted that a refused draft held nothing (sales AReturnAfterAClaimGivesBackWhatIsLeftTests, purchasing ADebitLineIsHeldToItsShareOfTheLineTests); under the decided rule a waiting draft holds its quantity, so they now delete the refused draft first (assertions on posted figures kept).
- O298: ANoteMadeInCodeKeepsToItsInvoiceTests (sales.tests_partial_returns, 5) and ANoteMadeInCodeKeepsToItsBillTests (purchasing.tests_lifecycle, 4): each `ValidationError not raised` without the fix. Each also changes the row behind save()'s back and shows the posting refuses it. Three fixtures broke the rule and were dated/sized right, assertions unchanged: sales tests_deposits ADraftCreditNoteGivesNothingBackTests and purchasing tests_prepayments ADraftDebitNoteGivesNothingBackTests (drafts dated before their originals), sales tests_gst test_a_mixed_note_rounds_its_own_lines_without_the_bound_ones (a note of 110.10 on an invoice of 30.15; the invoice is now 30 at 10.05; the bound line's tax is 0.90 either way).
- O294: apps.sales.tests_audit_round2.CommissionOnCollectedCashLeavesTheTaxOutTests: `Tuples differ: (Decimal('1200.00'), Decimal('60.00')) != (Decimal('1000.00'), Decimal('50.00'))`; part paid `(600.00, 30.00) != (500.00, 25.00)`.
- O295: apps.sales.tests_audit_round2.ADunningReminderOnlyGoesUpTests: `Lists differ: [60, 30, 7] != [60]`; levels reached in turn still send [7, 30, 60].
- O296: apps.e2e.tests_races.TwoDunningRunsAtOnceRaceTests on PostgreSQL, without the lock: `Lists differ: ['done', 'refused: duplicate key value violates uni[106 chars]ts.'] != ['done', 'done']`; with it OK.
- O297: apps.purchasing.tests_lifecycle.ADebitNoteByHandIsDatedByTheCorrectionRuleTests: `ValidationError: ['A debit note on BILL-2026-00001 ... is not dated 2026-10-21: it is dated on 2026-10-31.']` (O298's save refusing the old default; before O298 the note was dated before its bill).
- O299: apps.sales.tests_approvals.ARepIsNotToldTheMarginTests, through the API as a Sales Rep and as AR Manager: `AssertionError: '-300.00' is not None`.
- O337: ApprovedFiguresPutInTheBaseMigrationTests in sales.tests_approvals and purchasing.tests_budgets (MigrationExecutor, TransactionTestCase, @tag("migration")); with the conversion taken out:
  `{... 'total': '50000.00', 'margin': '-300.00'} != {... 'total': '4500000.00', 'margin': '95.56'}` and the purchasing mirror.

Probes moved into the normal test files: S1, S2, S2b, S5, S7, S8, R1, P1, P2, P2b, P3, P4. Left in the probe files (not mine): S6, S9, S10 (O292/O293, already fixed in the base), P5, R2.

## Runs

At the tip 6d0ceac. At the integrator's request (machine overloaded, then rebooted) no whole module was run; the whole-module lane I had started was killed unfinished.
- Touched and added test classes (fastsettings, --exclude-tag migration, --parallel 1): sales tests_partial_returns, tests.AReturnAfterAClaimGivesBackWhatIsLeftTests, tests_gst.RecordedTaxTests, tests_deposits.ADraftCreditNoteGivesNothingBackTests, tests_audit_round2, tests_approvals; purchasing tests_lifecycle, tests_audit.ADebitLineIsHeldToItsShareOfTheLineTests, tests_prepayments.ADraftDebitNoteGivesNothingBackTests, tests_budgets: 171 tests OK (/home/user/work/calc_fix_trade2/touched.txt).
- Earlier, on each fix in turn: sales + purchasing whole after O336 (13 failures, all other probes or the three tests then put right); sales + purchasing + accounting whole after O298: 2,666 tests, the only failures the then-open S7, S8 and P4 probes and the three fixtures put right in 6d56816. Every file using run_dunning or commission_report: OK.
- --tag migration (default settings): the two O337 migration tests OK (/home/user/work/calc_fix_trade2/migration.txt). The other migration tests of sales and purchasing were not run.
- PostgreSQL (database fix_trade2, test databases dropped first): apps.e2e.tests_races.TwoDunningRunsAtOnceRaceTests OK; fails without the lock (above). The rest of tests_races was not run.
- makemigrations --check: no changes. audit_invariants (after O336, and after O298): one finding, inventory, not mine (below); not run on the final tip.
- Not run: apps.gst, apps.core, apps.web whole, browser tests, Kolkata time zone.

## Questions for the owner (nothing decided)

1. O299: the margin is shown to whoever holds sales.approve_order. Should another permission (a cost permission, the Sales Manager) decide it? A rep still reads the floor ("below the 20.00% floor").
2. O298: a note with a typed line is held, all its lines together, to the original's booked net less posted notes. A note whose every line names a line is held line by line only, so a return credited after a typed goodwill credit of the whole invoice can still credit the goods: 1,000 goodwill, then 10 x 100 returned, receivable -1,000. Should the whole-document ceiling apply to every credit note? (I did not, to keep the per-line paisa rule of O199 the only rule on goods notes.)
3. O336: a draft note now reserves what it gives back until it posts or is deleted; a refused or forgotten draft blocks a hand credit of the same goods. Wanted, or should drafts expire?
4. O337: the migrations assume every kept approval predates O293, true only if they ship in the same release as O293. A database that ran g23's O293 code and approved foreign orders since would be converted twice. The sales margin is recovered from the kept 2-place figure (100 - (100 - m) / rate), so it may differ from a fresh calculation by a paisa.
5. O294: the collected basis takes the net share pro rata to the invoice total (TCS included in the total). Should TCS be left out of the divisor?

## Defects seen and not fixed

- O299 mirror (security): apps/core/approvals.py `refuse_going_past_the_policy` words its refusal with policy_breaches() text, so a rep editing a confirmed order past its approval reads "Gross margin is -300.00%, below the 20% floor." Same for Quotation approvals if they weigh margin (not checked).
- audit_invariants on my tree reports one finding in a module I did not touch: `named draw past the promise: inventory/models.py:StockMovement._settle_money:830 takes self.lot off without asking refuse_drawing_what_is_promised()`. Present before my first commit (run after 0bdbac7's edits, which touch no inventory code); another lane's row, or new in the base.
- O335 as written (two receipts of 5, bills A and B, two returns) was already held: receipts_billed_by() replays the first return's goods out of A. The defect needed a draft not made by a return (AP's own draft on A) to show; that is the regression test.
