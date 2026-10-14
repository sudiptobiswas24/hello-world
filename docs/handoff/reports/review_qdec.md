# review_qdec: adversarial review of gate g19 (fix_qdec, 3e33028..6c47c52)

Worktree: $SP/review_qdec (detached at 6c47c52, probes committed as c71219c and ef23e09, not pushed).
Probes: apps/quality/tests_probe_review_qdec.py (70 tests: 14 fail, each one a finding probe; 55 pass; 1 skipped stub G05)
and apps/e2e/tests_probe_review_qdec_races.py (6 tests, PostgreSQL database rq_review).
Logs: $SP/reports/review_qdec_probe_run_final.log, review_qdec_races_pg.log, review_qdec_audit.log.
On the tip, unchanged by me: `audit_invariants` prints "No invariant findings"; `makemigrations --check` "No changes detected".

Run: `PYTHONPATH=scripts/gate nice -n 19 .venv/bin/python manage.py test apps.quality.tests_probe_review_qdec --settings=fastsettings --exclude-tag migration`
Races: same with `DATABASE_URL=postgres://erp:erp@localhost:5432/rq_review` and `apps.e2e.tests_probe_review_qdec_races`.

## Findings: 11, one blocker

### 1. BLOCKER (rule/security): the Inspector can clear the hold by voiding it, then post a pass
Probes: `WhoMayDecide.test_Q01`, `test_Q01b` (fail on the tip).
Where: apps/quality/views.py:72-74 (`"void": "quality.change_inspection"`), :96 `void`; apps/quality/models.py:935 `void`
(no check on who, for an AWAITING inspection); frontend/src/modules/quality/InspectionForm.tsx (Void shows to anyone with change_inspection).
Scenario: R-001 reads 92, 92, 92 against 83.125 to 91.875 under an advisory plan. Meera (Quality Inspector, measured it) posts: AWAITING,
held, to-dos to asha, ravi, sunil. Meera POSTs `/void/` with reason "typo": 200, `lot_status` "uninspected", the gate says "" (advisory plan,
no verdict), all three to-dos close "as done by" meera with outcome "was voided, so there is nothing to decide". She then posts 88, 88, 88
the same day: R-001 reads released. No one decided; asha never learns beyond a closed to-do. This is the one thing decision 22 exists to
prevent ("the Inspector may not dispose of an advisory failure; the Quality Manager owns the decision"), done in two requests.
The door is old (an Inspector can void a mandatory rejection the same way), but the owner's new rule is defeated through it.
Blocks: yes, unless the owner says a void is the Inspector's to make. Smallest fix: voiding an AWAITING inspection needs
`quality.decide_inspection` (the Quality Manager still voids a wrong-lot entry), or at minimum the Quality Manager is told when it happens.

### 2. Rule: an escalation that told nobody is recorded as done, and is never made again
Probe: `Escalation.test_E03` (fails). Where: apps/quality/models.py:916-933 (`escalate` sets `escalated_on` at :930 after
`tell_the_administrator` returned nobody-told), apps/quality/decisions.py:226.
Scenario: the one superuser is inactive; inspection posted 1 June 10:00 (due 2 June); `escalate_overdue(3 June)` returns it with
"No active administrator login to tell." and sets `escalated_on = 3 June`. The superuser is reactivated; `escalate_overdue(4 June)` returns
[] and writes no to-do. The undecided failure never reaches the administrator. The cron log said so once, then never again.
Blocks: no (needs zero active superusers). Fix: set `escalated_on` only when `told.told` is not empty.

### 3. Minor: a decision note that is not text is a 500
Probe: `WhoMayDecide.test_Q07` (fails: `{"note": 123}` gives 500). Where: apps/quality/models.py:892 `" ".join((note or "").split())`;
apps/quality/views.py:102-108 passes `request.data.get("note")` through untyped. Blocks: no.

### 4. Minor: a decision note over 255 characters is cut without a word
Probe: `test_Q08` (fails). Where: models.py:896 `note[:255]`. A 300-character reason is stored as 255 and the answer is 200. The
post path through the serializer refuses the same length. Blocks: no (refuse, as the serializer does).

