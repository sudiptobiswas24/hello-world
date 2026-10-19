# g22 review of fix_stores (f2fb5de..162b5ca, 25 commits)

Worktree: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/review_stores (detached at 162b5ca, one commit on top: a197d12, probes only, unpushed).
Probes: 58 tests in six files, `tests_review_stores_probes.py` in apps/purchasing, apps/manufacturing, apps/assets, apps/quality, apps/core, and `apps/manufacturing/tests_review_stores_scale_probes.py`.
Run: `rs_fast.sh <module>` (fastsettings, SQLite, no migrations). Full log: reports/review_stores_probe_run.log.
Result on the tip: 47 pass, 11 fail. Each failure below is a finding; every finding names its probe.

## Gates

| Check | Result |
|---|---|
| `audit_invariants` | `No invariant findings.` (reports/review_stores_audit.log) |
| `makemigrations --check --dry-run` | `No changes detected` |
| `apps.e2e.tests_races` on PostgreSQL (`DATABASE_URL=postgres://erp:erp@localhost:5432/review_stores`, test_review_stores dropped first) | 98 tests OK in 667.953 s (reports/review_stores_races_pg.log), includes the O242 race |
| O241 browser test (`TheDecisionOfferedIsTheServersTests`, fresh `npm run build`, chromium) | 1 test OK |
| `--tag migration` (O27, 0039, 0031, 0084) | not run by me |
| The ~1100-test suite, SQLite or PostgreSQL | not run by me; only the author's per-area runs stand |

## Findings, by severity

### F1. HIGH, blocks. A re-batch voided before a landed cost is released leaves the freight on the shelf and credits cost of sales
- Probe: `apps/purchasing/tests_review_stores_probes.py` `StoresReviewLandedProbes.test_p11_rebatch_voided_before_release_foots`. FAIL: `Decimal('130.00') != Decimal('50.00')`.
- Scenario: 10 at 5 received as L-1 (50); 80 of freight landed (shelf 130); L-1 re-batched into L-2 (6) and L-3 (4); the re-batch voided; the freight released.
  - Should be: shelf 50, inventory 50, cost of sales 0, freight expense 80.
  - Is: shelf 130, inventory 130, cost of sales **-80** (a credit with no sale), freight expense 80. Shelf and ledger agree with each other and both are 80 too high; the profit is 80 too high.
- Cause: `pooled_into` (apps/manufacturing/rebatch.py:307) follows only `_standing_lines` (rebatch.py:295). Once the re-batch is voided its take-out has no standing line, so `follow_value` (apps/inventory/costing.py:416-431) calls the 80 "gone" at the take-out, and `_to_the_runs` books "the rest" to cost of sales. The void's bring-in (rebatch.py `void`, :178-205) is not linked to its take-out by `moved_from`.
- Kind: reverse path of a fix written the same round (CLAUDE.md mistakes 2 and 5). The author's seen #9 wrote the forward path (a re-batch followed into the batches made) and not its void.
- Suggested shape: have the void's bring-in carry `moved_from` (as `move_stock` does) or let `pooled_into` also follow a voided re-batch's take-out back to its bring-in. Share it, so the two cannot differ.
- Sibling, LOW (F10): the other order. `test_p10_rebatch_voided_after_release_foots` FAILS: after the release, `Rebatch.void` is refused with "L-2 has been used since it was made" (rebatch.py:193, `line.lot.movements.exclude(pk=...)`), because the release's value-only movements (quantity 0) count as use. A mistaken re-batch can then only be undone by a re-batch the other way.

