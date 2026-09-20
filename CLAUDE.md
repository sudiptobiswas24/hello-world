# Working on this ERP

Django 5.2 + DRF. `python manage.py test apps` runs everything (~960
tests). Use `.venv/bin/python`.

## Architecture rules

- `core` is the kernel and imports from no module. Party-side settings
  live on the dependent side (`PartyTaxProfile` in accounting,
  `CustomerProfile` in sales).
- `accounting` may not import `sales` or `purchasing`. Anything both
  trading modules need lives there: `mixins.py` (line arithmetic),
  `settlement.py` (FX, installments), `ChargeType`.
- `purchasing` → `sales` for drop-ship only, by lazy string FK. Acyclic,
  and the dependent side holds the pointer.
- **Derived, not stored.** On-hand quantity, weighted average cost,
  balances due, aging — all replayed from the ledger that produced them.
  A stored copy drifts the moment anything is corrected.
- **Posted is immutable.** Corrections are reversals: credit note, debit
  note, return, void. Never an edit.

## Mistakes this project keeps making

Not hypothetical. Every one of these has cost real rework here, most of
them more than once. Read this list before writing, not after.

### 1. A comment is not a constraint
`Payment.void()` carried the docstring "Allocations must be released
first." Nothing released them and nothing checked. A bounced cheque left
its invoice reading as paid while the ledger said otherwise. **If a
docstring states a precondition, the code must enforce it or the
docstring must go.**

### 2. Building the forward path and stopping
Three of the last four defects found were reverse paths: returning a
subcontract receipt destroyed the components, returning a drop-ship
invented stock, voiding a payment left documents settled. **Before
writing a flow, name its reverse and write it in the same sitting.**

### 3. Validating at the wrong point in the lifecycle
- `check_percentages()` fired on every line save, so a 50/50 term could
  never be built — the first line always totalled 50.
- `GoodsReceiptLine.clean()` checked a quantity that nothing validated,
  because receipts are built in code and Django never calls
  `full_clean()` for you.

**Ask when the answer can first be known, and whether the code path
actually reaches the hook you are using.** `save()` runs; `clean()`
often does not.

### 4. Recomputing a fact that has since changed
Landed cost allocated to nothing, because by the time it ran the bill
counted as billed and the accrual looked consumed. The same bug shape
hit debit-note posting accounts. **Anything a posted document did is a
fact to record (`posted_account`, `exchange_rate`, `unit_cost`,
`voided_entry`), never something later readers recompute.**

### 5. Copying a code shape and its bug with it
The approval-withdrawal hole — approve an order, then add a line —
existed in Sales first and was copied into Purchasing verbatim. **When
mirroring code, audit the original rather than trusting it. Better:
share it, so one fix closes both.**

### 6. Bending a helper past its assumptions
`post_inventory_entry(direction="out", reverse=True)` gives
`Dr Inventory / Cr COGS`, not the `Dr COGS / Cr GRNI` a drop-ship needs.
It posted backwards until a test caught it. **When no argument spells
what you mean, write the entry directly and say why the helper does not
fit.**

### 7. Assuming a failing test means the code is wrong
It has gone both ways here, roughly evenly:
- Tests asserting `GRNI` behaviour with no receipt were *encoding the
  defect* and were rewritten.
- FX and stock-value expectations were *my arithmetic being wrong*,
  twice; the code was right.

**Work out which before editing either.** Reconstruct the number by
hand. Never adjust an assertion to match output.

### 8. Pattern-matched edits landing on the wrong class
A `sales_order_line` FK landed on `RfqLine`, which has an identically
shaped `requisition_line` field earlier in the same file. **After a
scripted edit, check the migration says what you expected.** It named
the wrong model and that is what caught it.

### 9. Committing before the suite is green
Done once, amended. `git commit` and `manage.py test` in one command
means the commit lands whatever the tests say. **Run the suite, read the
result, then commit.**

### 10. Feature order that ignores dependencies
Working a list in the order it was written rather than the order things
depend on. Vendor prices had to precede blanket orders and RFQ, or both
would have been retrofitted. **Sort by what the next thing needs.**

## Auditing

`.claude/skills/audit/SKILL.md` holds the defect shapes three audits
found, and `python manage.py audit_invariants` checks the mechanical
half. Run both before calling a module done. The suite passing means
nothing — all 29 defects found so far were found with a green suite.
