# review_sec (g22): adversarial review of fix_sec3, 1d19f72..24fb445

Worktree: scratchpad/review_sec, detached at 24fb445 plus one commit of probes, 643f537 (unpushed). No product code was edited.
Scratch logs: scratchpad/calc_review_sec/ (probe_run4.log, pg_kolkata_run.log, probe_races.log, probe_browser1.log, shots/*.png).

Probe files (all in 643f537):
- apps/core/tests_probe_review_sec.py: 36 tests on SQLite (fastsettings). 18 fail on the tip, as listed below (20 with the two browser ones).
- apps/core/tests_probe_review_sec_races.py: 3 tests, PostgreSQL. All pass.
- apps/web/tests_probe_review_sec_browser.py: 4 browser tests. b01 and b02 fail on the tip; b10 and b11 (the screens) pass.
- apps/core/tests_probe_review_sec_migrations.py: reverse and forward of core 0036 and 0037 over existing logins (tag migration). See "Migrations".

## Verdict

Two things block, one for sure and one I would hold the release on:
- **F1 blocks.** O216's fix locks honest workers out of passwords they chose themselves, and shuts the email way back as well.
- **F2 should block the two-factor claim.** After an administrator resets a factor, the old session enrols its own phone with no password and is fully signed in again. The reset is the incident response, and it hands the session a permanent second factor.

The two-factor core is otherwise sound: a code is taken once (also at the same moment, on PostgreSQL), a recovery code once and only by its own login, the lock holds under 30 simultaneous guesses, and a password-only session reached none of 2079 routes.

## Findings, by severity

### F1 (BLOCKS) A worker's own password lapses with the person who made the login (O216)
- Where: apps/core/roles.py:439-457 `carried_by_the_maker`, used at roles.py:553-554 (`_a_saved_login`); the lapse is `_lapse` (379-398) driven by `lapse_with_the_setter` (422-436) and `lapse_on_giving` (401-419).
- Scenario, as the logins in the author's own fixture: `hr` (HR Admin) makes `worker` with a first password. `worker` signs in and changes it on /accounts/password_change/ to one HR never saw. Then:
  - `root` deactivates `hr` (p30), or takes HR Admin from it (p31): `worker`'s own password stops working.
  - `root` gives `worker` a role HR cannot give (p32, a direct superuser grant; the two-person route issues a new password anyway).
  - With HR having set the address as well, the reset mail is refused too (O157), so there is no self-service way back (p33). Control in each: the password works before the event.
- Reach: every login HR made whose person changed the password. The day an HR administrator leaves or is demoted, all of them lose their password at once, and a keeper must re-issue each one.
- Brief's questions:
  - "What tells set by the maker as the login apart from set by the person?" Nothing does (p34: the two cases leave the identical KeptCredential row, `(set_by=HR, as_confirmer=False, lapsed=False)`). `_a_saved_login` is a post_save receiver on `instance._password`; it never learns who is typing. So the rule is unsound in one direction: it never misses the maker, and it always catches the honest person.
  - "A person's own password must never lapse with someone else": it does, so this blocks.
- What I would do: revert `carried_by_the_maker` and keep the O217 half (O217's own tests pass). Put O216 back in RISKS.md as a stated residual (a maker who signs in as the login and changes the password keeps knowing it), and close it by not letting a maker know any password: an invitation link to the person's own address or phone, so the maker never sets a password to launder. Say so in the row.

### F2 (SHOULD BLOCK) Enrolment asks for no password, so a reset hands the old session a new factor (p06) and a password-only session of an optional login can lock the owner out (p13)
- Where: apps/core/second_factor.py:121-126 (`half_signed_in`), 231-239 (`reset`, whose docstring says "Every session the old factor passed ends with it"), 286-331 (`step`, 291 and 311-327 for the enrolment).
- p06, logins `ctl` (Controller, enrolled, session passed) and `root`: `root` resets ctl's factor. The old session is no longer passed (ME answers 403) but is not ended: it is half-signed-in, `step()` serves it the enrolment page with a fresh secret, and a code from the thief's own phone makes it fully signed in. No password was asked. The comment promises the session ends; it only becomes enrol-capable (CLAUDE.md mistake 1).
- Scenario: a Controller's laptop is lost with the application open (idle limit 30 min) or its cookie lifted; the phone is lost too; the administrator resets. The thief enrols and keeps the login, then keeps it alive.
- p13: `sm` (Stores Manager, optional factor), any password-only session (a cookie lifted at a shared PC) enrols a phone first; the owner's next sign-in asks for a code from a phone that is not theirs, and only an administrator can undo it.
- Fix: ask for the password again at enrolment (a field on the enrolment form, checked with `authenticate`), and end the target's sessions on a reset.
- Related, not provable as a defect: first enrolment of a required login that has never enrolled goes to whoever holds the password first (trust on first use). The owner's own first sign-in is the protection; say so in the RUNBOOK, or issue the first enrolment through a one-time link.

### F3 (MEDIUM, owner's decision) The guarded-rights list leaves out rights that move or redirect money (p09)
- Where: setup_roles.py:669-675 `SECOND_FACTOR`; roles at setup_roles.py:51 (Bookkeeper: `hr.pay_expenseclaim`) and 268 (Purchasing Clerk: `core.add_partybankaccount`, `change_partybankaccount`).
- Scenario: `bk` (Bookkeeper) pays an approved expense claim, and `pc` (Purchasing Clerk) changes a vendor's bank account, each on a password alone. The classic fraud chain is the clerk redirecting a payment the AP Manager (who has the code) then posts. Also readable without the code: `core.view_partybankaccount` to the Purchasing Clerk and others, `accounting.change_journalentry` and bank statements to the Bookkeeper.
- The brief listed post a payment, see pay details, manage logins, and the author followed it. The question is whether "money, pay, bank details" should read wider. The same gap lets an integration login holding these rights be used with a key and no code.
- Probe p09 asserts every role holding `hr.pay_expenseclaim`, `core.add_partybankaccount` or `core.change_partybankaccount` needs the code. It lists Bookkeeper and Purchasing Clerk.

### F4 (MEDIUM) The kept draft is not the person's: the next login at the computer is offered it, and it never expires (b01, b02)
- Where: frontend/src/forms/useDraft.ts:16 (`KEPT = "erp.kept-draft:"` plus the path), 39-48 (written on `SIGNING_IN`), 49-57 (RESTORE); frontend/src/shell/SessionClock.tsx:62-80 (`DraftOffer`).
- b01: `inspector` (Quality Inspector) types into a new quality alert, the session times out, `inspector_b` signs in at the same browser. B lands on the same page and sees "You were typing here when your session ended, at 12:13 AM. Put it back / Discard" (screenshot shots/06_draft_offered_to_the_next_person.png), and "Put it back" fills B's form with A's text.
- b02: B signs out; A's draft is still in localStorage. No expiry, no user key, no clearing on sign-out.
- What a draft can hold: a new payment, an employee's bank details or PAN, a pay change. On a shared plant PC the next person reads it, or submits it as theirs.
- Fix: key the entry by the login's id (from /api/core/me/), drop an entry older than the idle limit, and clear all entries on an explicit sign-out.

### F5 (MEDIUM) An API key outlives whoever saw it, and an integration's login can also take a password (p24, p25, p27, p39)
- Where: apps/core/api_keys.py:194-207 (`issue` keeps `created_by` only), 216-243 (`authenticate` consults no issuer); apps/core/roles.py:422-436 (`lapse_with_the_setter` sees KeptCredential rows only); apps/core/users_api.py:337 (`set_password` has no integration check).
- p24: `hr` issues a key for an integration with no rights; `root` deactivates `hr`; the key still signs in. p25: same with HR Admin taken from `hr`. p27 (the O157 shape): the integration is later given a right HR cannot give; HR, which saw the key, now holds a credential to a login above it, and the key is not refused. For passwords all three lapse.
- The author's question: is the integration login's own rights the right boundary? It is the right boundary for what the key can do. It is the wrong one for who knows the key: the issuer is a keeper of that credential, and the same rule that ends a keeper's password should end a keeper's key.
- Today's reach is small. HR cannot know the scale integration (it lacks `add_scalereading`), so only a superuser issues the bridge's key. It grows with the GST provider and the WhatsApp channel.
- p39: `set_password` on an integration's login works, and HTTP basic then signs in as it with no expiry, no revoke and no use recorded.
- Fix: keep the issuer on the key; when `lapse_with_the_setter` or `lapse_on_giving` would lapse a credential the issuer set, revoke their keys as well (say so on the screen, since the bridge needs a new one); refuse `set_password` on a login that has an Integration.

### F6 (LOW to MEDIUM) A background poll keeps a session open past the idle limit; the header meant to prevent it is sent by nothing (p16, p17)
- Where: apps/core/session_limits.py:79-81; apps/manufacturing/templates/manufacturing/station.html:386 (`setInterval(() => this.poll(), 1000)` against /api/manufacturing/stations/<code>/scale/); frontend/src/api/client.ts:170-181 (`check`, which no caller passes).
- p16: 400 seconds of the station page polling, nobody at the PC, keeps the session open past a 120 s idle limit. A supervisor who signs in at a station PC and leaves the page open stays signed in until the 12 h limit (p15 proves the absolute limit holds). `X-Session-Check` was built for this, and is inert (audit shape 2, "who calls it": nothing).
- Fix: the station's poll sends `X-Session-Check: 1`.

### F7 (LOW) A code of non-ASCII digits answers 500 (p05)
- Where: apps/core/second_factor.py:167 (`hmac.compare_digest` on a str raises TypeError for non-ASCII), reached through 202 (`str.isdigit()` is true for Arabic-Indic digits) and 317 (the enrolment has no guard).
- No bypass and no count (an exception, not a failure). It records a ServerError and shows a 500 to a person who typed from a phone keyboard in another script. Fix: `code.isascii() and code.isdigit()`.

### F8 (LOW to MEDIUM) No way back for a sole administrator who loses the phone (p37)
- Where: users_api.py reset_second_factor refuses one's own login (`_not_myself`); no management command; RUNBOOK.md does not mention the factor.
- Scenario: one superuser, phone and recovery codes lost: only SQL.
- Fix: a management command `reset_second_factor <user>` (history recorded), and a RUNBOOK paragraph with the first-enrolment and lost-phone steps.

### F9 (LOW) The scale bridge uses a key file any user can read (p26)
- Where: deploy/scale_bridge/scale_bridge.py:250. The RUNBOOK says mode 600; the program does not check it. The key is not in argv (the config path is), not in logs (it logs the HTTP error only), and a 500 does not carry it (ServerError keeps path, method, message and traceback; Django's report cleanses `HTTP_AUTHORIZATION`).
- Fix: refuse a key file with group or other bits on Linux, with a message.

### F10 (LOW) With no guarded rights on file, nobody but a superuser needs the code (p36)
- Where: second_factor.py:96-103 `required` reads GuardedRight, which only setup_roles writes. deploy/entrypoint.sh runs it at every gunicorn start, so a deployed container is covered. A restored database, a `runserver`, or a one-off command is not, and nothing says so.
- Fix: seed it in migration 0036, or fall back to `setup_roles.SECOND_FACTOR` when the table is empty, and let audit_invariants report an empty table.

## Unproven, listed without a probe that fails
- The code lock restarts at zero when it lapses (second_factor.py:184), so a holder of a password gets five guesses per 15 minutes, about 480 a day, three valid steps in 10^6: about 0.14 % a day. Measured as 5 judged of 30 at once, so the lock itself holds. Counting repeats of a lock (back-off) would close it. Anyone with the password can also keep the owner locked out.
- Recovery codes of ten digits only (1 in 10^6) would be read as a TOTP code and never match.
- Refused attempts with a revoked, expired or unknown key are not recorded (only successful uses are, api_keys.py:239-242), and ApiKeyUse has no pruning (a bridge posting every few seconds adds tens of thousands of rows a day).
- The TOTP secret is stored in clear (the author's decision: encrypting with SECRET_KEY would break every factor on a key rotation). Anyone reading the table holds every login's factor. The row is not reachable through any API or serializer (grep).
- A reset button on the Logins screen has no confirmation (shots/09_logins_reset_clicked.png); Deactivate beside it has none either.

## Probes: PASS and FAIL, one claim each

Two-factor (apps/core/tests_probe_review_sec.py, TwoFactorProbes, WayBackProbes; races file):
- PASS p01 a code given once does not sign in a second time (twice in a row).
- PASS r01 (PostgreSQL) one code submitted by two requests held at the same instant: exactly one signs in. race() cannot hold a queryset `update()` (it hooks pre_save), so the probe holds both requests inside `_took_step`.
- PASS p02 a code two windows away is refused; one window away is taken once; the earlier window's code is then refused.
- PASS p03 a recovery code signs in once, only its own login's, recorded in history, and the lower-case, hyphen-less form is the same code.
- PASS r02 (PostgreSQL) one recovery code in eight simultaneous requests: exactly one signs in.
- PASS p04 five wrong codes lock; the right code is refused while locked; a fresh password sign-in does not clear it; the admin sign-in takes no code (it forwards).
- PASS r03 (PostgreSQL) 30 simultaneous wrong codes: 4 judged "did not match", 26 answered locked.
- PASS p12 HTTP basic is refused for a login with a factor, and unchanged for one without.
- PASS p07 a password-only Controller session reached none of 2079 routes (control: a passed session got 200 on 125); `/healthz/` is public by design.
- PASS p08 a login that gains a guarded right after signing in is stopped on the next request.
- PASS p10 a reset: HR Admin may not reset a Payroll Officer's factor, nor its own; root may; recorded in the history; a second reset is refused.
- PASS p11 a leaver's factor goes, and the login enrols afresh when reactivated.
- PASS p38 `next` cannot leave the application.
- FAIL p05 non-ASCII digits answer 500 (F7). FAIL p06 and p13 enrolment without the password (F2). FAIL p09 (F3). FAIL p36 (F10). FAIL p37 (F8).
- Data: the guarded rights are data in setup_roles (SECOND_FACTOR, written to GuardedRight at every start): confirmed. Its content is F3.

Session time-outs:
- PASS p14 idle limit on the API (403 and `X-Session-Ended: idle`). PASS p15 absolute limit holds for a session in constant use.
- FAIL p16, p17 (F6). FAIL b01, b02 the draft (F4). The warning, the sign-out and the draft offered back to the same person work (the author's browser tests pass on my build; shots/05).

API keys:
- PASS p20 shown once, kept as a SHA-256 digest only, never in any list or history. PASS p21 revoked, expired and unknown refused; every use recorded, including a request the key's login is refused (403). PASS p22 a key reaches only its login's rights. PASS p23 a key is refused once its login holds a guarded right (at use, and at issue).
- FAIL p24, p25, p27, p39 (F5). FAIL p26 (F9).

O216 and O217:
- FAIL p30, p31, p32, p33, p34 (F1).
- PASS p35 a link mailed while the address was trusted still works for a role HR can give. The author's own tests cover a link refused after the address lapsed. I found no case refused wrongly beyond the intended one: after HR leaves, a worker whose address HR set cannot reset by mail (O157, by design), which together with F1 leaves no self-service way back.

## Runs
- `manage.py audit_invariants`: prints "No invariant findings."
- `manage.py makemigrations --check --dry-run`: prints "No changes detected."
- Author's tests on PostgreSQL with DJANGO_TIME_ZONE=Asia/Kolkata (the lane the author could not run): apps.core.tests_second_factor, tests_session_limits, tests_api_keys, tests_users_api, plus my probes and races: Ran 117, 16 failures, all mine (this run was before p27 and p39). None of the author's tests fail.
- Front end: `npm run build` clean; `npx vitest run`: 4 files, 42 passed. The draft code (useDraft, SessionClock, DraftOffer) has no unit tests.
- Browser (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium, fresh build): the author's TwoFactorInTheBrowserTests and ASessionEndsInTheBrowserTests pass; my b10 (Controller enrols with a wrong code first, signs out, signs in with the code step) and b11 (Stores Manager offered "Set up two-factor sign-in"; HR Admin sees "Reset second factor" on the Logins screen) pass. Screens read well: shots/01_enrol.png to 09.

## Migrations
- core 0036 (GuardedRight, SecondFactor, RecoveryCode) and 0037 (ApiKey, ApiKeyUse, Integration, then an AddField on ApiKey for its integration) contain only CreateModel and that AddField: no RunPython, no RunSQL, no data step to test with MigrationExecutor. Each names the models intended (mistake 8 checked). Both depend on auth 0012 and AUTH_USER_MODEL.
- My probe m01 (tests_probe_review_sec_migrations.py, tag migration, PostgreSQL, real settings): PASS in 20 s. At 0035 the six new tables are absent; a login created there survives the move to 0037; the tables exist; reversing to 0035 removes them all; forward to the leaf restores them.
- `manage.py test apps.core --tag migration` (SQLite, the three core migration tests: two in tests_users_api and one in tests_numbering_years): Ran 3 tests in 32 minutes, OK (exit 0).
