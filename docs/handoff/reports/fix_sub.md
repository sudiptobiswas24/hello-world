# fix_sub, gate g19: report

Worktree: $SP/fix_sub (detached; base 3e33028). Tip **11c5d6c**, 5 commits, not pushed.

| Commit | Closes |
|---|---|
| 042f599 | O147: every planned withdrawal asks the release gate from its warehouse |
| d1d4130 | O135, O148: assemblies from a batch held since it was sent are received and held |
| 1e6a80d | O149: a subcontract return gives each batch back what it gave, total first |
| 54253cc | O150: a complaint's batches are added and removed with the complaint held |
| 11c5d6c | O135, O148 follow-up: the assemblies' lineage carries a hold, never a pass |

## What changed, by id

**O147 (my shared rule: the outbound-warehouse check).**
- `apps/inventory/tracking.py`: `lot_held(item, lot, warehouse)` now has to be given the warehouse, and passes it to every registered hold. `allocate` gives it the warehouse, takes `holds_let_through`, and says in its shortfall message what the first refusal was.
- `apps/inventory/picking.py`: `plan_issue` asks the gate about everything it plans, from that warehouse: a named batch, or an item kept by no batch, is asked there; batches it chooses are asked through `allocate`.
- `apps/manufacturing/bales.py`: `pack()` passes its warehouse.
- `audit_invariants` `release_past_the_gate` has a new finding, "release asked without the warehouse". It reports any `check_released`/`why_held`/`lot_held` call outside the gate that names no warehouse, or names it as None. Run against the old `pack()`, it reports bales.py:202.
- One existing planted test in `apps/core/tests_audit_release.py` used `check_released(lot.item, lot)`, which is now itself a finding. It now passes `warehouse=run.warehouse`; what that test checks is unchanged.

**O135/O148 (decision 22).**
- `_consume_components` plans with `holds_let_through=held_by_quality`. Only a quality hold (rejected, held for rework, or re-made from such a batch) is let through. Expiry was already no bar there and still isn't. A batch that is uninspected under a mandatory plan still bars the receipt, and so does every other refusal; a test covers this.
- Lineage: `purchasing.assembled_from` is registered through `register_lot_sources` in `apps/purchasing/apps.py`. It names the held batches an assembly lot was consumed from.
- 11c5d6c corrects my own d1d4130, before integration. That version named every source batch. Quality treats a lot whose sources all passed as passed, so an assembly under its own mandatory plan would have read as released without being inspected. The provider now names only held sources.
- An assembly kept by no batch must land in a quarantine warehouse (`first_receipt_step().is_quarantine`). Any other destination is refused, with a sentence saying why, before anything is written.
- `GoodsReceipt.held_assemblies()` is worked out each time it is asked, never stored. `post_receipt` returns it as `"held"`, each row giving line, item, lot, quantity, warehouse and why.
- Reverse path, tested: returning the held assemblies, or `reject()` from quarantine, puts F-1 10 and motors 20 back at the subcontractor, at 80.00. Goods receipts have no void; the return is their reverse.
- **Where `tell_the_quality_manager` belongs:** in `GoodsReceipt.post()` (apps/purchasing/models.py), right after the final `super(GoodsReceipt, self).save(update_fields=[... "posted" ...])`. When `self.held_assemblies()` is not empty, call `tell_the_quality_manager(self, summary)`, with the summary built from the rows' "why". Not built here, as the brief says.

**O149.**
- `_restore_components` works out what each consumed batch is still owed: what it gave, less what earlier returns of that line put back. "Earlier" means posted returns, plus this return's own earlier lines.
- A return that completes the line puts back exactly what is left. Any other return shares `round_money(owed x qty / left)` in proportion to what each batch is still owed, using `_apportion`: whole units when every figure is whole, otherwise to the paisa, with the remainder going to the largest remainders.
- Each row put back now names its return line (`_returned_note`). Rows written before this change, which name no line, are found by the return's number.
- `post()` now goes through a receipt's lines in key order.

