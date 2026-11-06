# fix_yearend (g24): closing-rate restatement and tax depreciation by block

Worktree /home/user/work/fix_yearend, base d76ef6f. Calc script /home/user/work/calc_fix_yearend/calc.py
(plain Python; every expected figure in the tests is printed and asserted there). Not pushed.

## Commits

Three commits on d76ef6f, tip 7eb8ac1 (no register ids: the brief builds gaps A13 and A15's tax half,
not RISKS.md rows; docs/RISKS.md not edited):
- b358dce accounting: restate foreign balances at the closing rate (gap A13)
- 3063b34 assets: Income-tax depreciation by block of assets, beside the books
- 7eb8ac1 web: screens for the restatement and the tax schedule; their races and payable side

## What is built

### 1. Restatement of foreign balances (apps/accounting/restatement.py, restatement_api.py)
- `Restatement.make(as_of, by, rates=None)`, Controller only (`accounting.post_restatement`). It restates
  each foreign item as it stood at the close of `as_of`:
  - invoices, credit notes, bills, debit notes with something due (each side registers its documents
    with `register_restatement_source`, so accounting imports neither; `Invoice/Bill.restatement_item`);
  - payments with money applied to nothing (on their control account; a receipt on account is a credit);
  - bank accounts kept in a foreign currency (EEFC), at their balance in that currency.
- As at the day: payments applied or voided after `as_of` are replayed to it (the restatement can be
  made in April for 31 March). Any other settlement dated after the day (a note, a drawdown, a write-off,
  TDS, a discount) is refused in words rather than guessed at.
- One `RestatementLine` per item: open amount, rate, base carried, base restated, difference. The
  difference is the two bases, each rounded, subtracted. The base carried by a part-settled document is
  what it booked less the cumulative slice settled (`booked - round(booked * settled / total)`), which is
  what the next allocation reads.
- Rates are kept (`RestatementRate`: the rate and the rate-table row it came from, or "given").
- One entry dated `as_of`, one control line and one P&L line per item, and its reversal dated the first
  day after the period covering `as_of` (or the next day when no period covers it). The realised
  difference at settlement is unchanged: `settlement.booked_by` reads only the document's own entries.
- Void (`void(on_date)`): dated under `correction_date()` (not before `as_of`, not a day to come), and
  only before the restatement has reversed itself. It negates the entry on the void day and takes the
  reversal back on its own day. A second standing restatement for a day is refused (in code, and by a
  partial unique constraint).
- A restated day's figure is held: an allocation applied (or re-dated, or deleted) on or before a
  standing restatement's day, and a payment voided on or before it, are refused, naming the
  restatement (`refuse_moving_restated`, called from the shared `RealisedOnItsOwnDay` and from
  `Payment.void`, so both trading sides share one check). The restatement locks the payments and
  documents it reads (`lock_rows`) before reading them.
- API: `GET/POST /api/accounting/restatements/` (list, `restate/`, `preview/?as_of=`, `{id}/void/`).
  Screen: Accounts > Restate foreign balances (`frontend/src/modules/accounts/Restatements.tsx`).

### 2. Tax depreciation by block (apps/assets/tax.py, tax_api.py)
- `TaxBlock`, `TaxBlockRate` (a `DatedRate` from accounting.dated_rates: confirmed by the accountant,
  refused changes once filed at), `TaxBlockOpening` (WDV at the start of the first year the system
  computes), `TaxDepreciationYear` + `TaxBlockLine` (a filed year, kept as filed).
- `FixedAsset.tax_block` (copied from `AssetCategory.tax_block` as it goes into service, as the other
  STANDS_ON settings are) and `FixedAsset.disposal_proceeds` (kept as `dispose()` records it).
- `schedule(year_start)`: per block, opening (filed closing of the year before, or the opening row, or
  computed forward year by year), additions at full rate and at half rate (fewer than 180 days used),
  sales at proceeds, depreciation, closing, short-term capital gain or loss when the block goes negative
  or every asset in it is sold, book depreciation of the same assets for the year, and their book value
  at the year end (tax WDV less book value is the timing difference for deferred tax).
