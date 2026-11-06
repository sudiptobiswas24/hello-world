# g19 brief: fix_qdec — advisory failures wait for the Quality Manager; O145, O146

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names (it holds this brief and the probes), not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_qdec <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 150 tool calls. This round builds a small feature as well as fixes.
- Report: $SP/reports/fix_qdec.md.

## What the owner decided (docs/RISKS.md "Decisions taken", item 22)

A failed reading under an advisory plan (`InspectionPlan.is_mandatory`
False) is no longer rejected by the system. Today `Inspection.post()`
(apps/quality/models.py near 726) fills a blank disposition with REJECT
on a fail; `AdvisoryFailureProbe` in
docs/handoff/reports/review_qual_tests/tests_probe_review.py shows it.

1. Such an inspection posts as a new disposition, "awaiting the Quality
   Manager's decision". The batch is held meanwhile (`why_held` says so,
   naming the inspection and the day). A mandatory plan is unchanged.
   An inspector may not dispose of an advisory failure at posting: the
   Quality Manager owns the decision.
2. The posting answers with a stronger warning than today's, in words a
   shop-floor inspector reads: the batch fails, it is held until the
   Quality Manager decides, and who has been told.
3. Told at posting, each with a to-do on their own list
   (`apps.core.chatter.FollowUp`, linked to the inspection, due the next
   working day): every active login in the Quality Manager group, to
   decide; every active login in the Production Supervisor and Stores
   Manager groups, for information. And by mail where mail is set up
   (`apps.core.mail`). A mail not sent is said in the answer, never
   skipped silently (audit shape 7).
4. A decision: use as is (a concession), rework, or reject, with a note.
   Only a holder of a new permission the Quality Manager role carries
   (grant it in the roles' seed, so tests_roles sees it), and never
   whoever recorded the readings or raised the inspection
   (`apps.core.approvals.check_not_raised_by`, and `_signer` for the
   concession's signature, as post() does). The first decision stands:
   two at once, one wins, the other is told it was decided (prove it with
   `race()` on PostgreSQL). Deciding closes everyone's to-dos on it, as
   done by the decider.
5. Left undecided past the next working day, it goes to the
   administrator: a management command, safe to run twice (it never
   writes a second escalation for one inspection; audit shape 5), that
   gives each superuser a to-do. Use apps/hr/calendars.py for the working
   day if the architecture rules allow quality to import hr; if not, say
   why in the report and use the plain weekday. Say in your report how
   it is to be scheduled; do not edit RUNBOOK.md.
6. The reverse and edit paths, named before you write: voiding an
   awaiting inspection (its to-dos close, the batch returns to the
   verdict before it); voiding a decided one; a re-inspection posted
   while one awaits; a decision dated by `correction_date()` rules if it
   is dated at all. A concession decided later than the posting still
   needs everything post() asks of a concession today.
7. The office application: the inspector sees the warning on posting;
   the Quality Manager has the list of what awaits them (filter on the
   new disposition, oldest first, overdue marked) and the three decisions
   on the inspection's page. Add to src/app/registry.ts with the
   permission the server checks. Drive both in a browser test as the
   Inspector and as the Quality Manager (CLAUDE.md "Browser tests"; build
   first). The Inspector's attempt to decide is a 403 from the server.

## Defects (docs/RISKS.md rows; probes in the file above)

- O145 `SameDayReinspectionProbe`: two verdicts on one day are ranked by
  id; the later posting must stand.
- O146 `SplitBackdatedProbe`: the earlier-dated re-inspection rule reads
  only the lot's own inspections, not those of the lots it was re-made
  from (`lot_sources` in apps/quality/release.py).

## Shared rules

Yours: the decision (on Inspection), the telling and escalation module,
the new permission, the new disposition, apps/quality/release.py.
Existing, use them: `why_held` / `check_released` / `may_be_drawn` (one
gate), `@serialised`, `lock_rows`, `check_not_raised_by` and `authors`
(apps/core/approvals.py), `correction_date()`, `FollowUp`,
`apps.core.mail`, `apps.core.api.flag`.

Expose one function the other agent's work will call at integration:
`apps.quality.decisions.tell_the_quality_manager(record, summary, link="")`,
giving every Quality Manager a to-do (and mail) about `record`, returning
who was told and who was not mailed. Write it for any record, not only
an inspection.

The other agent (fix_sub) works in apps/purchasing on subcontract
receipts. Do not edit apps/purchasing; it does not edit apps/quality.