**O150.**
- `ComplaintLot.save()`/`delete()` are now `@transaction.atomic` and go through `answer_to_its_document` with `fixed=not is_open()`. `locked_before_it()` names the same rows.
- There are two `race()` tests on PostgreSQL: one for an add as the complaint is rejected (the probe), one for removing the only batch as the complaint closes. In each, the second step starts only once the decision holds the complaint, so the order is fixed and the result does not depend on which thread runs first.

## Tests that failed without each fix
- O147: `ComponentsNeverLeaveQuarantineTests` (both tests) and `tests_bales.NoBaleOutOfQuarantineTests`: "AssertionError: ValidationError not raised". QA keeps 100.
- O135/O148: `AssembliesFromABatchHeldSinceTests` and `UntrackedAssembliesFromAHeldBatchTests`: "ValidationError: Only 0.00 of FRM - Frame can be allocated at SUB - Acme Assembly; 10.00 short. 10 is on the shelf and held by quality." The API test got "400 != 200".
- 11c5d6c: `AssembliesAreNotPassedByTheirComponentsTests`: "ValidationError not raised" (A-2 read as released).
- O149: `ComponentsComeBackBatchByBatchTests`: "(Decimal('0.990000000000000'), Decimal('2.01000000000000')) != (Decimal('1'), Decimal('2'))" and "(Decimal('1.20000000000000'), Decimal('1.80000000000000')) != (Decimal('1'), Decimal('2'))".
- O150 (PostgreSQL, lock reverted): both `ComplaintRaceTests` tests failed with "Lists differ: [True, True] != [False, True]".
- The rejection in my tests uses a mandatory plan (passed 1 Jan, failed 2 Jan) with the probe's numbers, not an advisory failure.
- Expected figures are in $SP/calc_fix_sub/scenarios.py.

## Gates run
- At tip-1 (54253cc), fastsettings: purchasing, inventory, quality, sales, manufacturing, planning, e2e, core, gst, web: 5178 tests OK.
- At tip, after 11c5d6c: purchasing, quality, sales: 1877 tests OK.
- PostgreSQL with Asia/Kolkata: tests_subcontract, tests_bales, tests_complaints and the complaint races: 71 tests OK.
- `audit_invariants`: no findings. `makemigrations --check`: no changes (no migration).

## Could not do / not done
- The full suite was not run, per fix_common.
- The audit skill pass was not run.
- `held` appears only in post_receipt's answer, not on GET of a receipt; computing it for every row of a list would cost queries.

## Defects seen and not fixed
1. **Rule** (apps/purchasing/models.py `_consume_components`): consumption at the subcontractor is first-expired-first-out over every batch there, not the batches sent on that order. Example: F-1 (held, expires Dec 2026) went 10 for order A, and F-2 (released) went 10 for order B. Receiving B first consumes F-1, so B's assemblies are held and A's are released, although A's were made from F-1. Consumption should prefer the batches whose TRANSFER_IN carries the order's number.
2. **Question** (apps/quality/release.py `may_be_drawn(item, lot)`): it takes no warehouse, so anything counting usable stock through it must leave out quarantine bays itself. mrp.py:1003/1055 does; I did not check other callers. My new audit check does not cover `may_be_drawn`.
3. **Rule** (purchasing `GoodsReceipt.accept`): untracked held assemblies in quarantine are released by `accept()` (`change_receiptinspection`) with nothing tying that to quality's verdict on F-1. That may be intended (O128 gives that permission to quality), but it is the only way an untracked held assembly leaves the bay.
4. **Probe**: once the other agent's advisory change lands, `SubcontractHeldAfterSendingProbe` in docs/handoff/reports/review_qual_tests/tests_probe_review.py, and the existing `BatchKeptComponentsTests.test_a_batch_quality_holds_is_not_sent` (apps/purchasing/tests_subcontract.py), both build their hold from an advisory failure. Whether they still hold depends on whether "awaiting decision" counts as HELD. Check at integration.
5. **Behaviour change to note**: `pick_list` now reports a gate refusal on a named batch, or on an item kept by no batch in a quarantine bay, as a row "problem" instead of planning it.

