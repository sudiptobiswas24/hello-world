# fix_qdec, gate g19: advisory failures wait for the Quality Manager; O145, O146

Worktree: $SP/fix_qdec (base 3e33028). Not pushed.

## Commits

| Commit | Closes |
|---|---|
| a8fdd52 | O145: of two verdicts on one day, the later posting stands |
| 711fd9a | O146: a re-inspection follows the verdicts of the batches it was made from |
| 078363f | RISKS.md "Decisions taken" 22, second half: a failure under an advisory plan waits for the Quality Manager (the feature; no register id) |

RISKS.md is not edited (docs/ is off limits): O145 and O146 come off in the commits named above.

## Each fix, and the test that failed without it

- **O145.** `latest_inspection()` (apps/quality/release.py) ranked one day's
  verdicts by id. It now ranks them by `posted_at` (nulls last), then id.
  `audit_invariants`' release-gate check, widened, reports any function outside
  release.py that picks a standing inspection with `order_by("-inspected_on", ...)`
  (planted in apps/core/tests_audit_release.py, `AVerdictRankedOutsideTheGateTests`).
  Failed without it:
  `apps.manufacturing.tests_quality.AHeldBatchGoesNowhereTests.test_of_two_verdicts_on_one_day_the_later_posting_stands`,
  `AssertionError: ReleaseStatus.RELEASED != 'held'`.
- **O146.** New `lot_and_sources()` / `standing_verdicts(lot, lock=True)` in
  release.py: the lot's standing verdict and those of every batch it was re-made
  from (`lot_sources`, followed back, each once), all the batches locked in the
  one lock order. `Inspection._check_follows_the_standing_verdict` follows all of
  them through `correction_date()`. Failed without it:
  `AHeldBatchGoesNowhereTests.test_a_split_is_not_passed_by_a_verdict_dated_before_its_parents`,
  `AssertionError: ValidationError not raised`.
