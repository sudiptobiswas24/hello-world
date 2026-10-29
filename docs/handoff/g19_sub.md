# g19 brief: fix_sub — subcontract assemblies received and held; O147, O149, O150

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names (it holds this brief and the probes), not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_sub <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 90 tool calls.
- Report: $SP/reports/fix_sub.md.
- Probes: docs/handoff/reports/review_qual_tests/tests_probe_review.py.

## O135 and O148: what the owner decided (docs/RISKS.md "Decisions taken", item 22)

Assemblies a subcontractor sends back, made from a batch quality held
after it was sent, are received and held. Today the receipt refuses
("held by quality"); `SubcontractHeldAfterSendingProbe` shows it.

- The receipt is recorded and the component batch is consumed where it
  lies (at the subcontractor): it went before the verdict, and the
  processing charge is owed either way.
- The assemblies take the hold. Where the assembly is tracked by lot,
  through lineage: register a provider with
  `apps.quality.release.register_lot_sources` (or the inventory hook
  that already does this for splits and joins) so a held source holds
  the assembly lot until it is inspected itself. Where it is not tracked
  by lot, the receipt must go to a quarantine warehouse
  (`Warehouse.is_quarantine`): refuse any other destination with a
  sentence naming why. Either way nothing can issue or ship it until
  quality decides.
- Only a quality hold is let through at the subcontractor. Expiry and
  every other refusal stay as they are.
- The probe uses an advisory plan's failure to reject the batch. The
  other agent is changing exactly that (an advisory failure will await a
  decision instead), so build your test's rejection with a mandatory plan
  or an explicit reject disposition, keeping the probe's numbers.
- The reverse path, named before you write: a return of those held
  assemblies to the subcontractor, and a void of the receipt, must put
  back what was taken, as for any receipt.
- Leave the receipt's answer saying the assemblies are held and why. The
  Quality Manager is to be told; that call is wired at integration to the
  other agent's `apps.quality.decisions.tell_the_quality_manager(record,
  summary, link="")`, which does not exist in your worktree. Do not build
  your own; say in the report where the call belongs.

## Defects (docs/RISKS.md rows)

- O147 `SubcontractQuarantineProbe`: `issue_components` never gives the
  gate a warehouse, so components leave a quarantine bay; `pack`
  (apps/manufacturing/bales.py near 202) passes no warehouse either.
  Share the check so the next outbound path cannot be written without
  the warehouse (`audit_invariants` has a release-gate check; widen it).
- O149 `SubcontractReturnByBatchProbe`: a return gives each batch its
  proportional share rounded to 2 places; returned one at a time, each
  batch must get back exactly what it gave. Total first, divide last.
- O150 `ComplaintLotRaceProbe`: a batch added to a complaint as it is
  rejected both finish. The batch's add and removal hold the complaint
  first, as O118 did for actions. Prove it with `race()` on PostgreSQL.

## Shared rules

Yours: the subcontract consumption and its lineage provider, the
outbound-warehouse check. Existing, use them: `lot_held` and
`register_lot_hold` (apps/inventory/tracking.py), the release gate's
`check_released` (do not edit apps/quality; the other agent owns it),
`answer_to_its_document()`, `@serialised`, `lock_rows`,
`priced_to_give_back()` and `value_left()` (apps/accounting/mixins.py),
`cost_of_removing()`.
