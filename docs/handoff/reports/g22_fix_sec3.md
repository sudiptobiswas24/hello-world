# fix_sec3 (g22): L12 sign-in security; O216, O217

Worktree: scratchpad/fix_sec3, detached, on 1d19f72. 2 commits, tip 24fb445. Nothing pushed.
Scratch files: scratchpad/calc_fix_sec3/ (run logs run1..run5, migration outputs mm2/mm3, commit messages).

## Commits
- 4ee0ea3 O216 O217: a password set through the login or through its mailbox is known to whoever could set it.
  - Both rules are in apps/core/roles.py: `carried_by_the_maker` and `password_reset_through_email`.
  - users_api.KeptResetForm serves /accounts/reset/<uidb64>/<token>/.
- 24fb445 L12: two-factor sign-in, session time-outs, API keys for integrations, and the scale bridge on a key.
  - This is one commit, not three. The three items share settings.py, models.py, views.py (me) and the Shell, and splitting it would have cost the budget. The message describes each item separately.

## What was built (L12)
- **Two-factor (apps/core/second_factor.py)**
  - pyotp 2.9.0 for TOTP and segno 1.6.6 for the QR (SVG), both pinned in requirements.txt. Not django-otp: it keeps static tokens in clear and brings its own models and middleware.
  - Who needs it is data in `setup_roles.SECOND_FACTOR`, written to the GuardedRight table. The rights are post_payment, view_payslip, view_employeepaydetails, add_user and change_user.
  - That covers the Controller, AR and AP Managers, the Payroll Officer, the HR Admin, every superuser, and anyone given one of those rights directly. Any other login may enrol from the user menu, and then needs the code.
  - Enrolment shows a QR and the key, confirmed with one code, then ten recovery codes kept as SHA-256 digests and shown once.
  - Each code is taken once (a conditional update on its step). Each recovery code is used once, and the use is recorded. Five wrong codes lock the code step for 15 minutes.
  - The `Gate` middleware makes a password-only session anonymous everywhere: API, admin, shell. HTTP basic is refused for a guarded login. /admin/login/ sends people to the same sign-in.
  - Reset: `users/{id}/reset_second_factor`, allowed for whoever passes check_may_know, never for one's own login. It is recorded in the login's history and ends every session the old factor passed. The Logins screen has the button.
  - A deactivated login loses its factor.
- **Session time-outs (apps/core/session_limits.py)**
  - Idle limit is `SESSION_IDLE_SECONDS` (env DJANGO_SESSION_IDLE_MINUTES, default 30). Absolute limit is 12 h, and the cookie age matches it.
  - The check runs before authentication.
  - /api/core/me/ reports the limits. `SessionClock` warns before the end and offers "Stay signed in".
  - `useDraft` keeps the open drafts in localStorage on client.ts's `erp:signing-in`. `DraftOffer` offers them back after sign-in ("Put it back" / "Discard").
- **API keys (apps/core/api_keys.py, api_keys_api.py)**
  - An Integration is a login with no password, and its maker is kept.
  - Keys are shown once and stored as a digest plus a prefix. They expire within a year and can be revoked. Every use is recorded (ApiKeyUse).
  - A key is refused, at issue and at use, if its login holds a right that needs a second factor.
  - Issuing needs check_may_know.
  - The scale bridge now reads `api_key_file` and sends `Authorization: Api-Key`. The RUNBOOK covers making, rotating and revoking a key.
- Migrations: core 0036 (GuardedRight, SecondFactor, RecoveryCode) and 0037 (Integration, ApiKey, ApiKeyUse). I read both: each creates only those models.

