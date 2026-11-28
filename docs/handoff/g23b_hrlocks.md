# g23b brief: fix_hrlocks — O262, O360, O270, then O257, O258

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.

## Setup
- Base: 9e9d4d4. Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_hrlocks 9e9d4d4`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 160 tool calls. Report: /home/user/work/reports/fix_hrlocks.md.
- You own: apps/hr (advances, leavers, expense claims, write-offs) and apps/core/lock_order.py.
  Another agent owns apps/accounting/settlement.py, sales commission, assets capitalisation and the
  item account rules; another owns apps/core/api.py, chatter and the second factor. For the lock
  shapes in manufacturing, assets and accounting (O257) change only the locking, never the money:
  if a shape needs a money change, stop and report it.
  Shared rules that exist, use them: `lock_rows()`, `@serialised`, `locked_before_it()`,
  `in_a_transaction_it_opened`, `login_ended`/`check_login_acts`, `correction_date()`.

## Order: what the next needs
1. **O262** a leaver's unrecovered advance: the shortfall settlement of O215 settles the advance's
   remainder too, by a receipt or a write-off (the reverse path of each, and its date rule). Work
   the numbers by hand first.
2. **O360** (decided) an expense claim line to expense accounts only: on save and in the API, as
   the role.
3. **O270** the write-off holds the employee (the same row a payment from them holds) and reads the
   slip's figure, not the person's whole balance on the account. A race() test on PostgreSQL that
   fails without the lock.
4. **O257** the eight UNSEEN shapes in apps/core/lock_order.py, one commit each: hold the rows in key
   order with `lock_rows()` first, then walk, take the shape off UNSEEN, and a race() test. Shapes
   that cannot be raced in a test (say why) still come off UNSEEN with a sentinel test.
5. **O258** `accounting.account`'s rank: run the survey (LOCK_ORDER_SURVEY=<dir>) on PostgreSQL over
   accounting, sales, purchasing, hr, inventory and e2e (the apps that reach hold_money_account;
   this is the one run of more than your own tests you may make), read every pair it records, then
   give it its rank. Not a guess.

## Before reporting
Run hr, accounting, core, manufacturing, assets, inventory, e2e (fastsettings, --exclude-tag
migration), `--tag migration` for core (lock_order.py is touched), and every race() test you
added on PostgreSQL with your own database name.
