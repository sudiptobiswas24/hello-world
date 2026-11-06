# audit2_core: core, assets, planning, imports and the office application

Base b3c7a93. Worktree /home/user/work/audit2_core. Read-only for product code. Probes are tests in
`apps/*/tests_audit2_*.py` and `apps/web/tests_browser_audit2_core.py` (committed, unpushed). Scripts that
worked things out first: `calc_audit2_core/` (screens*.py, adders.py, inert.py). Each finding below has a
probe that fails on the base (name given). Nothing was run on PostgreSQL: no race() probe was written (see
"not reached").

`audit_invariants`: "No invariant findings", exit 0 (`reports/audit2_core_invariants.txt`).
`makemigrations --check --dry-run`: "No changes detected", exit 0 (`reports/audit2_core_makemigrations.txt`).
Kolkata run of assets, imports, planning and core: see the last section.

## Findings

### Books

**C1. The opening-stock import has no once-only guard (go-live blocker, same family as O280).**
`apps/imports/importer.py` `_opening_stock` (about line 262). The same file committed twice makes a second
set of posted adjustments: 1,000 kg of granules at 3.50 on a 1,500 kg shelf reads 2,500 kg after the second
run and the opening-balance account is credited twice (3,500.00 each time). `_open_document` refuses a repeat
by reference and the cutover kinds by name; `_opening_stock` and `_opening_balances` (O280) do not. A retry
after a browser timeout is enough. Shape: mirror gap (silent double processing). Logins: Controller
(`core.import_records`). Probe: `apps/imports/tests_audit2_core.py`
`test_opening_stock_file_run_twice_does_not_double_the_stock`. Fix with O280: one guard, shared.

**C2. The nightly backup reports success and deletes old backups when pg_dump fails (go-live blocker).**
`deploy/backup.sh`: the loop runs `dump || echo BACKUP FAILED`. A shell ignores `set -e` inside a function
called that way, so a failed pg_dump (database down, disk full) does not stop `dump`: it goes on to archive
the media, `mv` fails on the missing partial, prints "backup written: ...dump ()", and the `find -mtime +30
-delete` lines then delete every backup older than 30 days with no new dump made. "BACKUP FAILED" is never
printed. After 30 nights of a broken dump, no backup is left; only the Health screen's "no backup in the last
day" (26 hours) warns, and only if someone reads it. A truncated tar has the same shape (the manifest and
`mv` still run). Mistake 1 shape (the header comment says each part stays `.partial` until all three are
written; nothing enforces it). The `now` path is not affected (it calls `dump` bare). Probe:
`apps/core/tests_audit2_backup.py` (stub pg_dump and sleep; fails on "BACKUP FAILED" missing).

### Rule

**C3. Planning: a requisition turned into a draft purchase order falls out of supply.**
`apps/planning/mrp.py` `requisition_supply` (about line 473) counts requisition quantity less
`quantity_ordered()` (purchasing/models.py about 966), which counts lines on any order not cancelled, draft
included; `purchase_supply` (about 384) counts confirmed orders only. Between "convert to order" and
"confirm", the 1,000 kg is in neither, and the next plan asks for 1,000 kg again (reorder minimum 1,000 kg,
requisition 1,000 kg, draft order 1,000 kg: the plan proposes PP-RAFFIA 1,000). The docstring says the
ordered part "is already counted as a confirmed purchase", which is only true after confirm. Probe:
`apps/planning/tests_audit2_core.py` `test_a_requisition_turned_into_a_draft_order_is_still_expected_supply`.
Kind: books-adjacent rule, not a blocker (a buyer sees the draft), but it is the exact double-ordering the
module says it prevents.

**C4. Planning: a planned order can be edited and deleted after it was firmed.**
`PlannedOrderViewSet` is a plain ModelViewSet and the serializer makes `quantity`, `item`, `kind`, `bom`,
`needed_by` writable. As Production Planner: PATCH quantity 1000 to 10 on a firmed order answers 200, DELETE
answers 204 (the draft work order stays at 1,000 with no account of itself, and the plan's firmed-order
bookkeeping is gone). Mirror gap with the firm/cancel guards. Probe: `tests_audit2_core.py`
`test_a_planner_cannot_change_or_delete_a_planned_order_after_it_became_a_work_order`. Minor.

### Security

**C5. A follow-up's link can leave the site: `/\host` passes the check.**
`apps/core/chatter.py` line 94 refuses a link that does not start with `/` or starts with `//`. A browser
reads `/\pay-vendor.example/login` (and `/<tab>/pay-vendor.example`) as `//pay-vendor.example`. `Home.tsx`
renders `<Link to={row.link}>` for the assignee. A Sales Rep (any holder of `core.add_followup`) can put a
phishing address on the Controller's Home list. CSP `script-src 'self'` and React 19 stop `javascript:`;
nothing stops this. Server side proven (201 on the backslash form, the other three forms refused); the click
in a browser is the standard URL rule, not run. Probe: `apps/core/tests_audit2_followup.py`. The same
check in `saved_filters.py` (the `screen` field) has the same hole but the value is never rendered as a link.
Minor.

### Crash

**C6. Non-finite numbers answer 500.** `assets/views.py` line 115: `Decimal(str(...))` accepts "Infinity";
`POST assets/{id}/dispose/` with `proceeds` "Infinity" answers 500 (NaN and -5 answer 400). `planning/views.py`
line 365: `GET /api/planning/promise/when/?quantity=NaN` answers 500; `quantity=-5` and `Infinity` answer 200 with
a date. One shared parser (like `apps.core.api.flag`) would close all the `Decimal(str(request...))` sites
(also sales/price_variation_views.py 103, accounting/views.py 313, manufacturing/views.py 209, 1054, 2249,
station_views.py 58: not probed). Probes: `apps/assets/tests_audit2_core.py`, `apps/planning/tests_audit2_core.py`
`PromiseQuestionTests`. Minor.