### F2. MEDIUM, should be fixed before release. MRP now counts the tape on a backflushed run's creels twice
- Probe: `apps/manufacturing/tests_review_stores_probes.py` `StoresReviewCreelProbes.test_q1_loading_a_doff_on_the_run_that_needs_it_does_not_change_the_cover`. FAIL: `Decimal('97.959184') != Decimal('197.959184')`.
- Scenario: a released backflushed run needs the tape; 100 kg of D-1 is loaded on its warp creel. Nothing is used or gone. Cover (opening balance less what live runs still draw) falls by exactly the 100 kg loaded.
- Cause: seen #2's fix (apps/planning/mrp.py:129-150, `total += promised`) takes the creel's tape out of the opening balance; the run's own demand (`work_order_demand`, mrp.py:332, `required - component.quantity_issued()`) is not reduced, because a backflushed load issues nothing. The kilos are subtracted as unusable and again as the run's need. Before the fix MRP overstated cover by the creel's kilos; now it understates it by the same, and raises a purchase for tape standing on the loom.
- Suggested shape: demand for a backflushed run should net `on_the_creels` (or the opening balance should not subtract the run's own creels).

### F3. MEDIUM-LOW. O133's guard stops at a pick naming no batch; a doff on a creel is issued away by name
- Probe: `StoresReviewCreelProbes.test_q2_a_named_doff_on_a_creel_cannot_be_issued_to_another_run`. FAIL: `ValidationError not raised : 100 kg promised to L-17's creel was issued away by name`.
- Scenario: D-1, 100 kg, loaded on a backflushed run's creel; a second run's issue naming D-1, 100 kg, posts. The first run's roll can no longer be booked ("creels hold X kg not yet drawn" at the output, mid-shift).
- Cause: `plan_issue` honours a named lot without asking the commitment (apps/inventory/picking.py:61-71; only `allocate` subtracts `committed_elsewhere`). `load_tape` checks the creel at load time only.
- The author's design ("anything named explicitly is honoured") is stated in `plan_issue`; the row's title was the unnamed pick. Reported so the owner decides whether a named pick of promised tape should also be refused or warned.

### F4. MEDIUM. A hand-typed asset in service before the books opened is depreciated into months before the books opened
- Probe: `apps/assets/tests_review_stores_probes.py` `StoresReviewAssetProbes.test_a6_depreciation_never_posts_before_the_day_the_books_opened`. FAIL: `9 != 0`.
- Scenario: books opened 1 January 2026; Loom, 12,000, life 48 months, acquired and in service 1 April 2025, no `depreciated_before`/`opening_depreciation`. Placed in service: Dr plant 12,000 / Cr opening 12,000 on 1 January 2026. `depreciate(through 31 Jan 2026)` posts nine charges of 250 dated April to December 2025, before the books existed, and the January one.
- Effects: expense and accumulated depreciation sit in months before the opening day with no cost beneath them (at 31 December 2025 the plant account reads 0 and accumulated depreciation 2,250 credit); the opening day can never move again (BooksOpening refuses once anything is posted on or before it). The register foots to the ledger today, so the health check says nothing.
- Cause: `_month_ends` (apps/assets/models.py:501) skips months only up to `depreciated_before`; `place_in_service` / `_bring_in_as_an_opening_balance` (:422) does not hold a typed-in asset's charges to the opening day. The author's own test (`again.depreciate(through=2026-01-31)` in `test_removed_before_anything_else_is_posted_against_it`) takes this path.
- Owner's call: refuse placing it in service without saying what the old system took, or start its charges at the opening day.

### F5. LOW. BooksOpening is guarded when changed, not when first set
- Probe: `test_a9_the_books_cannot_be_opened_on_a_day_after_things_were_already_posted`. FAIL: `ValidationError not raised`.
- Scenario: an entry (5) posted on 1 November 2025; `BooksOpening.objects.create(opened_on=2026-01-01)` succeeds. The change path refuses exactly this (apps/accounting/models.py:1837-1853: the posted-on-or-before check sits in the `self.pk is not None` branch only).

### F6. MEDIUM-LOW. O203 is fixed to the letter and the plant's own calendar is still not read
- Probes: `apps/quality/tests_review_stores_probes.py` `StoresReviewWorkingDayProbes.test_w1_...` FAIL `'2026-06-08' != '2026-06-06'`; `test_w2_...` FAIL `'2026-06-02' != '2026-06-03'`. `test_w3` (a Sunday, a company-wide holiday) passes.
- Scenario 1: `PlanningSettings.working_days = "123456"` (the plant works Saturday); a failure posted Friday 5 June falls due Monday 8 June. The plant works Saturday 6 June.
- Scenario 2: `PlanningSettings.holiday_region = "WB-KOL"` with a WB-KOL holiday on Tuesday 2 June; a failure posted Monday 1 June falls due Tuesday 2 June, the holiday that shuts this plant.
- Cause: core hard-codes Monday to Friday (apps/core/models.py:485, `WORKING_WEEKDAYS`) and hr registers `plant_works_on` (apps/hr/calendars.py:74), which reads holidays with region "" only. The plant's own week and region live in `PlanningSettings.working_days` and `holiday_region` (apps/planning/models.py:98-109), and `WorkingCalendar` (apps/planning/leadtime.py:70) already reads them. The author's own test files a "WB-KOL" holiday as a decoy to be ignored. The brief told the author to register hr's calendar; the plant's settings are the other half.

### F7. MEDIUM. The scale bridge posts a net weight as the gross weight
- Probe: `apps/manufacturing/tests_review_stores_scale_probes.py` `StoresReviewScaleBridgeProbes.test_s6_a_net_weight_is_not_posted_as_the_gross_one`. FAIL: `['100'] != []`.
- Scenario: an indicator tared on the core sends `ST,NT,+0100.0kg`; four frames post `{"gross_kg": "100"}`. The station then subtracts the core's tare from it again.
- Cause: the shipped pattern captures `kind` (GS|NT) (deploy/scale_bridge/scale_bridge.py:52) and `FrameReader.read` (:87) and `payload` (:158) never look at it.
- The commit's own pattern comment says "gross or net". Fix: send only GS frames, or refuse to start with a pattern that can send NT unless the config says net is what is wanted.

### F8. MEDIUM-LOW. A reading whose answer is lost is posted twice
- Probe: `StoresReviewScaleBridgeProbes.test_s7_a_post_whose_answer_was_lost_is_not_posted_a_second_time`. FAIL: `['106.8', '106.8'] != ['106.8']`.
- Scenario: the server stores the 106.8 kg reading and the answer times out; after `retry_seconds` the bridge posts it again (scale_bridge.py:173-178), still standing. The server keeps both (`post_reading`, apps/manufacturing/station_scale.py:69, creates a row each time). The first is used by the roll's booking; the second is fresh and unused, so one confirm on the same roll again is not refused ("one reading weighs one roll").
- Fix: a reading id from the bridge (made once per settled weight) that the server de-duplicates.

### F9. LOW. The bridge builds its credentials for an `http://` server
- Probe: `test_s9_the_password_is_never_in_a_log_line_and_is_not_sent_in_clear`. FAIL on its last assertion only: `poster()` (scale_bridge.py:225) accepts an `http://` server and sends HTTP Basic in clear. The log half of that probe passes: the password and its base64 form are in no log line, including the error path.
- Note: the brief said "the station's token"; the bridge uses its own user's password from a mode-600 file (RUNBOOK.md:20-35), which the commit says is deliberate.

## Probes that PASS (the claim holds on the tip)

O210 (shelf foots to the inventory account after each; freight returns to the right accounts), under the average and FIFO unless a method is named:
- p1: freight landed on 10 in inspection, 4 cleared, released: stock room 52 then 20, inspection 78 then 30, cost of sales 0.
- p2: the same with the freight landed after the 4 were cleared.
- p3: 4 cleared and 6 sent back from inspection, then released.
- p4: all cleared, 6 issued to a run, 2 returned to the vendor, then released: shelf 10, cost of sales 0.
- p5: two shelves, 6 moved, 2 back, 4 issued to a run, then released.
- p7: a re-batch into two batches under the average, FIFO and specific identification, then released.
- p8: a pool of two batches (one with the 80) into two, three methods: 180 before, 100 after.
- p9: re-batched, 6 of L-2 issued to a run, released: freight to the run, cost of sales 0.
- t1, t2: a put-away of 6 cancelled, freight landed before or after, released (O11 with O210), both methods.

O133: q3 (half drawn leaves only the undrawn promised; the pick offers only what is free), q4 (a run closed frees its creels), q5 (a withdrawn load frees the doff), q6 (the warehouse form of `on_creels_undrawn`).

O29 and O34 (apps/assets):
- a1: a period with depreciation uncharged does not close.
- a2: the admin has no box that closes a period around `close()`.
- a3: a mid-period disposal closes and the ledger foots.
- a4: a charge taken back by a disposal and reinstated is owed again or charged.
- a5: reopened, a new asset in service, closing again asks for the depreciation.
- a7: an asset typed in before (31 Dec) and on (1 Jan) the opening day comes in; one after it is refused; plant 24,000, opening -24,000, register foots.
- a8: the opening day and account cannot move once an asset stands on it.
- a10: `remove_opening` works before depreciation and is refused after depreciation, after a disposal, and twice; the register foots after.
- a12: the health check goes red for 500 posted to the plant account with no asset behind it.

O12, O13, O14:
- a13: no day to come as an in-service date.
- a14: go-live depreciation is a day that has come and is never edited, before or after putting in service.
- a15: status changes by no door but the method (model, admin read-only, API PATCH).
- a16: no stale status door after a save.
- r1: by API, as each of the roles `setup_roles` makes, only those with `accounting.post_journalentry` place in service, remove an opening, uncapitalise or depreciate; `dispose` follows `assets.dispose_fixedasset`; the list follows `assets.view_fixedasset`.
- r2: `capitalise` is the posting roles' alone.

O232: c1 (a check taken on the second roll holds its bundles, not the first roll's), c2 (a withdrawn check holds no bundle).

O231:
- g1: for a recall, an unmeasured batch, a rejection, a quarantine bay, a batch in two places and a derived hold, the page gives the same words as `held_because`.
- g2: an advisory wait is the same words.
- g3: 10 queries for 25 batches and 10 for 100.
- g4: 10 queries for 8 inspected batches and 10 for 40.
- Observation: an expired batch gets no words from either gate; the page and the single gate agree.

O241: h1-h3 (the flag is true for any role over a standing hold and the server refuses the Inspector's release and concession; false where nothing holds the batch). The browser test passes.

O45: p1 (every admin of a model with a `posted` field carries the shared mixin), p2 (a posted record is neither changeable nor deletable through it and no `list_editable` reaches one).

O225, by recorded frames:
- s1: an unstable frame is never posted and restarts the count.
- s2: a held weight is one reading; the next roll of the same weight is another after the empty scale.
- s3: a dropped port is reopened and nothing across the gap counts (2 frames before + 3 after is not 3).
- s4: a malformed frame breaks the run.
- s5: grams convert to kg; `lb` and upper-case `KG` are never read (silently not posted).
- s8: a refused post is retried while the weight stands and dropped once the scale moves.
- Zero and negative weights are posted (the empty platform's zero, a drifting negative); the server stores them and `take_reading` refuses them ("nothing is on it"), so a zero between rolls is what lets the next roll of the same weight through. Not a finding.

## Suspicions with no failing probe (unproven)

- O232, a stock fabric roll dismounted and mounted again: the check keeps the first `RollMount`, the coated roll is tied to the second, so the check does not reach it (process_rolls.py `mount_roll` makes a new mount for a lot; station_coat.py `off_coatings` matches mounts, not the roll). Whether the remounted roll's later metres should wait is a call for the Quality Manager; not probed to a verdict.
- The bridge has no cap on its read buffer (scale_bridge.py `frames`): a wrong baud rate that never sends a newline grows it for ever. About 1 KB a second at 9600 baud.
- A reading is fresh for 60 s (`FRESH`, station_scale.py) and the bridge posts a settled weight once, so a roll confirmed more than a minute after it settled needs lifting and putting back. Safe; likely to be met on the floor.
- An installation that imported its opening balances by the old route and holds a registered but undrawn hand asset would post a second opening entry when that asset is placed in service (`_bring_in_as_an_opening_balance`); assets already in service are untouched. Not built as a fixture.
- Not probed: O210 for a sale by delivery or for backflush consumption (what `_to_the_runs` does with a backflush issue that has no MaterialIssueLine of its own); O202 beyond the author's tests; the lock order of `Inspection.post` against `ReceiptInspection.accept` (the author's race covers post against void only); O23's void answer; O134 on PostgreSQL.

## Notes

- Probe files are fixtures-on-the-author's-classes with the inherited tests stripped (`only_mine`), so a change to the author's fixture changes the probes.
- The browser build wrote under apps/web/static in the worktree; it is ignored by git and not committed.
- Commit: a197d12, `Gate: {GATE}` and the two trailers as the brief says; nothing pushed.
