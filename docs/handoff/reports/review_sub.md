# review_sub: adversarial review of g19 subcontract work (09d277d..b2fb38f + 34e4042)

Worktree: $SP/review_sub (detached from d341ff3). Probes committed there, not pushed.
Files: apps/purchasing/tests_probe_review_sub.py (48 tests), apps/manufacturing/tests_probe_review_sub.py (3),
apps/e2e/tests_probe_review_sub_race.py (2, PostgreSQL).
Run: `PYTHONPATH=scripts/gate .venv/bin/python manage.py test apps.purchasing.tests_probe_review_sub --settings=fastsettings`.
Failing probes = findings (13 of 48 purchasing probes fail; the first 44 were run on SQLite and on PostgreSQL with
DJANGO_TIME_ZONE=Asia/Kolkata with the same 12 failures, the last 4 on SQLite only). Every other probe passes. No source was edited.

Findings: 11 (1 blocker recommended, 10 not blocking).

## F1. BLOCKER (recommended): held assemblies join a lot that already passed its own inspection, unheld
- Kind: rule. Probe: ReceiveAndHoldProbes.test_held_assemblies_do_not_join_a_lot_already_passed
- Where: apps/quality/release.py:109-121 (`release_status` reads lineage only when the lot has no inspection of its own);
  apps/purchasing/models.py:5944 (`assembled_from`, the provider, is therefore ignored for such a lot);
  apps/purchasing/models.py:6938 (the quarantine rule applies only to `line.lot_id is None`, so a named lot is never asked).
- Scenario: F-1 (10 frames) passed 1 Jan, sent on order PO for 10 assemblies. 5 assemblies come back 1 Feb into lot A-1;
  A-1 is inspected and passed 15 Jan (assembly has a plan). F-1 is rejected 2 Feb. The other 5 come back into the same
  lot A-1: the receipt posts, A-1 holds 10, `why_held(assembly, A-1, store)` is "" (released). By code reading
  `held_assemblies()` is [] too (it asks the same status), so no to-do and the Quality Manager is not told.
  Decision 22 ("nothing can issue or ship it until quality decides") is false for 5 assemblies made from a rejected batch.
  Partial receipts into one vendor lot is the ordinary way this happens.
- Blocks: yes, recommended. Cheapest closure: refuse (or hold) a receipt of held assemblies into a lot that stands on its
  own inspection, in `_consume_components` beside the existing untracked refusal.

## F2. Poster of the receipt is told to decide it and may concede it
- Kind: rule. Probes: ConcessionProbes.test_the_poster_is_not_told_to_decide_what_she_received,
  ConcessionProbes.test_the_poster_does_not_concede_what_she_received
- Where: apps/purchasing/models.py:6428-6431 (`barred=authors(self, "lines")`) and :6685 (`check_not_raised_by`);
  apps/core/approvals.py:36 (`authors()` counts created/updated stamps and CREATED/UPDATED events only; posting stamps neither).
- Scenario: clerk keys the receipt and lines; "stores_qm" (Warehouse Staff + Quality Manager) posts it over the API.
  `quality_told.told` = [qm, qm2, stores_qm]; stores_qm's accept with a reason returns 200. The existing test only covers
  the receiver who also created the receipt.
- Blocks: no (shared `authors()` rule; the gap is what "received" means). Name it for the owner.

