# review_cost, gate g20 (fix_cost, 6817708..ad4a2d0)

Worktree `$SP/review_cost` (detached at ad4a2d0), probes committed as 24f6904 (not pushed):
`apps/{purchasing,hr,inventory,gst}/tests_probe_review_cost.py`, `apps/manufacturing/tests_probe_review_cost_pay.py`.
Hand figures: `$SP/calc_review_cost/{landed,pay,grni_seeds}.py`. Run log: `$SP/reports/review_cost_probe_run.log`.
170 probes, 22 fail; every failure is a finding below (or part of one). Run with
`PYTHONPATH=scripts/gate .venv/bin/python manage.py test <label> --settings=fastsettings --exclude-tag migration`.
`audit_invariants`: no findings. `makemigrations --check`: no changes. Both at ad4a2d0.

10 findings (8 with failing probes, 2 read from code and not probed). 5 block the gate: R1 to R5.

## Findings

### R1. Landed freight on goods written off is given to, and taken back from, cost of sales, not the write-off's account
Kind: books (the integrator's ruling). **Blocks.**
Probes: `EveryFateTests.test_forward_the_written_off_share_posts_where_the_write_off_posted`,
`.test_reverse_in_three_notes_each_step_is_its_share`, `.test_landing_first_then_fates_then_refund`, the same three in
`EveryFateFifoTests` (FIFO behaves identically), and `RunReturnsAndUnitsTests.test_an_order_in_cases_stocked_in_eaches`.
Code: apps/purchasing/models.py:7930 `_to_the_runs` (a "gone" row that is not a run issue or a vendor return falls to
the caller), 7866 `land_on_goods` and the `placed` rows in `Bill._build_journal_entry` (about 3856 to 3868), 4151
`_give_back_landing` (`cogs_account_for(item)` for the rest), 7978 `take_back_landed`, 8020 `_post_landed_cost`.
Scenario (10 @ 5 into the bay, 6 put away, 2 issued to a run, 2 written off to a scrap account, freight 80 = 8 a unit):
forward, scrap holds 10 and cost of sales 16; the ruling wants scrap 26, cost of sales 0. A whole refund leaves scrap 26 and
cost of sales -16 (wanted: scrap 10, cost of sales 0). Landing first, then the fates, then the 80 refunded: the same.
A write-off's own account is on `StockAdjustmentLine.movement.adjustment.reason.account`; the "gone" row is that movement.
Cost of sales is right only for goods sold (delivery movements).

### R2. A bill's freight is spread over every receipt of the order line, not the receipts that bill paid for
Kind: books (new with the "follow the goods" rule; at base all of it went to the one destination, so the spread was
harmless). **Blocks.**
Probe: `SecondBillTests.test_fifo_a_second_bills_freight_lands_on_the_goods_that_bill_paid_for` (fails: 40.00 != 55.00).
Code: apps/purchasing/models.py:4276 `_landed_on_receipt_lines` (and the older 4318 `_split_across_warehouses`): all posted
receipt lines of the order line, `share * quantity_received / total`.
Scenario: 20 @ 5. Receipt 1 (10) written off; bill 1 pays for it. Receipt 2 (10) in stock, 5 of it written off; bill 2 pays
for those 10 and carries freight 60. Truth: the 60 is on receipt 2's 10 units, 5 on the shelf: shelf 25 + 30 = 55, 30 to the
5 gone. Got: 30 of the 60 is put on receipt 1's goods (long gone, paid by bill 1) and goes to cost of sales; the shelf is
25 + 15 = 40. Under the average cost the same two-receipt case lands right only by coincidence (the shelf units are
fungible: `test_average_...` passes). A debit note follows the same landings, so the reverse repeats the error.
Fix direction: take only the receipt lines inside the range the bill's goods line covers (the O94 range logic,
`_bill_lines_that_paid_for`, already knows which receipts a bill line paid for).