### 5. Minor (design question): a superuser who recorded the readings decides his own failure
Probe: `test_Q06` (fails). Where: apps/core/approvals.py:71 (`check_not_raised_by` exempts a superuser), models.py:895.
Scenario: superuser "boss" creates the inspection and three readings of 92 over the API, posts, then decides REJECT: 200.
Decision 22 says whoever recorded the reading cannot make it. The shared helper's exemption is deliberate, so this needs the owner's word,
not a code change by itself. Blocks: no.

### 6. Minor: the answer names the barred recorder as "told to decide"
Probe: `test_Q12` (fails). Where: apps/quality/decisions.py:195-212 (`tell_on_posting`).
Scenario: asha is the only Quality Manager and raised the inspection; Meera posts. The warning reads "Told to decide, by a to-do due ...:
asha." She cannot decide it (400 "raised or changed ...; somebody else approves it"). Nothing tells Meera that nobody eligible exists;
only the next-working-day escalation finds out. Blocks: no.

### 7. Minor: an unexpected error in the mailer after the commit answers 500 for a posting that stood, and stops the other mails
Probes: `Telling.test_T04` (error under TestCase), `OnCommit.test_X06` (real commit, fails: 500 != 200, on PostgreSQL and SQLite).
Where: apps/quality/decisions.py:113-121 (`send()` catches only ValidationError, SMTPException, OSError).
Scenario: `EmailMessage.send` raises RuntimeError (any non-OSError, such as a ValueError from a header) for asha's mail. The inspection is
committed, the inspector gets 500, the callbacks after it (ravi's mail) never run, and no Note is written for either. Blocks: no.
Fix: catch Exception in `send()` and write the Note.

### 8. Minor (data): D2 remains on the lab-to-finish path of the tape and film stations
Probe: `TheTapeLabPath.test_S02` (fails). Where: apps/manufacturing/station_tape.py:144 and station_film.py:136 test
`inspection.disposition == AWAITING` at booking time; with a plan that has more than the gauge line (tenacity), the inspection is left
open, its disposition is still "", so `conceded_by` = the supervisor. When the lab posts 4.8 x n and the inspection becomes AWAITING,
the doff still names the supervisor as conceding a batch that waits for the Quality Manager. The bag path (conversion.py, `waits`) does not
have this; tape and film should use `waits` too. Blocks: no.

### 9. Rule/minor: the station answers do not say the doff, roll or bundle is held
Probe: `TheTapeApi.test_S03` (fails). Where: apps/manufacturing/station_views.py:636-645 (doff), :516 (film), :360-370 (bags).
Scenario: denier 1,085 against 950 to 1,050, advisory plan, supervisor PIN and reason "Customer accepts heavy": 201 with
`{"mean_denier": "1085.00", "inspection": "QC-2026-00001", "awaiting_lab": false}` and no word that the batch is held or who decides. The
warning exists (`inspection.telling.warning`, kept in `inspection.told`) and is dropped at the station; the supervisor who typed a PIN to
concede it finds out when the tape load is refused. With no Quality Manager login the only record that nobody was told is `inspection.told`
(`FilmTests F04`). Brief item 2 says "the posting answers with a stronger warning". Blocks: no, but the integrator should rule on it.

### 10. Rule (known, O10, aggravated): a posted inspection can be deleted, which removes the hold
Probes: `DeletePath.test_D01`, `test_D02`, `test_D03` (fail). Where: apps/quality/models.py:440 `Inspection` has no `delete()` guard;
apps/core/management/commands/audit_invariants.py:1076 `DELETABLE_REPORTED`; docs/RISKS.md O10 (open).
Scenario: asha (Quality Manager, holds delete_inspection) DELETEs a posted AWAITING inspection: 204. The lot reads uninspected and passes
the gate; the readings go by cascade; the three open to-dos stay on a record that no longer exists. Same for a decided one.
Pre-existing and registered, but this change adds a hold and an accountability record (`decision_login`, `decided_at`) that this door
erases; the pre-flight list asks for the delete path of every guard and the report does not name it. Blocks: no (O10); worth raising its priority.

### 11. Minor: the list facet "Awaiting decision" shows voided inspections, newest first
Probe: `TheFacet.test_V01` (fails). Where: frontend/src/modules/quality/Inspections.tsx:28 (`disposition=awaiting`).
Scenario: one failure voided, one live: the facet lists both (ids [2, 1]); a voided inspection keeps disposition "awaiting". The screen
`awaiting-decision` (endpoint `awaiting/`) is right; only the facet is wrong, and its order is the list's default (`-inspected_on`),
not oldest first. Blocks: no.

## What held under pressure
- Who decides: only the Quality Manager role holds `quality.decide_inspection` (P01); Inspector, Production Supervisor, Stores Manager get 403 (Q05, roles tests, P04); a manager who PATCHed the draft (Q02) or added a reading (Q03) is refused; a second manager decides and every to-do closes as done by the decider (Q04); concession decided later is signed by the employee's party, with the note (Q11); voided, never-awaiting and already-decided are refused with the first decision named (Q10); bad disposition values are 400 (Q09).
- Races on PostgreSQL: decide vs void (X01), a failing and a passing post at once (X02), two escalations (X03), decide vs escalate (X04), two failing posts of one lot (X05): each ends consistent, nothing open, one hold. The author's two-decider race also stands.
- Reverse and edit: void awaiting returns to the earlier pass and closes all three to-dos (R01); earlier, same-day and later re-inspection while one waits are refused with the sentence (R02); a new failure after a void waits again with three new to-dos (R03); void of a decided one reopens nothing and cannot be decided again (R04, R06); a blank void reason leaves the to-dos open (R05).
- O145 with numbers: reject drafted first (id 1), pass drafted second, pass posted first, reject posted last stands and the lot reads held (R07); a pass after a voided failure releases (R08).
- O146 with numbers: grandchild dated 7 days back is refused by the grandparent's rejection, then accepted dated on the day (L02); child of an awaiting batch is held and refused a pass, then free once the parent's inspection is voided (L01); a join is held by either parent (L03); a cycle in lot sources terminates (L04); the child follows the parent's concession (L05).
- Gate: issue, backflush text, tape load, roll mount, bale, delivery, pick (`allocate`), MRP (`may_be_drawn`) and the rework action all return the same awaiting sentence (G01); split children are held and stay held after a rework decision (G02); a move to a rejects store is allowed and the batch still waits (G03); reject and rework keep it held (G04); a plan made mandatory or retired afterwards does not free it (K02).
- Stations: tape mandatory off-denier is still a concession by the supervisor (S01); film advisory waits with no concession, mandatory concedes, withdrawal closes the to-do (F01-F03), `told` keeps "Nobody signs in as Quality Manager" (F04); bag bundle advisory, mandatory, on target, withdrawn (B01-B04).
- Telling: a Quality Manager who cannot open the record is named in `not_told` and in the warning (T01, T02); a rolled-back posting leaves no to-do, no mail, no `told` (T03); one person in two roles gets one to-do (T05).
- Escalation: Friday posting due Monday, silent Fri to Mon, one to-do on Tuesday, silent after (E01); decided or voided in between: nothing, admin to-do closed (E02, E07); the command run twice says 1 then 0 (E05); two superusers get one each (E06); Kolkata day, 04 Jun 20:00 UTC = Fri 05 Jun IST, due Mon 08 Jun for the to-dos and the property (E04, K01).
- Roles and screen: registry `awaiting-decision` carries `quality.decide_inspection`, the permission the `awaiting` action is mapped to (P03); the list is oldest first with `overdue` (P05); anonymous 401/403 and GET on decide 405 (P06); the two told roles hold view_inspection and view_reading and nothing else in quality (P02).
- `audit_invariants` clean; migration 0006 touches `inspection` only; `makemigrations --check` clean.

## Not covered
Browser tests (not rebuilt), the full suite, the PostgreSQL and Kolkata lanes in full; delivery and backflush were asked through the shared gate, not through a full sales delivery or a backflushed production entry (the author's `tests_backflush` covers the latter); fix_sub's call of `tell_the_quality_manager` (not in this range).
