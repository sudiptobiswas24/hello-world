# g22 month: a plant month replayed end to end

Worktree: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/month (base b11015b, tip ea15e60). Nothing pushed. Tests only; no product code touched.

Test: `apps/e2e/tests_plant_month.py` (PlantMonthTests, 26 tests, 2 expectedFailure).
Script: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/calc_month/month.py. Every expected figure in the test comes from it. Where the code and the script disagreed, I worked out which side was wrong by hand before changing either (twice it was my script: see "Script corrections").

## Commits

| sha | what |
|---|---|
| b25c659 | setup and polymer bought (IGST and CGST+SGST, short receipt, inspection reject/return, landed freight, 194C TDS, NEFT, MSME days) |
| 1a8fea2 | tape, selling (advance, e-invoice, e-way bill, return and credit, cheque bounce, USD export under LUT), scrap with TCS and challan, bank statement, end-of-month footings; the two findings as expectedFailure |
| 32f45ce | loom capitalised and depreciated, GSTR-1, GSTR-3B, TDS and TCS returns, the month closed |
| f1c9f58 | AR and AP aging foot to the control accounts |
| ea15e60 | the docstring lists every model call and what the month leaves out |

No commit closes a register id: none of this round's ids were mine.

## Runs (tip f1c9f58 / ea15e60, the test file only)

- SQLite, fastsettings: OK, 26 tests, expected failures=2.
- PostgreSQL `postgres://erp:erp@localhost:5432/month`, fastsettings, test databases dropped first: OK, 26, expected failures=2.
- `DJANGO_TIME_ZONE=Asia/Kolkata`: OK, 26, expected failures=2.
- Default settings with migrations (SQLite), at 32f45ce: OK, 25 tests (before the aging test), expected failures=2.
- `audit_invariants`: no findings. `makemigrations --check`: no changes. ruff: clean.

## How the people sign in (L12)

Roles with a guarded right (AP Manager, AR Manager and Controller post payments) need the second factor through a session. The suite's own API tests (apps/core/tests_second_factor.py ALostPhoneTests.api, apps/purchasing/tests_tds.py, apps/sales/tests_tcs.py) use `APIClient.force_authenticate`. DRF honours it without a session, so the Gate middleware never sees those requests. The month does the same: one login per role from `setup_roles`, and the Sales Rep carries every customer.

## The month as run

Every document goes through the API as the role that does the job. Each step runs on its story day: `django.utils.timezone.now` is patched to 11:00 Kolkata on that day, so a document the server dates (a return, a void) is dated in October whatever day the suite runs.

1. Polymer. The Controller confirms the 194C rate (the AP Manager is refused). 10,000 kg of PP is ordered from Gujarat (IGST). It arrives as 6,000 and then 3,800, short by 200, and the Quality Inspector passes both receipts. 500 kg of masterbatch comes from a West Bengal micro enterprise (CGST+SGST): 50 kg fail (a rejection with no reason is refused) and go back, and 450 are billed. The transporter's 39,200.00 freight has 784.00 TDS deducted and is landed 24,000 / 15,200 by value, so PP stands at 104.00 a kilo. The masterbatch is paid by NEFT on 20 Oct; MSME due 5 Nov, paid on time.
2. Tape. The Production Supervisor makes 5,000 kg from 4,850 PP and 150 MB: 5,49,400.00, 109.88 a kilo, with nothing left in WIP.
4. Selling.
   - Hooghly Cement: 4,000 kg at 150.00 plus GST, 7,08,000.00. A 1,00,000.00 advance is invoiced, paid and drawn down. The invoice gets an IRN and an e-way bill.
   - 200 kg come back and are credited, 35,400.00.
   - A 3,00,000.00 cheque is deposited and matched on the statement, then bounces on 28 Oct. It is voided (the Sales Rep is refused) and the return line matched. 5,72,600.00 is left owed.
   - An export of 1,000 kg at USD 2.00 under a recorded LUT is booked at 84.00 and paid at 84.50: 1,000.00 exchange gain.