### Statutory

**C7. The printed tax invoice drops text in angle brackets from the customer's name.** `core/documents.py`
puts party name and addresses into reportlab `Paragraph` markup unescaped: a customer "Sharma <Raipur> & Sons"
prints as "Sharma & Sons" (the line description keeps its `<50 kg>`). No crash. The name on a GST invoice must
be the buyer's. Rare in practice; the cure is `xml.sax.saxutils.escape` on every inserted field. Probe:
`apps/core/tests_audit2_paper.py` `test_what_the_paper_says_is_what_was_typed`. Minor.

### Inert

**C8. Methods nothing calls** (`audit_invariants` reads module-level helpers only, so these pass it):
`UnitOfMeasure.to_base_quantity` (core/models.py 733), `PlanningRun.fenced`, `PlanningRun.into_the_fence`,
`PlannedOrder.was_fenced` (planning/models.py 285, 289, 470). Found by `calc_audit2_core/inert.py`
(definitions with one mention in the code, templates, the office application and deploy). Minor; the checker
could be widened to methods.

## Probed and held (each by a probe or a script)

- Every screen in `registry.ts` against the API's read permission, per role (`calc_audit2_core/screens*.py`):
  232 screens; the 17 apparent mismatches are reports whose action map names the real right
  (cost-centre report, budget report, GSTR-2B, bank import ...) or a conditional reference list; no role is
  offered a screen whose first read it would be refused, except none found. Two reported (GST Officer on
  GSTR-2B, Bookkeeper on the asset register) are custom permission classes / a conditional reference, fine.
- A login with no role: every parameterless GET under /api/ refused except /me/, its own saved views, the
  endpoint list, inbox and search (`tests_audit2_doors.py`).
- Follow-up link: absolute, `//` and `javascript:` refused. Custom field value on a posted invoice: PATCH
  refused (400). The browser: a charge type given a tax (Controller) and a price-index value published
  (AR Manager) both work end to end (`tests_browser_audit2_core.py`).
- Read by hand and found sound: users_api (two-person rule, own roles, deactivate, reset), roles
  proposals (serialised confirm/decline), API keys (digest only, expiry, revoke, every use kept, second-factor
  logins refused, issuer ceiling), second factor (one use per code step, lock-out doubling, recovery codes
  hashed, sessions ended on reset), password lock-out (name+address, address, name; proxy count), session
  limits (signed idle cookie bound to the session), attachments and history and notes (view permission of the
  kind and of the record, `may_read`), numbering (per-year counters, no gap on a rolled-back post, no
  lowering), assets (depreciation by month ends, closed-month checks, disposal reverses later charges,
  reinstate re-charges), custom fields, licences, saved filters, restore.sh (staging and manifest check).
- `timezone.now().date()`, `date.today()`, `float(` in these four apps: none.

## Unproven (what would prove it)

- Opening-invoice import dates are not held to before the books' opening day (O281 family; prove with a
  BooksOpening and a 2027-01-01 reference).
- Two import commits at once of the same parties/items file: `exists()` then create. A race() on PostgreSQL
  would show a unique-constraint 500 (not run).
- `go_live_check` does not ask for BooksOpening (O251), a first backup, or an enrolled second factor for the
  guarded roles; a fresh install can pass it and refuse the first asset.
- A login switched off then on again within 12 hours has its old session back (the factor is deleted so
  guarded logins re-enrol; an unguarded login's session simply resumes). Prove: sign in, deactivate,
  reactivate, reuse the cookie.
- Anyone who attached a file removes it, also from a posted, period-closed document (`attachments_api.destroy`);
  and `follow-ups/people/` lists integration logins. Both look intended.
- A confirmed sales line with no warehouse is planned in every warehouse (mrp.py `sales_demand` comment says
  one site); with a second warehouse its demand is counted twice.

## Not reached

- No PostgreSQL race() probe (DATABASE audit2_core unused): attachments, follow-ups, import commits,
  `depreciate_all` against a disposal were read as serialised but not raced.
- The lock-order sentinel and the UNSEEN list (known: O257, O258).
- MRP netting numbers (lot sizing, safety stock, expiry, phantoms): read for filters (status, warehouse,
  drop-ship, closed-short), not recomputed by hand; the suite there is 1,100 lines.
- 23 of 25 adders no browser test names were not driven (BOM substitute/by-product/route, standard cost,
  machine position, routing step, party bank account, pay, applicant, slab, tier, delivery, blanket call-off,
  quality check, fiscal-position replace, item attribute value, packing, item unit, ...). Checked statically
  by `calc_audit2_core/adders.py` that each posts the fields the test already covers; only the two above were
  driven.
- Each role's mutating endpoints in core (`tests_roles.py` style sweep).

## Kolkata

`DJANGO_TIME_ZONE=Asia/Kolkata`, fastsettings, assets+imports+planning+core, 1,092 tests, 1,251 seconds:
failures=6, skipped=4. All six failures are probes of this audit (C1, C2, C3, C4, C7, and the open-doors probe
in its first form, since corrected to pass); every test that existed on the base passed. The probes for C5 and
C6 were written after the run began and were not in it; each was run alone on UTC. Output:
`reports/audit2_core_kolkata.txt`.