- `TaxDepreciationYear.file(year)`: refused before the year ends, with an unconfirmed rate, while an
  earlier year with figures is unfiled (a block opened that year excepted), or when already filed.
  `withdraw()` for a revised return, refused once the next year is filed. While a year is filed, an
  asset put into service, disposed of, reinstated, or moved to another block in it is refused.
- `manage.py load_tax_depreciation_rates`: nine blocks, a row from 1 April 2017 (1961 Act) and one from
  1 April 2026 (2025 Act), all unconfirmed, idempotent.
- API: `/api/assets/tax-blocks/`, `tax-block-rates/` (+ `confirm/`), `tax-block-openings/`,
  `tax-years/` (`schedule/?year=`, `file/`, `{id}/withdraw/`). Screen: Accounts > Tax depreciation by
  block (`frontend/src/modules/accounts/TaxDepreciation.tsx`).

### 3. Roles
`setup_roles`: the Controller gets `post_restatement` and the restatement views, the tax blocks, rates
and openings, `view_taxdepreciationyear`, `file_taxdepreciationyear`, `confirm_taxblockrate`, and reads
assets. Nobody else gets either.

## The standards and the law (research)

Official sites (incometaxindia.gov.in, mca.gov.in, indiacode.nic.in, icai.org) could not be fetched from
this machine (the proxy refused them); what follows is from search results that quote or summarise
them. Every rate is loaded unconfirmed.

- Which standard: a company under the Companies (Indian Accounting Standards) Rules, 2015 (listed, or net
  worth of Rs 250 crore or more, rule 4) reports under Ind AS 21; otherwise under AS 11 (revised 2003), as
  notified in the Companies (Accounting Standards) Rules, 2021. An unlisted plant below the threshold
  reports under AS 11. ACCOUNTANT TO CONFIRM which. Both restate monetary items at the closing rate
  (AS 11 para 11(a), differences to P&L para 13; Ind AS 21 para 23(a), 28). Source:
  https://kb.icai.org/pdfs/69239asb55316-as11.pdf, https://indasaccess.icai.org/Volume-III/AS/asb.html?a=112
- Advances are not restated: an advance paid or received for goods or services is a non-monetary item
  (AS 11 para 11(b); Ind AS 21 para 16 and Appendix B, Foreign Currency Transactions and Advance
  Consideration, notified by the Companies (Ind AS) Amendment Rules, 2018). The brief listed advances;
  the standard says otherwise, so down payments and prepayments are left out (they stay at the rate they
  came in at; post_drawdown already clears them at that rate). Source:
  https://assets.kpmg.com/content/dam/kpmg/in/pdf/2017/04/IFRS-Notes-ICAI-Ind-AS-40-and-Appendix-B-to-Ind-AS-21.pdf
- Restate-and-reverse: the brief's decision. The year's balance sheet and P&L carry the restated
  figures; the next year carries the reversal plus the realised difference against the booked base, which
  together equal the movement after the year end. Total P&L over the two years is the same as restating
  without reversal (1,213.30 in the test). Documents and books agree throughout.
- Income-tax Act, 2025 (from tax year 2026-27): section 33 (depreciation on WDV of a block at the
  prescribed percentage; half for an asset put to use under 180 days in the year), section 74 (capital
  gains on depreciable assets, successor of 1961 s.50: short-term when proceeds exceed opening WDV plus
  additions, or the block ceases). Rates in the Income-tax Rules, 2026 (rule 25 / Appendix I in the draft
  reported), said to carry the 1961 percentages over. Sources (secondary):
  https://www.incometaxindia.gov.in/w/section-33-187 (not reachable here),
  https://www.taxtmi.com/tmi_notes?id=2117, https://www.aubsp.com/income-tax-act-2025-section-33/,
  https://www.taxsutra.com/lsi/act/section/146617, https://eztax.in/income-tax-act-2025/section-74,
  https://www.patronaccounting.com/blog/depreciation-rules-2026-rates-block-assets