5. Scrap.
   - The Stores Manager counts in 500 kg of sweepings at 30.00.
   - They sell at 40.00 plus GST, 23,600.00, under the 2% TCS rate (394(1)). Posting is refused until the Controller confirms the rate.
   - 472.00 TCS is collected, the buyer pays 24,072.00, and the TCS is paid by challan on 31 Oct.
7. Close. A loom is billed 1 Oct at 6,00,000.00 plus GST, capitalised from its bill line, put into service and depreciated 5,000.00. GSTR-1, GSTR-3B and the TDS and TCS quarter returns are read. The Controller closes October (the Bookkeeper is refused).

End assertions, each its own test:
- GRNI 0, deposits 0, TDS payable 784.00, TCS payable 0.
- GST output CGST and SGST 53,100.00 each, IGST 0. Input IGST 1,76,400.00, CGST and SGST 66,150.00 each.
- FX gain 1,000.00, loss 0. Suspense 0. WIP 0.
- Shelf item by item, footing to the inventory account at 6,26,776.00.
- AR by party, footing to 5,72,600.00. AP by party: V-PP 11,56,400, V-TRN 38,416, V-LM 7,08,000.
- Aging both sides footing to the control accounts.
- The asset register footing to cost less depreciation.
- Bank 1,33,300.00, the statement reconciling with no difference.
- The trial balance balances. Profit 2,26,576.00 equals what the balance sheet gained (no capital came in).

## Model calls (no screen exists)

- Master data the month opens with: accounts beyond `setup_indian_books`, the company, currencies and rates, items, warehouses, parties and tax profiles, the BOM, ManufacturingSettings, an adjustment reason, an asset category.
- `BooksOpening` (O251, no screen).
- `GstSettings.einvoicing_from`.
- `BillLine.allocate_landed_cost` for a separate transporter's bill (admin only).
- The sweepings' `StockAdjustmentLine.unit_cost` (finding 2 below).

`setup_indian_books` was run as the command an accountant runs.

## Register rows (every failure)

1. **apps/sales/models.py:4906, with :2620. Two at once: locks taken against the written order (audit shape 9).**
   - **Scenario.** Hooghly's order of 4,000 kg at 150.00. A down payment of 1,00,000.00 is posted and paid. All 4,000 kg are delivered, and the invoice of 7,08,000.00 draws the advance down. The Warehouse Staff then post `/api/sales/deliveries/{id}/customer_return/` with `{"quantities": {line: "200"}}`.
   - **Result.** 500, `LockOrderViolation: sales.invoice 1 locked after inventory.stockposition 5`. Invoice 1 is the down-payment invoice.
   - **Why.** `create_return` holds, before the stock, the invoices that billed the delivery's order lines. It does not hold the deposits each of those invoices draws down, and `Invoice.locked_before_it` (:2620) names those deposits as held first. So the credit note's `held_first` takes the deposit after the returned goods' stock position.
   - **How I know the product side is wrong.** apps/core/lock_order.py says "a return the invoices or bills it corrects before the goods come back", and `locked_before_it` puts the drawn deposits before the invoice. The suite's full-return API test (apps.e2e.tests_api_lifecycle.TakingGoodsBackTests) passes only because its invoice drew no advance.
   - **In production.** The sentinel is off, so there is no 500, but the locks are taken in the order the sentinel exists to forbid.
   - **Test.** apps/e2e/tests_plant_month.py:697 `test_a_return_credits_an_invoice_that_drew_an_advance` (expectedFailure). The month goes round it: goods back with `credit_invoices: false`, then the invoice's `credit_note` with 200. Same figures.
   - **Not done.** I wrote no `race()` test, so a deadlock on PostgreSQL is not demonstrated, only the sentinel's refusal.

2. **apps/inventory/serializers.py:208 against apps/inventory/adjustments.py:500-503. A refusal whose remedy no screen offers (API gap against the model; inert path).**
   - **Scenario.** 500 kg of floor sweepings, never stocked, are counted in at 30.00 by the Stores Manager over `/api/inventory/stock-adjustment-lines/` with `"unit_cost": "30.00"`.
   - **Result.** The line comes back with `unit_cost: null`, because `read_only_fields = ["unit_cost"]` (since fa984a0). Posting answers 400: "There is no SWEEP - Floor sweepings at STORE - Stores to take a cost from. Give the line an explicit unit cost."
   - **Why I say it is a defect.** No screen can do what the refusal asks, so a first count-in of any item (sweepings, regrind, a found pallet) cannot be booked at all.
   - **Which side is wrong.** The model handles an explicit cost and asks for it. Whether the field was frozen on purpose, to stop typed costs on gains, is a question for the owner. If it was, the refusal must name a path that exists.
   - **Test.** apps/e2e/tests_plant_month.py:713 `test_sweepings_counted_in_keep_the_cost_they_were_given` (expectedFailure). The month sets the cost by a model call.

