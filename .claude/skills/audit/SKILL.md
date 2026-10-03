---
name: audit
description: Adversarial audit pass over a module or a new feature in this ERP. Use before calling any module "done", after building a feature, or when asked to audit, review for defects, or check what a module is missing. Encodes the defect patterns that three prior audits actually found, so they are checked deliberately rather than rediscovered.
---

# Audit pass

Three audits of this codebase have found 26 defects that a passing test
suite did not. Not one was a subtle algorithm error. Every single one
was a gap in *coverage of the problem*, and they fall into eight shapes
that keep recurring.

This is that list, in the order that has found the most per minute
spent. Work it against the surface you are auditing.

## How to run one

1. **Write a probe, not tests.** A throwaway script in the scratchpad
   that sets up real documents and asserts what *should* be true. Tests
   encode what you already believed; a probe asks questions you have not
   answered. Print PASS/FAIL per check and a count at the end.
2. **State each check as a claim about money or state**, not about
   code. "GRNI clears to zero after receipt and bill" is checkable.
   "posting_account is correct" is not.
3. **Fix, then re-run the probe to zero.**
4. **Convert every finding into a regression test.** The probe is
   disposable; the test is the record. One test per finding, named after
   the fact it protects.
5. **Do not weaken a guard to make a test pass.** Three times now the
   failing tests were encoding the defect, and the fixture was wrong.
   Twice the guard was wrong. Work out which before editing either.

## The eight shapes

### 1. Mirror gap — a guard on one path, absent on its mirror
**Found 9 of 26. Check this first, always.**

For every guard you can see, name its mirror and prove it too:

- forward ↔ reverse: receipt ↔ return, delivery ↔ customer return,
  invoice ↔ credit note, bill ↔ debit note, payment ↔ void, issue ↔
  consume. *Three of the last four findings were reverse paths.*
- create ↔ edit ↔ delete: a rule checked when a document is created and
  never again is not a rule. Line quantities, prices and approvals were
  all editable past their own checks.
- sales ↔ purchasing: the two sides are mirrors. A guard on one is a
  missing guard on the other until proven otherwise.
- generated ↔ hand-entered: `create_bill()` respected the match;
  a bill typed in by hand did not.

### 2. Inert feature — built, configurable, and never invoked
**Found 4 of 26, and 7 of 41 by the time the mechanical check landed.**

Something fully modelled, admin-editable, documented — that no code path
reads. It looks handled, which is worse than missing.

- every settings field: is it *read* anywhere? (`audit_invariants`
  checks this mechanically — run it.)
- every optional model: does a document consult it, or only display it?
- every module-level helper: does anything outside its own tests
  mention it? (`audit_invariants` checks this too.)
- fiscal positions, settlement discounts (twice), `mark_sent()`.

**A probe cannot find this shape, and one did not.** FEFO lot
allocation, bin routing and put-away were each probed thoroughly and
each passed, because a probe exercises the thing it was written for and
this shape is about what *else* does. Twelve reports across four
modules had the same problem — financial statements included, which
nothing could ask for. Ask the question the other way round: not "does
this work" but "who calls it".

### 3. Control account that never clears
**Found 4 of 26.**

GRNI, deposits, prepayments, FX, suspense — accounts whose *purpose* is
to net to zero. Any of them holding a permanent balance is a defect,
and it is invisible in a single-transaction test.

- run the full cycle and assert the account returns to zero.
- vary one input (price, rate, quantity, date) and assert it *still*
  returns to zero. The residue only appears when something differs.

### 4. Document disagrees with ledger
**Found 3 of 26.**

Every derived balance must foot to the ledger it summarises.

- `amount_due()` vs the control account.
- `stock_value_at()` summed vs the inventory account.
- statement/aging totals vs `outstanding_balance()`.
- after a *correction*, not just after the happy path.

### 5. Double processing
**Found 3 of 26.**

Can the same economic event be recorded twice?

- bill the same delivery twice; invoice the same order twice.
- the same vendor invoice number twice.
- re-run a scheduled job over the same period.
- does a failed attempt consume a sequence number?

### 6. Partial operations
**Found 2 of 26.**

Everything that happens in full happens in part. Ship half, bill half,
credit three of ten, pay 40%, return one line.

- is the partial path supported at all?
- does the remainder stay correct and re-usable?
- do rounding remainders survive, or evaporate?

### 7. Silent skip
**Found 2 of 26.**

A path that quietly does nothing is worse than one that fails.

- a record written for something that did not happen (dunning notices
  with no email) permanently exempts that record from future attempts.
- `except: pass`, `if not x: return`, `.filter()` that silently drops.
- anything "skipped" must be *returned to the caller*, not swallowed.

### 8. Scope and filter omission
**Found 1 of 26.**

Every lookup that crosses a dimension must filter on it: currency,
company, date validity, active flag, party, warehouse. A price list that
ignored currency priced a EUR order from a USD list.

## Questions that are not on this list

Deliberately. They matter, but no audit has found them and a checklist
that lists everything gets read by nobody:

- performance, N+1 queries
- error-message quality (one finding, low value)
- concurrency beyond the existing `select_for_update`
- security and permissions — use `/security-review`

## Is this list any good?

Scored against all 29 defects found here to date: **28 map to a listed
shape.** The one it misses is a confusing error message on a zero-value
invoice, which is a quality issue rather than a correctness one.

Distribution, which is the useful part — it says where to spend time:

| shape | found |
|---|---|
| 1. Mirror gap | 11 |
| 2. Inert feature | 5 |
| 3. Control account never clears | 4 |
| 4. Document vs ledger | 2 |
| 5. Double processing | 2 |
| 7. Silent skip | 2 |
| 6. Partial operations | 1 |
| 8. Scope filter | 1 |

**That 28/29 is close to worthless as evidence, and should be read as
such.** The list was derived from those findings, so it fits them by
construction. A checklist written from the past always explains the
past.

The evidence that it *predicts* is separate and much smaller: the
mechanical half, written from shapes 1 and 2, immediately found six
defects no human pass had — `Company.base_currency` stated a fact
nothing read, and five line models were missing the sign constraints
their mirrors already had. That is the number to trust, and it is one
data point.

Two honest weaknesses:

- **Shape 1 is doing most of the work.** Eleven of twenty-eight. If you
  only ever check mirrors you get most of the value, and the other seven
  shapes are long-tail. Do not let their presence make the list feel
  thorough.
- **It cannot find what it has not seen.** No audit here has looked for
  concurrency, performance or permission defects, so none are listed,
  so none will be found. The list is a record of where we have looked,
  not of where the bugs are.

## Mechanical half

`python manage.py audit_invariants` checks what can be checked from the
models themselves: unread settings fields, correction paths with no
test, posted documents with no immutability guard, numbered documents
with no partial unique constraint, money fields with no sign
constraint. Run it first — it is free — then work the list above for
everything a machine cannot see.