## Follow-up (integrator's items 1 and 3)

Two more commits on the worktree; the tip and hashes are in the final line of this section.

**Item 1: each order consumes its own batches first.**
- `GoodsReceipt._sent_on_the_order(item, warehouse)` works out, per batch, what that order still has at the subcontractor:
  - what went there under the order's number (TRANSFER_IN, or TRANSFER_OUT, at the subcontract warehouse);
  - less what the order's receipts consumed, this receipt's earlier lines included;
  - plus what the order's returns put back.
- `allocate`/`plan_issue` take a new `preferring` argument ({lot: quantity}). Those batches are taken first, soonest to expire first, then anything else at the subcontractor, first-expired-first-out. Both passes apply the same gate and expiry rules, and each batch comes out as one row.
- The scenario is the test (`EachOrderConsumesWhatWasSentOnItTests`). F-1, held, went 10 for A; F-2, released, went 10 for B. B is received first and consumes F-2, so B's assemblies are released; A then consumes F-1 and its assemblies are held. `held_assemblies()` returns [] for B and one row for A.
- Mirror paths, each tested:
  - Partial receipts 5/5/5/5, A and B in turn: each order takes its own batch.
  - A full return of B's receipt (receipts have no void; the full return is how a receipt is undone) puts F-2 back. B received again consumes F-2, not A's F-1.
  - Short of its own: with A grown to 14, A takes its 10 of F-1, then the 4 of F-2 that B's partial return put back.
- Failed without the fix: "(Decimal('0'), Decimal('10')) != (Decimal('10'), Decimal('0'))" for B received first and for B received again; "(Decimal('0'), Decimal('10')) != (Decimal('5'), Decimal('5'))" for the partial receipts. The short-of-its-own test passes either way; it guards the fallback.

**Item 3: held assemblies cleared out of quarantine are a concession.**
- `GoodsReceipt.accept(..., by=None, reason="")`: when a selected line is kept by no batch and was consumed from a batch quality holds (`held_sources`), the call needs a decider and a reason. `check_not_raised_by(by, authors(receipt, "lines"), receipt)` refuses anyone who made or changed the receipt or its lines.
- The API's accept passes `request.user` and `{"reason"}`.
- New field `ReceiptInspection.decided_by` (FK to the user, editable=False). Migration 0066 was read from a file: it adds that one field to receiptinspection, nothing else. The reason goes in `note`.
- Goods held only for inspection clear as before; tested.
- Tested through the API as people in roles (`HeldAssembliesAcceptedAreAConcessionTests`):
  - A Quality Manager with no reason: refused.
  - A person who is both Warehouse Staff and Quality Manager and created the receipt: refused, "somebody else approves it".
  - A second Quality Manager with a reason: cleared, with decided_by and note recorded.
- Failed without the fix: "200 != 400" for both refusals, and "(None, '') != (<User: qm>, 'Frames re-checked at the vendor')".

**Gates (follow-up).**
- Touched modules with fastsettings (purchasing, inventory, quality, sales, manufacturing, planning, e2e, core, gst, web): 5187 tests OK.
- `audit_invariants` and `makemigrations --check`: see the last lines of this section.

**Choices to note.**
- Who may decide the concession: anyone holding `purchasing.change_receiptinspection` (Quality Inspector or Quality Manager) who did not receive the goods. I did not limit it to the Quality Manager. The integrator may want it narrowed to match the advisory-decision owner (decision 22).
- The decider is the logged-in user (`settings.AUTH_USER_MODEL`), not an `hr.Employee`.
- `audit_invariants`: no findings. `makemigrations --check`: no changes (run on the final state and again on each commit's state).
- PostgreSQL with Asia/Kolkata: tests_subcontract and inventory, 493 tests OK.
- Commits: 04c561f (item 1, consumption order) and 80f256a (item 3, concession and migration 0066). The tip is now **80f256a**, 7 commits on 3e33028, not pushed. Worktree has no uncommitted changes.
