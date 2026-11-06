# review_purch, gate g22: adversarial review of fix_purch (e73428e..674ac8a)

Worktree: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/review_purch (detached at 674ac8a; one commit of probes on top, nothing pushed, no product code edited).

Probe files (all new, all assert what SHOULD be true; a failing probe is a finding):
- apps/purchasing/tests_review_purch.py (TDS, TCS, invoice numbers, credit-note number, MSME, returns, match, policy)
- apps/purchasing/tests_review_purch_roles.py (who may, over the API; the confirmed-order paths; sales mirror)
- apps/purchasing/tests_review_purch_migrations.py (MigrationExecutor, tag migration)
- apps/e2e/tests_review_purch_races.py (PostgreSQL race())
- apps/web/tests_browser_review_purch.py (Playwright; needs `npm run build`, PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium)

Run: `PYTHONPATH=<scratchpad> .venv/bin/python manage.py test <module> --settings=fastsettings`; the races with `DATABASE_URL=postgres://erp:erp@localhost:5432/review_purch` (test_review_purch dropped first).

Mechanical gates on the tip: `audit_invariants` prints "No invariant findings."; `makemigrations --check --dry-run` prints "No changes detected."

The brief's "AP Clerk" is not a role in setup_roles; I used AP Manager (it holds purchasing.post_bill and add_tdsdeduction) and Bookkeeper.

## Findings, by severity

### Blocks (fix before this merges)

**F1. O233: the TDS rate follows a free `date`, so a deduction dated before its bill picks the old rate.**
- `apps/purchasing/tds.py:133-134` (`day = to_date(on_date) or to_date(bill.bill_date)`, then `deduction_terms(..., day)`); the API takes it at `apps/purchasing/views.py:270` (`date`).
- Scenario: Loom Fitters (PAN on file, 194C) bills Rs 50,000 on 5 Jul 2026. The rate is 2% to 30 Jun and 3% from 1 Jul. The AP Manager posts `deduct_tds` with `date=30 Jun`. Result: Rs 1,000 at 2%, journal entry dated 30 Jun, five days before the bill, so it lands in the April-June 26Q. The law's figure is Rs 1,500; Rs 500 short, and the return period is wrong. The reverse (a later date picks the later rate) over-deducts the same way.
- Before O233 the date only moved the entry; now it also picks the rate. The office screen sends no date (its button is a confirm box), so only the API and integrations reach it.
- Probe: `TdsDatedRateProbes.test_02b_a_deduction_cannot_be_dated_before_the_bill...` FAILS ("deducted 1000 at 2.0000% dated 2026-06-30"). Fix direction: refuse a deduction date before the bill's date, and read the rate for the bill's own day (the credit), or the earlier of credit and payment.