### R3. The ECR and ESI files read a leaver's PF/ESI wages as earning + what was taken back
Kind: books (statutory file, caused by the O28 split into two lines). **Blocks.**
Probe: `LeaverProbes.test_pf_wages_of_a_final_slip_are_the_earning_not_earning_plus_what_was_taken_back` (fails: 932 != 675).
Code: apps/hr/statutory_files.py:58 `wage_base`: `sum(other.amount for other in slip.lines.all() if other.component_id in
wanted)` has no kind filter. Since ad4a2d0 the final slip has an EARNING and a DEDUCTION line on the same piece-work
component, both positive amounts.
Scenario: PF 12% of piece work. 1,500 m at 0.45 = 675.00 earned on the 21st; 400.00 to take back. PF = 12% of 675 = 81.00
(payroll sums `computed`, earning only: correct). Cap: half of 675 = 337.50, less PF 81.00 = 256.50 taken back, 143.50
owed. The ECR files EPF wages 675 + 256.50 = 931.50 -> 932. The decision says PF/ESI wages are not reduced by the recovery;
the file now says they are raised by it. `ecr_rows` and `esi_rows` both call `wage_base`. One-line fix: count EARNING lines.

### R4. A run's freight stays on the run when the issue it came with is voided
Kind: books, reverse path (CLAUDE.md mistake 2). The author's own defect 3, unfixed. **Blocks.**
Probe: `RunReturnsAndUnitsTests.test_an_issue_voided_after_freight_reached_its_run_takes_the_freight_back_with_the_material`
(got inventory 82 and WIP 48; wanted 130 and 0).
Code: apps/manufacturing/orders.py:2469/2479 (issue void), 1284 `add_cost`, 2247 `AddedCost` (never reversed by anything but a
release or a debit note).
Scenario: 10 @ 5, 6 issued (WIP 30), freight 80 lands (shelf 4 x 13 = 52, WIP 78). The issue is voided: the 6 units come back
at 30; the 48 of freight is left on a run with no material, and the 6 units on the shelf carry none of it. Returning goods
from the run to the store (a return, not a void) is right: `test_goods_returned_from_the_run_to_the_store_...` passes
(WIP 52, inventory 78).

### R5. O89 is not closed for a foreign-currency bill ahead of its goods
Kind: books. The author's defect 2, unfixed. **Blocks closing O89's row** (file the remainder under a new id if the
integrator wants the row off).
Probe: `GrniForeignAheadTests.test_a_euro_bill_ahead_of_its_goods_clears_grni_at_the_rate_the_goods_came_in_at`
(fails: GRNI 150).
Code: apps/purchasing/models.py:5114 `accrual` / 6687 `_freeze_accrual`.
Scenario: EUR 10 @ 5 billed as ordered on 3 Jan at 95 (Dr GRNI 4,750); received on 5 Jan at 92 (Cr GRNI 4,600). GRNI +150
for good; wanted 0 with the 150 in exchange. An import is the plant's common case.

### R6. GRNI still keeps a paisa: a bill ahead in parts, and a return between
Kind: books, minor (the shape of O93, not covered by its fix). Does not block; fix with R5.
Probes: `GrniPaisaTests.test_billed_ahead_in_three_parts_clears_to_the_paisa` (GRNI 0.01),
`.test_a_return_between_clears_to_the_paisa` (GRNI 0.01); randomised: `GrniBilledAhead` seeds 10, 22 and `GrniWithReturns`
seeds 7, 13, 22 (5 of 120 random orders; `GrniInParts` and `GrniPriceChanges` 60 of 60 clear).
Code: apps/purchasing/models.py:5114 `accrual` (the O93 total-first only runs inside a receipt layer; a bill ahead has no
layer, so each bill rounds alone) and the GRNI debit of a return (`create_return`, 7418).
Scenario 1 (the O93 numbers, billed ahead): 3 @ 10.10 less 2.5% = 9.8475. Three bills of 1 ahead debit 9.85 each = 29.55;
the receipt credits round(29.5425) = 29.54. GRNI +0.01.
Scenario 2: 7 @ 9.8475: receipt credits round(68.9325) = 68.93. 1.4 sent back unbilled debits round(13.7865) = 13.79; the
5.6 left billed 2.8 + 1.4 + 1.4 clear round(55.146) = 55.15 total first. 13.79 + 55.15 = 68.94. GRNI +0.01. Total first
would take the return as 68.93 - 55.15 = 13.78.

