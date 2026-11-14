# g24 brief: fix_yearend — closing-rate restatement and tax depreciation by block

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it, especially the
numbers checklist on exchange differences. Then docs/handoff/reports/odoo19_gap_analysis_oct26.md
(gaps 6 and the depreciation row).

## Setup
- Base: d76ef6f (g23b pushed). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_yearend d76ef6f`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 150 tool calls. Report: /home/user/work/reports/fix_yearend.md.
- You own the period-end restatement in apps/accounting and tax depreciation in apps/assets. Other
  agents own apps/core/history.py and audit.py (the audit trail), hr/statutory.py and the seeds,
  and GST reverse charge. Do not touch their files. Shared rules that exist, use them:
  settlement.in_base, settlement.post_drawdown, correction_date(), the period close, the
  numbering-first lock (O257), @serialised and lock_rows, StatutoryFigure for dated figures.

## Why
The plant exports and imports, so at the year-end (31 March 2027) its foreign-currency receivables,
payables, bank balances and advances must be restated at the closing rate (Ind AS 21 or AS 11,
whichever the plant reports under: research it and say so; the accountant confirms). The Income-tax
Act computes depreciation by block of assets at written-down value, at the block's rate, with half
the rate for an asset used under 180 days in the year. The books keep straight line under the
Companies Act. Today only straight line exists, and nothing restates foreign balances.

## Decided (do not reopen)
1. **Restatement** is a document, made for a date (normally a period end) by the Controller:
   - Each open foreign-currency item (an invoice or bill with something due, an unallocated
     receipt or payment, a foreign-currency bank account's balance, an advance) is restated at the
     closing rate for that date.
   - The difference is posted to unrealised exchange gain or loss, one line per item, so each item
     knows its own restated base.
   - It reverses on the first day of the next period. When the item is later settled, the realised
     difference is worked against the base it was booked at, never against the restated one, so
     restate and reverse leave settlement unchanged (mistake 4). If the standard requires
     otherwise, say why in the report and do what keeps the books and the documents agreeing.
   - The rates used are kept on the document.
   - Its reverse is a void, under correction_date().
   - A second restatement for the same date is refused.
   - Every exchange difference is the base each side booked, each rounded, subtracted.
2. **Tax depreciation** is a second schedule beside the books, never posted to the ledger:
   - Blocks by rate, with the rates as dated data. Research the current Income-tax Act 2025
     depreciation rules and the 1961 Act's for the years before 1 April 2026, cite them, and load
     them unconfirmed for the accountant.
   - Additions with the 180-day half rate.
   - Sales reduce the block by what was received. A block that goes negative, or is emptied with
     assets sold, gives a short-term capital gain or loss.
   - A year's schedule is a kept fact once filed (mistake 4).
   - A report shows the block-wise schedule and the difference to book depreciation (for deferred
     tax, if the plant computes it: research and say).
3. Screens for both. Each is driven in a browser test as the Controller, and refused to a role
   without the right.

## Tests
Work every number by hand first, in a plain-Python script in /home/user/work/calc_fix_yearend/:
- a USD invoice booked at 83.3367, restated at 84.1, reversed, then paid at 84.55;
- the same invoice paid before the period end;
- a part-paid one;
- a bank account in USD;
- a block with an addition on day 179 and day 181, a sale that empties the block, and a sale for
  more than the block.
Each with a test that fails without the fix.

## Before reporting
Run accounting, sales, purchasing, assets, e2e and web (fastsettings, --exclude-tag migration,
browser tests after `npm run build`), `--tag migration` for anything you migrated, a race() on
PostgreSQL for a restatement against a payment of a restated invoice (your own database,
fix_yearend, `nice -n 19`), audit_invariants and makemigrations --check. Do not edit docs/RISKS.md.
