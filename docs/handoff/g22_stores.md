# g22 brief: fix_stores — open rows in stores, the floor, quality and assets

Read docs/handoff/fix_common.md first; it binds you. Then read CLAUDE.md, all of it.

- **Base:** the commit your prompt names.
- **Worktree:** `git -C /home/user/hello-world worktree add --detach $SP/fix_stores <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- **Budget:** about 150 tool calls. Work in the order below. At the budget, commit what is done and report what is left.
- **Report:** $SP/reports/fix_stores.md.

The plant runs tape lines, looms, lamination, printing and conversion to bags. It weighs doffs, rolls and bales at the stations.

## Your rows, in order (read each row in docs/RISKS.md, "Open defects")

**Books and stock first**

1. **O210.** `follow_value` (inventory/costing.py) follows transfer steps only. Goods cleared out of inspection are not followed, so the share of landed freight on them is left behind.
   - The shelf must foot to the ledger after a landed cost released on goods inspected and moved (audit shape 4).
2. **O133.** `allocate()` does not subtract tape loaded on a backflushed run's creels and not yet drawn. A pick naming no lot can choose a doff already on a creel.
3. **O29.** A period closes while its depreciation is uncharged.
   - Decided: refuse it, through a period-close check that accounting keeps and assets registers. The row says how.
4. **O34.** The fixed-asset register is not footed against the ledger in the daily health checks. Add it.
5. **O12, O13, O14.** Assets:
   - `place_in_service` accepts a date still to come;
   - `depreciated_before` is not enforced on a draft made by hand;
   - the admin lets an asset's status be edited directly.
6. **O11.** Cancelling a put-away transfer leaves its receipt route move standing.
7. **O23.** The adjustment post endpoint answers with a stale `total_value`.
8. **O26.** Capitalising a bill line has no API action. Add it, driven by a test as the role that does it.
   - It may be called from purchasing, since purchasing → assets is allowed.

**Quality and the floor**

9. **O232.** One coating check off its weight holds every bag bundle of its run.
   - Tie a coating check to the roll it was taken on, so only bundles cut from that roll wait.
   - Check what the station already records about which roll was on the coater when.
10. **O231.** The "Held because" column is N+1: 248 queries for 25 batches.
    - Give the gate a page-wide form, so each provider is asked once for the page.
    - Measure it before and after, and assert the count in a test.
11. **O241.** The inspection screen still offers Concession and a releasing re-post to a Quality Inspector. Offer each only to whom the server allows. Browser test as both roles.
12. **O242.** `Inspection.post` locks the draft before its lots, where `void` and `escalate` lock the lot first. Put them in one order, and prove it with `race()`.
13. **O203.** The Quality Manager's escalation counts Monday to Friday and never sees a plant holiday.
    - Quality may not import hr. So core keeps a registry for a working-day provider, hr registers its calendar there, and quality asks the registry.
    - Do not let quality import hr.
14. **O202.** A cost sheet, a test certificate and a price-variation bill made over the API carry no created_by. Pass the person through.
15. **O45, O127.** Duplicate rules:
    - three admin mixins refuse the same thing;
    - `FROZEN_AT_RELEASE` copies the frozen-once-moved rule.
    Fold each into one, with every caller kept refused (mistake 5).
16. **O134.** tracking.py's shortfall message shows 300.0000 on PostgreSQL. Normalise it.
17. **O27.** Inventory migrations 0030 and 0037 have no migration test. Write them with MigrationExecutor.
18. **O225.** The station's scale bridge has its endpoint, but the program that reads the scale and posts to it is not in the repository. Write it under deploy/:
    - **What it does:** a small Python program that reads a serial or USB-serial weighing indicator and posts each stable weight to the station's endpoint, with the station's token.
    - **Configuration:** the frame pattern is configured, not coded. Indian indicators send continuous ASCII frames such as `ST,GS,+00123.4kg`. Ship patterns for two common shapes, and a stability rule (the same weight N times).
    - **Tests:** with recorded frames, not hardware:
      - an unstable frame is never sent;
      - a dropped connection retries;
      - a weight is never sent twice.
    - **RUNBOOK.md:** how to install it on the station PC.

## Shared rules

- **Yours:**
  - `follow_value`'s reach;
  - the page-wide gate;
  - the core working-day registry;
  - the one posted-document admin rule;
  - the scale bridge.
- **Existing; use them:**
  - the release gate (`why_held`, `lot_held`, `may_be_drawn`, `lots_made_from`);
  - `cost_of_removing()`;
  - `lock_rows`, `@serialised`, the lock order;
  - `correction_date()`;
  - `AccountingPeriod.refuse_closed()`;
  - `AuditableViewSetMixin`.
- **Other agents:**
  - fix_core works in core and hr at the same time. The working-day registry is yours. Tell them nothing; the integrator does.
  - Keep to inventory, manufacturing, quality, assets, and the period-close check's registration.
