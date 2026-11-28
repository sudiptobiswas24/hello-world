# g23b brief: fix_sec4 — O255, O317, O318, O348

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.

## Setup
- Base: 9e9d4d4. Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_sec4 9e9d4d4`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 120 tool calls. Report: /home/user/work/reports/fix_sec4.md.
- You own: apps/core/second_factor.py and its migration, apps/core/chatter.py's link rule,
  the shared finite-number reader (new, O318), and HidesStockMoney's reach (O348).
  Another agent owns accounting/settlement, sales commission, assets capitalisation,
  purchasing subcontract and the item account rules; another owns hr and apps/core/lock_order.py.

## O255: the second factor's secret encrypted at rest (decided)
- Fernet from `cryptography`, pinned (44.0.2; add it to requirements.txt). Keys come from the
  environment, `ERP_FIELD_KEYS`: comma-separated, the first encrypts, every one decrypts
  (MultiFernet), so a key can be rotated. Mirror settings.py's DJANGO_SECRET_KEY rule: in
  PRODUCTION a missing key refuses to start, with a sentence saying what to set; outside it a
  fixed development key, never used in production.
- Keep the key out of the database and out of backups. Say in deploy/ docs (or the backup
  README) that a restore needs the key as well, or every enrolled login must re-enrol.
- A data migration encrypts every existing secret, reversible, tested with MigrationExecutor.
  A row that will not decrypt answers "re-enrol", never a 500 and never a sign-in.
- Recovery codes are hashed already; check, do not touch if so.
- A management command `rotate_field_keys` re-encrypts under the first key; test it.

## O317: a follow-up link stays on the site
Read chatter.py near 94. Accept a path only: it starts with a single `/`, and no `\`, no
second `/`, no scheme, no control character or encoded form of them (`%5c`, `%2f%2f`) can make a
browser read it as another host. Parse it, do not pattern-match on the prefix alone. Then check
what Home and every other screen that renders a stored link does with it
(frontend/src): the screen refuses to render anything but a same-site path, so an old row
already stored is harmless too. Tests: the server refusal (the auditor's probe, C5 in
docs/handoff/reports/g23_audit2_core.md), and a vitest for the screen.

## O318: one reader for a number from a request
One shared function in apps/core/api.py beside `flag`: a Decimal from request data that refuses
NaN, Infinity, signs where none is allowed, and more places than the field holds, as a 400 beside
the field. Replace every `Decimal(str(request...` and `Decimal(request...` in apps/ (grep them all,
not only the two the auditor probed). Add an `audit_invariants` check that names any new one,
with a test that it reports a planted instance.

## O348: stock money hidden from floor roles (decided)
HidesStockMoney (g23) hides money without inventory.view_stock_value. Extend it to an item's
standard_cost wherever an item is served, and to a work order's costs for a Quality Inspector:
every serializer and report that carries them. Test as the roles (apps/core/tests_roles.py):
a Machine Operator, a Shift Supervisor and a Quality Inspector see no figure; the Production
Supervisor and Maintenance keep it (decided in g23). A list endpoint and its detail both.

## Before reporting
Run core, assets, inventory, manufacturing, quality, web (with the browser tests after
`npm run build`), and `--tag migration` for core. vitest in frontend/.