**F2. O211 (reverse path): goods returned before any bill covers them are debited against a bill that paid for other goods.**
- `apps/purchasing/models.py:7765-7787` (`_bill_lines_that_paid_for` falls back to `rest`, oldest first, even though `receipts_billed_by` (6327) already knows those goods are unbilled) feeding `_debit_returned_goods` (7789-7830).
- Scenario: 20 ordered at 5.00. R1 = 10 and R2 = 10 received. B1 bills 10 at 5.00 (R1's goods). 4 of R2 go back, `debit_bills` defaulting to true. A posted debit note of Rs 20.00 is raised against B1. The supplier then bills what is held of R2, 6 at 4.80. Owed should be 50.00 + 28.80 = 78.80; the books say 58.80, and GRNI is stuck at 20.00 credit for ever. Supplier's statement and ours differ by Rs 20 on B1.
- It is the brief's "a return between two bills". Pre-existing fallback, not a regression, but O211's replay does not close it: the replay knows the goods are unbilled and the debit loop ignores it.
- Probe: `UnbilledReturnProbes.test_01...` FAILS: `([('B-1', 20.00)], -20, 58.80) != ([], 0, 78.80)`. The three scenarios in the author's spirit (return before the bills, a return between two bills of different receipts, two returns of two receipts) debit the right bills (`ReturnsInPostingOrderProbes` 01-04 PASS), and GRNI = (net received - (billed - debited)) x price held over 25 random sequences (`test_02` PASS), so the replay itself is sound where a bill holds the goods.
- A return voided: there is no void path for a goods return, so nothing to probe.

**F3. O167, shared with sales: a tax added to a confirmed line is never asked of the policy, on either side.**
- The policy is asked only from `PurchaseOrderLine.save/delete` (`models.py:2659`, `2852`) and `SalesOrderLine.save/delete`. A line's `taxes` is a many-to-many the serializer writes after the save, so `ChangesWithinItsApproval` (`apps/core/approvals.py:203`) never sees it.
- Purchasing: 18 x 50 = Rs 900 confirmed under a Rs 1,000 limit. The Purchasing Clerk PATCHes `/api/purchasing/purchase-order-lines/<id>/` `{"taxes": [GST18]}`: HTTP 200, order now Rs 1,062.00 with no approval.
- Sales: Rs 100 confirmed under a Rs 110 limit; PATCH `/api/sales/sales-order-lines/<id>/` `{"taxes": [GST18]}` as a superuser: HTTP 200, total 118.00. (A Sales Rep gets 403 there, so that route is shut by permission only.)
- Probes FAIL: `ConfirmedOrderPathProbes.test_01`, `SalesPathProbes.test_02`, `PolicyProbes.test_01`. Line adds, removals, price and discount changes, raising past an approval, and an agreed price falling after the order all PASS (`PolicyProbes.test_02, 03`, `ConfirmedOrderPathProbes.test_02`). The Django admin bulk "delete selected" on a received purchase-order line leaves it standing (`test_03`, non-vacuous: a draft line is deleted by the same call).

### Should fix, does not block

**F4. O233 race: a rate edited as a bill deducts leaves the deduction citing a row that disagrees with it.** PostgreSQL, `race()`: outcomes `['done', 'done']`; the deduction keeps 2.0000%, its rate row now says 5.0000% and is unconfirmed. `dated_rates.py:37-48` reads `taken()` and then saves without a lock, and `deduct` (`tds.py:~120`) locks the vendor and bill but not the rate. Money in the ledger is right (the kept 2%); the fact the deduction cites is not. Probe: `apps.e2e.tests_review_purch_races.ReviewPurchRaces.test_02` FAILS. Fix: `lock_rows(rate)` in `deduct` and `@serialised` on the save/confirm.

**F5. O95: the key folds only ASCII separators, so Unicode look-alikes are a second invoice.** `models.py:3184-3197` strips `\s`, `-`, `/`, `.` then upper-cases. Each of these is accepted beside "INV-77", same vendor, same year: zero-width space, BOM, soft hyphen, hyphen U+2010, non-breaking hyphen U+2011, en dash U+2013, em dash U+2014, minus U+2212, full-width hyphen U+FF0D, full-width letters and digits, full-width solidus, division slash (12 of 12). "INV-77" and "INV-77" with an en dash (pasted from a PDF) both post, and are paid twice. NBSP, narrow NBSP, ideographic space and tab ARE folded (PASS). Probe `InvoiceNumberProbes.test_02` FAILS. Fix: NFKC, then drop everything in categories Cf, Pd, Zs/Zl/Zp and the three ASCII separators. The migration's frozen copy equals the live key today (`test_08` PASS), so change both together.

**F6. O95: the key can overflow its column on PostgreSQL.** `reference` is 64 characters, `upper()` can make 3 of one ("ﬃ" -> "FFI", "ß" -> "SS"); the key is `"2026:"` + up to 192 characters in `max_length=80`. 30 x "ﬃ" -> DataError "value too long for type character varying(80)" (a 500, not words). Probes `InvoiceNumberProbes.test_09, test_10` FAIL on PG (test_10 only on PG). Contrived; low.

**F7. O95: a separator-only reference ("-", "/", ".") is no longer unique.** Its key is empty so the constraint skips it: two bills "-" from one vendor both stand, where the exact constraint refused the second. Probe `test_07` FAILS. Probably intended; the accountant should say.

**F8. Pre-existing, now carrying TCS: a person without `post_bill` posts a debit note by returning goods.** `return_receipt` asks `purchasing.post_goodsreceipt` (`views.py:386`), defaults `debit_bills` to true (`views.py:459`), and the notes post at once and now take back the bill's TCS. Warehouse Staff: HTTP 200, one posted debit note. Probe `RoleProbes.test_07` FAILS. Mirror of "debit_note needs post_bill".

**F9. O233: `confirm` vouches for whatever is in the row, not what the accountant looked at, and can be applied twice.** A screen loaded at 2%, a second edit to 20%, then Confirm: 20% is confirmed and deducted from then on (`dated_rates.py:54-58`). A second confirm overwrites `confirmed_by`. Probes `TdsDatedRateProbes.test_09`, `RoleProbes.test_08` FAIL. Only the Controller can edit, so it needs two Controllers.

**F10. O234: TCS cannot be keyed in the office application.** Known to the author. Browser probe: the bill screen shows "TCS collected (206C(1F)) 1,000.00" and "Total 1,01,000.00" correctly, but the new-bill screen has no TCS field (`tests_browser_review_purch.test_02` PASS as a record of the gap). The Purchasing Clerk can key it over the API (draft 201), cannot post it (403), the AP Manager posts it (200), and nobody can PATCH it once posted (400).

### Questions for the accountant (not defects I can prove)

- **Q1. O154: "never changed after" has no remedy.** A mistyped number, or a number given without its date, is frozen on a posted note and a posted note cannot be corrected. The key still has its year (`'2026:CN-9'` without a date), so I could not show GSTR-2B failing to pair; only that the date can never be added (`SupplierNoteProbes.test_05` FAILS as a record).
- **Q2. O95 carried over, not new:** a bill fully debited still holds its invoice number, so the supplier's corrected invoice with the same number is refused (`InvoiceNumberProbes.test_06`).
- **Q3. O234, unproven:** `returns.py:153` feeds `source.total()` (TCS included) to GSTR-2B's "value", and "value" is one of the compared figures (`gstr2b.py:39`). If the supplier's filed invoice value excludes income-tax TCS, every TCS bill shows a value difference. The author raised it too.
- **Q4. O234, unproven:** the TCS receivable account is any asset account that does not hold money (a bank account was refused by something I did not trace); Inventory would be accepted. No cap on the TCS amount against the bill.

## What PASSED (each is a probe on the tip)

- **O233.**
  - Rate changes on the 1st and mid-month: 30 Jun 1,000 at 2%, 1 Jul 1,500 at 3%, 14 Jul 1,500, 15 Jul 2,000 at 4%, 31 Jul 2,000 (`TdsDatedRateProbes.test_01`).
  - A rate used cannot be edited, moved, or deleted, model and API (Controller gets 400, not 500), also after the deduction is reversed (`test_03`, `RoleProbes.test_02`).
  - The return and the challan stand at the kept rates: Rs 1,000 and Rs 1,500, TDS payable clears to 0 (`test_04`).
  - No-PAN rate: 20% on 50,000 = 10,000, PAN-NOT-AVBL in the return, a PAN given later moves nothing before it (`test_05`).
  - 194C: 25k x3 then 30k across a rate change takes the year, 105,000, at the day's 3% = 3,150, covering 4 bills (`test_06`).
  - 194Q: the excess over 50 lakh at the new 0.25% = 2,500 (`test_07`).
  - A rate edited while untaken needs reconfirming (`test_08`).
  - TCS collected by the supplier stays out of the TDS base (`test_10`).
  - Roles: Controller reads/creates/confirms (200/201/200), AP Manager reads only (200/403/403), Purchasing Clerk, Stores Manager and Bookkeeper nothing (403).
  - A rate confirmed as a bill deducts: nothing deducted at an unconfirmed row (PG `test_03`).
  - Data migration 0030 on old rows (MigrationExecutor, default settings): PASS, one unconfirmed row per section from 2000-04-01 with its figures.
- **O234.**
  - 7 x 14,285.71 + 1,000.00 TCS = 100,999.97: bill total, payables credit, `amount_due` agree; receivable 1,000.00 (`TcsProbes.test_01`).
  - Three part notes take back 428.57 + ... exactly 1,000.00, receivable = tcs - taken back after each, payables and receivable end at 0 (`test_02`).
  - A third of the bill takes 3.33 of 10 (`test_03`).
  - A goods return takes its 4.00 of 10: note 404.00, receivable 6.00, payable 606.00 (`test_04`), GRNI 0 (`test_09`).
  - The payment run foots to the payable ledger (`test_05`).
  - A posted bill's TCS cannot be changed, by save or PATCH (`test_07`, `RoleProbes.test_05`); a hand-made note cannot take back more than is left (`test_08`).
- **O91, O99.** The match compares net prices, the tolerance on the net: 100 less 10% passes, 94.50 passes, 94.51 and 100 refuse (`MatchProbes`; my expectation for "99.45 less 5%" was my arithmetic, 94.4775 is inside 94.50, and the code was right).
- **O167.** Lines added, raised past a limit, raised past an approval, an agreed price falling after the order: all as the policy says (see F3 for the one path that is not).
- **O95.** ASCII, whitespace, NBSP variants refused; the 31 Mar / 1 Apr boundary (a new supplier year); re-dating a draft into a collision refused; two vendors may share a number. **Migration 0076 on old rows (MigrationExecutor, default settings): PASS.** "INV-77" (10 Jun) is keyed `2026:INV77`; "inv 77" (12 Jun, same vendor and year) is left unkeyed and named in the printed report ("left unkeyed, not merged"); "Inv-77" in the next year, "INV.77" in the previous year and another vendor's "INV-77" are keyed on their own; a blank reference is unkeyed. (Old rows cannot hold an exact duplicate, so every collision here is a case, spacing or separator variant.)
- **O154, O190.** Set once, a second number (same or different, any case) refused, a full save cannot slip one in, key kept, one credit note answered by one debit note. API: AP Manager 200 then 400; Purchasing Clerk, Stores Manager, Bookkeeper 403; a plain PATCH of a posted note's number is refused (`SupplierNoteProbes`, `RoleProbes.test_03, 04`). Race (PG): two numbers set at once, exactly one wins and the stored number is the winner's (`test_04`).
- **O188, O189.** TDS 100 on 10 Jun + note 300 on 20 Jun + payment 600 on 25 Jul on a 16 Jul due: paid 25 Jul, 9 days late; TDS + note only: paid 20 Jun, 0 late; a reversed deduction is not a settlement; objected then removed (5 Jul): 600 due 16 Jul, 400 due 19 Aug, `pay_by()` 19 Aug, and the payment run's amount and date agree with `installments()` for four cut-off dates; once the first part is paid the run shows 400 beside 4 Aug (`MsmeProbes`).
- **O227, refuted.** The author's race test is sound. Same scenario on PostgreSQL: one of the two is refused, no outcome mentions "deadlock", released <= 100 (`test_05`). With the agreement's lock taken away by patching `lock_rows` (no source edit) both go through and 110 are called off, so the test does fail when it should (`test_06`). One gap in the author's test as written: it asserts `sum(done) == 1`, which a deadlock victim would also satisfy; my copy adds the "deadlock" check.
- **Races on PostgreSQL.** Two bills with one invoice number at once: one done, one refused by the constraint (`test_01`). A rate confirmed as a bill deducts: nothing at an unconfirmed row (`test_03`). F4 above is the one that fails.
- **Who may.** Tests above. The office screens: a pending rate reads "To confirm" for the AP Manager with no Confirm button; the Controller confirms it; no console problems or 403/404 on either (`tests_browser_review_purch.test_01` PASS).

## Mistakes of mine, caught by hand (mistake 7)

Two expectations of mine were wrong and the code was right: "99.45 less 5%" is 94.4775 (not over 94.50), and two returns' payable is 98.00 - 34.20 = 63.80 (I had typed 53.80). Neither was adjusted to the output; both recomputed from the scenario.

## Not run

- The whole suite on PostgreSQL, the Asia/Kolkata lane, the four existing migration-tag tests, the author's own new tests.
- The migration probes take about 9 minutes each run (the test database is built by migrating from scratch).
