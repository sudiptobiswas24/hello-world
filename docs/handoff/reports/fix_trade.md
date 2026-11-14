# fix_trade: O185, O186, O183

Worktree: scratchpad/fix_trade, base 5b7257f, tip 85dc235 (3 commits, tree clean). Calc scripts and logs: scratchpad/calc_fix_trade/.

## Commits
- 898453e O185: a note gives back a line's share of what is left of it, per unit.
- 52c5728 O186: sales 0064 seeds from the approval, historical models only.
- 85dc235 O183: the demo holds its customers in key order; `unchecked()` removed.

## What changed
- O185 (apps/accounting/mixins.py, shared with purchasing): `value_left()` (value left on a line, every posted note counted, claims included; quantity left, goods notes only). `refuse_correcting_past_what_it_holds(..., by_value_only=)` holds a note's value on a line to quantity x value left / quantity left, checked after summing the note's lines; a claim is held to the value left. `priced_to_give_back()` prices a return: the line's own price, or after a claim the goods' share of what is left, total divided last, one unit taking the paisa. Sales: `create_credit_note` uses it; `credit_claim` spreads by what is left on each line; a claim note now goes through the value check at post.
  Numbers (calc_fix_trade/o185.py): 10x100, claim 100, return all 10 -> 900 credited, AR 0, stock back; returns of 5+5 -> 450+450, AR 0; 3x100 claim 100 then credit -> 2 at 66.66 + 1 at 66.68 = 200.00, AR 0; 1 at 1000 on 10x100 refused (cap 100), the 10 then credit, AR 0; 5 at 150 refused (cap 500); two lines 10x100, line 1 credited whole, claim 1000 -> all on line 2 (1000/1000 each), AR 0.
- O186: 0064 uses apps.get_model only. A line with updated_at <= approved_at is seeded at what it holds; a line changed since at max(what it holds, the figure the approval_note names for its label). Total and margin from the note ("The order is X, above", "Gross margin is X%, below") or None; `SalesOrder._covered()` reads a None total as no cover. Lines created after the approval are left out.
- O183: `lock_rows(sahyadri, konkan, narmada, malwa, godavari, kaveri, gulf, refresh=False)` at the start of the sales section; `unchecked` class and the `_lock_order_off` check in `taken()` removed.

## Tests that failed without each fix
- O185 (apps/sales/tests.py AReturnAfterAClaimGivesBackWhatIsLeftTests, 6 tests; apps/purchasing/tests_audit.py ADebitLineIsHeldToItsShareOfTheLineTests): "ValidationError: ... has 900.00 left to give back; this note gives back 1000.00." / "has 400.00 ... 500.00" / "has 200.00 ... 300.00" / "AssertionError: {1: Decimal('1500.000000'), 2: Decimal('500.000000')} != {1: Decimal('1000'), 2: Decimal('1000')}" / "'1 of 10 left: 100.00); this note gives back 1000.00' not found in ''" / "'5 of 10 left: 500.00) ...' not found in ''" / purchasing "ValidationError not raised". 7 of 7 failed.
- O186 (apps/sales/tests_seed_approved_figures.py AnOrderCutBeforeTheSeedKeepsItsApprovalTests, fast; with HEAD's 0064): "AssertionError: {'1': '10.00'} != {'1': '20.00'}". The MigrationExecutor test (tag migration) now covers an uncut and a cut order; it passes run alone (Ran 1, OK).
- O183 (apps/web/tests_load_demo.py test_a_whole_plant_loads_and_its_books_agree under fastsettings, sentinel on, with the lock_rows line removed): "CommandError: The demo loaded only in part; these sections failed: Three months of orders, deliveries, invoices and money received."

## Runs
- sales+purchasing+accounting, fastsettings, O185 state: Ran 1930, OK.
- Full suite on the tip, PYTHONPATH=scripts/gate, fastsettings, --exclude-tag migration --parallel 2: Ran 5734, OK (skipped=174).
- audit_invariants: no findings; makemigrations --check: no changes (on the tip).
- `test apps.sales --tag migration` on the tip: 2 ran OK, 1 setUpClass ERROR (see defect 1). Run alone, the seed MigrationExecutor test passes.

## Could not do
- The migration-tagged run was done once on the tip, not before each of the three commits (each run is ~10 min on SQLite); the O185 and O183 commits do not touch migrations.

## Defects seen, not fixed
1. apps/sales/tests_seed_approved_figures.py (AnOrderApprovedBefore0063KeepsItsApprovalTests, serialized_rollback=True): in `manage.py test apps.sales --tag migration` it errors in setUpClass, "IntegrityError: UNIQUE constraint failed: django_content_type.app_label, django_content_type.model", after apps.sales.tests_reps.EveryoneKeepsSeeingEveryCustomerMigrationTests. Seen with HEAD's (5b7257f) version of the file too, so the O174 migration test has not run in a combined migration lane. Passes alone. Kind: test fixture.
2. apps/accounting/mixins.py priced_to_give_back: for a fractional quantity (q <= 1, or q-1 not whole), a share that the paisa does not divide leaves under one paisa per line on the receivable, and round_money((q-1) x P) can post up to half a paisa over the unrounded cap. E.g. 0.5 kg on a line left at 33.333... Kind: books (rounding), low.
3. sales 0064: an approval given with the approver's own note names no figures, so a line cut after such an approval is still seeded at what it holds (loses its cover); charge lines are never matched by label (a historical model has no ChargeType __str__). Total/margin are seeded None unless the note names them. Kind: books, low; the row's "where the history can tell" boundary.
4. (review_trade3 #3d, P8, unchanged) a typed note line may credit any revenue account, not the one its original line used. Kind: books.
