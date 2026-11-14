# g23b brief: fix_books3 — O260, O261, O350, O353, O349, O347, O329

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.

## Setup
- Base: 9e9d4d4. Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_books3 9e9d4d4`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 150 tool calls. Report: /home/user/work/reports/fix_books3.md.
- You own: apps/accounting/settlement.py, the sales commission base, assets capitalisation from a
  bill line, purchasing's subcontract return costing, the item account rules
  (inventory and cogs), and inventory migration 0040's successor if one is needed. Another agent
  owns apps/core (api.py, chatter, second factor, HidesStockMoney); another owns hr and
  apps/core/lock_order.py. Shared rules that exist, use them: `settlement.in_base`,
  `settlement.post_drawdown`, `given_back_or_waiting`, the inventory-account rule (g23, a stock
  asset account changeable only with accounting rights), `cost_of_removing()`, StockMovement.value.

## Order: what the next needs
1. **O260** `settlement.document_slice` reads the booked base with the TCS entry, as the drawdown
   does since g22: one reader of "what this document booked", used by both. Prove it with a
   foreign-currency case even though TCS is refused in foreign currency today (call the reader
   directly), and a rupee case that must not move.
2. **O261** a note partly taken as a refund counts at what it booked, not its slice of the
   document's base. Sales and purchasing share the code: one fix (mistake 5). Numbers that do not
   come out even, with an exchange rate.
3. **O350** (decided) TCS is not revenue: the commission base and its divisor leave it out.
4. **O353** capitalising a bill line asks `given_back_or_waiting`: a waiting draft debit note
   reduces what can be capitalised, as a posted one does.
5. **O349** a legacy standard-costed subcontract return with no recorded consumption leaves
   -3.17 of price variance where average and FIFO net to 0. Work it by hand first
   (docs/handoff/reports/g23_fix_stores2.md, round 2), then decide which side is wrong (mistake 7).
6. **O347** (decided) the item's cogs_account: the shared account rule asks it too, an expense
   account kept for cost of sales, changing it needs accounting rights. Old items on a wrong
   account still post? No: posting asks again and refuses with a sentence, like the inventory
   account.
7. **O329** inventory 0040: read docs/handoff/reports/g23_audit2_stores.md item 6. The summary must
   count drops without netting, and the health check must not net across inventory accounts. Where
   a row's booked figure can be read from its document's entry, the migration takes it. No released
   database holds 0040's output yet (g23 shipped it today, the plant is not live): fixing 0040 in
   place is allowed if its test proves both directions; say which you chose and why.

## Before reporting
Run accounting, sales, purchasing, assets, inventory, manufacturing, e2e, web (fastsettings,
--exclude-tag migration), `--tag migration` for inventory and anything you migrated, and the races
in apps/e2e/tests_races*.py that touch settlement on PostgreSQL.
