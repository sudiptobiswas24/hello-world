# review_audit: fix_audit d76ef6f..29e2e8b (edit log, two layers, two roles)

Probes (uncommitted before this note, now committed unpushed): apps/core/tests_review_audit.py (run on PostgreSQL, database review_audit; 14 tests, 9 failures, 1 error as listed), apps/hr/tests_review_audit.py and apps/sales/tests_review_audit.py (written; run was still going when stopped: RESULTS NOT IN).

## Findings (each has a failing probe on the tip)
1. MEDIUM-HIGH, blocks: the application layer's own log is rewritable and deletable. RecordEvent.objects.filter().update()/.delete() raise nothing and leave no trace (history.py not_edit_logged); the app role also holds UPDATE/DELETE on core_recordevent (ledger_log.py grant_app grants all tables). Masters (parties, users, roles, rates) are logged only there. Probes p03, p13 FAIL. The 19-table trigger layer does not cover it.
2. MEDIUM, blocks: a database role's change to a trigger to "REPLICA" state: ledger_log.tables_without_their_trigger tests tgenabled <> 'D', so ENABLE REPLICA TRIGGER (never fires in normal mode) reads as healthy. Probe p10 FAIL (returns []). One-word fix: tgenabled IN ('O','A'). (Owner-can-disable is known; this is that the check cannot see one form of it.)
3. MEDIUM: SET_NULL on deleting a parent (every AuditModel.created_by/updated_by on user delete) updates child rows through Collector.update_batch, bypassing the wrapped QuerySet.update: no event. Probe p02 FAIL. Same for a role deleted: the logins' membership loss has no line on the login (p07 FAIL).
4. MEDIUM: a migration's RunPython (historical models, module __fake__) writes no application event: edit_logged() excludes them (history.py edit_logged). Probe p01 FAIL (0 lines for create, update, delete). On PostgreSQL the books tables still log, as "unattributed".
5. MEDIUM: raw SQL is traced only on the 19 tables. Chart of accounts, opening balances (accounting_booksopening), pay terms (hr_employeecompensation), TDS/GST/bank statements, deliveries and receipts have no trigger. Probe p11 FAIL (UPDATE accounting_account leaves no core_ledgerchange row).
6. MEDIUM (cost, growth): every API-key request writes an UPDATED edit-log line for ApiKey.last_used_at (api_keys.py:245), kept for ever. Probe p05 FAIL: 5 lines for 5 requests. Same for SecondFactor stamps at sign-in (second_factor.py:246-299) and User.last_login.
7. LOW-MEDIUM (access): the Edit log (core.view_recordevent, given to Controller and Auditor) shows old/new values of every model, including ones the role has no right to read in place (pay). Probes p21, p22 written, RESULT NOT SEEN (run unfinished): unproven.
8. LOW (personal data): PartyBankAccount.__str__ puts "A/c <whole number>" in a deleted record's summary and in FK labels; SHOWN_MASKED applies to field names only. Probe p04 did not fail/pass in the captured output (not listed among failures: PASS), so only FK labels remain unproven. Stored values of account numbers/PAN/Aadhaar are whole by decision.
9. LOW: a management command called inside a request/acting_as block loses the person (acting_as in BaseCommand.execute resets user to None). Probe p08 FAIL. Shell changes carry no person at all (OS user not kept).
10. LOW: the app role cannot setval a sequence (permission denied for sequence core_currency_id_seq): loaddata/sequence-reset under the app role fails. Probe p14 ERROR. load_demo does not call setval; restore runs as owner: so likely harmless. Unproven for the demo.
11. LOW (unproven): the app role can set erp.actor to any user id, so database-layer attribution is forgeable by the application; db_user still shows the role.

## Passed
Secret names: the SECRET regex covers every secret-named field (p06 only lists non-secret "key" fields, regex tightened); books-table columns hold no password/digest/secret/token column (p12 PASS). Admin/shell/command/API attribution tests in fix_audit's own suite stand.

## Not done (stopped by usage limit and a restart)
Cost timings (p30 pay run of 200, p31 500-line invoice, trail on/off): written, the run had not finished, so no numbers. Pay run post attribution (p20), roles reading (p21/p22) results not seen. Two-role run (demo load, go_live_check, backup/restore as the app role) not done. audit_invariants and makemigrations --check not run by me (author reports none/none).