- **Decision 22 (the feature).** Failed without it:
  - `AHeldBatchGoesNowhereTests.test_a_failure_under_an_advisory_plan_waits_for_the_quality_manager`:
    `AttributeError: AWAITING` (at base, AdvisoryFailureProbe reads REJECT).
  - `apps.e2e.tests_races.QualityDecisionRaceTests` on PostgreSQL, with `decide()`'s
    lock taken off by mock (scratchpad/calc_fix_qdec/nolock_race.py, no source edit):
    `AssertionError: Lists differ: [True, True] != [False, True] : ['done', 'done']`.
    With the lock: OK.
  - `apps.manufacturing.tests_station_tape.DoffTests.test_off_denier_under_an_advisory_plan_waits_for_the_quality_manager`
    without the station change: `ValidationError: [... failed under T-LINE (1000 den) — as specified, which is advisory, so the Quality Manager decides ...]`.
    So the new post() rule, alone, would have blocked an off-gauge doff, film roll or
    bag bundle under an advisory plan at the station. Fixed in
    apps/manufacturing/station_gauge.py and conversion.py: such a batch posts
    awaiting the Quality Manager, and the floor's reason goes in `notes`.
  - New tests: apps/quality/tests_decisions.py (28), two in apps/core/tests_roles.py,
    `AnAdvisoryFailureInTheBrowserTests` in apps/web/tests_browser_quality.py
    (Inspector posts and reads the warning; her `decide/` gets a 403 from the
    server; the Quality Manager opens "Awaiting decision", sees it overdue and
    due 02 Jun 2026, opens it and rejects it).
  - Two existing tests encoded the old auto-reject and were rewritten to the
    owner's rule: `tests_process_rolls...test_a_held_roll_is_not_mounted_on_a_backflushed_run_either`
    (the fabric plan is advisory there; the roll is still refused, now "held: it
    failed QC-..."), and the probe's evidence test, rewritten in tests_quality.

## What was built (decision 22)

- `Disposition.AWAITING`, plus Inspection fields `decided_at`, `decision_login`,
  `told` (the warning as it was said, who was told, who was not mailed),
  `escalated_on`; permission `quality.decide_inspection`. Migration
  quality/0006_advisory_failures_wait (every operation on `inspection`, read).
- `post()`: re-reads the plan; an advisory fail with a disposition already set
  is refused, whoever posts; a blank one becomes AWAITING; "awaiting" typed by
  hand is refused. The concession checks are one method, `_check_concession(by)`,
  shared by post() and decide().
- Gate (release.py `why_held`): "X is held: it failed QC-... on <day> and waits
  for the Quality Manager's decision. It cannot <action> until they decide."
  Not even a rework run may draw it.
- apps/quality/decisions.py, mine: `tell`, `tell_the_quality_manager`,
  `tell_on_posting`, `tell_the_administrator`, `escalate_overdue`,
  `close_to_dos`, `next_working_day`, and `Told` (`told`, `not_mailed`,
  `not_told`, `nobody`, `failures()`).
- **For integration (fix_sub):**
  `apps.quality.decisions.tell_the_quality_manager(record, summary, link="", by=None, body="", due_on=None) -> Told`.
  It gives every active Quality Manager a FollowUp on `record` (due the next
  working day unless `due_on` is given) and a mail. `told.told` lists the
  usernames given a to-do; `told.not_mailed` maps username to why no mail went;
  `told.not_told` maps username to why no to-do could be given; `told.nobody`
  is set when no login holds the role; `told.failures()` gives the sentences.
  FollowUp refuses a person who cannot open the record, so the Quality Manager
  must hold view permission on the receipt's model, or they land in `not_told`.
  (They read purchasing.goodsreceipt and goodsreceiptline today.)
- `Inspection.decide(by, disposition, note)`: concession, rework or reject; it
  needs `quality.decide_inspection`; it always checks `check_not_raised_by`;
  a concession also needs everything post() asks. It is `@serialised`, so the
  first decision stands, and it is stamped rather than dated by hand. It closes
  every open to-do on the inspection, as done by the decider.
- `void(reason, by=None)`: one that waits closes its to-dos, as done by the
  voider (or by each person's own login when voided from code), and the batch
  goes back to the verdict before it. A re-inspection of the batch, or of a
  batch made from it, is refused while it waits.
- API: `POST inspections/{id}/decide/` and `GET inspections/awaiting/` (oldest
  first, `due_on`, `overdue`, `escalated_on`) both need `quality.decide_inspection`.
  The post answer adds `warning`, `told`, `not_mailed` and `not_told`.
- Roles: the Quality Manager gets `quality.decide_inspection`. The Production
  Supervisor and the Stores Manager get view of inspection and reading, so the
  to-do they are given can open its record.
- Office application: `RecordScreen` ActionDef.done may be worked out from the
  answer. The inspection page shows the kept warning while the inspection waits,
  and the three decisions to the decide permission. The list has an "Awaiting
  decision" facet. New screen quality/Awaiting.tsx, in registry.ts as
  `awaiting-decision` with `quality.decide_inspection`.

### Working day and scheduling
Quality may not import hr: apps/quality/models.py says the app depends on
inventory and core and nothing else, so the modules that ask it whether a batch
may move never find it asking them back. So the working day is the plain
weekday, Monday to Friday (hr's DEFAULT_WORKING_DAYS "12345"), and plant
holidays are not seen. Scheduling, for whoever edits RUNBOOK.md: once a day,
before the morning checks:
`45 5 * * * cd /srv/erp && docker compose run --rm web python manage.py escalate_quality_decisions`.
It is safe to run twice: `escalated_on` is set under the inspection's lock.

## Checks run on the tip
- Touched modules (quality, manufacturing, core, purchasing, inventory, web,
  sales), fastsettings, `--exclude-tag migration --parallel 2`: Ran 4414 tests, OK (skipped=55).
- Browser: apps.web.tests_browser_quality, after `npm run build`, with
  PLAYWRIGHT_CHROMIUM_EXECUTABLE set: Ran 2 tests, OK.
- PostgreSQL: `apps.e2e.tests_races.QualityDecisionRaceTests` OK, and it fails
  without the lock (see above).
- `audit_invariants`: No invariant findings. `makemigrations --check`: No changes detected.
- Not run: the whole suite, the DJANGO_TIME_ZONE=Asia/Kolkata lane, the
  PostgreSQL lane beyond the race.

## What I could not do / open questions
- Whoever *posts* an inspection is not among `raised_by()` (authors and
  history only). A Quality Manager who posts someone else's draft without
  changing it may still decide it. The brief named "recorded the readings or
  raised the inspection", and I kept to that. The owner may want the poster
  counted too.
- Mail is sent inside the posting's transaction. A request that rolls back
  after post() returns would leave a mail about a posting that did not stand.
  Nothing in the post view does that today.

## Defects seen, not fixed
- D1 (rule, minor) apps/manufacturing/orders.py:1301: a rework run may still be
  raised for a batch that is waiting. `release_status() != HELD` counts AWAITING
  as held. Its material issue is refused at the gate (fixed in release.py), but
  the run itself is made and released. Scenario: QC-1 fails PP-A under an
  advisory plan; a supervisor raises a rework WorkOrder with `rework_of=PP-A`;
  `release()` succeeds; the issue of 100 kg is refused.
- D2 (data, minor) apps/manufacturing/station_tape.py:141 (`conceded_by=None if passed else supervisor`),
  and the same in the film and bag paths: under an advisory plan the doff names
  the supervisor as having conceded it, though the batch now waits, held, for
  the Quality Manager. Scenario: denier 1,085 against 950 to 1,050, advisory
  plan; `doff.conceded_by` is the supervisor while the inspection reads AWAITING.

## Follow-up (integrator's three items)

Commit 6c47c52 on top of 078363f. It closes D1, D2 and the mail-before-commit defect (open question 2 above). The integrator settled open question 1: who only posts an inspection is not barred from deciding it. The holiday-blind working day goes to the register.

1. **Mail on commit.** `decisions._mail` writes the to-do inside the
   transaction and sends the mail through `transaction.on_commit`. The answer
   still names every mail known at posting not to go: no address, or no mail
   server. That check is `isinstance(get_connection(), NotConfiguredBackend)`,
   and its sentence is now `apps.core.mail.NOT_SET_UP`, shared with the
   backend. A mail the server refuses after the commit is written on the
   record as a Note ("The mail of the to-do to asha did not go: ..."), because
   the answer has already gone by then. Tests that read `mail.outbox` now wrap
   the posting in `captureOnCommitCallbacks(execute=True)`.
   Failed without it:
   - `ItWaitsHeldTests.test_a_posting_rolled_back_after_it_mails_nobody`:
     `AssertionError: Lists differ: [<django.core.mail.message.EmailMessage ob[85 chars]ed0>] != []`
   - `ItWaitsHeldTests.test_a_mail_the_server_refuses_after_the_commit_is_written_on_the_record`:
     `AssertionError: Lists differ: [] != ['The mail of the to-do to asha did not go[81 chars]sed']`
2. **D1, rework runs.** A rework run is refused for a batch that waits, in the
   gate's own sentence ("... waits for the Quality Manager's decision. It
   cannot be reworked until they decide."). The sentence comes from the new
   `apps.quality.release.awaiting_decision(lot, action)`, which `why_held`
   now asks too. All three paths are covered:
   - raised for it: `WorkOrder.save` → `_check_not_reworking_a_decision`;
   - re-pointed at it: the same check, asked whenever `rework_of` changes;
   - released after its batch came to wait: `_check_rework` at release.

   After the decision is rework, the run may be raised and released (test).
   Failed without it: `apps.manufacturing.tests_backflush.AReworkRunIsTheQualityManagersToDecideTests`,
   `test_not_raised_for_it`, `test_nor_re_pointed_at_it` and
   `test_nor_released_once_its_batch_came_to_wait`, each with
   `AssertionError: ValidationError not raised`.
3. **D2, no concession named.** On a batch that waits:
   - `TapeDoff.conceded_by` (station_tape.py), `FilmRoll.conceded_by`
     (station_film.py) and `BagCount.supervisor` (conversion.py) stay empty;
   - the inspection's notes say whose PIN let it be booked, and why.

   Failed without it:
   - `DoffTests.test_off_denier_under_an_advisory_plan_waits_for_the_quality_manager`:
     `AssertionError: <Employee: EMP-0087 - Supervisor> is not None`
   - `WeighedBeforeBookedTests.test_off_weight_under_an_advisory_plan_names_no_concession`:
     `AssertionError: Tuples differ: (False, <Employee: EMP-0087 - Supervisor>) != (False, None)`

   The film path has the same one-line change as the tape path and no test of
   its own.

Checks on 6c47c52:
- touched modules: Ran 4421 tests, OK (skipped=55);
- apps.web.tests_browser_quality: Ran 2 tests, OK;
- `audit_invariants`: No invariant findings;
- `makemigrations --check`: No changes detected.

The PostgreSQL race was not re-run: `decide()`'s locking did not change.
Station coating checks (station_coat.py) and process rolls (process_rolls.py)
also record `conceded_by`, but they post no quality inspection, so decision 22
does not reach them. Left as they were.

## Review fixes (review_qdec)

Commit e248a0e on top of 6c47c52. It closes O10 and review findings 1-4 and 6-11. Finding 5 (Q06) is left as the integrator ruled. The reviewer's probe files were run on the tip and not committed; copies are in scratchpad/calc_fix_qdec/.

**The blocker, and every other way round the decision:**
- **Voiding.** `Inspection.void(by=...)` refuses a login without
  `quality.decide_inspection` in two cases:
  - an inspection that waits: "Voided, R-001 would go with nobody deciding: the Quality Manager voids it";
  - one the Quality Manager decided: "Voided, their decision would be undone".

  The page offers that Void only under the decide permission. A station
  withdrawing a batch together with its output names nobody, so it is not
  asked; the batch goes with it.
- **Deleting (O10).** `Inspection.delete()` refuses a posted inspection, from
  the API or from code. `quality.Inspection` is off `DELETABLE_REPORTED`, so
  the audit's "deletable posted document" check now guards it: with the guard
  removed it reports the inspection. Its stale-exemption test now plants a
  model that is still on the list.
- **Re-inspecting.** Refused while an inspection waits, for everyone with or
  without the decide permission, and also for any batch made from it (the
  standing-verdict rule; review R02 and L01 pass).
- **Already closed before this round.** A plan made mandatory or retired after
  posting (K02), a rework run raised for the batch (6c47c52), and editing a
  posted inspection (save() refuses).

**The rest:**
- **E03.** `escalated_on` is set only when somebody was told. Otherwise the next run tries again and says so again.
- **Q12.** `tell_the_quality_manager(..., barred=raised_by)` leaves out a
  Quality Manager who raised the record (a superuser excepted). They are told
  for information instead, and the answer says "Nobody who may decide it has
  been told: asha raised or changed it".
- **T04 / X06.** The mail sent on commit catches any error and writes it on the record. The posting answers 200, and the other mails still go.
- **S02.** Tape and film record no concession under an advisory plan, whatever the lab finds later.
- **S03.** The doff, film roll and bag bundle answers carry `held`: what posting
  said (held, until whom, who was told), or null when the batch does not wait.
- **V01.** `?disposition=awaiting` lists only posted inspections that have not been voided.
- **Q07 / Q08.** A note that is not text is refused with a 400 in words. So is
  one over 255 characters ("at most 255 characters; this is 300"), and nothing
  is cut.

**Failed without each fix** (each file reverted in turn):
- apps/quality/models.py:
  - test_the_inspector_does_not_void_one_that_waits: 200 != 400
  - test_nor_one_the_quality_manager_decided: 200 != 400
  - test_a_posted_inspection_is_not_deleted: 204 not found in (400, 403)
  - test_told_nobody_it_is_tried_again: datetime.date(2026, 6, 3) is not None
  - test_a_note_that_is_not_words_is_refused_in_words: 500 != 400
  - test_a_long_reason_is_refused_not_cut: 200 != 400
- apps/quality/decisions.py:
  - test_an_unexpected_mail_error_after_the_commit_neither_fails_the_post_nor_stops_the_rest: RuntimeError: boom
  - test_a_quality_manager_who_raised_it_is_not_told_to_decide: 'Told to decide' unexpectedly found
- apps/quality/views.py:
  - test_what_awaits_them_oldest_first_overdue_marked: Lists differ: [2, 1] != [2]
- apps/manufacturing/station_tape.py:
  - test_off_under_an_advisory_plan_names_no_concession_when_the_lab_finishes: <Employee: EMP-0087 - Supervisor> is not None
- apps/manufacturing/station_views.py:
  - test_the_station_is_told_a_doff_is_held_for_the_quality_manager: KeyError: 'held'

One existing test was rewritten to the new rule: the test of voiding an inspection that waits now has the Quality Manager void it.

**Reviewer's probes on the tip:**
- apps.quality.tests_probe_review_qdec: 68 of 70 pass, 1 skipped (G05 stub), 2 fail.
  - Q06 fails, as the integrator ruled (a superuser is not bound by check_not_raised_by).
  - Q01b fails at its own precondition, `assertEqual(self.post(passing).status_code, 200)`.
    The fix now refuses both the void and the pass, so the bypass it describes
    cannot run. My test_the_inspector_does_not_void_one_that_waits asserts the
    refusal instead.
- apps.e2e.tests_probe_review_qdec_races on PostgreSQL, together with
  QualityDecisionRaceTests: Ran 7 tests, OK (X01 decide against void, X06 real
  commit with a failing mailer).

**Checks.** Touched modules: Ran 4500 tests. The only failures were Q01b and Q06
from the copied probe file (removed before the commit) and
test_an_exemption_outlives_nothing; that test was then fixed and re-run (OK, 4
tests). Quality browser tests (rebuilt first): Ran 2 tests, OK.
`audit_invariants`: No invariant findings. `makemigrations --check`: No
changes detected.
