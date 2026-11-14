# fix_sec2 (g20): O124 O129 O131 O191 O195 O196 O198

Worktree scratchpad/fix_sec2, detached, on c78da57. Eight commits, tip cbf3d67, not pushed.
Scratch scripts and logs: scratchpad/calc_fix_sec2/.

## Commits
- b1d38fc O124: seven lines answer to both documents, held (save, delete, locked_before_it); LINES_REPORTED emptied.
- adcc291 O129: held_for named only to a login that may read that customer; off UNSCOPED_PARTY_READS_REPORTED.
- 3fe5d06 O131: a deleted record's history needs `endpoints.may_read_gone`; LeaveRequestViewSet.reads_only_some = leave_readable.
- 8ae9b9d O124 (follow-up): sales.tests_audit_trade_rules stale-exemption test plants its own LINES_REPORTED entry (it relied on StockAdjustmentLine being listed; failed "Lists differ: [] != ['stale exemption']" after b1d38fc).
- 8dbc0e2 O191: `apps.core.api.ObjectJSONParser` is the default JSON parser; new audit check "body read past the object parser".
- bdd5d58 O198: `roles.held_beyond`; lapse on any right; `fingerprint` instead of the hash; migration core 0033; credentials said in words on the proposal (API field + confirm screen).
- cbf3d67 O195 O196 (one commit: both are give_or_propose / may_confirm asking only the login in hand; same two places): `who_knows`, `acts_as`, `refused_for_its_person`, `users_api.linked_holding`.