3. **apps/accounting/management/commands/setup_indian_books.py:16. A comment is not a constraint (low).** The documented example `--gstin 19AAACB1234C1Z5` fails its own check character, so running the command exactly as its docstring shows raises "fails its check character" and seeds nothing. No test (documentation).

## Questions, not defects

- **GSTR-1 B2B "value" includes TCS.** The scrap invoice reads 24,072.00, which includes the 472.00 TCS; taxable is 20,000.00. Under CBIC circular 76/50/2018 as corrected, income-tax TCS is not part of GST taxable value, and the code does that right. Whether the return's invoice value should carry the TCS is for the accountant. Asserted as the code gives it.
- **"Partial" with nothing paid.** The freight bill reads `settlement_status: "partial"` with nothing paid, only TDS deducted. It reads oddly but is consistent with `amount_due` 38,416.00.
- **Unit code warning.** The GSTR-1 "unit with no GST unit code" warning comes from my fixture: kg has no UQC.

## Script corrections (mistake 7: my arithmetic, not the code)

- Profit was first typed as 1,58,576. The script's own balance-sheet check gave 2,31,576 (2,26,576 after the loom's 5,000), and the code agreed.
- I had the MSME due date as 3 Nov (acceptance plus 30). Net 30 runs from the bill (6 Oct), so it is 5 Nov, inside the Act's 45 days from acceptance (18 Nov). The code was right.

## Not done (budget of about 150 calls reached)

- **Section 2.** The station flow and the scale bridge's endpoint (with its idempotency key), piece work for weavers, scrap and regrind at stations, the hold the Quality Manager decides, and the recall of a tape lot after fabric. Tape was made through the office API (work order, issue, production entry, close). The stations need the RollTestCase / `build_tape_line` fixtures (loom stations, core types, specifications, operator PINs), which conflict with this month's own company and accounts.
- **Section 3.** The job-work challan out and back, and the re-batch.
- **Section 6, payroll, entirely.** That is monthly, daily-wage and piece-rate pay; overtime; the advance recovered; PT, LWF, and EPF with a member past 58; the final settlement; the bank file and a returned transfer. With it went the assertions on net pay payable, the advance ledger, and the ECR, PT and LWF registers.
- **Section 1.** Price circulars (vendor prices) were not used. 194Q does not apply below 50 lakh in the year, so TDS is shown on 194C only.
- **Next.** The `Month` builder takes new steps before `close()`. Payroll is next, built on apps/hr/tests_payroll.py PayrollTestCase's components and its statutory settings.

---

# Part 2: payroll and the floor (continued from ea15e60)

Same worktree. Tip c5799ef. Nothing pushed. Tests only; no product code touched. Findings 1 and 2 are left `expectedFailure` here, as asked; the integrator lifts them in the integration tree.

The container restarted at about 01:27 UTC with my payroll edits uncommitted. They were intact and I resumed from them; nothing was lost.

## Commits

| sha | what |
|---|---|
| 59bf6a9 | October's payroll on the month's books (section 6), through the API |
| 2028e07 | the floor through the stations, on the station fixtures' books (sections 2 and 3) |
| c5799ef | a re-batch on the floor; a job-work challan out and back |

## Runs (tip c5799ef, the file: 46 tests, 3 expectedFailure)

- SQLite, fastsettings: OK, 46, expected failures=3.
- `DJANGO_TIME_ZONE=Asia/Kolkata`: OK, 46, expected failures=3.
- PostgreSQL db `month` (test databases dropped first): OK, 46, expected failures=3.
- Default settings with migrations (SQLite): OK, 46, expected failures=3.
- `audit_invariants`: no findings. `makemigrations --check`: no changes. ruff: clean.

