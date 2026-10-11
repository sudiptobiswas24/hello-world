# Working on this ERP

Django 5.2 + DRF. `python manage.py test apps` runs everything (~1100
tests). Use `.venv/bin/python`.

## Architecture rules

- `core` is the kernel and imports from no module. Party-side settings
  live on the dependent side (`PartyTaxProfile` in accounting,
  `CustomerProfile` in sales).
- `accounting` may not import `sales` or `purchasing`. Anything both
  trading modules need lives there: `mixins.py` (line arithmetic),
  `settlement.py` (FX, installments), `ChargeType`.
- `purchasing` → `sales` for drop-ship, and `purchasing` → `assets` for
  capitalising a bill line. Acyclic, and the dependent side holds the
  pointer: the document that would be meaningless without the other one
  is the one that knows about it.
- `assets` depends on `accounting` and `core` only.
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

### 11. The shapes the 8 October audit found, and what guards each
Three read-only audits (stores, manufacturing, payroll with fixed assets)
found over forty defects with a green suite. Each shape below has a
guard now. Where it can be seen in code, `audit_invariants` reports it,
so a new instance fails the gate instead of waiting for the next audit.

| Shape | Example | Guard |
|---|---|---|
| A posted record deleted | A supervisor deleted a posted material issue over the API; its movements and entry stayed | check "deletable posted document": delete() says posted and raises |
| A correction dated anywhere | June's pay run voided on 1 May; a lathe disposed of before it was bought | `correction_date()` in apps/core/models.py; check `corrections_dated_without_the_rule` |
| Quantity in the document's unit, cost in the stock unit | Output in grammes at a per-kg cost: 1,000 times off the ledger | `movements_in_another_unit` |
| A per-unit rate multiplied back up | A write-down at a four-place unit cost | `per_unit_withdrawal_rates` |
| A void at the figure it was posted at | A re-batch void re-priced at today's average | `outbound_at_a_posted_figure`: book `cost_of_removing()`, the difference to variance |
| A calculation posted after its inputs moved | June's run posted at 4,400 after unpaid leave made it 3,400 | `posted_as_calculated`: post() works it out again |
| A rate row paid for the whole period | A mid-month rate change paid both rates in full: 9,900 for 4,950 | none in code: test a change on the 1st, mid-month and the last day |
| A check made before the lock | Delivery.post read the shelf, then locked it | `shelf_read_before_lock`, `unlocked_open_runs`; a PostgreSQL `race()` test |
| Two edits of one record at once | One person's new name kept, the other's new email lost | the viewset mixin holds the row from read to write |
| Locks taken in two orders | A stoppage corrected as its repair completed: deadlock | `locked_before_it()` names the rows a save() locks first |
| A setting read live where a fact should be kept | Disposal read the category's accounts after they changed | `kept_settings_read_live`, `entries_kept_past_the_edit_guard`: record it at the event |

A reverse path skipping its forward path's check (a return not matched
to its issue, a void of one part of a set) is still the most common
shape, and no check can see it. Share the check between the two paths,
so the reverse cannot be written without it.

## Pre-flight checklist

The list above is about design. This one is about the mechanics that
turned green work red in the last sessions, every item more than once.
Go through it before running anything.

**Before writing code**
- Scenario table first: every case with its expected number worked by
  a separate plain-Python script, and the refusals listed before the
  happy path.
- Name the reverse path, the edit path and the delete path of every
  guard (see audit shape 1).

**Numbers**
- Read the printed number before typing it into a test or a commit
  message. Never type an expected value or a test count from memory.
- Expected values carry only the places the column stores: a 6-place
  kilogramme is a 3-place gramme, and a hem stored at 2 places holds
  3.18, not 3.175. A constant with more places than its field builds
  the bill from one figure and reads back another.
- A frozen record is computed from its frozen figures; the test must be
  too.
- `round()` of a small negative Decimal is `-0.00`; add `+ 0`.
- Something held and drawn down later (a deposit, a prepayment) leaves
  its account at the rate it came in at; the gap to the document it
  settles is exchange. And what is "left" on it is less what has been
  credited back as well as what was drawn: both holes were in sales
  first and copied to purchasing (mistake 5, again). The money side is
  now one function, `settlement.post_drawdown`; use it.
- An exchange difference is the base each side booked, each rounded,
  subtracted — not the rate difference times the amount, rounded. The
  second can leave a paisa nothing explains.
- A settlement checks what it settles with, not only the amount: posted,
  direction, party, control account, not already used.
- Never round a per-unit cost and multiply it back up. A component's
  share of a sack rounded to the paisa lost 2.90 on 1,000 sacks; a
  four-place unit cost lost 0.16 on 4,000 kg of tape. Total first,
  divide last; `StockMovement.unit_cost` keeps eight places.