### R7. The statutory files still read two settings live that the slip does not keep
Kind: rule (the audit shape of O6; the ids as written named the ceiling only). Does not block.
Probes: `FiledMonthKeptTests.test_pf_base_list_edited_after_the_run_does_not_restate_the_ecr` (12000 != 9000),
`.test_esi_wages_base_list_edited_after_the_run` (19800 != 22300),
`.test_a_component_re_marked_after_the_run_does_not_restate_which_line_is_pf` (June's ECR goes from A, B to nothing).
Code: apps/hr/statutory_files.py:50 `_lines` (`line.component.statutory`) and 58 to 66 (`component.base_components`).
Scenario: C basic 9,000, HRA 3,000, PF taken of basic + special: PF wages 9,000. After June posts, HRA is added to what PF is
taken of: June's ECR says 12,000. The ceiling is now kept per line; the list it caps and the marker are not.

### R8. The deduction cap can exceed half the wages by a paisa
Kind: minor. Does not block.
Probe: `LeaverProbes.test_the_cap_is_never_above_half_of_an_odd_paisa` (333.34 taken against wages of 333.33).
Code: apps/hr/payroll.py:1256 `_within_the_deduction_cap` (`round_money(earned * DEDUCTION_CAP)` rounds 166.665 up).
Note for the accountant, not a defect: other deductions, PF included, count against the cap (`others`), so in R3's scenario
the take-back is 256.50, not 337.50, and 143.50 is owed.

### R9. Posted slips from before migration 0022 are not frozen (not probed, by construction)
Kind: rule, minor. `kept_paid_days`/`kept_ncp_days` are null on old slips and `kept_days()` reads the pattern live for them
(apps/hr/payroll.py `kept_days`; migrations/0022 has no backfill). A June already posted still restates when the pattern is
edited; only months run after the deploy are protected. A backfill at migration time would stop further drift.

### R10. O41 stops at the count; later conversions of `quantity_produced()` use today's factor (not probed)
Kind: books, minor, cross-chain run units only (a run counted in pieces of an item stocked in kg; needs a unit provider that
moves). apps/manufacturing/orders.py:804 `maximum_output`, 814 `check_output`, 885 to 888 `outside_cost_earned`, 963 to 988
convert `quantity_produced()` (already per-day, in the run's unit) with `to_stock_quantity(..., self.uom)` at today's date.
Not probed: no existing fixture runs an order in a unit of another chain.

## Filing
The author's defects 2, 3, 4, 5, 7, 8, 9 (reports/fix_cost.md) are not in docs/RISKS.md at ad4a2d0. R1 to R10 are new to it.
Under the CLAUDE.md rule each needs an id, or the fix, before the gate closes. Defect 4 (inspection clearance not followed by
`follow_value`) and 5 (O94 ranges vs a return between bills) I did not probe.

## Rewritten tests: what they encoded
- `test_it_debits_the_bill_that_paid_for_the_receipt_returned` (was `..._oldest_first`): 10 ordered, receipt 1 of 4 billed 4 @ 5 =
  20.00, receipt 2 of 6 billed 6 @ 5 = 30.00, the six returned. Old: first due 0, second due 20 (four off a bill that never paid
  for the six). New: second due 0, first due 20. The old one encoded the defect; the new figures are right.
- `..._it_is_owed_back` (was refused by name): no wages, cap 0, 400.00 owed, net 0.00. Right under item 20.
- `..._stays_wages_beside_what_is_taken_back` (was netted to 275.00): 1,500 m x 0.45 = 675.00; half = 337.50 deducted, 62.50 owed,
  net 337.50. Reconstructed by hand (calc `pay.py`); right under item 20. It also pins PF/ESI base = 675, but the files did not
  follow (R3), and it has no PF component, so nothing caught it.
- `core.tests_audit_posted.test_an_exemption_outlives_nothing`: used `StockCount` as a stand-in on `DELETABLE_REPORTED`; the list
  is empty now, so the test patches one in. A stand-in change, not a weakened assertion.

## What held under pressure (one line each)
- ITC-04 (O187): a receipt of 700 kg booked 1 Jun, a second of 150 kg on 1 Aug, the first voided 14 Oct: H1 unchanged, H2 shows -600 and -100 on the challans the 700 was laid on, nothing else; booked and voided in H2: on neither; a new H2 receipt after the void nets to what is back (3 probes).
- Landed freight forward over bay 4, stock room 2, run 2, scrap 2 of 10 units (8 a unit): inventory 78, WIP 26, shelf = ledger, under the average and FIFO.
- Reverse in one note and in three (25, 25, 30 of 80): shelf, WIP and inventory each give back their share and the shelf equals the ledger after each note (cost of sales aside, R1).
- Freight landed first, then put-away, issue and write-off, then refunded: inventory 30, WIP 10, shelf = ledger.
- A partial return of the goods between landing and refund: the return's variance (32) comes back off PPV, leaving inventory 30, PPV 0.
- Freight 33.33 over four fates then refunded whole: every account back to before the bill (total first, no paisa).
- Goods returned from a run to the store before the freight: shelf 6 bears 48, run 32 (WIP 52, inventory 78).
- A closed run: freight lands as variance, WIP stays 0, a later refund comes back out of variance; an open run's freight is cleared to variance by the close (2 probes).
- Order in cases, stock in eaches, half a case put away and written off: inventory 162, shelf = ledger (R1 aside).
- A bill with freight posted before any receipt balances and keeps the 130 (payable 130, GRNI/inventory/freight hold it).
- A standard-cost item: landing and whole refund leave shelf = ledger.
- GRNI over 120 randomised orders: receipts and bills in parts, price changes: 60 of 60 clear to the paisa (R6 and R5 are the exceptions above).
- O94: receipts of 10 and 10 billed 5.00 and 5.40, 4 + 4 + 3 sent back: debited 57.80, PPV 1.20, GRNI 0, shelf = ledger.
- O101: three machines @ 4,000 billed at 4,100: plant 12,300, inventory 0, PPV 0, GRNI 0; un-capitalised: plant 0, inventory and shelf 12,000, PPV 300; a machine still in the bay comes from the bay; a EUR machine (90 / 95): plant 95,000, inventory 0, GRNI 0.
- O206/O38: partial and uneven fractional returns (0.333 and 0.1237 a unit) stay within half a paisa of the shelf at every step, not only at the end.
- O3: a write-down at the shelf's 4-place rate books 3,333.33 on shelf and ledger; a per-each cost on an order in cases is accepted; a different cost is refused in words.
- O205: as the Stores Manager, deleting or editing a line of a posted adjustment, and deleting a line of a posted count, over the API: refused, rows stand.
- O4: the importer calls `clean()`, which refuses 3.33333333 against the four-place field in words (already so at base; the new test only pins it).
- O28/O31: an advance of 150 plus the take-back stay inside half the wages (150 taken, 250 owed, net 300); voiding the final run reverses the receivable and a re-run takes the same again; voiding the October run of a late leaver reverses it and the leaver is owed again in a re-run.
- A leaver's no-wage slip (all 400 owed) does not break `ecr_rows` or `esi_rows`.
- O5/O6 as written: a PF ceiling raised after the run, and a four-day week after the run, do not restate June (the author's tests; my R7 is the neighbouring settings).

## Not run
PostgreSQL and race lanes, Asia/Kolkata, browser tests, the full suite. O7, O8, O38 (beyond the fractional steps), O47 and O41's
cross-chain case rest on the author's tests plus code reading. Debit-note posting takes `lock_rows` on the original lines (models.py:4075)
before `_give_back_landing` reads what earlier notes gave back, so I did not expect a race there; unproved without PostgreSQL.