## Payroll (PlantMonthTests, same Month, same books)

Figures are in calc_month/month.py section 6, and the script's balance-sheet check holds with them in. Who does what:

- **Payroll Officer.** Keeps the components and slabs:
  - BASIC and HRA;
  - OT per overtime hour;
  - PF and PF-ER: 12% of BASIC to 15,000, rupee;
  - ESI and ESI-ER: 0.75% / 3.25% of all earnings, rupee up, for those whose BASIC + HRA is within 21,000;
  - PT on West Bengal's bands: to 10,000 nil, to 15,000 110, to 25,000 130, to 40,000 150, above that 200;
  - LWF and LWF-ER: 3 / 15, in June and December only;
  - ADV, recovering advances into 1400.

  Also keeps the compensation and the bank and return details, and opens and calculates the run.
- **HR Admin.** Hires four people and terminates L1 on 21 Oct.
- **Production Supervisor.** Marks D1's attendance register.
- **Controller.** Gives the two advances, posts the run (the Payroll Officer is refused, R9), pays the bank file and records the returned transfer.

The people, and what each slip holds (all as the script gives):

| slip | who | gross | deductions | net | employer |
|---|---|---|---|---|---|
| M1 | 18,000 + 7,000; not covered by ESI; PT 130 | 25,000.00 | 1,930.00 | 23,070.00 | 1,800.00 |
| M2 | born May 1967, past 58 in October; 14,000 + 4,000 | 18,000.00 | 1,945.00 | 16,055.00 | 2,265.00 |
| L1 | leaves 21 Oct, 18 of 27 working days | 10,000.00 | 5,000.00 | 5,000.00 | 1,285.00 |
| D1 | day-rated: 24 of 27 days at 13,500, 3 absent, 8 OT hours at 125 | 13,000.00 | 3,648.00 | 9,352.00 | 1,863.00 |

- **M2.** The ECR gives EPS wages 0, EPS 0, and the whole 1,680 to EPF.
- **L1.** PF 960 and ESI 75 are taken. The final slip takes 3,965.00 of the 6,000.00 advance, the room the 50% cap leaves. 2,035.00 stays owed by the former employee.
- **D1.** The 2,000 instalment of a 4,000 advance is taken.

