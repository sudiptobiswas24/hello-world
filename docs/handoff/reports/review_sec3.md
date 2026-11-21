# review_sec3: g20 (14b8b61..0ed3d8f; tested at d4f1827, same code)

Worktree: scratchpad/review_sec3, one commit 810b59e (probes only, not pushed).
Probe files: apps/core/tests_probe_review_sec3.py, apps/inventory/tests_probe_review_sec3.py,
apps/hr/tests_probe_review_sec3.py, apps/manufacturing/tests_probe_review_sec3.py,
apps/e2e/tests_probe_review_sec3_races.py. Sweep output: scratchpad/review_sec3_out/*.json.
Run: `PYTHONPATH=scripts/gate nice -n 19 .venv/bin/python manage.py test <label> --settings=fastsettings --exclude-tag migration`
(races with DATABASE_URL=postgres://erp:erp@localhost:5432/review_sec3).
Last full run of the non-sweep probes: 45 tests, 13 failures; each failure below is a finding or an informational probe named as such.

8 findings, 1 blocker (F1).

## Findings

### F1 BLOCKER, security: O195 is bypassed in two calls; the record the rule reads is wiped by the login's "own" password change
- Probes: core.tests_probe_review_sec3.O195 `test_laundered_through_the_logins_own_set_password_action`, `test_laundered_through_djangos_password_change`, `test_laundered_through_a_chain_a_second_login_made_by_the_first`, `test_the_confirmer_is_shown_whose_login_proposed_it`.
- Where: apps/core/roles.py:323-327 (`record_credential`: `by.pk == user.pk` deletes the KeptCredential); apps/core/users_api.py:299 (`set_password` has no `_not_myself`, unlike grant/revoke/deactivate at 277-330); Django's `/accounts/password_change/` (config/urls.py:29) never touches the record, and a changed hash makes `in_force` false (roles.py:316).
- Scenario, as HR Admin and a Bookkeeper, through the API: HR makes `puppet` (HR Admin, password HR chose). HR signs in as puppet and POSTs `/api/core/users/<puppet>/set_password/` for puppet itself (200; or changes it at /accounts/password_change/). The record is gone, `who_knows(puppet)` is empty, puppet is "its own". Puppet POSTs `/users/<hr>/grant/` Bookkeeper (200, was 400 before the wipe). A real Bookkeeper confirms (200). End state `{'propose': 200, 'confirm': 200, 'hr_holds_bookkeeper': True}`: HR Admin and Bookkeeper on one login, which is the O195 attack unchanged. The confirmer sees "proposed by puppet" and the subject's credentials only; nothing says puppet's password is HR's (`test_the_confirmer_is_shown...`: "puppet hr set third's password...", no word of puppet).
- The author's own test `test_once_its_person_sets_their_own_password_it_is_theirs` is this same call, made by the puppet client: the test encodes the hole as the "way back".
- Chain variant: HR makes p1, signs in as p1, p1 makes p2, p1 does the same own-password change; `who_knows(p2)` loses HR though HR typed p2's password.
- Blocks: yes. The rule decides on "who set it", a fact a single call by the login itself destroys. A fact that survives it (who made the login, from the creation event) is what the brief's second option ("name the maker") would have kept.

### F2 security, does not block (new row): a credential outlives its keeper's rights
- Probes: `O195.test_a_demoted_keeper_still_signs_in_as_the_login_it_made`, `O195.test_a_deactivated_keeper_still_signs_in_as_the_login_it_made`.
- Where: apps/core/roles.py:333 `lapse_on_giving` is called only when a right is given (m2m post_add, post_save); nothing runs when the setter's rights are taken or the setter is switched off (`lapse_what_died` marks proposals only).
- Scenario: HR makes puppet (HR Admin). A superuser revokes HR Admin from HR, or deactivates HR (a leaver). `Client().login(puppet, <password HR chose>)` still succeeds, and puppet still holds HR Admin. O192/O193 closed this for proposals, not for credentials.

### F3 security, does not block, but fix with F1 (same record): a re-hash on sign-in makes the office forget the keeper
- Probe: `O195.test_a_hash_upgrade_forgets_the_keeper`.
- Where: apps/core/roles.py:284 `fingerprint(hash)` and :316 `in_force` compare the current hash's digest; Django re-hashes on sign-in when the hasher or its cost moved on (every Django release raises the PBKDF2 count).
- Scenario: HR makes puppet under the old cost; after an upgrade, HR signs in as puppet, the hash is rewritten, `in_force` is false: `who_knows(puppet)` is `set()` (probe: `1 not found in set()`), and `lapse_on_giving` skips it (`not kept.in_force`), so O198's lapse and O195's refusal both stop applying to a password HR still knows.
- Related, F8 below.

### F4 security, minor, does not block O131 (log a row): the O131 shape remains for expense claims, expense lines and appraisals
- Probes: hr.tests_probe_review_sec3.O131 `test_a_deleted_expense_claims_history_is_not_read_by_a_colleague`, `test_a_deleted_appraisals_history_is_not_read_by_a_colleague`; survey `test_which_viewsets_narrow_by_login_with_no_say_about_a_gone_record`.
- Where: apps/hr/people_api.py:73 `OwnOrReportsMixin` (ExpenseClaimViewSet :90, ExpenseLineViewSet :160), :206 `AppraisalViewSet`: queryset narrowed by login, no `reads_only_some`, so `endpoints.may_read_gone` (apps/core/endpoints.py:89) allows.
- Scenario: Employee Self Service + Quality Inspector (standing: 404). HR deletes a colleague's claim/appraisal. The inspector's GET /api/core/history/ answers 200: "Claim by E005 - Riley Chen" / "Appraisal of E005 - Riley Chen, Jan 2026 to Jun 2026", who deleted it and when. (Author flagged both; not in docs/RISKS.md.)
- The survey lists 36 viewsets that narrow by `request.user` with no `reads_only_some`. Most are party-scoped and covered by the `visible_parties` branch for reps; the ones not party-scoped to look at: core.saved_filters.SavedFilterViewSet, sales.crm_api LeadViewSet/OpportunityViewSet/ActivityViewSet (owner-narrowed; covered only while the reader is party-limited), core.roles_api.RoleProposalViewSet (no delete).

### F5 crash, minor, does not block: deeply nested JSON answers 500 past the new parser
- Probe: `O191Parsers.test_deeply_nested_json_is_a_400_not_a_500`.
- Where: apps/core/api.py:231 `ObjectJSONParser.parse` calls DRF's `JSONParser.parse`, which converts ValueError only; `json.load` raises RecursionError for `[` x 200000 (and `{"a":` x 200000).
- Scenario: a Bookkeeper POSTs it to /api/core/role-proposals/1/confirm/: 500 (error row logged per request). Any body-reading endpoint, any signed-in person.

### F6 crash, minor, pre-existing, not O191, does not block: POST to three GET-only report routes answers 500, not 405
- Probe: `O191Sweep.test_sweep_broken_empty_and_empty_object_bodies` (scratchpad/review_sec3_out/sweep_other.json), same with every body kind including `{}` and empty.
- Routes (as a non-superuser holding every permission): POST /api/manufacturing/job-work-challans/still-out/ (views.py:1980), /api/manufacturing/customer-material-receipts/register/ (views.py:529), /api/quality/inspections/awaiting/ (quality/views.py:119). Cause not traced.

### F7 display, minor, does not block (author's #3, probed): the waiting inbox lists proposals whose Confirm is not offered
- Probe: `O196.test_a_proposal_for_a_linked_login_is_not_listed_as_waiting_for_a_holder_who_may_not_confirm`: a Controller sees `?waiting=true` rows with `may_confirm: False` (roles.py:257 `waiting_for` ignores the O195/O196 refusals).

### F8 rule, minor, does not block: the digest is keyed by SECRET_KEY, so a rotation forgets every keeper
- Probe: `O195.test_rotating_the_secret_key_forgets_every_keeper`. roles.py:284 `fingerprint` uses `salted_hmac` (SECRET_KEY); after a rotation `who_knows` is empty and no password lapses. Not stated in the commit or the migration 0033 note.

## Informational (failing probe, not a finding)
- `O198.test_a_right_given_by_a_queryset_update_...`: `User.objects.filter().update(is_superuser=True)` fires no signal. No such door in the app; the admin saves through `save()`. Not counted.
- The O191Sweep non-object test fails only on three routes that never read the body and answer 200 as before (users/<pk>/reactivate, errors/<pk>/reopen, manufacturing/dispatch/commit): they are not a regression. (Its other 18 "crashed" rows are the F6 routes.)

## What held under pressure
- O191, parser: every routed view's JSON parsers are ObjectJSONParser subclasses, none overrides `get_parsers`, and no code in apps/ reads `request.body`, `request.stream` or `json.loads(request...)` (`O191Parsers`).
- O191, bodies: 881 routes x [], [{..}], "x", 7, null, true as POST/PATCH/PUT as a non-superuser with every permission: no 500 caused by the body, no 2xx except the three body-ignoring actions above. With data behind the route, `O191Actions`: users grant/revoke/set_password/PATCH/PUT/create and proposal confirm/decline answer 400 in words for all six bodies and for broken JSON, and an empty body still works. Caveat: `load_demo` refuses under `MIGRATE=False` ("393 migrations to go"), so the sweep met 404 on most detail routes; the real-data check above is what covers actions.
- O191, frontend: client.ts sends a body only when `body !== undefined`; no `useAct.run`/`send` caller in frontend/src passes an array, null or string (grepped every caller; the 3 with `[...]` are fields inside objects).
- O195: a proposal lapses when its proposer loses auth.change_user (revoke, or the permission removed from the role) or is deactivated (`O195.test_a_proposal_outlives_neither...`).
- O196, both orders and re-link: proposed then linked, confirmed then linked, unlinked-proposed-confirmed-relinked, and a superuser's grant then HR moving the login to another employee are all refused (400/403). Race on PostgreSQL, Controller confirm against HR link at the same moment (`O196Race`): one of the two refused, no Controller login linked.
- O198: a permission of its own, is_staff, and `group.user_set.add` each lapse the keeper's password; an email set by HR stops resetting once the login gains the admin site; no hash appears in RecordEvent or the history API; the proposal's `credentials` names HR as setter.
- O129: a Sales Rep gets `held_for: null` for another rep's customer on list, detail, `?held_for__in`, `__name`, `__name__icontains`, `__code`, `__gt`, `__exact`, `?search=`; ordering by `held_for`/`held_for__name` does not sort the list; a store created by a Stores Manager with history written, then read by the rep, names no customer; the grouped list (`by=held_for`) does not; a GET of every list route as the rep never contains the hidden customer's name or id. A Sales Rep holds no delivery write permission, so sales/models.py:4377 (and purchasing/models.py:6556, transfers.py:228, orders.py:2348) is not reachable by the one scoped login; unscoped roles read every customer anyway.
- O131: leave rule as the author tested; the manager of a deleted request's employee is refused too (only HR/payroll read it), acceptable.
- O124, all seven lines through the API as Stores Manager / Production Supervisor: line moved off a posted document, a draft's line moved onto a posted one, edited, deleted, and added to a posted document are 400 for StockAdjustmentLine, StockCountLine, CustomerMaterialReceiptLine, CustomerMaterialReturnLine, MaterialIssueLine, ProductionByproduct and ProductionScrap (the author's tests covered one direction on two of them).
- O124 on PostgreSQL (`apps.e2e.tests_probe_review_sec3_races`, 4 tests run, none skipped): a line added to an adjustment as it posts is booked or refused, never left unbooked; a line moved off an adjustment as it posts leaves no posted-empty document; two lines swapped between two drafts do not deadlock (both 200, `LOCK_ORDER_SENTINEL` on); a line added to a material issue as it posts is booked or refused. The barrier does not prove the two met, so these show absence of a failure, not a proof.

## Not probed
- No Asia/Kolkata lane; no browser test of the confirm screen's `credentials` field (RoleProposalForm.tsx); `audit_invariants` not re-run here; migration 0033 relied on the author's MigrationExecutor test.
