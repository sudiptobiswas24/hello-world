# fix_sub2: the g19 review's findings (review_sub.md)

Worktree: $SP/fix_sub2, made from d341ff3. Tip **3065cb7**: 7 commits, not pushed. Each commit message ends with the literal `Gate: {GATE}`. The review's probe files are copied in but left untracked.

| Commit | Closes |
|---|---|
| 7d00a21 | F1 (blocker): held assemblies are not received into a lot that already stands on its own inspection |
| 3da45c3 | F3: a pass of a batch made from a held batch waits for the Quality Manager |
| 9364a66 | F2: whoever posted a receipt counts as having received it |
| 55256ff | F5, F7: `held_assemblies()` lists only what is still held, and reads only subcontract lines |
| fc9caa5 | F4: the Quality Manager's to-dos close when nothing on the receipt is held, by every path |
| 265899d | F6: a missing reason is no reason |
| 3065cb7 | F9: the receipt inspection's answer names who decided |

F8 and F11 were left alone, as asked. F10 needed no fix: it exists before g19 (see below).

## F1, the blocker
**Rule.** In `_consume_components`, a receipt of assemblies made from a batch quality holds is refused when it names a lot that has a standing inspection of its own. The refusal says: "... in A-1 they would be read by that inspection, not held: receive them into a lot of their own."

**Why this is enough.** `release_status` reads a lot's lineage only while the lot has no inspection of its own. So the one sound way to keep held material held is to never let it land in a lot that only its own inspection speaks for. Into a new lot, the lineage holds it as before.

**Test.** `HeldAssembliesKeepALotOfTheirOwnTests`, built on the review's scenario (5 into A-1, A-1 passed on 15 January, F-1 rejected on 2 February, the other 5):
- the second receipt into A-1 is refused, and A-1 still holds 5 while 5 of F-1 are still at the subcontractor;
- received into A-2 instead, the receipt lists the held assemblies.
- Failed without the fix: "ValidationError not raised".

**Other ways held material could join an inspected lot:**
1. **A re-batch join into an existing lot.** Closed already: `apps/manufacturing/rebatch.py:157` refuses an output batch that has held stock before ("a re-batch makes new batches"). Read, not probed.
2. **A lot already inspected whose source is rejected afterwards.** The first 5 assemblies in A-1 above were made from F-1 too. A-1's pass on 15 January still stands after F-1 is rejected on 2 February, because a lot's own inspection outranks its lineage. Nothing refuses this. It is a recall question for the owner: should a later rejection of a source re-hold lots that were inspected on their own? Not fixed.
3. **Production output (a work order receipt) into an existing lot code that has been inspected.** Lineage between work-order inputs and outputs is not registered with quality at all, so a held input never holds its output by lineage in the first place. Not probed.
4. **An inventory adjustment or count naming an inspected lot.** It brings in quantity, not held material with a lineage. Not probed.

## F3
**Rule.** `Inspection.post` judges a pass of a lot whose sources quality holds as awaiting the Quality Manager's decision, using the existing awaiting flow (`tell_on_posting`, `decide()`). The sources come from `lot_sources`; the check is the new `release.held_inside(lot)`. A poster who holds `quality.decide_inspection` releases the lot directly, as before.
- `decide()` may concede such a pass (`_check_concession(awaited=True)`).
- The to-do, the posting's answer and the release gate's sentence now say the lot passed but is made from the held batch. Before, they said it failed.

**Splits and joins: covered.** `lot_sources` includes the re-batch provider. A split of a rejected batch that then passes is that batch tested back into use, and a join's pass dilutes what is held; both are the Quality Manager's decision.

**One existing test was changed.** The O146 test `test_a_split_is_not_passed_by_a_verdict_dated_before_its_parents` ended with a pass posted by nobody in particular releasing a split of a rejected drum, which is exactly this shape. Its last step is now posted by a superuser. The new test `test_a_split_of_a_held_batch_passed_by_anyone_else_waits_for_the_quality_manager` covers the waiting path.

**Tests.** `AssembliesFromAHeldBatchPassedWaitTests` and the new split test. Without the fix they failed with "Disposition.ACCEPT != Disposition.AWAITING" and "(Disposition.ACCEPT, ReleaseStatus.RELEASED) != (Disposition.AWAITING, 'held')".

**Not tested:** `decide(CONCESSION)` on a pass that waited. It needs a login linked to an hr Employee, and I did not build that fixture. The code path is open but unproven.

## F2
- New field `GoodsReceipt.posted_by`, set by `post(by=...)`; the API passes the person asking. Migration 0067 adds this one field to goodsreceipt; I read it from its file.
- New `received_by()` = the authors of the receipt and its lines, plus whoever posted it. Both the telling and `accept()`'s `check_not_raised_by` now use it.
- Test: `WhoeverPostedTheReceiptReceivedItTests`, through the API. Without the fix: "['qm', 'stores_qm'] != ['qm']" and "200 != 400".

## F5, F7
- **F7:** `held_assemblies()` reads only lines made from components, and asks for the held sources first. On a plain receipt that is one query, down from 61.
- **F5:** a row is listed only while something is still held, at the quantity still held:
  - kept by batch: what the line received, less what went back, never more than the lot holds;
  - kept by no batch: what of the line is still in quarantine.
- Test: `HeldAssembliesSayWhatIsStillHeldTests`. Without the fix: "Decimal('10.0000') != Decimal('6')" twice, and "61 != 1".

