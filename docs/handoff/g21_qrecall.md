# g21 brief: fix_qrecall — the owner's decision 23 (O204, O207)

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names, not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_qrecall <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Budget: about 130 tool calls.
- Report: $SP/reports/fix_qrecall.md.

## What the owner decided (docs/RISKS.md "Decisions taken", items 22 and 23)

O204. A failure under a MANDATORY plan at the machines (a doff, a film
roll, a bag bundle) no longer goes out on the supervisor's concession at
the station (`apps/manufacturing/station_gauge.py` `gauged_batch`, and the
film and bag paths). It waits for the Quality Manager exactly as an
advisory failure does under decision 22: Disposition awaiting,
`apps.quality.decisions.tell_on_posting`, the Quality Manager's `decide()`,
the escalation. The station's answer says the batch is held and who was
told. Decide, and say in the commit, what becomes of the supervisor's PIN
and reason at the station: kept as the floor's note, never as a concession.
The lab-to-finish path is the same.

O207. A recall. When a batch is rejected (or comes to await a decision)
after lots were made from it, what is still in stock of those lots is held,
and the Quality Manager is told once, naming every lot and where it is.
- The lineage is `apps.quality.release.lot_sources` (splits, joins,
  re-batches, subcontract assemblies, and whatever else registers). Follow
  it downward from the batch, as far as it goes.
- A lot that passed its own inspection is held too: its pass predates what
  is now known about what is inside it. The hold lifts only by the Quality
  Manager's decision on that lot (decide(): use as is, rework, reject) or
  by the source batch's verdict changing back.
- Nothing already shipped or consumed is touched; say in the Quality
  Manager's to-do what has already gone, by document, so they can call it
  back if they choose.
- The gate (`why_held`) gives the reason in words: "made from F-1, rejected
  on 2 February (QC-12)".

The owner also asked: stock lists keep showing those lots, with an extra
column saying why each is held. Do this for every hold, not only a recall:
one column ("Held because") on the stock screens that list lots on hand
(find them under frontend/src/modules/stores and the API behind them),
filled from the gate's own words (`why_held`), empty for a lot that may go.
Add it to the API as a read-only field, computed, not stored. Drive it in a
browser test as the Stores Manager.

## Mirrors and reverses, name each before writing

- The source's rejection voided: the recalled lots return to what they read
  before (a lot with its own pass reads released again).
- The source re-inspected and passed (after a rework): the same.
- A recalled lot split or joined while held: its pieces stay held.
- A recalled lot the Quality Manager released: a later recall of another
  source holds it again.
- Two rejections at once on two sources of one lot: one to-do, not two
  (race() on PostgreSQL if the telling can double).

## Shared rules

Yours: the recall, the "held because" column. Existing, use them: the
release gate (`why_held`, `check_released`, `may_be_drawn`, each with its
warehouse), `lot_sources`, `latest_inspection`, `apps.quality.decisions`
(`tell_the_quality_manager`, `tell_on_posting`, `close_to_dos`,
`escalate_overdue`), `Inspection.decide()`, `@serialised`, `lock_rows`,
the lock order (apps/core/lock_order.py; another agent is working on it
now: if you need a new rank, say so in the report rather than editing
LOCK_ORDER).

Test every rule as a person in the role through the API.