- Every outbound stock path asks `cost_of_removing()` and books what it
  says. The return to vendor credited the price it paid instead, and the
  shelf and the ledger parted for good. Where the two differ, the
  difference is a price variance, posted.
- Every `Decimal` leaves the API as exact text (`apps/core/renderers.py`),
  raw `Response` and `SerializerMethodField` included: no `str()` needed.
  What still needs care is the places: a ratio from a division goes out
  at every digit it has unless it is quantized first.

**Dates and the database the plant runs**
- The plant's day is `timezone.localdate()` or `to_date(moment)`, never
  `timezone.now().date()`: in Kolkata, UTC is still yesterday until
  05:30. A suite on UTC cannot see it; `audit_invariants` checks it.
- PostgreSQL returns a sum at the column's scale (`100.0000`), SQLite
  as `100`. A message showing a quantity formats it
  (`format(x.normalize(), "f")`).
- Before calling a release done, also run the suite with
  `DATABASE_URL=postgres://...` and with `DJANGO_TIME_ZONE=Asia/Kolkata`.
  The four `@tag("migration")` tests take minutes each on PostgreSQL;
  `--exclude-tag migration` runs the rest in about five minutes.

**The API**
- A test that calls the API as a superuser proves nothing about who
  may. Reading took no permission at all until review found it; test
  as a person in the role (`apps/core/tests_roles.py`).
- Drive a new document's actions through the API in a test, not only
  its model methods: three report endpoints crashed on their first row
  and `create_bill` on its first call, all with green model tests.
- DRF drops a field its serializer does not list, without a word.
  `audit_invariants` checks every model field is settable or listed as
  set by the system.
- `bool("false")` is True. Read a yes-or-no with `apps.core.api.flag`.
- Never replace `AuditableViewSetMixin`'s `perform_create`/`perform_update`;
  pass your own field through `super().perform_create(serializer, owner=...)`.
  Ten viewsets replaced it to word a refusal the exception handler words
  already, and lost the transaction and `created_by` with it: a job-work
  loss locked its period outside any transaction, a 500 on PostgreSQL
  only. `audit_invariants` (`writes_around_the_mixin`) reports it now.

**Migrations**
- Redirect `makemigrations` to a file and read it; never pipe it into
  `head`. Check every change names the model you meant (mistake 8).
- The autodetector puts `RemoveField` first. Any data-copy step must
  come before it: reorder by hand.
- `AddField` with a default rewrites every existing row. If the new
  default would change what existing rows compute, add at the old
  meaning and `AlterField` to the new default after.
- Test a data migration with `MigrationExecutor` in a
  `TransactionTestCase`.

**Test fixtures**
- Codes are unique: a helper that builds a tape or fabric each call
  fails the second call in a test. Reuse one (`default_fabric()`).
- Never name a test helper or attribute `run` or `order`.
- Two refusals in one test need two separate builds, each of which
  must not collide with the other.
- A test the machine's speed can decide is a defect, not a flake.
  Costing on a past day reads an edit off timestamps (a save more than a
  second after the create), so a fixture whose recipe stood before the
  day says so with `settled_before(day)` and reads the specification
  again; its own saves otherwise count as edits whenever the gate is
  loaded. Prove such a fix at the worst case (the threshold at nothing).
- Django never calls `full_clean()` for you; `ModelSerializer` does not
  run check constraints either. Guard in `save()` or the view. What gets
  past both reaches the database: `apps.core.api.exception_handler` turns
  a refused constraint into a 400 beside its field and a delete of
  something still used into a 400 in words, and each viewset write runs
  in its own savepoint so the refusal does not poison the request. That
  is the floor, not the answer: a rule worth a better sentence goes in
  `save()`.

**Browser tests**
- Playwright `evaluate` calls a function expression: wrap statements in
  `() => {...}`.
- A class mixing `TestCase` and `LiveServerTestCase` needs
  `_databases_support_transactions` returning False.
- Run with `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium`,
  or they skip and the count looks right.
- On SQLite every cursor call is serialised (`OneCallAtATime` in
  `apps/web/tests_browser.py`). Without it two requests at once deadlock
  on the GIL inside Django's date functions and the whole run hangs,
  with nothing printed. Playwright's greenlet also gives the test body a
  different connection from `setUpClass`'s; patch the class, not the
  instance.

**Mutation testing**
- Never edit sources while a harness runs; it restores from its own
  copy. If you kill one, restore the file from that copy and check it.
- For model-code mutations, a settings module with
  `TEST["MIGRATE"] = False` runs in seconds instead of minutes. Check
  the unmutated tests pass under it first, or a missing seed row reads
  as a kill.

**Two people at once**
- Every method that reads a document's state and then changes it is
  `@serialised("the", "state", "fields")` (`apps.core.models`): it locks
  the row and re-reads those fields. Ten of eleven money paths let two
  simultaneous clicks both through until this existed.