- Income-tax Act, 1961 (to 31 March 2026): s.32(1)(ii) and its second proviso (180 days), s.43(6), s.2(11),
  s.50; Appendix I to the Income-tax Rules, 1962 (rule 5). Rates loaded: buildings residential 5%, other
  10%, temporary 40%; furniture 10%; plant and machinery 15%; motor cars 15%; computers and software 40%;
  P&M at 40% (energy saving, pollution control); intangibles 25%. Sources:
  https://tallysolutions.com/accounting/depreciation-rate-as-per-income-tax-act/,
  https://www.bankbazaar.com/tax/depreciation-rates-for-ay-2015-2016.html
- Deferred tax: AS 22 (or Ind AS 12) recognises deferred tax on the timing difference; the schedule shows
  the year's difference and tax WDV less book value per block. The deferred-tax entry itself is not built.

## For the accountant

1. AS 11 or Ind AS 21: which the plant reports under.
2. Confirm every tax block rate (Accounts > Tax depreciation / tax-block-rates confirm): none is confirmed,
   so no year files until they are. The 2025-Act section numbers (33, 74) and the 2026 rules' rates are
   from secondary sources.
3. Enter each block's opening WDV from the last return (TaxBlockOpening) and set each asset category's
   tax block; assets already in service take their category's block only if set before they went into
   service, otherwise set `tax_block` on the asset.
4. Rules decided in code, to confirm: an asset enters its block in the tax year it is put to use; days
   counted from the day put to use to 31 March, both included (179 half, 180 full); sales come off the
   full-rate part first; proceeds are what the disposal recorded, no transfer expenses; figures to the paisa.
5. Additional depreciation (s.32(1)(iia) / 2025 s.33) is not computed; whether the plant claims it
   depends on its tax regime (not under 115BAA).
6. Whether to post the unrealised difference to separate unrealised accounts: today the company's FX gain
   and loss accounts are used, and kept on the restatement.
7. The closing rate: the rate table's latest row on or before the day is used unless given; the row's date
   is shown and kept. RBI/FEDAI reference rate or the bank's is the accountant's choice.

## Tests

Every expected figure is worked in calc_fix_yearend/calc.py and asserted there before it went into a test.

| Case (brief) | Test | Figures |
|---|---|---|
| USD invoice 83.3367, restated 84.1, reversed, paid 84.55 | tests_restatement.AnOpenInvoiceTests.test_restated_reversed_and_paid_against_its_booked_base | 763.30 unrealised; realised 1,213.30 against 83,336.70; receivable 0 |
| restated after the April receipt was applied | ...test_restated_as_it_stood_though_paid_since | still 1,000 open at 31-03, 763.30 |
| paid before the period end | ...test_paid_before_the_year_end_leaves_nothing_to_restate | realised 563.30; refused "Nothing in a foreign currency was open" |
| part-paid | ...test_a_part_paid_invoice_restates_what_is_left | 400 paid realising 225.32; 600 carried 50,002.02, restated 50,460.00, 457.98; last 600 realises 727.98 |
| USD bank | tests_restatement.AForeignBankTests | EEFC 700 USD: 58,730.00 to 58,870.00, +140.00; receipt on account -200.00; payment on account +60.00 |
| USD bill (added) | tests_restatement.AnOpenBillTests | -763.30 loss on the payable |
| refusals | WhatARestatementRefusesTests | second for the day; allocation and payment void on the day until voided; void after reversal and before the day; closed period; down payment not restated |
| block: day 179 / 181, sale | tests_tax.TheScheduleTests | 2,02,500.00 depreciation, 12,47,500.00 closing; 179/180/181 days |
| sale emptying the block | ...test_a_block_emptied_by_its_sales_ceases_with_a_loss | ceased, -30,000 |
| sale above the block | ...test_a_sale_for_more_than_the_block_is_a_gain | +15,000, closing 0 |
| filing kept | tests_tax.FilingTests | unconfirmed rate refused; filed lines kept; dispose/reinstate/move refused in a filed year; rate change refused; next year opens at 12,47,500.00; withdraw then allowed |
| API as a role | tests_restatement.ThroughTheApiTests, tests_tax.ThroughTheApiTests | Controller restates, voids, reads, confirms, files; Bookkeeper 403 |
| browser | apps/web/tests_browser_yearend.py | Controller restates (83,336.70, 84,100.00, 763.30) and voids; confirms a rate from the screen, reads 2,10,000.00 and 12,90,000.00, files; Bookkeeper offered neither, API 403 |
| race (PostgreSQL, db fix_yearend) | tests_races.ARestatementAndAPaymentOfWhatItRestatesRaceTests | one of the two goes through |

