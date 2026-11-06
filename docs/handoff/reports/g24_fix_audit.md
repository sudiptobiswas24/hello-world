# fix_audit: the two-layer edit log (g24, gap item 6 / A1, RISKS known limit 35)

Worktree /home/user/work/fix_audit, base d76ef6f. Two commits: 7dc2181 "core: an edit log that keeps old and new values and cannot be bypassed (two layers)". Then 29e2e8b "core: the audit trail cannot be disabled from the application: two database roles (rule 3(1))". The session was restarted once; the work was intact and was committed, then the remaining lanes were run again on that tip.
Not pushed. docs/RISKS.md not edited (known limit 35 can come off in the integrator's commit naming this one).

## The rule, as checked
- Companies (Accounts) Rules 2014, rule 3(1) proviso, inserted by G.S.R. 206(E) of 24 March 2021: every company
  using accounting software must use software that records an audit trail of each and every transaction,
  creates an edit log of each change made in the books of account with the date when such changes were made,
  and ensures the audit trail cannot be disabled. Deferred twice (G.S.R. 247(E) 1 April 2021, G.S.R. 235(E)
  31 March 2022); in force for financial years from 1 April 2023. Auditors report on it under rule 11(g) of the
  Companies (Audit and Auditors) Rules 2014 (feature existed, operated throughout the year for all relevant
  transactions, not tampered with, preserved as statute requires).
- Retention: Companies Act 2013 s.128(5), eight years. The log is never pruned.
- For the accountant (not confirmed from the Gazette text itself, only from ICAI and commentary pages):
  1. the exact wording and whether "books of account" reaches masters (party, item, rates) or only
     transactions: built to log every record either way;
  2. whether rule 3(5) (books kept on a server in India, daily backup) is met by the deployment;
  3. whether an edit log of the *database's* own superuser actions (ALTER TABLE ... DISABLE TRIGGER by the
     owning role) is expected: the health check reports it but cannot prevent it (see "Could not do").

## What is built

### Application layer: apps/core/history.py
- `install()` (called from CoreConfig.ready, after the lock-order sentinel so it wraps the sentinel's
  wrappers): pre_save/post_save, pre_delete/post_delete and m2m_changed receivers for every sender, plus
  QuerySet.update/bulk_create/bulk_update wrapped, plus BaseCommand.execute wrapped to say which command.
- Each line (RecordEvent) now has `changes` {field: {"from", "to"}}, `through` (api, admin, web, shell,
  command:<name>, script), `who_name` (kept if the login is deleted), and `action` for a change an API action's
  own handling made (history.DOING). content_type is PROTECT (was CASCADE): a log line cannot be deleted by
  removing a stale content type. New index on -at for the edit log.
- Who: apps.core.middleware.Acting (new, in MIDDLEWARE after RequestMemo) holds the request; the user is read
  when the line is written, so DRF's token, API-key and session sign-ins are all seen. `acting_as()` for code.
- Values: Decimal as format(v, "f") at the column's own places (83.25 in an 8-place column reads
  "83.25000000"); dates ISO; FK {"id", "label"}; JSON as is; files by name. Secrets by field name
  (password|digest|secret|sealed: the user's password hash, ApiKey.digest, RecoveryCode.digest,
  SecondFactor.sealed_secret, Employee.pin_digest) only {"changed": true}, never the value, old or new.