What is asserted:
- Net pay payable is 9,352.00: the bank file paid 53,477.00, D1's transfer came back, and D1's slip alone is unpaid.
- The advance ledger reads L1 2,035.00 and D1 2,000.00. It foots to 1400 in total and by person.
- The ECR has four lines; the ESI file three (with L1's last day); the PT register three rows by band.
- The LWF register is empty: October asks none in West Bengal.
- PF payable is 11,760, ESI 1,641, PT 370.
- Wages are 66,000.00 and employer contributions 7,213.00.
- The profit moves by 73,213.00 to 1,53,363.00 and the bank to 79,175.00, the statement still matched.

There is no ESI register endpoint beyond the run's ESI file, so the file is what is asserted. The per-month ECR and ESI come from the run's `ecr/` and `esi/` actions.

Model calls added: the company's net pay account, and the payroll accounts (master data). Everything else in payroll went through the API.

Not covered: piece-rate pay on the month's books. Piece work is counted from rolls the loom stations record, so it is paid in the floor's run instead (below). Nor a repayment by the leaver of the 2,035.00 still owed, which would need a path I did not look for.

## The floor (PlantFloorTests, own books: StationApiTestCase + build_tape_line, 1 June 2026)

The stations' fixtures keep their own company, accounts and stock, so the floor runs on them. The fixture's opening stock has no journal entries; the footing is of movements: what the floor did moved the shelf and the inventory account alike.

Steps, through the stations with PINs and the screens as the people who do them:
- The bridge posts 106.8 kg with key `SC-1:1780292520:7:gross:106.8`, then again: 201, then 200 with the same id.
- The weaver signs in at LX-1 and loads all 100 kg of tape batch T-1 on L-30's warp. He weighs a roll from the scale, naming himself by his PIN: 104.4 kg net, 1,000 m by weight against 1,006 declared, 0.60% and inside 2%.
- The Production Supervisor issues 110 kg PP and 10 kg filler to the tape run.
- Two doffs come off TX-1, 56.0 kg net each. One is in denier (mean 1,001.67). The other is at 1,085, booked on the supervisor's PIN and held. The Quality Manager concedes it, and the decision names her party.
- 5 kg of waste comes back as regrind at its standard, 300.00.
- The good batch is re-batched into TP-A (30) and TP-B (26). The tape's shelf value and the ledger do not move.
- T-1 is rejected after weaving (posted by the Quality Manager). The roll is held, saying it is made from T-1.
- The weaver's 1,006 m at 0.50 is paid in June's run: 503.00. Net pay payable is 503.00.
- Both runs are closed and WIP is 0.

Model calls (no screen):
- the fixture's books and stock;
- loom L-30, of the plant's own (the fixture's looms are a contractor's);
- LX-1 bridged to SC-1;
- T-1 made lot-tracked and stocked;
- the bridge's login;
- the company's net pay account;
- the specification-made inspection plans set advisory, and T-1's set mandatory for its rejection. A plan computed from a specification refuses edits over the API: "this plan is rebuilt from it". The suite's own tests set it by model too.

## Job work (JobWorkOutAndBackTests, own books: JobWorkTestCase, 1 June 2026)

- **Out.** The Purchasing Clerk orders 1,000 kg of coating at 2.00 against the run's outside step, as a non-stocked service item. The Stores Manager sends 1,000 kg on challan JWC-2026-00001 (valued 95,000.00, 5%, the job worker's GSTIN frozen on it).
- **Back.** The Warehouse Staff receive the coated 1,000 kg against the order. 2,000.00 goes into WIP, against GRNI. The AP Manager's bill of 2,000.00 clears GRNI.
- **Asserted.** The challan's allocation reads sent 1,000, back 1,000, outstanding 0. The run takes its material, books its output and closes with WIP at 0, and the shelf moves with the inventory account.

Model calls: the fixture's books and stock; the laminator made a vendor; the coating service item; GRNI and price variance named on the company.

## Register rows (new)

4. **apps/manufacturing/orders.py:3168 (ProductionEntry.post). The figure booked is not the figure on the shelf (audit shapes 10 and 4).**
   - **Scenario.** Two tape doffs at TX-1, 56 kg net each, are received at the run's planned 95.83505100 a kilo: 5,366.762856 each.
   - **Why.** `value = self.unit_cost * made` goes unrounded into `_post_entry`, which books each 5,366.76. The `StockMovement` keeps unit_cost 95.83505100, so the shelf replays the two as 10,733.525712, reading 10,733.53. The ledger holds 10,733.52.
   - **Result.** Over the floor's day the shelf moved 8,917.20 and the inventory account 8,917.19. It drifts by up to half a paisa on every receipt that does not come out even.
   - **How I know the product side is wrong.** The project rule is that the shelf foots to the inventory account (CLAUDE.md "Derived, not stored"; audit shape 4). Here the movement's value and the entry's value differ for the same receipt. The movement printout shows MV 11 and MV 12 at 5,366.762856 and JL 4 and JL 5 at 5,366.76.
   - **What I ruled out first.** My fixture's opening stock had been stamped by the wall clock (19 Oct) while the floor ran on 1 June, so the replay ran an issue before its receipt. I fixed that in the test (the fixture is started on the floor's day); the paisa remained.
   - **Test.** apps/e2e/tests_plant_month.py:1439 `test_the_shelf_moves_with_the_inventory_account` (expectedFailure).

## Test mistakes caught, not findings (mistake 7)

- The fixture clock: above. The fix is in the test.
- The salary file was read before the return was recorded. Re-read after.
- An inspection's decider is a party (the person), not a login.
- `law_section` and `section_code` (part 1) and the TCS rate read: my request was wrong; the code was consistent.

## Still not done

- Piece-rate pay on the month's own books (it is in the floor's run instead).
- A leaver's repayment of the advance left on the final slip.
- The station `waste/` record for a contractor's loom, which carries no value. The line's regrind is booked and valued instead.
- `race()` tests for finding 1 (lock order). Only the lock-order check's refusal under the test settings is shown.