Failing without the fix:
- refusal hook removed: test_settling_it_on_the_day_it_restated_until_voided, "AssertionError: ValidationError not raised".
- filed-year guard removed: test_a_filed_year_is_kept_and_holds_its_assets, "AssertionError: ValidationError not raised".
- race, refusal hook removed: "AssertionError: Lists differ: [True, True] != [False, True]".
- race, the restatement's own lock_rows removed: still passed. Both paths take the company numbering row
  first (O257: next_for() in make(), lock_rows() in the allocation), so they are already serialised; the
  refusal under that serialisation is what decides it. The lock_rows stays as the documented hold but is
  not what the race test proves.
- The rest are new behaviour (a module that did not exist): they fail without the commit by not importing.

Runs (worktree tip before the last commit had the same code as 7eb8ac1 for these labels):
- accounting sales purchasing assets e2e web, fastsettings, --exclude-tag migration, --parallel 2, browser
  on, after npm run build: 3443 tests, 1 failure (web EveryCollectionIsKeptOnAScreenTests: the three
  tax-block collections had no screen), 200 skipped (PostgreSQL races). Fixed (panels on the tax screen),
  then apps.web again: 199 tests OK, none skipped.
- accounting, assets --tag migration (default settings): 3 tests OK.
- audit_invariants: no findings (two rounds fixed: FOR UPDATE without no_key in make(); tax_block not on
  the serializers). makemigrations --check: no changes.
- Not run: the whole suite on PostgreSQL, and DJANGO_TIME_ZONE=Asia/Kolkata (the brief did not name them).

## Defects and limits seen, not fixed

1. apps/accounting/restatement.py document_open_on: only payments are replayed to the restated day. A
   note, drawdown, write-off, TDS or discount on a foreign document dated after the day refuses the whole
   restatement (a limit, by design; the person is told which). Scenario: a 1,000 USD invoice with a 100 USD
   credit note dated 2 April, restated as at 31 March on 5 April: refused. Kind: missing feature.
2. restatement.py document_item: the base carried by a part-settled document is the slice rule
   (booked - round(booked * settled / total)). Where a note was absorbed and counted at its own booked
   base (settlement.base_of_the_part, O209/O261), the ledger's residue for that document can differ from
   the slice by a paisa, and the restatement's difference by the same paisa; it reverses next day.
   Kind: rounding, paisa.
3. restatement.py bank_item: an EEFC account's foreign balance is the sum of the payments through it; the
   code refuses every other way into such an account (statement lines, hand journals, O24), but the
   restatement's own lines carry base only, as they must. Probe O24 (mirror) in
   tests_settlement_and_reconciliation_facts.py:301 still prints FAIL: JournalEntry.post() itself does
   not refuse a base-money line on an EEFC account; only the hand-keyed path asks. Pre-existing.
   Kind: guard at the wrong point (not changed here).
4. A foreign invoice or bill posted after a restatement, dated on or before its day, is not restated and
   not refused; the period close is what stops it. Scenario: restate 31 March on 2 April, then post a
   USD invoice dated 30 March with March still open: the year end misses it. Kind: missing guard.