## Decisions to check
- O191: I did not convert the ~330 `request.data.get` / `request.data[...]` sites in 30 files to a reader function. The fix sits once, where every action's body is read: a DRF parser (in apps.core.api, set as DEFAULT_PARSER_CLASSES) that refuses a JSON body that is not an object, 400 in words. Why: a reader each action must remember is the shape this checkout keeps losing; a spelling check on `request.data.get` would miss the ~20 `data = request.data` sites (station_views etc.); and 30-file churn would collide with fix_cost. No view sets parser_classes and no endpoint or frontend call posts a top-level list (grepped). The audit check reports the one way back: a routed view naming a JSON parser of its own that is not ObjectJSONParser while it reads request.data. If the integrator wants the literal reader + spelling check instead, it is a mechanical follow-up.
- O195: refuse, not "name the maker": a name on the confirm screen is a comment, not a constraint. Rule: a login acts as every login whose password or email it set (in force, not lapsed), transitively. give_or_propose refuses acting for someone who acts as the actor; may_confirm refuses a confirmer the subject or proposer acts as, and a proposal made through a login its subject acts as (covers pending ones made before). A login whose person sets their own password is theirs again.
- O196: the link rule per role is `linked_holding(actor, group)` (employee's own role, or keeper who gives it, or superuser). refused_link now asks it of every held/pending role. At give/propose the actor is the keeper; at confirm it is the confirmer, so a superuser, or a keeper holding the role, may still confirm one for a linked login; a plain holder may not. Side effect: a keeper not holding Employee Self Service may now link a login holding only ESS plus roles the keeper gives (before, check_may_know refused ESS as "beyond").
- O198: rows with set_by empty (written by 0033 for every login with a usable password or an email and no record) are said, never lapsed: "Who set X's password is not on record ... Issue the next password with the confirm unless it was its person's own." The confirm screen (RoleProposalForm.tsx) shows them in a read-only textarea. Reverse of 0033 deletes those rows; the digests are not turned back into hashes.
- O124: the brief said save(). The check needs save() and delete(), so both changed, plus locked_before_it() on each class (answer_to_its_document's docstring asks for it). Imports are local, inside the methods, so the import blocks fix_cost may touch are unchanged. Refusal wording kept.

## Tests that fail without each fix (fix reverted, test kept)
- O124 inventory.tests_adjustments.ALineAnswersToBothAdjustmentsTests (Stores Manager PATCHes a posted adjustment's line onto a draft): "AssertionError: 200 != 400". manufacturing.tests_scrap.ScrapAnswersToBothEntriesTests (Production Supervisor): "AssertionError: 200 != 400".
- O129 inventory.tests_api.AStoreNamesItsCustomerOnlyToTheirRepTests (two Sales Reps): "Tuples differ: (3, 4) != (3, None)".
- O131 hr.tests.ADeletedLeaveRequestsHistoryTests (an inspector with self service; HR Admin deletes): "AssertionError: 200 != 404 : a colleague reads the history of a deleted leave request". (Plain self service was already refused through the party scope; every role reading every customer was not.)
- O191 core.tests_permissions.ABodyIsAnObjectTests (Bookkeeper, confirm reads its body first): "Lists differ: [('[]', 500, False), ...] != [('[]', 400, True), ...]". Planted check tests: a viewset with JSONParser reading request.data.get is reported; one with ObjectJSONParser is not.
- O198 core.tests_users_api.AKeepersCredentialLapsesWithAnyRightTests: "True is not false : HR signs in with a permission it does not hold"; "True is not false : HR signs in to the admin site it cannot open"; "'md5$...' == 'md5$...'" (kept value was the hash); the predating test errors (no fingerprint/no empty setter). KeptPasswordsAreDigestedTests (tag migration, MigrationExecutor 0032 -> 0033, real migrations): OK.
- O195 core.tests_users_api.NobodyActsThroughALoginTheyKeepTests: "AssertionError: 200 != 400" (puppet proposes Bookkeeper for HR; and a pre-existing one confirmed by bk). test_once_its_person_sets_their_own_password_it_is_theirs is the way back (passes both ways).
- O196 core.tests_users_api.APersonsLoginIsGivenWhatItsLinkWouldTakeTests: "AssertionError: 200 != 400" (HR proposes Controller for a linked login); "Tuples differ: (200, True) != (400, False)" (Controller confirms a pre-existing one).

## Runs (fastsettings, --exclude-tag migration, parallel 2)
- inventory + manufacturing after O124: Ran 2131, OK. core + hr + inventory + web.tests after O129/O131: Ran 1204, OK.
- core + purchasing + sales + quality + web.tests with the parser: Ran 2393, 1 failure (the stale-exemption test, fixed in 8ae9b9d; module rerun OK).
- core + hr + imports after O198: Ran 775, OK. core + hr + imports + sales.tests_reps at the tip: Ran 813, OK.
- inventory + manufacturing + sales.tests_audit_trade_rules at the tip: Ran 2141, OK apart from my mistyped label "apps.payroll" (no such app; loader error only).
- audit_invariants: No invariant findings, and makemigrations --check: No changes detected, before every commit. 0033 read: AlterField keptcredential.set_by + RunPython.
- frontend: `npx tsc --noEmit` clean. No `npm run build`, no browser tests.

## Could not do
- No PostgreSQL race() test for O124's lock (line edit vs document post). The lock is answer_to_its_document's, proven for other lines; these seven are not raced. No PostgreSQL or Asia/Kolkata lane for any commit.
- O195/O196 not split into two commits (budget); the message names both.
- Browser tests for the confirm screen's new field not run.

## Defects seen, not fixed
1. apps/hr/people_api.py:73 OwnOrReportsMixin (ExpenseClaimViewSet and the others using it) and people_api.py:~215 AppraisalViewSet narrow reads by login but declare no `reads_only_some`, so a deleted expense claim's or appraisal's history is read by any login holding the kind's view right that the party scope does not limit (e.g. an inspector with self service), the O131 shape. Fix: give the mixin `reads_only_some(user) = not (superuser or has_perm(every))`. Kind: security, minor. No audit check sees "a get_queryset narrowing by login without reads_only_some" yet.
2. apps/sales/models.py:4377: a delivery from a store held for a customer is refused with "{warehouse} holds {held_for}'s material": the customer named to whoever ships, including a rep who does not carry them (the O129 read, through a refusal). Kind: security, minor.
3. apps/core/roles.py:257 waiting_for still lists a proposal the O195 rule now refuses (subject/proposer acting as the confirmer); the inbox count can show one whose Confirm is not offered (may_confirm is false on the row). Kind: display.
4. O197 (UserViewSet writes outside a transaction) is still open; a create refused by the new O195/O196 checks inside _given_or_proposed leaves the login made without its roles, as before.

## Review fixes (review_sec3)

Five more commits on cbf3d67 in scratchpad/fix_sec2, tip dfa2e29, not pushed. They apply on the integrated tree as the first seven did.

### Commits
- 0c9ab86 O191, review F5: ObjectJSONParser turns RecursionError into a 400 ("The body nests deeper than any record's fields do...").
- 9191946 Review F6: `AuditableViewSetMixin.dispatch` answers 405 for a method the route does not map. DRF had picked the handler by method name, so an action named `post` answered the method. That gave 500s on the three GET-only reports. It also let a POST to a document's own address post it with only add_ (a Stores Manager posted a draft adjustment: 200). Eleven viewsets have an action named post. New audit check "action named after a method" reports a viewset with such an action that lacks the mixin.
- c4e0250 O131, review F4: one rule for every deleted record's history. `endpoints.narrows_for(cls, user)` compares the WHERE of the screen's own queryset for the login with a superuser's. Nothing needs declaring. The leave hook is gone, and `AuditableViewSetMixin.reads_only_some` answers from the same rule.
- 48fc991 O195 O198, review F1 F2 F3 F8: who can sign in as a login is kept at the event (roles.KeptCredential).
  - Kinds: LOGIN (who made it), PASSWORD (ended by the next change of the password by anyone, read from Django's `_password`, which a re-hash at sign-in clears first) and EMAIL. No hash or digest is kept.
  - who_knows follows the maker, and no password change ends that fact.
  - The way back is someone other than the maker confirming or giving a role the maker could not give. That lapses the LOGIN row, kills the password unless someone else issued it, and stops trusting the email for a reset.
  - `lapse_with_the_setter` runs on every door that takes a right away. A setter switched off, losing the right it set with (keeping logins, or `as_confirmer`), or no longer covering the login knows nothing it set.
  - The confirmer is told who can sign in as the proposer.
  - The employees import records its runner as maker.
  - Migration core 0034:
    - kind gains "login", value may be empty, and `as_confirmer` is added False.
    - A 0033 digest still matching the login's hash stands with its value emptied. It is marked as the confirmer's where the setter confirmed a role for that login. A digest that no longer matches goes.
    - Makers are recorded from the API's "created" history, lapsed where the login already holds what the maker could not give.
  - The old test test_once_its_person_sets_their_own_password_it_is_theirs encoded the bypass. It is rewritten to the rule: test_its_own_password_changed_frees_no_login_from_its_maker, plus a way-back test. The commit says so.
- dfa2e29 O195 O196, review F7: waiting_for keeps only what may_confirm allows.

### Tests failing without each fix (fix reverted, test kept)
- F5 core.tests_permissions.ABodyIsAnObjectTests.test_a_body_nested_past_the_parsers_depth_is_refused_in_words: "Lists differ: [500, 500] != [400, 400]".
- F6 core.tests_permissions.AMethodARouteDoesNotMapIsRefusedTests:
  - test_every_route_answers_405_to_a_method_it_does_not_map (every routed path of every such viewset, each unmapped method, as a login holding every right): "{('post', '/api/inventory/stock-adjustment...': 404, ...} != {}".
  - test_a_post_to_a_drafts_address_does_not_post_it (Stores Manager): "Tuples differ: (200, True) != (405, False)".
  - A planted check test.
- F4 hr.tests_people.ADeletedRecordsHistoryIsReadAsTheRecordWasTests (reviewer's numbers, expense claim and appraisal): "Tuples differ: (404, 200, 200) != (404, 404, 200)".
- F1/F2/F3/F8 core.tests_users_api, 7 of 8 new tests fail with roles/users_api/roles_api/importer and 0034 reverted:
  - own password changed: "200 != 400"
  - Django password change, and the p1 -> p2 chain: "200 != 400"
  - confirmer told about the proposer: "... not found in 'hr set third's password; ...'"
  - demoted keeper: "True is not false : a login holding HR Admin is open to someone who no longer does"
  - switched off: "True is not false : a leaver who made a login still knows its password"
  - right lost through the role: "True is not false"
  - re-hash and new SECRET_KEY: set differs
  - The way-back test passes both ways (positive path).
  - KeptCredentialsAtTheEventTests (tag migration, MigrationExecutor 0033 -> 0034) passes, and so does KeptPasswordsAreDigestedTests (0032 -> 0033, digest now inlined in the test).
- F7 NobodyActsThroughALoginTheyKeepTests.test_the_inbox_lists_only_what_the_confirmer_may_confirm: "Lists differ: [('third', True), ('hr', False)] != [('third', True)]".

### Runs (fastsettings, --exclude-tag migration, parallel 2)
- core + inventory + manufacturing + quality + hr after F5/F6: Ran 3039. Failures were only the reviewer's probes, still present then (O131 x2, plus loader errors from probe files I misplaced).
- core + hr + web.tests + sales.tests_reps after F4: Ran 802, OK.
- core + hr + imports + web.tests + sales.tests_reps + inventory.tests_adjustments after F1/F7: Ran 898, OK. core.tests_users_api + tests_roles at the tip: Ran 73, OK.
- audit_invariants found nothing and `makemigrations --check` detected no changes before each commit.
- Not run: PostgreSQL (including the reviewer's O124 race probes, untouched since), Asia/Kolkata, browser tests.

### Reviewer's probes at dfa2e29 (copied in, run, removed): 49 tests, 46 pass, 3 fail
- Now pass: every F1 probe (own set_password, the password-change page, the chain, the confirmer shown the proposer's credentials), F2 demoted and deactivated, F3 hash upgrade, F8 key rotation, F5 nested JSON, F7 waiting list (O195 and O196 variants), both F4 O131 probes, and the F6 crashes (sweep_other: 0 crashed).
- O191Sweep.test_sweep_non_object_bodies fails "(0, 12, 18) != (0, 0, 0)". Nothing crashes now. The 12 accepted are POST users/1/reactivate and manufacturing/dispatch/commit, which never read a body. The 18 that "worked before" add errors/1/reopen. The reviewer listed these as informational, not a regression.
- O198.test_a_right_given_by_a_queryset_update_...: `QuerySet.update(is_superuser=True)` fires no signal and is no door in the app. Informational, as the reviewer said.
- O131 survey test_which_viewsets_narrow_by_login_with_no_say_about_a_gone_record lists 4 viewsets: core.SavedFilterViewSet, core.PartyRoleAssignmentViewSet, sales.CustomerTdsViewSet, sales.InvoiceWriteOffViewSet. It looks for a `reads_only_some` attribute, and these 4 are not on AuditableViewSetMixin. Its premise no longer holds: may_read_gone asks `narrows_for` of every serving class, declared or not.

### Decisions and what remains
- A made login whose maker leaves or is demoted keeps its own password; only the passwords and emails the maker set lapse. The LOGIN fact stays, so O195 still refuses what the login does for its maker. Killing every made login's own password would lock out every worker HR ever made a login for. The residual: a keeper who laundered a puppet's password and then left can still sign in as it.
- A password changed through a reset link is recorded as nobody's. Suppose HR set a colleague's email (one HR did not make), reset the password through it, and the colleague is later given a role above HR. The email lapses, but the reset password does not, so HR still knows it. O195 still holds through the email row. Fix: a SetPasswordForm on /accounts/reset/ that records the email's setter as the password's. Kind: security, low.
- `group.user_set.clear()` (a reverse clear) sends no pk_set, so its setters are not re-checked. No such door in the app.
- The commit messages say "eleven viewsets" for F6; the test enumerates them from the routes, so the count is not load-bearing.
