# review_blockers: review of 7f7645b, 484a59e, 14a33fa, a979412, 1199be3

Probes committed on the review tree (/home/user/work/review_blockers): apps/{imports,hr,sales,purchasing}/tests_review_blockers.py
(print-only). Author's own tests for the five rows: 130 tests OK (fastsettings). Backup probes: scratchpad bk.sh with stubbed pg_dump/tar/sha256sum.

## Findings

1. BLOCKS. deploy/backup.sh:57-58 (the manifest pipe `tar -x --to-command=... | sort > manifest || failed`). A pipeline's
   status is sort's, so a failing step upstream is invisible. Scenario: sha256sum exits 3 (missing, killed, out of memory).
   Result: "backup written", exit 0, manifest holds 20 bytes of empty hashes, and retention then deletes every older backup
   (verified: the 40-day-old set was gone). restore.sh runs `sha256sum --check --strict` on it, so that night's backup cannot
   be restored: the O314 failure shape (a false success that retires the good ones) is still open on this one step. Dump, empty
   dump, archive, read-back and rename failures were each verified to print BACKUP FAILED, exit 1 and keep the old files.
   Fix: write the hashes to a file with a status check (no pipe), or check the manifest's line count against the archive's
   file count before the renames.
2. BLOCKS. apps/imports/base.py:101 once_only() + importer.py:424 (_opening_balances) and :294 (_opening_stock). The guard
   reads and then writes with no lock, so two imports at once (the double click its docstring names) both pass.
   PostgreSQL race() at JournalEntry: both "done", bank 500,000.00 for a 250,000 file. Opening stock: both done, on hand
   2,500 where one run gives 1,500. CLAUDE.md "Two people at once": lock_rows on a shared row (Company, or BooksOpening) at
   the top of run() / before once_only, then re-read. The same hole exists for open invoices, bills and orders (pre-existing
   exists() shape, now routed through the shared guard, so the fix belongs in once_only's callers once). No race() test was
   added by the author.
3. Does not block. apps/hr/models.py:_switch_login (about 428). `self.user.is_active == gone` cannot tell a login HR switched
   off from one the leaving switched off. Scenario: admin disables a login, HR sets a past leaving date (nothing to switch),
   then withdraws the date: the login is switched ON (verified is_active True). The "only for a login it would itself have
   switched off" claim holds only when the order is leave-date first. Needs the fact recorded (a flag at the switch-off), not
   inferred from the current state (mistake 4).
4. Does not block, worth a row. apps/accounting/settlement.py in_base() -> Currency.rate_on raises ValidationError when no
   rate stands on the document's date. Budget.committed()/spent() (purchasing/models.py:1497, 1523) walk every confirmed
   order in the period, so ONE confirmed foreign order dated before the currency's first rate (a backdated or cut-over open
   purchase order; confirm asks no rate while no policy is active) makes every budget read, every budget check and every
   other order's approval_reasons raise "No exchange rate for EUR effective on ...". Verified. Before O293 nothing looked up a
   rate there. Same for sales policy_figures on a line save of such an order. Suggest skipping or flagging that order, and a
   cutover check that every foreign open order has a rate on its date.
5. Does not block, tidy. backup.sh failed(): the .partial files of a failed night stay (verified for dump, archive and
   read-back failures); they are removed only after a later success (`-mmin +60`, line 71). On a disk-full failure, the
   case most likely, each night adds more. failed() should remove its own partials.
6. Does not block. Doors not given the O300 check at model level: PurchaseRequisition.approve/reject (purchasing/models.py:798),
   leave/timesheet doors are covered, but also manufacturing changes approve, quality decide. Reachable only by
   force_authenticate or a script, since the backend refuses a leaver's request, so a note, not a hole over HTTP.
   Verified over HTTP: leaving day signs in and works; the day after, an open session, a new sign-in and HTTP Basic are all
   refused (403/False); leave approval by a leaver with forced auth is 400, and 200 on the leaving day.
7. Does not block, wording. check_still_working message reads "B1 is not working here on 20-10-2026 (active)" for a
   leaver whose status is still active: it names the status, not the leaving date. login_ended also adds one Employee query
   to every authenticated request (ModelBackend.get_user), which the claims do not mention.
8. Not blocking, register gap. The author's D4 (Budget.requested() adds a requisition's estimated_price with no currency;
   a requisition has none, so base is right today) and questions Q1, Q4, Q5 (leaving-day rule, withdrawing a date, one
   opening-balances file) have no row in docs/RISKS.md (only O332 to O337 do). CLAUDE.md "Auditing": a defect seen and not
   fixed needs a row. Q5 is real behaviour: a second opening-balances file of other accounts is refused (verified).
9. Not blocking. vendor_prices (cutover.py:_vendor_price) has no repeat guard (run twice gives two rows); the brief's "every
   opening kind" claim covers money kinds only. fixed_assets and compensation keep their own pre-existing guards, and are not
   in importer.OPENING, so audit_invariants does not watch them.

## Held up
- O280/O313: second identical file refused; second file of other accounts refused (one file or reverse the entry); reversed
  first entry then second accepted, third refused; failed file leaves no entry; stock refused under a second reason posting
  to the same account; voided adjustment lets it in again.
- O292 over the API: Warehouse Staff return posts a draft (400.00), receivable stays 1,000.00; AR Manager posts it, due 600.00;
  Warehouse Staff cannot post it (403); AR can delete a draft; a second return takes only what remains; AR Manager's own
  return posts.
- O293: a 4.5M-base EUR order is refused for the 1M limit with the base figure in the words, approved by AR Manager over the
  API, confirm 200; tiers refuse the small group and pass the big one; a later-dated rate row changes nothing; a corrected
  rate row on the order's date moves the figure to 5,000,000 and a cut still saves.