5. apps/assets/models.py FixedAsset.disposal_proceeds is new: disposals recorded before this change have
   none, so the tax schedule reads their sale as 0. A backfill from the disposal entry's "proceeds" line
   was not written. Kind: data migration not written.
6. apps/assets/tax.py _block_year: "every asset of the block sold" is judged from the register. A block
   opened from the last return (TaxBlockOpening) whose older assets are not in the register can read as
   ceased when the registered ones are all sold. Kind: assumption for the accountant.
7. An asset acquired in one tax year and first put to use in a later one enters the block in the year it
   is put to use, half rate by days used; the Act's proviso speaks of an asset acquired in the year.
   Kind: rule for the accountant to confirm.
8. Additional depreciation (20% / 10%) is not computed. Kind: not built (regime-dependent).
9. The deferred-tax entry is not computed or posted; the schedule gives the difference. Kind: not built.
10. The restatement posts to the company's FX gain and loss accounts (kept on the document), not to
    separate unrealised accounts. Kind: decision for the accountant.
11. Payment.void with a standing restatement refuses for any document the payment was applied to that the
    restatement restated with as_of on or after the void day, even where that allocation came after the
    restated day and would not change it. Kind: over-refusal, safe side.

---

# Round 2 (review_yearend: three blockers, finding 4, own limits 1-4)

Base 7eb8ac1; the reviewer's probes cherry-picked first (6c5e062, 23eda82, cb0b808; each ends with
`Gate: {GATE}` and the two trailers, none reworded). Figures in /home/user/work/calc_fix_yearend/calc_round2.py.

| Letter | Commit | What it fixes | Failed without it |
|---|---|---|---|
| A | 0ba9bcc (+ 767a088, its help-text migration) | review F1, F2: reversal dated as_of + 1; each item worked against booked plus any restatement standing that day (`standing_on`) | P01 "Decimal('84663.3000000000') != Decimal('84100.00')"; P02 "Decimal('763.3000000000') != Decimal('0') : nothing is open at 31 Mar" |
| B | 0ddc770 | review F3: a block ceases when every asset is disposed of in the year, whatever the proceeds | T01 "(False, Decimal('60000.00'), Decimal('0')) != (True, Decimal('0'), Decimal('-100000'))" |
| C | 1607ceb | review F4: an asset event (in service, disposal, reinstatement, block change, opening) in or before a filed year is refused; the guard holds the block first, as file() does | T03 "Decimal('100000.00') != Decimal('65450.00')" |
| D | 66869f2 | own limit 1: notes, drawdowns, write-offs and recoveries, TDS and reversals, discounts and withdrawals dated after the day are replayed to it (signed records, `settled_otherwise_on`) | test_a_credit_note_after_the_day_is_replayed_to_it: refused "cannot be restated as it stood on 31-03-2026" |
| E | 3b33d0f | own limit 4: a foreign invoice, bill or note dated on or before a standing restatement's day is refused at post(), in words (void and restate, or date it after) | P12 "Decimal('125768.350000000') != Decimal('126150.00')" |
| F | 49217be | own limit 3: JournalEntry.post() refuses base-only lines on an EEFC account unless the entry moves it in its currency (payment, restatement and its void); reversals pass | O24 (mirror) printed "FAIL ... [posted]"; it now asserts |
| G | 3e5b54a | own limit 2: carried base = booked less what each settlement standing on the day recorded (allocation document_base, the entries of notes, drawdowns, write-offs, TDS, discounts) | test_the_base_carried_is_the_ledgers_not_the_slice: "37501.51 ... 343.49 != 37501.52 ... 343.48" |
| probe | 1bac273 | R3 split into one order per test; claim corrected (see below) | |