## Tests failing without each fix
- O216/O217 (core.tests_users_api.WhatTheMakerOrTheMailboxKnowsTests, as the HR Admin and a superuser; fix reverted, test kept):
  - maker leaves: "True is not false : a leaver still signs in as the login it made"
  - maker demoted: "True is not false : a demoted keeper still signs in as its login"
  - reset through HR's address: "True is not false : HR reads the mailbox the reset went to"
  - link used after its address lapsed: "302 != 200"
  - Two reverse tests pass both ways (someone else issued it and its person changed it; a reset through the person's own address).
- L12 is new behaviour. These tests fail on the base because the endpoints and pages do not exist there. I did no per-test revert run.
  - core.tests_second_factor: 16 tests.
  - core.tests_session_limits: 4.
  - core.tests_api_keys: 7, including the bridge's own program posting to a live server with its key.
  - Browser: tests_browser_logins.TwoFactorInTheBrowserTests (Controller enrols, signs out, signs in with a code, a wrong one refused).
  - Browser: tests_browser_session (idle at 9 s: warned, signed out, next click goes to sign in, draft put back; and Stay signed in).

## Runs (fastsettings, --exclude-tag migration, --parallel 2)
- After O216/O217: core + hr + imports, Ran 948, OK.
- Tip, before the last two audit fixes: apps.core + apps.hr + apps.web with browser tests on a fresh `npm run build`, Ran 1122, 2 failures.
  - Both were audit-shaped tests: action_permission_map for issue_key and revoke, and the two new collections on no screen. Both are fixed in 24fb445.
- Then the touched modules plus browser logins and session: Ran 153, OK.
- audit_invariants: no findings. makemigrations --check: no changes. `npx tsc --noEmit` is clean.

## Could not do
- No PostgreSQL lane, no Asia/Kolkata lane, no `--tag migration` run, although I added two migrations. No race() test for two submissions of one code or recovery code: the guard is a conditional UPDATE, which I have not proved on PostgreSQL.
- No vitest run for the client.ts and useDraft changes.
- No screen for integrations and keys. An administrator uses the API's own pages; the RUNBOOK says how, and web.tests' collection list names both with that reason.
- API keys do not lapse with their issuer's reach, as KeptCredential passwords do. An issuer who is later demoted still knows a key they saw once. Revoke it by hand.
- One commit for the three L12 items instead of one per item.

## Decisions to check
- O216 cost: a worker whom HR made, who changed their own password, loses it when HR leaves or is demoted. They keep it only if someone else has issued one since, or if they reset through an address of their own. Nobody can tell the worker from HR typing as the worker. The previous agent (fix_sec2) chose the opposite and recorded the residual; O216 asked for it closed.
- The HR Admin may reset a factor only for logins whose rights it holds (check_may_know). So a Controller's or Payroll Officer's factor is reset by a superuser.
- Refused keys answer 403, not 401. DRF takes WWW-Authenticate from the first authenticator, which is the session's. I did not reorder it, because tests across the suite expect 403 for anonymous requests.
- The idle clock is written at most every 10 s (a tenth of a short setting). Writing on every request made an in-flight request answer 400 once another request had signed out and flushed the session.
- The TOTP secret is kept in clear, because checking a code needs it. Encrypting it with SECRET_KEY would break every factor on a key rotation.

## Defects seen, not fixed
1. apps/web/tests_browser_review_purch.py:28-31, test_01: a test the machine's speed can decide. The Controller clicks Confirm straight after signing in. Run alone in my tree it failed 2 of 3 times: once "unexpectedly None" (confirmed_on), once a 400 from the confirm (the figures read were not in place yet). It passed in the full run. The extra code step changes the timing, but the race is the test's. Kind: test defect.
2. apps/manufacturing/station_scale.py: a reading posted through the API keeps no created_by. The bridge's key posted a reading with created_by None, so the reading does not say which integration posted it, although ApiKeyUse does. Kind: rule, minor.
3. apps/core/session_limits.py flushes an ended session before authentication, so `user_logged_out` is not sent for a time-out. Nothing listens to it today. Kind: note.

## Review fixes (review_sec, integrator's decisions)

Worktree scratchpad/fix_sec3, on 24fb445 with the review's probes cherry-picked (ba00cf4). 9 more commits, tip b003a48. Nothing pushed. A second worktree, scratchpad/fix_sec3_run, is only for test runs (kept).

### Commits
- 2cce5e4 O216, review F1. `carried_by_the_maker` is reverted; O217 stays.
  - `roles.must_set_own_password`: a login whose password someone else set (a keeper, an administrator's set_password, a confirmer, a reset through an address someone else set) reaches nothing until it sets its own.
  - It is sent to /accounts/password_change/ (`second_factor.SetOwnPasswordView`) at sign-in, by the code step and by the Gate. HTTP basic refuses it, and the given password is refused as the new one.
  - Until the change, the given password lapses with its setter. After it, the password is the person's own and lapses with nobody: a LOGIN row's lapse no longer takes the password.
- 613abfd (integrator's addition): `apps/core/tests_second_factor.staff_session(client, user)` is the one helper that signs a login in through a session with its factor passed. The stores bridge probe now tests the key.
- 112caaf F2: enrolment asks the password again (through authenticate), and a reset ends every session the login has (`end_sessions`).
- e1d6054 F3, F10:
  - `SECOND_FACTOR` adds hr.pay_expenseclaim, core.add/change_partybankaccount and, found by reading every role's rights, hr.add/change_employeepaydetails, hr.add_salarytransferfile, hr.add_salaryadvance and purchasing.hold_vendor_payments. The Bookkeeper and Purchasing Clerk now need the code.
  - Migration core 0038 seeds GuardedRight.
  - `required()` falls back to the setup list when the table is empty.
- 8ed1c9b F4: a kept draft is keyed by login id, dropped after 24 h, and every one is cleared at sign-out (frontend useDraft.ts, SessionClock.tsx, Shell.tsx).
- 828a8e4 F5:
  - A key does not lapse with its issuer.
  - When the issuer leaves or loses the right to issue, `issuer_out_of_reach` marks the keys `issuer_left_at` and gives every remaining administrator one FollowUp naming them. The keys list shows issuer and issuer_left.
  - `issuer_could_give` is kept at issue. A right past it is refused in words, in give_or_propose and in m2m pre_add receivers (groups, own permissions, a role widened), and so is a superuser's standing.
  - An integration's login takes no password: the backend refuses it and set_password refuses to set one.
  - Migration core 0039.
- 1a43512 F6: station.html's scale poll sends X-Session-Check. The office clock asks /api/core/me/ with check:true as the warning nears; me now says idle_left_seconds.
- 9eadea4 F7, F8, F9 and the unproven items:
  - an ASCII-digit check on codes;
  - `manage.py reset_second_factor <login>`, recorded in the history, with a RUNBOOK.md section;
  - the bridge refuses a key file with group or other bits;
  - locks back off (15, 30, 60 min, 24 h at most; SecondFactor.lockouts);
  - refused key attempts are recorded (ApiKeyUse.refused and prefix; key may be empty);
  - `manage.py prune_api_key_uses` keeps 18 months;
  - migration core 0040.
- b003a48 F3 follow-up: core.tests_errors signs its Bookkeeper in through staff_session. It was the only failure in the whole fast suite at 9eadea4.

### Where I followed the integrator over a probe, and changed the probe
- p13: its setup asserted the takeover (`assertTrue(confirmed)`). It is now assertFalse, and its claim passes.
- p16: it now polls with the header, as the page does.
- p24, p25 and p27 asked a key to lapse with its issuer, and p33 and p34 asked for the O216 rule that was replaced. Each now points to the regression test that holds the decided expectation:
  - tests_api_keys.AKeyOutlivesItsIssuerAndTheAdministratorsAreToldTests;
  - tests_users_api.APasswordSomeoneElseSetIsReplacedFirstTests.
- p05 expects 200 (the page with "did not match"), as for every wrong code. The integrator wrote "400, not 500". I kept 200 and the probe; the point, no 500, holds.

### Tests failing without each fix (fix reverted, test kept)
- F1: 7 of 8 in APasswordSomeoneElseSetIsReplacedFirstTests fail, e.g. "Tuples differ: (302, '/app/') != (302, '/accounts/password_change/?next=/app/')" and "200 != 403". On the tip, probes p30 to p32 failed ("the worker's own password lapsed because HR left").
- F2: "'Type the password you signed in with' not found". p06: "True is not false : a session that gave only its cookie (no password) enrolled its own phone...". ALostPhoneTests errors.
- F3: p09 "{'Bookkeeper': ['hr.pay_expenseclaim'], 'P[urchasing Clerk...]} != {}", and p36 on the tip.
- F4: the vitest functions did not exist before. b01 and b02 failed on the tip (review).
- F5: all 4 fail, e.g. "Lists differ: [] != [('root', 1, True)]", "ValidationError not raised", "200 != 400".
- F6: all 3 fail, e.g. "'api(\"scale/\", { check: true })' not found".
- F7, F8, F9: on the tip p05 "500 != 200", p26 "the bridge used a key file with mode 0o644", p37 "no management command resets a factor".

### Runs, at the tip unless said
- Whole fast suite (apps, fastsettings, no migration tag, parallel 2) at 9eadea4: Ran 7201, one failure (core.tests_errors, fixed in b003a48), 285 skipped (browser). The earlier run at 613abfd was killed by the container restart and gave no result.
- `DJANGO_TIME_ZONE=Asia/Kolkata`, apps.core, hr, manufacturing, purchasing and web at b003a48: Ran 4259, OK (134 browser skipped).
- PostgreSQL races at 9eadea4: apps.core.tests_probe_review_sec_races and apps.e2e.tests_races, Ran 101, OK (r03: 3 judged, 27 locked).
- core `--tag migration` (real migrations, SQLite) at 9eadea4: Ran 5, OK (2065 s). That includes the 0038 seed test and the reviewer's m01.
- vitest at 9eadea4: 5 files, 45 passed. `npm run build` and `tsc --noEmit` clean.
- audit_invariants: no findings, and makemigrations --check: no changes, before every product commit.
- Browser lane: see "Open" below.

### For the integrated tree (g22tree, read only)
- The tests there that need staff_session are exactly the sites changed in 613abfd and b003a48, and they are the only force_login sites there outside station pages and the browser tests:
  - manufacturing/tests_api.py:334;
  - manufacturing/tests_who_lifts_a_hold.py:103;
  - purchasing/tests_review_purch_roles.py:186;
  - e2e/tests_races.py:1992 (1944 in mine);
  - e2e/tests_probe_review_race.py:43, 231, 345;
  - core/tests_errors.py:47.
- The core sites already use `for_tests(..., sign_in=True)` from 24fb445; 613abfd renames them to staff_session.
- Both commits apply as they are.

### Open: the browser lane is not green (blocks the gate)
- apps.web with the browser tests, after a fresh `npm run build` at 9eadea4/b003a48, ran three times and never finished:
  - parallel 2, under load: hung;
  - the sweep alone, parallel 1: finished;
  - parallel 2, quiet machine: hung.
- Each time, tests_browser_sweep's role subtests fail. The solo run (calc_fix_sec3/sweep.log) gave "FAILED (failures=4, errors=8)":
  - PlanningAndTheFloorSweepTests failed for Controller, Maintenance, Quality Inspector and Quality Manager. The page and API GETs answered 400 (e.g. /app/sales/aging, /app/making/routings, /api/core/payment-terms/), and /api/core/units... answered 500.
  - The errors were "database table is locked", in login_for (the test thread's create_user and groups.add) and then in the flush between tests.
  - A 400 on a GET is Django's SessionInterrupted: its db session save turns any DatabaseError, a table locked included, into UpdateError.
  - This is SQLite shared-cache table locking between the live server's threads and the test thread. The sweep passed twice at 24fb445.
- I did not establish which of this round's commits tips it. Suspects:
  - the Gate's per-request reads (must_set_own_password on keptcredential);
  - the new pre_add receivers on every groups.add in login_for;
  - two more roles taking the code step.
- The next step is to bisect one sweep class, about 10 min a run, between ba00cf4 and 9eadea4.
- A fix that removes the session writes entirely is a signed idle-clock cookie instead of `_last_seen` in the session. I have not done it.
- A separate, older defect makes it worse: in a parallel run, a failed sweep subtest hangs the worker for good. BrowserMixin.__getstate__ (apps/web/tests_browser.py:152-163) calls pickle.dumps on each attribute, and attributes that refer back to the test (bound methods such as `_note_failure`) recurse into __getstate__ (py-spy: 74 frames deep, then on). The lane prints nothing and never ends. Kind: test harness.
- Every other browser test that completed before the hangs passed (dots only, besides the sweep's F). I cannot say whether b01, b02, b10 and b11 ran before the hang in the last run. The reviewer ran b10 and b11 on 24fb445, and b01 and b02 are F4's subject.
- Not rerun after the restart: core + hr + web as one lane with the browser tests. core and hr were covered by the whole fast suite (9eadea4) and the Kolkata lane (b003a48).

### The sweep blocker, fixed (integrator's diagnosis and decision)
- 37d13df: the idle clock leaves the session. It is the signed cookie `erp_idle`:
  - a TimestampSigner of its own salt, on this module's clock;
  - binding sha256("idle-clock:" + session key)[:32], never the key;
  - set on the response to any non-poll request, at most once per `_resolution()`, and again when the session key changes;
  - the session cookie's Secure, HttpOnly, SameSite and domain, path /;
  - deleted when the session ends.
- SIGNED_IN_AT is written at sign-in only. A session without it ends. Nothing saves the session on an ordinary request.
- Deviation, said in the commit: a missing, forged or another session's cookie counts as no request since sign-in, so the session ends once it is idle_seconds() old. It does not end at once. The test client's login() and force_login() set no response cookie, and the strict rule would have ended every session test on its first request.
- Tests: core.tests_session_limits.TheIdleClockIsACookieNotTheSessionTests.
  - An ordinary API GET, a poll and a page GET leave the session row's data and expiry exactly as they were.
  - The cookie's flags; the key is not in its value.
  - Fresh within the limit stays open. Missing, tampered, another session's and stale each end it, over the API and the pages.
  - The idle and absolute tests are unchanged.
  - Without the fix, the row test fails ("Tuples differ: ('.eJxV...") and six cookie tests error (KeyError: 'erp_idle').
- ef3add0 is cb9d92d cherry-picked. The conflict in apps/web/tests.py came only from a neighbouring test my base lacks (EveryAdderSendsWhatItsListRequiresTests). I left that test out; the commit's own 33 + 30 lines are as in cb9d92d.

### Runs at the final tip, ef3add0
- tests_browser_sweep whole, parallel 1, three runs in a row (each 3 tests):
  - run 1: OK (2810 s);
  - run 2: OK (2753 s);
  - run 3: OK (2691 s). The three-run loop hit the 2-hour background limit during run 3; run 3 was started again on its own and passed.
- apps.web whole with the browser tests, parallel 2, after `npm run build`: Ran 192, OK. That includes the sweep, the review's b01, b02, b10 and b11, and the 2FA and session browser tests.
- Whole fast suite (apps, fastsettings, no migration tag, parallel 2): Ran 7210, OK (285 skipped: the browser tests, run above).
- apps.core and apps.web with DJANGO_TIME_ZONE=Asia/Kolkata: Ran 697, OK (133 browser skipped).
- PostgreSQL races, apps.core.tests_probe_review_sec_races and apps.e2e.tests_races: Ran 101, OK.
- vitest: 5 files, 45 passed.
- No migration was added after 9eadea4, so no new `--tag migration` run (core: 5 OK at 9eadea4).
- audit_invariants: no findings, and makemigrations --check: no changes, at 37d13df.