- Bulk writes: chosen to write events, not refuse. update(): reads the matched rows' pks and old values,
  updates only those (`filter(pk__in=read)` on top of the caller's filter, so no row is written unlogged),
  reads the new values back (an F() is resolved), one line per changed row. bulk_create / bulk_update the
  same. QuerySet.delete() needs nothing: a receiver on every model means the collector sends each row's
  signals. Product uses grepped: 35 QuerySet.update() calls (list in /home/user/work/calc_fix_audit/
  update_uses.txt; sign-in's SecondFactor and ApiKey stamps among them), bulk_create at gst/gstr2b.py:284,
  bulk_update at purchasing/management/commands/record_posted_totals.py:44,46. All now logged; none refused.
- Which models: every concrete model under apps.* plus auth.User and auth.Group, unless the class says why
  not with `not_edit_logged`: RecordEvent and LedgerChange (the logs), LoginFailure, ApiKeyUse,
  StationAttempt (append-only logs of sign-in attempts) and ServerError (a log of failures; resolving one is
  still an action line). A proxy is logged as its concrete model whatever it says. Auto-created m2m tables are
  logged on their owner (m2m_changed: a role's permissions, a login's groups, before and after by label).
  Sign-in and access records (numbering_exempt: SecondFactor, RecoveryCode, ApiKey, GuardedRight,
  KeptCredential, User) are logged by this layer only, with no row lock taken: a SELECT of the old row and an
  INSERT of the line, so a sign-in waits on nothing new.
- A save that changes nothing but updated_at writes no line. A create records every non-empty value except
  its own key and created_at.
- The API layer (apps/core/audit.py) now writes only action lines; made/changed/deleted are the model
  layer's for an edit-logged model (so there are no duplicates).
- Kept whole, shown by its last four on every read (history and edit log): a bank account number, PAN, UAN,
  Aadhaar (SHOWN_MASKED). This was forced by an existing test (hr tests_review_people W8: the history of a
  pay-details change must not show the number). Decision taken: the stored line keeps the full value (the
  trail is complete; the model stores it in clear too), the display masks it. Say so to the owner if the
  auditor must read full numbers from the screen: it is one regex.

### approvals.authors() (apps/core/approvals.py)
Counts a login as an author from CREATED/UPDATED lines with `action=""` only. Without this, the order an
approver's own acceptance of a quote made (now logged by the model layer, as the approver) made them its
author, and sales tests_approvals SelfApprovalThroughARevisionTests refused the approval ("ar raised or changed
SO-draft-1"). Admin and shell edits now count as authorship too, which they did not before (stronger).

### Database layer: apps/core/ledger_log.py, migration web 0001_books_change_log
- core_ledgerchange (unmanaged model LedgerChange, so a test's flush, which TRUNCATEs managed tables, leaves
  it be): id, at (clock_timestamp), table_name, operation, row_id, old_row, new_row (jsonb; read back with a
  Decimal-keeping decoder), acting_user_id, through, transaction_id (txid_current), db_user (current_user).
- One AFTER INSERT OR UPDATE OR DELETE FOR EACH ROW trigger `erp_ledger_change` on 19 tables:
  accounting journalentry, journalline, accountingperiod, payment; sales invoice, invoiceline,
  invoicelinetax, invoicepayment, invoicewriteoff; purchasing bill, billline, billlinetax, billpayment;
  inventory stockmovement; hr payrun, payslip, payslipline; assets fixedasset, depreciationentry. Credit and
  debit notes are invoice and bill rows. (Decision taken where the brief did not list: write-offs and the
  line-tax tables are included as part of the documents' figures.)
- Append-only: BEFORE UPDATE OR DELETE row trigger and BEFORE TRUNCATE statement trigger raise
  insufficient_privilege; TRUNCATE also revoked from PUBLIC and CURRENT_USER (the app role, `erp`, not a
  superuser here).
- Who: an execute wrapper on every PostgreSQL connection runs `SELECT set_config('erp.actor', '<user
  id>|<through>', true)` before the first write statement of each transaction (again when the actor changes;
  validity tracked by a marker in connection.run_on_commit, which Django drops at a rollback or savepoint
  rollback, as PostgreSQL drops the setting). A write in autocommit is wrapped in its own atomic so the SET
  LOCAL reaches it. A change with no setting (psql, a script on its own connection) is recorded with through
  "unattributed".
- Migration: core 0043_edit_log (fields, PROTECT, index, the unmanaged model's state); web 0001 (RunPython:
  create(), idempotent: CREATE ... IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
  CREATE). Placed in apps/web because it reaches six modules' tables and core may depend on none.
  Reverse: drops the 19 triggers and the functions; drops the log table only while it is empty (decision
  taken: a reverse migration must not destroy the trail; forward again reuses it).
  A later books table is added by a migration that calls create() with it.
- SQLite: nothing; the tests skip there.

### Reading it
- History panel (frontend/src/views/HistoryPanel.tsx): each field's old -> new, "changed" for a secret, and
  what the change came through.
- Edit log: /api/core/edit-log/ (EditLogViewSet, read only, ?from=&to= on `at`, ?model=app.model, ?who=,
  ?kind=, ?through=, search over model, person, summary), permission core.view_recordevent; screen
  Settings > Edit log (frontend/src/modules/settings/EditLog.tsx, registry.ts).
- Roles (setup_roles): Controller gets core.view_recordevent; new "Auditor" role: the edit log, REFERENCE,
  view of journals, periods, payments, invoices, bills and their lines/allocations/write-offs, stock
  movements, fixed assets and depreciation, every customer. Added after the loops that give every role note,
  attachment and follow-up rights, so the auditor adds nothing.
- Health (apps/web/health.py): probe "books_change_log": every books table has its trigger enabled, and no
  unattributed change in the last 30 days (the window is my choice, so one old unexplained row does not
  leave the check red for good; the rows are named). On SQLite it answers None (cannot be proved).
- audit_invariants: `writes_without_an_event` reports raw SQL writes (execute("INSERT|UPDATE|DELETE|...")) in
  product code of every app, an AuditModel left out of the log or saying `not_edit_logged`, and the bulk
  wrappers taken off; planted-instance test in core tests_edit_log. None found in product code today
  (the only raw SQL is SELECT ... FOR SHARE in accounting/money.py and the health check's SELECT 1).

## Tests, and what failed without the fix
New: apps/core/tests_edit_log.py (18), apps/sales/tests_edit_log.py (1), apps/web/tests_books_change_log.py
(1, @tag migration, PostgreSQL), apps/web/tests_browser_edit_log.py (2).
Proof run in a scratch copy (/home/user/work/calc_fix_audit/proof, install() taken out and create() not
called; then a second run with the log's guards and the per-table triggers taken out), on PostgreSQL:
- admin change, shell change, setup_roles command, bulk update, delete, key label, password masked, API
  values, acting_as: each `IndexError: list index out of range` (no line at all);
- every-AuditModel-logged: `AssertionError: False is not true` (installed());
- auditor reads the shell's change to a posted invoice's date: `AssertionError: 0 != 1 : []`;
- raw SQL UPDATE / DELETE on PostgreSQL without triggers: `AttributeError: 'NoneType' object has no attribute
  'operation'`; app tells the database who: `Lists differ: [] != [('INSERT', 1, 'api'), ('UPDATE', 1, 'api')]`;
- log UPDATE, DELETE, TRUNCATE without the guards: `AssertionError: DatabaseError not raised` (each subtest);
- health with no layer: `AssertionError: None is not true`.
Changed to the new truth (each was encoding the API-only history): core tests_history (counts and a record
made in code now has its "Made" line), tests_attachments (the party's own "Made" line), tests_users_api
(count of the user's created line, not every row with that id), sales tests_screens_api (posting's "Changed"
and the delivery's "Made"), purchasing tests_msme (action lines filtered by kind="action": the model-layer
lines now carry the action's name too), web tests_health (the new probe).

## Runs (all on the final code unless said)
- fastsettings, --exclude-tag migration, --parallel 2, browser tests with PLAYWRIGHT_CHROMIUM_EXECUTABLE and
  a fresh `npm run build`: see "Final lane results" below.
- PostgreSQL (database fix_audit, nice 19), default settings with migrations: core/sales edit-log tests and
  the web migration test: 19 OK (before the masking and DOING edits); every apps/e2e/tests_races*.py
  module: 125 OK on the code before the masking/DOING edits; rerun on the final code: see below.
- `--tag migration` for core and web: see below (the first run was killed at the two-hour background limit,
  restarted detached).
- audit_invariants: no findings. makemigrations --check: no changes.

## Final lane results (tip 7dc2181)
- apps.core: 617 tests OK (skipped 9).
- apps.accounting apps.sales apps.purchasing: 2744 tests, one failure (purchasing tests_msme, the action-line
  filter), fixed before the commit; tests_msme then passed on its own (27 tests; its migration class fails
  under fastsettings exactly as it does at base d76ef6f: "table django_content_type already exists").
- apps.inventory apps.hr apps.assets apps.e2e apps.web, with the browser tests: 1827 tests OK (skipped 199,
  the PostgreSQL-only tests).
- PostgreSQL (fix_audit) with migrations: every apps/e2e/tests_races*.py module plus the core, sales and web
  edit-log tests, 145 tests OK.
- `--tag migration` for apps.core and apps.web (default settings): 8 tests OK (skipped 1, the
  PostgreSQL-only one, which passed in the PostgreSQL run).
- audit_invariants: no findings. makemigrations --check: no changes.

## Second commit: two database roles (the trail cannot be disabled from the application)
- Settings (config/settings.py): DATABASE_URL is the application's role, DATABASE_OWNER_URL the owner's.
  ERP_DATABASE_AS_OWNER=1 connects as the owner (deploy/entrypoint.sh sets it for migrate; wait_for_db.py
  waits on the owner's URL). In production with a DATABASE_URL and no owner URL, start-up refuses:
  "DATABASE_OWNER_URL is not set: ...". The same role named twice is refused. Tests in core tests_settings.
- apps/core/ledger_log.grant_app(), run by migration web 0002 and again at the end of every migrate
  (post_migrate, as the owner, so a restore is put right by the next start): makes the app role if missing
  (password from DATABASE_URL), refuses if it is the migrating role or owns a table, then REVOKE ALL and
  GRANT SELECT, INSERT, UPDATE, DELETE on all tables, USAGE, SELECT on sequences, default privileges for
  tables and sequences the owner makes later, and on core_ledgerchange SELECT only. No TRUNCATE, TRIGGER,
  REFERENCES or ownership. The trigger function is SECURITY DEFINER with search_path = public, pg_temp.
- docker-compose.yml: web DATABASE_URL=erp_app (POSTGRES_APP_PASSWORD), DATABASE_OWNER_URL=erp; the backup
  service (backup and restore) connects as erp. .env.example, backup.sh and restore.sh say so.
- Test apps/web/tests_books_log_roles.py (PostgreSQL, TransactionTestCase): makes a role in the test
  database, connects as it, posts a journal entry (its INSERT and posting UPDATE and both lines land in the
  log) and is refused ALTER TABLE ... DISABLE TRIGGER (named and ALL), DROP TRIGGER, UPDATE, DELETE, INSERT
  and TRUNCATE of the log, TRUNCATE of a books table, CREATE TABLE. Without SECURITY DEFINER it fails with
  "permission denied for table core_ledgerchange"; with the app role granted ALL it fails "DatabaseError not
  raised" for the log INSERT and the books TRUNCATE.
- Needs CREATEROLE on the role the test lanes connect as: I ran `ALTER ROLE erp CREATEROLE` on this machine's
  cluster. Without it the test skips and says so. The gate machine needs the same.
- The owner can still disable a trigger: it is the role migrations run as, and the health check's "books
  table without its trigger" stays to catch it.

### Runs on tip 29e2e8b
- PostgreSQL (fix_audit) with migrations: every apps/e2e/tests_races*.py module, core and sales
  tests_edit_log, web tests_books_change_log and tests_books_log_roles, core tests_settings: 164 OK.
- PostgreSQL `--tag migration` for apps.core and apps.web: 8 OK (web 0002 included).
- SQLite fastsettings apps.core and apps.web, browser tests included: 818 OK (skipped 10).
- audit_invariants: no findings. makemigrations --check: no changes.

## Third round: the review's findings (review_audit.md, probes 06872d4)
Probes cherry-picked as 0040a4a (message ends with `Gate: {GATE}` and the two trailers: kept as is).
Commits, one per decision:
- c8bdadf decision 1 (finding 1, p03 p13): RecordEvent append-only: save() of a saved line, delete(), and its
  QuerySet's update/bulk_update/delete/_raw_delete refuse (KeptAsWritten); a PostgreSQL trigger refuses UPDATE
  and DELETE on core_recordevent; the app role has SELECT and INSERT on it only; who is PROTECT. The log, its
  guards and the triggers are made again at the end of every migrate.
- 7e0ac9a decision 2 (finding 2, p10): a trigger is on only when tgenabled IN ('O','A').
- b9483fd decisions 3 and 5 (findings 4, 5; p01 p11): the trigger on every product table (migration web 0003 and
  every migrate); left out only django_session, django_admin_log, django_content_type, auth_permission,
  django_migrations and the two logs; health names any product table without a firing trigger. A migration's
  historical model is logged as the model it stands for, when the edit log's table has its present shape.
- 18024fa decision 4 (finding 3, p02 p07): a login that has acted (any line naming it as who, any
  created_by/updated_by) and a role that has acted (its own lines, or logins holding it) are never deleted:
  delete() refuses with a sentence (deactivate the login; take the role's logins and rights off), a QuerySet
  delete is asked row by row first, pre_delete refuses a cascade, the admin offers no delete (403). p02 and p07
  rewritten to the decided truth: the delete is refused and nothing changes.
- ea41ef2 decision 6 (finding 6, p05): `edit_log_ignores` on the class: ApiKey.last_used_at, SecondFactor
  last_step/failures/locked_until/lockouts, auth.User.last_login (stated in history.py). An update of only
  these writes no line.
- b34e556 decision 7 (finding 9, p08): a command inside acting_as or a request keeps the person.
- b1febe7 decision 8 (finding 10, p14): UPDATE on the sequences for the app role (setval); none on the change
  log's own sequence.
- 36463f3 p21/p22 decision (finding 7): the Edit log lists only kinds the person may read in place (superuser
  all); the Auditor reads every model read only (every view_ right but sessions'). What let a role in: looped
  over every role in setup_roles, every role but the Controller and the Auditor gets 403. The reviewer's 200 was
  the Controller's History of a compensation row: the Controller holds hr.view_employeecompensation by design
  (it posts the runs), so it reads that change in place and in the log alike. p21/p22 rewritten to the
  decision: the Auditor reads pay; the Controller reads only kinds it may read; a login with only the log's
  right reads no pay through it and is refused History. p22 names every role it asks.
- 94e7c37 (found in the two-role run): restore.sh restores with --no-privileges; the dump's grants named the app
  role, which a new machine has not got until the first migrate, so a restore there stopped at its first grant.
- 05adade follow-up found by the PostgreSQL --tag migration run: the end-of-migrate create and grants only
  where the database log exists; the edit log's guard trigger only where its table exists; a historical model's
  write (link included) logged only when the edit log has its present columns, asked by listing the tables
  first (a failed statement aborted the whole migration transaction on PostgreSQL).
- 3b8b9d3 the cost measurement: apps/core/tests_trail_cost.py trail_off() (the reviewer's errored on pending
  deferred checks and then left the wrappers off). Test-only; the product has no switch.

### Cost on PostgreSQL (seconds, trail on / off; three runs, probes p30 p31)
| | run 1 | run 2 | run 3 |
|---|---|---|---|
| 200-employee pay run: calculate | 9.48 / 7.30 | 8.94 / 7.06 | 9.87 / 8.31 |
| 200-employee pay run: post | 10.27 / 8.12 | 10.16 / 9.54 | 11.65 / 9.32 |
| 500-line invoice: build | 3.52 / 3.29 | 4.00 / 3.30 | 3.55 / 2.68 |
| 500-line invoice: post | 8.57 / 7.27 | 10.29 / 8.45 | 8.55 / 7.02 |
Roughly 10 to 30 percent on posting, measured on this loaded machine (two other agents' lanes running). The
"on" run of each test goes first, so it also pays any cold cache.

### The two-role run (real database fix_audit_tworole, app role erp_app_fa)
- migrate as the owner (makes the role): ok. setup_roles, load_demo and go_live_check as the app role: ok
  ("Ready, with 3 warning(s)"). As the app: DISABLE TRIGGER "must be owner", DELETE FROM core_recordevent and
  TRUNCATE core_ledgerchange "permission denied". 4,688 change-log rows, 0 unattributed; 2,183 edit-log lines;
  331 tables with a firing trigger.
- An INSERT by psql as the app role, with no actor set, made go_live_check fail on "The books' change log is
  whole: 1 change ... named nobody". Intended.
- backup.sh as the owner: written (with an attachment in the media directory). With the media directory empty
  it failed: "the manifest does not verify against the archive" (pre-existing, see Defects).
- restore.sh as the owner on this machine's cluster failed "out of shared memory" (max_locks_per_transaction is
  64 here; docker-compose.yml sets 1024). On a scratch cluster started with 1024 it restored (after 94e7c37),
  the attachment came back, migrate as the owner gave the grants, go_live_check as the app: Ready; refusals as
  above, and the owner's TRUNCATE of the change log refused too.

### Runs, third round
- On 3b8b9d3, fastsettings, --exclude-tag migration, --parallel 2, browser tests after `npm run build`:
  accounting+sales+purchasing 2745 OK; inventory+hr+assets+e2e+web 1832 OK (skipped 200).
- On 05adade (the tip): core 636 OK (skipped 15); PostgreSQL (fix_audit) every apps/e2e/tests_races*.py
  module with every review probe (core, hr, sales) and the edit-log, role and settings tests: 186 OK;
  --tag migration for core and web on PostgreSQL 8 OK and on SQLite 8 OK (skipped 1). 05adade touches only
  the migrate-time and historical-model paths; the two big lanes were not rerun on it.
- audit_invariants: no findings. makemigrations --check: no changes.

## Could not do / left
- (Closed by the second commit for the application's role; the owner role can still do it, as above.)
- The edit log screen lists the application layer only; the database layer (core_ledgerchange) is read by the
  health check and by SQL, not on a screen.
- remove_stale_contenttypes will now refuse to delete a content type that has log lines (PROTECT). Intended.
- Cost: every save of an existing row reads its old values first (one SELECT), every write of a logged model
  inserts a line, an FK label may be read. core's 617 tests ran in 944 s on a loaded machine.

## Defects seen and not fixed
- deploy/backup.sh:72-80 (pre-existing; kind: reverse/edge path of the backup): with an empty attachments
  directory the night's backup fails. The manifest of an archive holding no file has no line, and
  `sha256sum --check` refuses an empty manifest ("no properly formatted checksum lines found"), so a plant with
  no attachment yet has no backup at all. Seen in the two-role run (2026-10-27 05:12). Not fixed: not this brief.
- A restore on a cluster below max_locks_per_transaction=1024 fails "out of shared memory" (expected per
  docker-compose.yml; RUNBOOK should say so for a database not run by the compose file).