- A decision that reads other rows (what is left on a payment, due on an
  invoice, open on an order) takes `lock_rows(...)` on them first.
  It re-reads them too: an object read before the lock is stale.
- Prove it on PostgreSQL with `apps/e2e/tests_races.py`'s `race()`;
  SQLite serialises writers by accident and proves nothing.
- A method that locks opens its own `transaction.atomic`; it does not
  trust its caller to. SQLite ignores `select_for_update`, so under tests
  the sentinel refuses a lock outside a transaction the code opened, on
  every database (`LockedOutsideATransaction`).

**The office application (`frontend/`)**
- Every call goes through `src/api/client.ts`: it sends the CSRF token,
  reads the page count from `X-Total-Count`, and turns every failure into
  one `ApiError`. A screen never calls `fetch` itself.
- Money and quantities arrive as exact strings. Never `Number()` or
  `parseFloat` them; show them with `src/lib/format.ts`.
- A screen is added to `src/app/registry.ts` with the permission the
  server checks to read it, or it is unreachable. Hiding is not
  security: the API refuses whatever the page offered by mistake.
- A list field a view declares (`search_fields`, `filter_fields`,
  `date_field`, `ordering_fields`) must exist; `apps/web/tests.py`
  checks them all.
- Build before the browser tests (`npm run build`), or they skip and the
  count looks right.
- A panel that reads a second endpoint asks only if the person may read
  it (`RelatedList` takes the permission). An order page asked a rep for
  deliveries on every open; the browser tests now fail on any 403 or 404.
- Anything a handler decides after a save reads a ref, not state: state
  lags a render. The leave guard asked "lose your changes?" straight after
  Create, and a "no" left a filled form one click from a duplicate order.
- Drive each flow in the browser as the people who do it. The model and
  API tests were green while a line added from the screen had no name and
  Ship could never be answered which warehouse.

**Committing**
- Suite, `audit_invariants`, `makemigrations --check`, then commit.
  Never in one command (mistake 9).

**Agents working in parallel**
- Two fix agents each built the same shared rule: a correction-date
  rule in hr and another in core, and two deletable-posted checks. The
  merge cost a commit and nearly kept both. Name in each brief the shared
  rules that already exist, and give any new shared rule one owner.
- A "stale exemption" is a question, not a fix. A rewrite took the
  landed-cost release out of the date check's pattern while it could
  still be dated anywhere. Read the method before taking it off a list.
- Do not hide the output of a step whose failure matters (`2>/dev/null`,
  `| tail -1`): a `git worktree add` failed that way, unseen.

## Interrupted work: find it and resume it

Usage limits, container restarts and the two-hour background limit stop
work mid-step without a word. The owner's standing instruction is to look
for anything stopped abruptly and resume it from where it stopped, never
to start over or leave it.

Do this at the start of every session, and again after any limit,
restart or timeout:
- **Agents.** A task notification reading "failed: ... session limit" or
  "killed" is a stopped agent. Resume it with a message to its id (it
  keeps its transcript), saying what changed: the machine restarted,
  background jobs are gone.
- **Background jobs.** `exit=124` is the time limit, not a test result.
  A restart kills every job silently (`uptime`). Re-run the lane. A
  PostgreSQL lane drops its test databases first.
- **Worktrees.** List them with `git worktree list`. Any with commits not
  on a pushed branch, or uncommitted edits, is unfinished work: finish
  it, or push it to a `claude/erp-*` backup branch before anything else.
- **Gates.** A gate is done only when every lane printed OK on the same
  tip. Check docs/HANDOFF.md for the gate in flight.
- **Snapshots.** While agents work, keep `scripts/snapshot_worktrees.sh
  --every 600` running, detached: `setsid nohup ... &`. It pushes every
  worktree, uncommitted and untracked files included, to
  `claude/erp-wip-<worktree>`. A session paused by a usage or token
  limit can lose its container, and with it every scratch worktree; the
  snapshots are what survives. On a new machine, restore each one with
  the three lines at the top of the script. Start the loop again after
  any restart: it dies with the container.
- **Agents in a new session cannot be resumed;** their transcripts stay
  with the old one. Restore their worktrees from the snapshots, then
  start one new agent per worktree. Give it docs/handoff/fix_common.md,
  its ids from docs/HANDOFF.md, and `git diff HEAD` to see where its
  predecessor stopped.

## Auditing

Every defect seen and not fixed goes into docs/RISKS.md, "Open
defects", with an id, the day it is seen. A notes file, a commit
message or an agent's report alone is not a record: none of them is
read when choosing the next work. A row comes off only in the commit
that fixes it, naming its id.

`.claude/skills/audit/SKILL.md` holds the defect shapes three audits
found, and `python manage.py audit_invariants` checks the mechanical
half. Run both before calling a module done. The suite passing means
nothing — all 29 defects found so far were found with a green suite.
