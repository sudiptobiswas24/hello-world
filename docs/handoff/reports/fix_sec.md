# fix_sec: O157, O192, O193, O194

Worktree scratchpad/fix_sec, on 3b76f30. One commit, 0434a03, closes all four (one root cause: the
two-person rule read only what a login held at each check, not what the keeper knew or had proposed).
Scenario table: scratchpad/calc_fix_sec/scenarios.py. Mutation driver: scratchpad/calc_fix_sec/mutate.py.

## What changed
- apps/core/roles.py: `KeptCredential` (who set a login's password or email, and what was set);
  `record_credential`; `lapse_on_giving` run by an `m2m_changed` receiver on User.groups, so every door
  that gives a role (confirm, grant, admin, group.user_set) makes a password the setter chose unusable and
  marks an email it set untrusted for reset when the setter could not give that role. `trusted_for_reset`.
  `ProposalStatus.LAPSED`; `RoleProposal.lapsed()` (login switched off, or proposer no longer keeps logins)
  asked first in `may_confirm`; `waiting_for` leaves dead ones out (`keepers()`); `lapse_what_died` marks
  them; `give_or_propose` replaces a dead pending one with the new keeper's.
- apps/core/users_api.py: `check_may_know` (check_may_administer plus pending proposals the actor could
  not give) for set_password, an email change, and `refused_link` (which now also counts pending
  proposals, O194); `issue_password` shared by set_password and the confirm; create and email change
  record the credential; deactivate, revoke and edit call `lapse_what_died`; `TrustedEmailResetForm`.
- apps/core/roles_api.py: confirm takes an optional {"password"}: the confirmer issues the next one, only
  if the login holds nothing else above them (check_may_know); refused, the confirm is rolled back too.
- config/urls.py: /accounts/password_reset/ uses TrustedEmailResetForm (before auth.urls).
- Migration core 0032_kept_credentials: AlterField RoleProposal.status (lapsed choice), CreateModel
  KeptCredential. Read: names the intended models.
- Frontend: Confirm action has an optional "Their password" field; "Lapsed" state and list filter; first
  password hint says it stops working. Browser test tests_browser_logins updated to issue the password.

## Tests (apps/core/tests_users_api.py), each failing with its fix reverted (mutate.py)
- KeeperKnowsNoCredentialAboveItTests (O157), receiver off -> 5 of 6 fail, e.g.
  test_the_keepers_first_password_stops_working_when_the_role_is_confirmed:
  "AssertionError: True is not false : HR signs in as the Bookkeeper with the first password it chose";
  test_the_keepers_email_takes_no_reset_after_the_confirm_until_set_again: "Tuples differ: (302, 2) != (302, 1)".
  test_the_reviewers_chain_stops_after_the_one_honest_confirm runs F1's steps: step 3 (HR signs in as asha) is refused.
- Rule 1 (pending counted) off -> test_an_existing_colleague_is_not_the_keepers_once_a_role_above_it_is_proposed
  "AssertionError: 200 != 403" (and UsersApiTests.test_made_signed_in_given_roles_and_let_go).
- O192 live check off -> test_O192_a_proposal_lapses_with_its_proposers_right_to_propose
  "Tuples differ: (200, True) != (400, False)". Also test_O192_taking_the_keepers_role_away_marks_its_proposals_lapsed.
- O193 marking off -> test_O193_a_proposal_lapses_when_its_login_is_switched_off "'pending' != 'lapsed'"
  (the live check still refuses the confirm: two guards).
- O194 off -> test_O194_a_login_with_a_role_proposed_is_linked_as_if_it_held_it "AssertionError: 200 != 400".
- Changed: UsersApiTests.test_made_signed_in_given_roles_and_let_go set asha's password while Bookkeeper was
  pending (encoded the defect); now asserts 403, withdraws the proposal, then sets it.

## Runs
- core, hr, purchasing, sales, fastsettings, --exclude-tag migration, parallel 2: Ran 2324 tests, OK
  (before the last one-line change); after it, core.tests_users_api + hr: Ran 404, OK.
- apps.web.tests_browser_logins + apps.web.tests after npm run build, with Chromium: Ran 22, OK (browser test ran, not skipped).
- audit_invariants: No invariant findings. makemigrations --check: No changes detected.
- Reviewer probes (probe_sec2.py, copied in and removed): 2 of the 5 pass as written (O192, O193). The three O157
  probes fail at their own helper, `signed_in` asserting the sign-in works: "AssertionError: asha cannot sign in" /
  "ravi cannot sign in". That refusal is the fix (the brief's rule 2: the password is made unusable); the probe
  cannot pass literally. The regression tests assert the refusal instead, with the probe's numbers.

## Could not do
- Logins whose password or email a keeper set before this commit have no KeptCredential (who set them is not
  known), so a later role does not lapse them. Asking a superuser to set new passwords on logins holding roles
  above their keeper would close it for existing data.
- No PostgreSQL race() test: confirm vs deactivate. `confirm` locks the proposal row only; `lapsed()` reads the
  login's is_active without lock_rows, so a deactivate committed between that read and the groups.add still
  ends with an inactive login holding the role. Not run on PostgreSQL or Asia/Kolkata.
- No audit_invariants check added: the receiver on User.groups covers every door, so no code shape remains to look for.

## Defects seen, not fixed
1. apps/core/roles.py give_or_propose (and RoleProposal.confirm): the reverse order of O194 still works. HR links
   an empty login to employee B1 (200), then proposes Controller for it; a Controller confirms (200): a Controller
   login linked to an employee, which refused_link would refuse. Kind: security, low (the confirmer sees only the username).
2. apps/core/roles.py give_or_propose: "nobody acts on their own login" compares pks only. HR makes `puppet` holding
   HR Admin (HR knows its password legitimately, as it holds nothing above HR), and as puppet proposes Bookkeeper for
   HR's own login; a real Bookkeeper confirms, seeing "proposed by puppet". A second person still confirms. Kind: security, low.
3. apps/core/users_api.py UserViewSet.perform_create/perform_update override the mixin's savepoint without
   transaction.atomic: a create refused in _given_or_proposed (e.g. a login with add_user but not change_user
   making one with ["Bookkeeper"]) returns 400 and leaves the login made, without roles. Kind: partial write.
4. lapse_on_giving watches roles only: a permission of its own (user_permissions) or is_staff given by a superuser
   in the admin to a login whose password HR set does not lapse it. Kind: security, low.
5. KeptCredential.value keeps a copy of the password hash the keeper set (not exposed by any API or admin). Kind: note.
