# review_sec2: second review of the security fix commits (404753a..3b76f30)

Reviewed at 3b76f30 in a detached worktree (scratchpad/review_sec2, removed at the end). Nothing was fixed.
Probes: scratchpad/reports/review_sec2_tests/probe_sec2.py (36 tests with the auditor's 8; 5 fail on purpose, each failure is a hole below).
Run logs: reports/review_sec2_probe_run.log, reports/review_sec2_suite.log.

## Verdict

- O142 (two-person rule): the rule holds against every direct move. It does not hold against the O157 first-password gap, and that gap is worse than the row says (F1): after one honest confirmation the keeper confirms its own proposals through the login it made, and then gives its own login the role. Two smaller holes: F2, F3. One low: F4.
- O143, O144, O128: held. No hole found. Two small notes under O144.

## Runs

- Auditor's probes (tests_probe_review.py, run first in the review worktree): 8 OK.
- Touched modules: `manage.py test apps.core apps.hr apps.purchasing apps.sales --settings=scripts.gate.fastsettings --parallel 2 --exclude-tag migration`, under nice 19: Ran 2314 tests in 213.9s, OK. The tag exclusion is mine: fastsettings sets TEST MIGRATE False, so the `@tag("migration")` tests cannot mean anything there. Not run: PostgreSQL, browser tests, Asia/Kolkata, frontend build.
- `makemigrations --check --dry-run`: no changes. Migration core 0031 names RoleProposal, the partial unique constraint `one_pending_proposal_per_role` on status=pending, and the confirm permission.
- My probes: 28 in probe_sec2.py; 23 pass, 5 fail (listed below).

## O142: what I tried and what happened

Held (probes pass):
- HR confirms its own proposal: 400. HR confirms through a second login it made holding only what HR holds (HR Admin): refused. The login the role is for confirms: refused. A login holding the role only as a pending proposal confirms another: refused, and its `?waiting=true` queue is empty. A real holder confirms: 200, role held.
- Two keepers proposing one role for one login: the second gets 200, one row, `proposed_by` stays the first, neither can confirm. (SQLite only: a true simultaneous race was not proven; the partial unique constraint plus get_or_create is the right shape.)
- Confirmer loses the role before confirming: refused (live `may_give`).
- Decline after confirm: 400, role stays.
- Other doors, all give nothing: user API create, PATCH, PUT, `grant` by role name and by id turn into one pending proposal; keeper granting or PATCHing its own login: 400 and no proposal; PATCH of is_staff, is_superuser, groups, user_permissions is ignored; role-proposals and roles endpoints take no write verb (403/404/405); Django admin as a staff HR Admin is 302/403/404 for users, groups and role proposals; login delete is 403 for HR and 400 for a superuser; the employees import refuses to link a Controller login for HR and ignores a `roles` column (new login gets no groups).

### F1 (high): O157 confirmed, and it undoes the rule. Probe: FirstPasswordGapTests

Exact steps (all through the API, HR Admin only, one honest Bookkeeper `bk`):
1. HR `POST /api/core/users/ {username:"asha", password:P, roles:["Bookkeeper"]}`. HR does not hold Bookkeeper: proposed, 201.
2. `bk` `POST /api/core/role-proposals/{id}/confirm/`: 200. asha holds Bookkeeper. This is the one real second person.
3. HR signs in as asha with P (`APIClient.login`, the real backend). `GET /api/core/me/` shows roles ["Bookkeeper"]. This confirms O157. HR's own `set_password` on asha is now 403 (check_may_administer), so only the first password is the leak, but nothing makes asha change it and the app has no forced change.
4. HR `POST /api/core/users/ {username:"puppet", password:P, roles:["HR Admin","Bookkeeper"]}`. HR Admin is given at once (HR holds it); Bookkeeper is proposed, `proposed_by` = HR.
5. HR, as asha, `POST /api/core/role-proposals/{puppet's}/confirm/`: 200. `proposed_by` (HR) is not asha, asha holds Bookkeeper, asha is not puppet. Every check passes. Output: `HR-as-asha confirms HR's own proposal for puppet -> 200`.
6. HR, as puppet (HR Admin + Bookkeeper, password P), `POST /api/core/users/{hr.pk}/grant/ {role:"Bookkeeper"}`: 200. puppet holds Bookkeeper (`may_give`), puppet is not HR's pk, and `grant` is in `KEEPER_GAINS_NOTHING` so check_may_administer is skipped. Output: `puppet grants Bookkeeper to HR's OWN login -> 200 ; HR now holds Bookkeeper = True`.

Result: the keeper holds a role no other person gave it, on its own login, and can repeat for any role that has ever been confirmed for any login it made. "The proposer never confirms their own proposal, and nobody acts on their own login" are both bypassed by a proxy login. So the row's remedy has to be larger than "ask the person to change the password": the rule is only as strong as the proposer's ignorance of the confirmer's credentials.

Variants, same cause:
- Existing colleague (probe `existing_login_variant`): ravi holds HR Admin only. HR `set_password` on ravi is allowed (he holds nothing beyond HR's roles), HR proposes Bookkeeper, `bk` confirms, HR signs in as ravi with the password it chose and gets ['Bookkeeper','HR Admin']. (An Employee Self Service-only colleague is refused at set_password, 403, because HR does not hold that role.)
- Email: HR creates the login with `email` = a mailbox HR reads. After the confirm, `POST /accounts/password_reset/` with that email returns 302 and puts one mail in the outbox. A reset survives the person changing the first password. The proposal screen shows the username only, so the confirmer never sees the email.
- Where the code stands: `users_api.UserSerializer.create` (keeper sets the first password), `KEEPER_GAINS_NOTHING` (grant), `roles.give_or_propose` and `RoleProposal.may_confirm` (compare pks only).

### F2 (medium-low): a proposal outlives its proposer. Probe: test_proposer_loses_the_keepers_right...

HR proposes Bookkeeper for a login, then HR's HR Admin role is removed and HR is switched off. A holder confirms: 200, role given (`(200, True)`). `may_confirm` never asks whether the proposer still keeps logins or is still active. A keeper dismissed for misconduct leaves live proposals behind.

### F3 (low): a proposal for a switched-off login is still listed and still confirmable. Probe: test_a_proposal_for_a_login_then_switched_off

Deactivating asha leaves her pending Bookkeeper proposal in the holder's queue; confirm is 200 and the inactive login now holds Bookkeeper. HR cannot reactivate it afterwards (403), so only a holder can switch it back on, but a leaver ends up holding a role and the deactivate did not withdraw what it had pending.

### F4 (low): the employee link reads current groups, not pending proposals. Characterisation in OtherDoorTests

HR links a login that holds nothing (or has a Controller proposal pending) to an employee record: 200. A Controller holder then confirms: 200. The result is a Controller login linked to an employee, which `refused_link` says HR may not do directly (Controller login: 400). Either order works. Impact is small (the confirmer sees only the username), but the rule is checked at link time only.

## O143: held

`authors()` now takes related names and walks `revision_of` up the chain. Probe RevisionOfARevisionTests: original writer w1, first reviser w2, second reviser w3.
- Accept of the second revision with approval by w1, by w2, by w3: all 400. By a fourth person: 200.
- The resulting order's `raised_by()` contains w1, w2, w3.
- Edge cases checked in code: `revision_of` is read-only in the serializer (so no cycle can be written), on_delete PROTECT (no dangling original), only Quotation has the field; all four `authors(` callers (quality, purchasing requisition, sales order, approvals) pass names; revisions made before the fix, which carry no stamps, are covered through the chain. `create_revision` has one caller (the view) which passes `by`.

## O144: held, two small notes

- Requisition cancel by every role (20 roles in setup_roles, approved requisition, none linked to the requester): only AP Manager (holds decide_purchaserequisition) gets 200; every other role 403. The requester, wearing in turn each of the 20 roles on top of Employee Self Service: 200 every time. Requester whose employee record has no login: a colleague gets 403. The tuple in `action_permission_map` is "any of" (`permissions.holds`), so the requester passes the gate with change_ alone.
- Note: creating a requisition needs `requested_by` (400 without it), so a clerk who raises one for someone else can call it off neither as requester nor as decider. Only the named person and AP Manager can. Not a hole; a behaviour change from "anyone with change_".
- Note: `cancel(by=None, may_decide=False)` defaults to refusing. The only caller is the view; any future code path calling `requisition.cancel()` bare will raise PermissionDenied.
- Converted lead and login link: auditor's probes pass; link matrix: only HR Admin holds hr.change_employee; see F4 for the gap.

## O128: held

All 20 roles against the four routes (tds-deductions reverse, tds-challans void, goods-receipts accept and reject) at a nonexistent pk: 403 versus not-403 equals whether the user holds the new right (post_bill; change_receiptinspection) for every role and route. No role lost the action and none gained it against the old rights (add_tdsdeduction, add_tdschallan, add_receiptinspection): the sets are identical in the shipped roles (AP Manager for the TDS pair; Quality Inspector and Quality Manager for accept/reject). A superuser passes. `deduct_tds` and challan `pay` correctly still ask the add_ rights. No leftover gate on the old rights outside those maps (frontend uses add_ only to offer create buttons).

## Not covered

- PostgreSQL concurrency of confirm/confirm and propose/propose; browser tests; frontend build (the new screens were read, not run; registry entry asks core.view_roleproposal as it should).
- docs/RISKS.md was not edited.
