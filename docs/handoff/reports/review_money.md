# review_money, gate g20 (adversarial review of 784af1c..829d5c6)

Worktree: $SP/review_money (detached at 829d5c6, probes committed on top, not pushed).
Probes: apps/accounting/tests_probe_review_money.py, apps/sales/tests_probe_review_money.py,
apps/purchasing/tests_probe_review_money.py. Hand-worked numbers: $SP/calc_review_money/calc.py
(and in each probe's comment). Run with
`PYTHONPATH=scripts/gate nice -n 19 .venv/bin/python manage.py test apps.<app>.tests_probe_review_money --settings=fastsettings --exclude-tag migration`.
A probe that fails is a finding; a probe that passes is listed under "held". The two
`TheAuditMissesProbe`/sales and `RequisitionEstimateProbe` etc. are written to assert the
right behaviour, so a failure there is the finding.

Totals: 11 findings, 2 blockers (F2, F4). F1 and F3 are conditional (see each).

## Findings

### F1. O49 is closed for one settlement only; a foreign document settled in two parts leaves a paisa (books, conditional blocker)
- Probes: accounting `PartPaymentResidueProbe.test_two_receipts_one_after_the_other_clear_the_receivable`
  and `.test_the_payable_mirror`.
- Code: apps/accounting/settlement.py:30-51 (`settlement_difference`, per allocation) with :175-190
  (`realise_exchange_difference`). The document's base for a part is `round(part x document rate)`; the
  last part does not take what is left of the document's booked base. Author's own "seen, not fixed" #1.
- Scenario (calc.py case 1b): 100.09 EUR booked at 1.12345 = 112.45. Receipts 50 and 50.09 at 1.09876
  book 54.94 + 55.04 = 109.98. Exchange per allocation 56.17-54.94 = 1.23 and 56.27-55.04 = 1.23. AR =
  112.45 - 109.98 - 2.46 = +0.01. `amount_due()` reads 0.00 while the ledger holds 0.01 (AP mirror -0.01).
  Settled once, the same invoice clears exactly (the author's test). One receipt cannot be split over
  one invoice (unique constraint), so the two-receipt shape is the reachable one.
- Blocks: not if the row stays open as a new id and O49 is not marked fully closed. It is not in RISKS.md.

### F2. O51 closed for drawdowns, not for the note that takes the last of the money (books, BLOCKER)
- Probes: accounting `HeldMoneyCycleProbe.test_deposit_drawn_in_half_then_the_other_half_credited_back_leaves_nothing_held`
  and `.test_prepayment_drawn_in_half_then_the_other_half_debited_back_leaves_nothing_held`.
- Code: apps/sales/models.py:3111-3158 (`_credit_deposit` -> `credit_note.post()`), apps/purchasing/models.py
  ~4482 (`_debit_prepayment`). Neither passes `held_left` or takes `deposit_base_left`; `post_drawdown(held_left=...)`
  is the only user of `held_base_left`.
- Scenario (calc.py case 2): 100 EUR held at 1.12345 = 112.35. Draw 50 takes round(56.1725) = 56.17. Credit
  the remaining 50: the note books round(56.1725) = 56.17. 112.35 - 56.17 - 56.17 = 0.01 stays on
  Deposits (-0.01, a liability with `deposit_unapplied()` 0.00). Prepayment mirror: Prepaid +0.01.
  Order reversed (note first, then the last draw) clears, so only the forward path was fixed (CLAUDE.md
  mistake 2: name the reverse path and write it in the same sitting).
- Blocks: yes, it is O51's own symptom (a held account that cannot reach zero) on the sibling path.

### F3. O80's second half has no purchasing twin: bill and debit note read wrong after a bounce (books, conditional)
- Probe: purchasing `DebitNoteAfterDiscountAndBounceProbe.test_note_then_bounce_then_pay_again`.
- Code: apps/purchasing/models.py:3558-3606 (`amount_absorbed`, `refund_due`, `amount_due`) still count a
  debit note's `reversed_discount` as debit; sales now subtracts `credit_spent_undoing()`. Author's #3.
- Scenario: bill 50 (10 @ 5), paid 49 with 1.00 settlement discount; debit note for 1 unit (5) undoes 0.10 of
  the discount; the 49 is recalled. Ledger payable = -45.00. Bill `amount_due()` = 44.90 (expected 45.00); the
  debit note `amount_due()` = -0.10 (a negative refund due; expected 0.00). Paying the bill 45.00 is refused
  ("only has 44.90 outstanding"). The vendor total happens to agree with the ledger; neither document does.
- Blocks: no, if entered as an id; it is the mistake-5 copy the brief names (share it).

### F4. A vendor's break is chosen by raw `min_quantity` across units; O98 made that reachable (books, BLOCKER)
- Probe: purchasing `VendorPriceUnitProbe.test_the_bigger_break_wins_across_units`.
- Code: apps/purchasing/pricing.py:44 (`sorted(..., key=-price.min_quantity)`), after `uom` was added to
  `VendorPrice` (models.py:219, migration 0066).
- Scenario (calc.py case 5): item in kg. P1 100 a kg from 1,000 kg. P2 90,000 a tonne from 2 t (= 2,000 kg).
  3 t ordered reaches both; P2 is the bigger break. Resolved price per tonne: 100,000.00 (P1), expected 90,000.00.
  A 3 t order is 30,000 dearer than agreed. Before 0066 every price was per the item's unit, so the sort was
  right; the fix introduced the way to break it.
- Blocks: yes. A kg price for small lots and a tonne price for bulk is an ordinary quotation.
- Related (rule, minor, same file :72-92): `preferred_vendor` compares `unit_price` across `uom` as it
  already did across currency (O100): V-1 100 a kg vs V-2 90,000 a tonne (90 a kg) picks V-1
  (`VendorPriceUnitProbe.test_the_cheaper_vendor_per_kilo_is_preferred_whatever_unit_it_quotes_in`). Extend O100.

### F5. A vendor price in another currency is still taken at face value when the caller passes no currency (books, minor)
- Probe: purchasing `RequisitionEstimateProbe.test_a_euro_vendor_price_estimates_a_requisition_line_in_base`.
- Code: apps/purchasing/pricing.py:47-60 (`resolve_purchase_price` converts nothing; the currency filter is
  skipped when `currency` is None). Callers with no currency: models.py:927-934 (`estimated_value`), :5358
  (consignment draw, which then builds a PO line at that price: not probed), :5447 (reorder rows).
- Scenario: 5 units, vendor price 10 EUR at 1.1. Estimated 50.00; expected 55.00 base. O78's shape on the
  purchasing side. The PO-line path is safe (an order always has a currency; a EUR price on a base order is
  refused, not mispriced: held).
- Blocks: no.

### F6. O201 does not reach a note line that names no original line (books, minor)
- Probe: sales `NoteLineAccountProbe.test_a_note_line_naming_no_original_line_credits_any_revenue_account`.
- Code: apps/accounting/mixins.py:119 (`if not getattr(line, f"{corrects}_id"): return`).
- Scenario: sale of 1,000 booked to 4000. A credit note line with no `credits_line`, description "Goodwill",
  100 to 4100, posts. 4000 stays -1,000 and 4100 reads +100 (expected 4000 -900, or a refusal).
- Blocks: no; the row's own probe (a line naming its line) is fixed. Record the residue.

### F7. `bad_debt_report` still reads the whole write-off as recovered (books, minor)
- Probe: sales `WriteOffSequenceProbe.test_bad_debt_report_after_a_part_recovery`.
- Code: apps/sales/models.py:3629 (`row["recovered"] += write_off.amount`); `recovered_amount` /
  `amount_recovered()` exist (3604) and only `customer_statement` was changed.
- Scenario: 1,000 billed, 400 paid, 600 written off, a credit note undoes 300, the other 300 recovered.
  Report: written off 600.00, recovered 600.00, net 0. Recovery put back 300. The net equals the ledger
  (bad debt 0) only because two errors cancel; the 300 the note undid is in neither column. A note that undid
  300 with no recovery reads net 600 against a ledger of 300 (older).
- Blocks: no.

### F8. Commission on a deposit drawn before it was paid is dated by the drawdown, so a closed period changes (rule, minor)
- Probe: sales `CommissionProbe.test_deposit_drawn_before_it_was_paid_is_counted_when_the_money_arrives`.
- Code: apps/sales/models.py:5785-5797 (`collected_up_front` covers `application.amount` by what the deposit
  holds paid *now*, dated `application.date`).
- Scenario: 300 deposit unpaid, drawn 6 March against a 1,000 invoice, 700 received 10 March, the 300 received
  10 April. March reads (700, 70) until 10 April, then (1,000, 100); April reads 0, expected (300, 30). Cash of
  April is commissioned in March and a March figure already reported moves.
- Blocks: no.

### F9. Pricing a foreign unit rounds the unit price and multiplies it back up (rule, minor, decision)
- Probe: sales `PricingProbe.test_a_thousand_euro_units_total_what_the_base_price_gives`.
- Scenario (calc.py case 4): 1,000 units listed at 10 USD, EUR at 1.1: unit price 9.09, line 9,090.00 EUR;
  exact 9,090.91. The unit-price column holds two places, so a fix needs a decision (total-first on the
  line), not a one-liner. Same shape as CLAUDE.md "never round a per-unit and multiply it back up".
- Blocks: no.

### F10. O199 untouched, shape confirmed (books, minor, already open)
- Probe: sales `FractionalGiveBackProbe.test_a_line_credited_in_two_halves_leaves_nothing_on_the_receivable`.
- Scenario: 2.5 @ 3.33 = 8.325 -> 8.33. Two notes of 1.25 book 4.16 + 4.16 = 8.32. Everything credited, AR and
  `amount_due()` both read 0.01. Row stays open.

### F11. The two new audit_invariants checks miss ordinary spellings (rule, minor)
- Probes: sales `TheAuditMissesProbe` (3 tests, all fail = all missed). Code:
  apps/core/management/commands/audit_invariants.py:1488 and :1510.
- `allocations_counted_past_a_void` matches only the text `.payment_allocations.all()`. A planted
  `self.payment_allocations.filter(amount__gt=0)` and a planted `payment_allocations.aggregate(Sum('amount'))`
  in `amount_paid` are not reported. `drawdowns_from_todays_setting` matches only `Company.get().<x>_account`
  inside the function that calls `post_drawdown(`: `company = Company.get(); account = company.customer_deposit_account`
  is not reported, nor a helper that reads the setting for it. Also keyed by function name only (a second
  function of the same name in the module is exempt with the first).
- Blocks: no. Both guard the shape of the day the finding was made, not the shape.

## Not findings, but record

- The author's "seen, not fixed" 1, 2, 3, 5 are not in docs/RISKS.md "Open defects" (O100 covers the currency half
  of 2 only; O202 is unrelated). CLAUDE.md "Auditing" says they go in with an id the day they are seen. F1, F3, F4
  (second part), F5 need ids.
- hr O151 test (`ClaimTests.test_submitted_decided_by_the_manager_and_paid_as_one_journal`): the rewrite was
  right. Numbers: claim 450 = 300 (6200) + 150 (6210). Paid from cash JE-1 (cash Cr 450), unpaid JE-2 (Dr 450),
  paid from bank JE-3 (bank Cr 450), unpaid JE-4. Cash 0, bank -450, 6200 = 300 - 300 + 300 = 300 after JE-3
  (the test's own dict), 0 after JE-4. The old line `first_reversal = claim.voided_entry` read the pointer
  *after* the claim was paid again, i.e. it read the stale reversal that O151 removes (the defect's trace in the
  setup); every assertion about the first reversal (`recorded_by() == claim`, `reverse_by_hand` refused) is
  unchanged and now meaningful, and the new test asserts `voided_entry is None` after the second pay and
  four distinct entries all in `claims_paid()`. Nothing outside hr/expenses.py reads a claim's `voided_entry`.
- Migrations: `makemigrations --check` clean. sales 0066 and purchasing 0066 add nullable columns (no row
  rewrite); NULL `recovered_amount` reads as the whole write-off (`amount_recovered()`), the old meaning.
  sales 0067 matches a charge line by `str(charge)` = `ChargeType.name`, which is what `SalesOrderLine.label()`
  wrote into the approval note; it only raises. Left as 0065 left it: a note with no figures, and a line raised
  after approval and not named in the note is seeded at what it holds (0065's own rule; 0067 does not widen it).
  Real-migration run of apps.sales.tests_seed_approved_figures (default settings, the three tests incl. the
  author's 0067 MigrationExecutor test): OK. `audit_invariants` on the tip: "No invariant findings" (so none of
  F1-F11 is visible to the gate).

## Held under pressure (each passed)

- Deposit and prepayment, three-way split 33.33/33.33/33.34 at a later rate: deposits 0, AR 0, FX loss 7.66;
  prepayment AR/AP 0.
- Deposit credited back in part then drawn to nothing; drawn then noted in the rest (60/40 divides cleanly).
- Setting moved after the deposit/prepayment, then a note and the last draw: both the old and the new account 0.
- Sales credit note and purchasing debit note refunded at one rate, bounced, refunded at another: AR/AP 0, only the
  second difference stays (gain 7.65 / loss 7.65), note due 0 (O70 both ways, O53 with FX).
- Bounced receipt then paid again at another rate: AR 0, only the second difference stays.
- Re-pointing a foreign allocation (X 500 EUR @1.1 to Y 500 EUR @1.2, receipt @1.15): AR 550, net FX 25, dues 500/0.
- Re-pointing over the API: AR Manager 400 on the over-ceiling re-point, Sales Rep 403, AP Manager 400 and
  Purchasing Clerk 403 on the bill side, allocation above its payment 400.
- Write-off sequences (note larger than the write-off then refunded then bounced; written off in full, noted,
  recovered, noted again, paid; note before the write-off; two write-offs with a note and two recoveries):
  ledger AR = sum of dues = outstanding_balance = aging (positive cases) = `still_owed()` set, after every step.
- Commission: bounced then paid again counts once; 1,000 EUR at 1.1 paid at 1.2 counts 1,100 / 110; credit note
  refunded a month later: March (1,000), April (-300), whole (700); deposit paid in February counts in the March
  of its drawdown; a deposit partly credited back counts only what was drawn; an invoice met by a deposit and
  a payment, credited and refunded in full, nets 0 with Deposits 0.
- Pricing: EUR list in boxes (84 and 96), a break at 24 eaches reached by 2 boxes and not by 1.9999, a default EUR
  list does not price a base order at face value, a quoted price survives a rate move into the order, a blanket
  release at 5% receives and bills to 950.00 with GRNI 0.
- Blanket, vendor price API, `makemigrations --check`, hr four-entry history: as above.

## Not covered

PostgreSQL and Asia/Kolkata runs; a taxed (job-work) advance in a foreign currency (the tax base per drawdown is
`round(tax x rate)` each, not what is left on the tax account: reasoned from settlement.py:276-280, not probed);
the frontend; `bill_variation` and other pricing callers outside the six `resolve_*` sites.