## F4
**In purchasing:** `close_when_nothing_is_held(by, outcome)` closes the to-dos once nothing is held. Both the concession and `create_return` (on the receipt being returned) call it; a partial return leaves the to-dos open.

**In quality, without importing purchasing:** a new registered listener, `apps.quality.release.register_lot_decided(fn(lot, by))`.
- Quality calls it from `Inspection.post` when the verdict does not wait, and from `decide()`.
- Purchasing registers `receipts_quality_released`. It checks every receipt that has an open to-do and whose assemblies are that lot or were consumed from it.

**Test:** `TheQualityManagersToDoClosesWhenNothingIsHeldTests` covers a full return, F-1 passed again, and A-1 released by the Quality Manager. Without the fix: "1 != 0" and "([], 1) != ([], 0)" twice.

**Not covered:**
- A verdict on a batch two steps away (for example a split of F-1, when the receipt consumed F-1 itself) closes nothing until F-1 or the assembly lot is decided.
- A void of an inspection that puts a lot back on hold does not reopen to-dos.

## F6
Only a string counts as a reason. Test: `ANullReasonIsNoReasonTests`, through the API with None, False, 0 and []. Without the fix: "200 != 400 : None".

## F9
The receipt inspection's decided rows now carry `decided_by` (the login name, or null). Test: `TheConcessionNamesItsDeciderTests`. Without the fix: `KeyError: 'decided_by'`.

## F10: the paisa existed before g19
I ran the review's scenario at 3e33028 (before g19): 0.333 frame per assembly, F-1 1.11 and F-2 98.89 at 8, receive 10, return 1, 1, 3, 2, 1, 2.

| | At 3e33028 | At this tip |
|---|---|---|
| Shelf change | 0.00 | 0.00 |
| Inventory account change | +0.01 | +0.01 |
| GRNI | 0 | 0 |
| PPV | -0.01 | -0.01 |

So g19 did not introduce it. The likely mechanism is the one the reviewer read: each return books its own price-difference entry, rounded on its own, against the shelf's average. Please register it. The test I ran at base is $SP/calc_fix_sub2/tests_f10_base.py.

## The review's probes at my tip (3065cb7)
I copied the review's three probe files into the worktree untracked: apps/purchasing/tests_probe_review_sub.py, apps/manufacturing/tests_probe_review_sub.py and apps/e2e/tests_probe_review_sub_race.py. I ran them on SQLite with fastsettings, and on PostgreSQL with Asia/Kolkata (which includes the 2 race probes). The results were the same on both.

**Now passing (9 of the 13 that failed at d341ff3):**
- F1: test_held_assemblies_do_not_join_a_lot_already_passed
- F2: both poster probes
- F3: test_an_inspector_passes_the_tracked_lot_the_manager_alone_may_concede_untracked
- F4: test_a_return_of_the_held_assemblies_closes_the_quality_managers_todo and test_the_hold_lifts_when_the_source_is_conceded_but_the_todo_does_not_close
- F5: test_a_returned_tracked_lot_is_no_longer_reported_as_held_on_the_receipt
- F6: both reason probes
- F7: test_a_plain_receipt_posts_without_a_query_per_line_for_held_assemblies
- F9: test_the_concession_shows_who_decided_it_on_the_receipt_inspection_answer
- Every probe that passed before still passes, except the two below that now fail by design. Both race probes pass on PostgreSQL.

**Still failing (4):**
- **F10** (test_fractional_returns_leave_the_ledger_and_the_shelf_agreeing): the paisa that exists before g19. Left for you to register.
- **F8** (test_may_be_drawn_asks_the_bay_only_when_given_it): left alone, as you asked.
- **test_tracked_assembly_without_a_plan_has_a_way_out** (passed before, fails now, by design): it asserts that an ordinary pass of the held lot, posted by nobody in particular, releases it. That is the F3 shape. The way out now is the Quality Manager: a pass they post, or their decision on a pass that waited.
- **test_partial_acceptance_leaves_the_todos_open_while_anything_is_held** (passed before, fails now, by design): it asserted the old quantity "10" as a fact ("the row still says 10 received"). After F5 the row says 6. Its to-do half still holds.

## Gates at 3065cb7
- Touched modules with fastsettings (purchasing, quality, manufacturing, inventory, sales, planning, e2e, core, gst, web; the untracked probes included): 5340 tests. The only failures are the 4 probes above; every committed test passes.
- PostgreSQL with Asia/Kolkata (tests_subcontract, the purchasing probes, the race probes, ComplaintRaceTests, quality, manufacturing tests_quality): 320 tests, the same 4 probe failures only.
- `audit_invariants`: no findings.
- `makemigrations --check`: no changes. 0067 is the only new migration; it adds goodsreceipt.posted_by and I read it from its file.
- Not run: the full suite, and the 4 tests tagged migration.

## Defects seen and not fixed
- **Rule** (apps/quality/release.py `release_status`): a lot inspected on its own stays released when a source is rejected later. F1's first 5 assemblies in A-1 are the example. Recall question for the owner (item 2 under F1).
- **Rule** (apps/purchasing/models.py `receipts_quality_released`): a verdict on a batch two steps away does not close a receipt's to-dos, and an inspection void that puts a lot back on hold does not reopen them.
- **Untested** (apps/quality/models.py `decide`): conceding a pass that waited. It needs an employee-linked login, which no fixture here builds.
- **Process**: tests_subcontract's F2, F6 and F9 test classes inherit HeldAssembliesAcceptedAreAConcessionTests, so its 7 tests run four times. That costs about 20 seconds; it is wasteful, not wrong.