Probes changed, and why (mistake 7: which was wrong):
- P11 asked for the refusal that decision D removes; it now asks for the replay (1,000 open, 763.30).
- P12 asked for the late invoice to be restated or refused; it now asks for the refusal decision E makes.
- T03 asked for the earlier-year event to pass and for the filed opening to equal 2024-25's closing.
  Decision C refuses the event. The footing claim cannot hold for a block whose 2025-26 opening row
  starts it: 2024-25 is not that block's year at all. It now asks for the refusal and the filed year
  unchanged.
- R3 built its fixture twice in one test (code collisions on the second pass) and failed whenever any
  restatement stood once the receipt was voided. Voided first, the restatement rightly leaves the
  receipt out and restates the unpaid invoice. Now one order per test; it fails on a standing line
  for a receipt voided on its day.

Races on PostgreSQL (database fix_yearend, nice 19): 7 tests OK. These were my restatement against an
allocation, the new file() against dispose() in both orders, and the reviewer's R1, R2 and R3 in both
orders. Without the refusal check the restatement race failed (round 1). The file-against-dispose
race is not decided by the new block lock: dispose(), place_in_service() and reinstate() are
@serialised and file() uses lock_rows, and both take the company numbering row first (O257). So the
two are already serialised, and the block lock is a second hold, not the one the test proves.

Runs, round 2 (tip 1bac273, worktree clean):
- accounting sales purchasing assets e2e web, fastsettings, --exclude-tag migration, --parallel 2, after
  npm run build, browser tests on: 3471 tests OK (skipped=206: the PostgreSQL races).
- accounting --tag migration (0033 is a help-text AlterField): 2 tests OK.
- Races on PostgreSQL: 7 OK (above).
- audit_invariants: "No invariant findings." makemigrations --check: "No changes detected".
- Every reviewer probe passes (P01-P12, T01-T04, X1-X2, R1-R3), four as amended above.
- Commits this round: 9 (A, A's migration, B, C, D, E, F, G, the R3 correction), plus the reviewer's 3
  cherry-picked. Tip 1bac273. Not pushed.

Limits still open after round 2 (from round 1: 5 to 11; new):
- D's record for a credit note uses the part it absorbed now; for a note that undid a write-off or a
  discount, the undo is a record of its own on the note's reversal day. Not tested.
- A write-off, TDS row, drawdown or discount dated on or before a standing restatement's day and
  recorded after it is not refused (E covers invoices, bills and notes only). Kind: missing guard,
  same shape as E.
- A partly absorbed note has no entries of its own for the carried base; it uses the document's rate
  (paisa, as before G).

---

# Round 3: the mirror gap of E (audit shape 1)

fbf40a8, tip. A single check, `restatement.refuse_behind_a_restatement`, replaces the two earlier ones
(posting and allocations). It is asked by Invoice.post and Bill.post (notes too), Payment.post and
Payment.void, every allocation and its removal (RealisedOnItsOwnDay), take_settlement_discount and
withdraw_discount, and, through the `AsksTheRestatement` mixin, by deposit and prepayment drawdowns,
write-offs and their recovery, customer TDS and TDS deductions and their reversals, and TCS
collections, whenever one is made, taken back or removed. Each is refused in words when dated on or
before a standing restatement's day.
- Tests: EveryEventAsksTheRestatementTests, one per event kind, including a new payment dated before
  the day.
- Sentinel (`test_every_path_that_settles_a_document_asks_it`): finds every model that books money
  against an invoice, a bill or a payment and names any that never asks. The exemptions in
  NEED_NOT_ASK each have a reason: payroll records in the base currency, and statement lines. With
  CustomerTds's mixin removed it failed: "AssertionError: Lists differ: ['sales.customertds'] != []".
  A planted class is named by `test_the_sentinel_names_a_path_that_does_not_ask`.
- Runs: accounting, sales, purchasing, e2e (fastsettings, --exclude-tag migration) 3107 OK
  (skipped=206). PostgreSQL races 7 OK. audit_invariants: no findings (one fixed: the sentinel helpers
  moved into the test module). makemigrations --check: no changes.
- The partly absorbed note's paisa goes to the register (coordinator), not here.