## F3. Tracked assemblies leave the hold by an Inspector's ordinary pass; untracked need the Quality Manager and a reason
- Kind: rule (question). Probe: ReceiveAndHoldProbes.test_an_inspector_passes_the_tracked_lot_the_manager_alone_may_concede_untracked
- Where: apps/quality/release.py:121-132; apps/quality/models.py:712 (re-inspection only has to be dated after the source's verdict).
- Scenario: 10 assemblies of lot A-1 made from rejected F-1; a Quality Inspector (add/change inspection, no decide_inspection)
  posts a passing inspection of A-1 dated after the rejection: lot released, no reason, no named concession.
  The same rejected frames made into untracked assemblies need `decide_inspection`, a reason, and not the receiver (b2fb38f/34e4042).
  The brief says "until inspected itself", so this may be intended; the two paths are not equally strict.
- Also (same probe family, test_the_hold_lifts_when_the_source_is_conceded_but_the_todo_does_not_close): the tracked lot's hold
  lifts with no inspection of the lot when F-1 itself is re-passed, because the provider is read live.
- Blocks: no.

## F4. The Quality Manager's to-do is closed only by accept(); a return, a lot inspection or a decision on F-1 leaves it open
- Kind: minor. Probes: ReceiveAndHoldProbes.test_a_return_of_the_held_assemblies_closes_the_quality_managers_todo,
  ReceiveAndHoldProbes.test_the_hold_lifts_when_the_source_is_conceded_but_the_todo_does_not_close
- Where: apps/purchasing/models.py:6733 (the only `close_to_dos`).
- Scenario: 10 held assemblies returned in full to the subcontractor (or F-1 re-passed on 3 Feb): `held_assemblies()` for
  the untracked/passed case is [] yet 1 open to-do "Decide GRN-...: 1 line(s) of assemblies ..." stays on the manager's
  list, with no escalation (receipts have none).
- Blocks: no.

## F5. `held_assemblies()` reports a returned tracked lot, and the full received quantity after a partial acceptance
- Kind: minor. Probe: ReceiveAndHoldProbes.test_a_returned_tracked_lot_is_no_longer_reported_as_held_on_the_receipt
  (the quantity half is asserted as a fact in ConcessionProbes.test_partial_acceptance_leaves_the_todos_open_while_anything_is_held)
- Where: apps/purchasing/models.py:6621-6623 (tracked rows are kept on the lot's status, not its stock; untracked rows on bay stock).
- Scenario: A-1 10 assemblies, all 10 returned (A-1 on hand 0): row still listed, quantity 10, "Nothing issues or ships
  them". Untracked 10 in bay, 4 conceded: row says 10, not 6.
- Blocks: no.

## F6. A null or false `reason` is accepted as a reason
- Kind: rule/minor. Probes: MoreProbes.test_a_null_reason_is_not_the_word_None, MoreProbes.test_a_false_or_zero_reason_is_not_a_reason
- Where: apps/purchasing/views.py:445 `reason=str(request.data.get("reason", ""))`.
- Scenario: QM posts `{"warehouse": 1, "reason": null}` to accept a conceded receipt: 200, note = "None". `false` -> "False", `0` -> "0".
  The blank string is refused correctly (ConcessionProbes.test_a_blank_reason_is_no_reason passes).
- Blocks: no (one line; but "reasoned" is the claim of the commit).

## F7. Every goods receipt post pays about four queries a line for the held-assemblies question
- Kind: minor (performance). Probe: MoreProbes.test_a_plain_receipt_posts_without_a_query_per_line_for_held_assemblies
- Where: apps/purchasing/models.py:6618-6623: `waiting` (`quarantined_quantity()`, ~3 queries) is computed before `sources` is
  tested, and `held_sources` queries movements per line. Runs in `post()` (6426) and again in the `post_receipt` answer.
- Numbers: 61 queries for `held_assemblies()` on a plain 15-line receipt with no components. Test sources first.
- Blocks: no.

## F8. `may_be_drawn(item, lot)` with no warehouse still reads a bay as usable
- Kind: minor. Probe: MoreProbes.test_may_be_drawn_asks_the_bay_only_when_given_it
- Where: apps/quality/release.py:211 (`warehouse=None` default kept). The only guard is the audit_invariants report (clean now).
- Blocks: no.

## F9. The concession's decider is recorded but shown nowhere in the API
- Kind: minor. Probe: VisibilityAndTrackedBooks.test_the_concession_shows_who_decided_it_on_the_receipt_inspection_answer
- Where: apps/purchasing/views.py:406-410 (`inspection` answer lists `note`, not `decided_by`).
- Scenario: qm concedes 10 with "Re-checked at the vendor"; GET .../inspection/ -> decided row has the note and no name.
- Blocks: no.

## F10. A paisa between the inventory account and the shelf after many fractional returns (attribution not shown)
- Kind: books, minor. Probe: LastProbes.test_fractional_returns_leave_the_ledger_and_the_shelf_agreeing
- Scenario: 10 assemblies at 0.333 frame each, F-1 1.11 / F-2 98.89, PPV account set. Receive 10 (ledger +50.00, shelf +50.00,
  GRNI -50). Return 1, 1, 3, 2, 1, 2. After the last: shelf delta 0.00, GRNI 0, inventory account +0.01, PPV -0.01. Per-return
  assembly cost booked 31.64 / 31.6667 / 31.68 against the shelf average 31.664; each return's price-difference entry is rounded
  alone. Frames come back exactly (1.11 and 2.22 after the last return; no batch above what it gave at any step).
- I did not run the scenario on the pre-g19 code, so I cannot say O149 introduced it; the mechanism (rounded per-return entries)
  looks older. Not blocking.

## F11. Commit bodies carry a literal "Gate: {GATE}"
- Kind: minor (process). `git log 440ce14..34e4042`: all eight commit bodies end `Gate: {GATE}`. Fill from the tip's gate lines
  (CLAUDE.md: a gate is done only when every lane printed OK on the same tip) before the commits are published.

## Observation (no probe): fallback consumption ignores holds
When an order is short of its own batches, `allocate` falls back to first-expired-first-out over everything at the
subcontractor (apps/inventory/tracking.py, `then` list). A held batch that expires sooner is taken before a released one
that expires later (B grown 10 -> 14 takes 4 of A's held F-1: ConsumptionOrder probe EachOrderItsOwnProbes.test_order_grown_after_the_first_receipt
passes as designed). B's assemblies are then held and A is short. Worth a sentence in decision 22; not a defect against the brief.

## What held under pressure
- Untracked held assemblies into a store: refused with the sentence, whole receipt rolled back (4 to bay + 6 to store on one receipt: no movement, receipt unposted); 4 to the bay then 6 to a store likewise.
- Untracked assemblies of the order whose own batch passed go to a store; the other order's go to the bay only (same item, two orders).
- Lineage through the assembly lot: a split into two lots and a join with a released lot are both held; a split of the clean lot stays released.
- Advisory failure (awaiting decision) holds assemblies; an Inspector cannot post a pass on the assembly lot while F-1 awaits (O146 follows the lineage); untracked go to the bay only.
- Books, untracked (bay): receive 370.00 for 10 (ledger +50.00, shelf +50.00, GRNI -50.00); reject returns inventory, GRNI and shelf to the start exactly; F-1 10 and motors 20 back; receive again: +50.00 again. Tracked lot: same, then received into a new lot.
- Concession then return from the store: ledger and shelf back to the start.
- Return of released assemblies, 3 then 7: F-1 10, motors 20, frames 80.00, motors 240.00.
- O149: ten returns of one over F-1 2 at 8 / F-2 3 at 9 / F-3 5 at 10: each batch back to 2, 3, 5 and the frames value equals what went out; a two-line receipt (4 and 6) returned in one go gives 4 and 6 back; 0.333 frame returns 1,1,3,2,1,2 never put a batch above what it gave; 4,000 random cases through `_apportion` (whole and paisa) never overshoot and always sum.
- Concession: blank reason refused; Quality Manager with a reason works; superuser works (rule exempts them); Quality Inspector and Stores Manager refused; receiver who created the receipt refused; 255-character reason fine, 256 refused; second part accepted by the same manager works; partial acceptance keeps the to-dos open while anything is held and the second manager's acceptance closes them; a mixed receipt: an Inspector clears the plain line alone, both together refused.
- Telling: only Quality Managers, the receiver-QM who created it barred, the "nobody who may decide" and "nobody signs in" sentences appear; a Quality Manager whose role lacks view on goods receipts comes back in `not_told`, not silently; mail is not sent before commit, one mail goes after, to the manager, naming the receipt; a post rolled back leaves no to-do and no callback.
- Each order consumes its own batches: grown order takes its own 10 then 4 of A's; one batch sent 6 to A and 14 to B: B consumes 14 and leaves A its 6; B and A received then both returned: F-1 10, F-2 10 back.
- O147: `plan_issue` refuses a quarantine bay for an untracked item, a named batch and a FEFO draw; `may_be_drawn(item, lot, bay)` is False. (Transfers out of a bay are refused by `Warehouse.is_quarantine` in apps/inventory/transfers.py:231, read not probed.)
- O150 on PostgreSQL: the three complaint races of the fix, plus mine: a batch moved to another complaint as the first one closes (X must not close naming nothing) and a concession racing a return of the same held assemblies (exactly one wins; bay empties): all pass. Through the API as a Quality Manager: a batch added to a rejected complaint is a 400 and names nothing; the only batch of a closed complaint cannot be deleted; a batch cannot be moved into or out of a closed complaint.
- `audit_invariants`: no findings. `makemigrations --check`: no changes. apps.purchasing.tests_subcontract (48 with two advisory probes): OK.

## Not done
- I did not prove my two PostgreSQL races fail with the lock removed.
- No browser/UI pass; no debit-note (billed) variant of the returns; F10 not run at base.
