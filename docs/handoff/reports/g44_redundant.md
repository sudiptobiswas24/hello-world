# g44_redundant: redundant-code audit at e141989 (read-only)

**What came of it (16 November 2026, the owner's decisions; the integrator's checks).** Kept as the list the
owner asked to hold: nothing below marked "remove" has been removed.
- Dead code (R1-R7, U-rows, the re-exports, the dead lines in D1-D3, the M12 alias): **remove nothing yet**, the
  owner's decision. The list stays here for when that changes.
- D1 and D2 are dead lines, not defects: every field they name is `editable=False` on its model, so
  ModelSerializer already makes it read-only. D3's dead `validators = []` means a duplicate energy meter pair is
  refused in DRF's words rather than the model's. Not a correctness defect.
- D4 was real and is fixed: morning_checks escalates overdue quality decisions (O594, g44).
- The 39 .py probe and test copies under docs/handoff/reports were removed (g44); docs/handoff/reports/README.md
  names the commit each is still in.
- Merged (g44, no behaviour change): M1+M2 (core.require_role, core.names_another), M3+M4
  (RealisedOnItsOwnDay's save and locked_before_it; settlement.LateByItsInstallments), M7-M9
  (AuditModel.write_past_its_guard, twenty copies; audit_invariants guard_bypasses_by_hand), M11 (core month_end).
  Not merged: M5, M6, M10, M13 (reasons in docs/HANDOFF.md, g42 to g45), M12 (a removal, deferred with the rest).

Worktree /home/user/work/g44_redundant, detached at e141989. Nothing in the repository was edited.
`audit_invariants` was run once at `nice -n 19`: **"No invariant findings." exit 0**. Its inert-feature checks
(`uncalled_helpers`, `uncalled_model_methods`, inert settings) see none of the rows below. The section "Why the
gate missed these" says why.

## How each candidate was proved (tools and counts)

- **pyflakes 4.1** (installed in the scratchpad, not the venv) over apps, config, scripts, deploy, manage.py:
  668 messages. 558 "imported but unused": 190 in tests, 368 in production. Each of the 368 was checked by a
  script against every `from <module> import <name>`, every sibling `from .<module> import`, every
  `<module>.<name>`, and every dotted string (`mock.patch("apps.x.y.name")`):
  132 re-exported (56 of them imported only by tests), 185 model-registration imports in models.py, and
  **50 imported by nothing**, with no dotted-string reference.
- **vulture 2.16** over the production files (tests and migrations left out), with confidence 60 or more:
  2,081 hits. Every class, function, method and property hit was then counted by word across the whole tree in
  five areas: prod, tests, migrations, frontend/src, and other (docs, scripts, deploy, RUNBOOK, CI).
- **AST reference count** (`astrefs.py`): any module-level def or class in production with no Name, Attribute
  or string-constant reference, with comments and docstrings not counted. It found **3 unreferenced** (R1-R3)
  and **2 test-only** (U8, M12).
- **Duplicates** (`dupes.py`): 74 module-level names defined in more than one production module, and
  15 groups of functions whose bodies are identical (4 or more statements), all read by hand. Most
  shared names are different things that happen to share a name (`money_of`, `shift_starts`,
  `whole_number`, `_money`); those are not reported.
- **Commented-out code**: AST-parsed every comment line. One hit, and it is prose (audit_invariants.py:1399).
  **None found.**
- **Backup copies** (`git ls-files | grep -Ei 'bak|_old|old_|copy|orig|backup|legacy'`): none in the
  repository. Each hit was read: `old_supply` is a live feature, `tests_backup.py` tests deploy/backup.sh,
  and the migrations are data migrations.
- **Frontend** (`fe.py`): every file in src/ is imported by something; no file is orphaned. 47 exports are
  used only inside their own file (U9). All 295 `/api/<app>/<route>` prefixes the frontend names resolve to a
  registered route.
- **Settings**: every custom setting in config/settings.py is read somewhere (`AS_OWNER` is a local of
  settings.py), and so is every key in .env.example.
- **Templates and statutory CSVs**: each one is named by code, except `registration/login.html` (S7).
- **Management commands**: each was counted across prod, tests, ops (RUNBOOK, deploy, CI) and docs. See U5,
  U6 and D4.
- **scripts/gate**: each script is named by lane.sh, CI or the README, except `nosentinel.py` (U2).

Classes used below: **remove**, **must stay**, **merge**, **unsure**. Rows marked **DEFECT** hold dead code that
hides intended behaviour. Removing them would delete the intent, so they must be fixed instead. Each needs a
docs/RISKS.md row from the integrator.

---

## Defects hidden by dead code (class: must stay; move the line, do not delete it)

| # | file:line | what | origin | evidence | risk |
|---|---|---|---|---|---|
| D1 | apps/quality/serializers.py:165 | `read_only_fields = ["number","posted","posted_at","voided_at","voided_reason"]` sits **after `return`** in `CalibrationSerializer.get_suspects`, so it never runs. `Meta` (149-155) has no read_only_fields. | written there from the start, 921d371 (2026-09-28) | vulture "unreachable code after 'return'" (100%), pyflakes "local variable assigned but never used". `Calibration.save()` (calibration.py:136) refuses only once the *stored* row is posted, so a PATCH or POST of `posted: true`, `number` or `voided_at` on a draft goes through and skips `post()`'s checks. | **DEFECT**: move it into Meta. Deleting it closes nothing. |
| D2 | apps/quality/serializers.py:37 | `read_only_fields = ["derived_from"]` after `return` in `PlanLineSerializer.get_characteristic_label` | 86f1a15 (2026-09-22) | same two tools. `derived_from` can be written through the API. | **DEFECT**: move it into Meta. |
| D3 | apps/manufacturing/serializers.py:363-365 | `validators = []` (with its comment "the model's own save says why a pair is wrong") after `return` in `EnergyMeterSerializer.get_serves` | d23c22f (2026-09-28) | same two tools. The model's message is not given; DRF's generic one is. | **DEFECT**, minor: move it into Meta. |
| D4 | apps/quality/management/commands/escalate_quality_decisions.py | Its docstring says "Run once a day by cron, before the morning checks". Nothing runs it. | 10af63a (2026-10-14). The cron line was handed off in docs/handoff/reports/fix_qdec.md:110 "for whoever edits RUNBOOK.md". | `git grep escalate_quality_decisions -- RUNBOOK.md deploy docker-compose.yml .github` returns 0. The RUNBOOK cron lines (337, 350, 390) are morning_checks, generate_recurring and check_health only. morning_checks does not escalate. | **DEFECT** (inert feature, mistake 1): decision 22's escalation never happens in production. Wire it (a RUNBOOK cron line, or call it from morning_checks). |

## Remove

### core
| # | file:line | what | replaced by | evidence | risk |
|---|---|---|---|---|---|
| R4 | apps/core/management/commands/audit_invariants.py:1922-1923 | `if False:` / `pass`: a dead branch inside `untested_corrections` | dead since 790f878 (2026-09-20) | vulture "unsatisfiable 'if' condition" (100%) | none |
| R5-core | saved_filters.py:17 `serializers`; second_factor.py:43 `login`; views.py:5 `serializers` | unused imports | | pyflakes. 0 importers from these modules, 0 dotted-string references. | none |

### sales
| # | file:line | what | replaced by | evidence | risk |
|---|---|---|---|---|---|
| R1 | apps/sales/serializers.py:231-232 | `class BackorderMixin(serializers.Serializer): pass`, an empty class | never used. Added empty in 93358f9 (2026-09-19); `git log -G BackorderMixin` shows only that commit. | `git grep -w BackorderMixin` returns 1 line (its definition). AST refs: prod 0, tests 0, migrations 0, frontend 0. | none |
| R5-sales | models.py:30 `money` (the one use, line 6428, imports it again locally); models.py:98 `slice_of`, `settlement_discount_to_take`; views.py:4 `transaction` (1135 imports it locally); views.py:38 `outstanding_balance`; third_party.py:45 `transaction` | unused imports: what is left of moved code | `settlement_discount_to_take`'s last use left in 6c800bd (O109, one function for both sides); `outstanding_balance` in views in 7fd5a05 (O77) | pyflakes, plus the importer and string check | none |

### purchasing
| # | file:line | what | replaced by | evidence | risk |
|---|---|---|---|---|---|
| R5-purch | models.py:76 `lock_position`; models.py:87 `slice_of`, `standing_allocations`, `settlement_discount_to_take`; models.py:7698 `authors`; pricing.py:16 `Decimal`; tds.py:188 `Bill` | unused imports | `settlement_discount_to_take`: 6c800bd (O109). `Bill` in tds: bc3134f. | pyflakes, plus the importer and string check. audit_invariants matches `lock_position(` as a call (line 422), so it does not depend on the import. | none |

### inventory
| # | file:line | what | replaced by | evidence | risk |
|---|---|---|---|---|---|
| R3 | apps/inventory/costing.py:360-367 | `value_still_held(movement)`, a wrapper that sums `where_value_is` | `where_value_is` / `follow_value`, which LandedCostApplication.release calls directly (purchasing/models.py:9237, 9240), since a35285e (O42) | AST refs: prod 0, tests 0, migrations 0, frontend 0. It is named only in a docstring (purchasing/models.py:9579) and two docs. The docstring mention is why audit_invariants misses it. | none. Change that docstring to name `where_value_is`. |
| R5-inv | adjustments.py:32 and transfers.py:30 `lock_position`; picking.py:25 `bins_holding`; variants.py:27 `Q` | unused imports | | pyflakes, plus the importer and string check | none |
| R6-inv | apps/inventory/models.py:1321 `describe_plan`, 1341 `reserved_at` | re-exported from models.py, imported from there by nothing | | importer check finds 0 | none (see U3 for describe_plan itself) |

### imports
| # | file:line | what | replaced by | evidence | risk |
|---|---|---|---|---|---|
| R2 | apps/imports/cutover.py:198-199 | `_month_end(day)` | never called. Added in ac18b53 (2026-10-07), and `-S"_month_end("` shows no later change. `hr.calendars.month_end` does the same thing. | `git grep -w _month_end` returns 1 line (its definition). AST refs are 0 everywhere. audit_invariants skips names that start with `_`. | none |
| R5-imp | cutover.py:114 `Tax` | unused import | | pyflakes | none |

### manufacturing
| # | file:line | what | evidence | risk |
|---|---|---|---|---|
| R5-mfg | admin.py:38 `WorkOrderSubstitute`; changeover.py:41 `ValidationError`; costing.py:37 `transaction`, :38 `Q`, :144 `default_bom_for`; energy.py:37, outside.py:38 and rebatch.py:45 `transaction`; jobwork.py:37 `defaultdict`; rolls.py:430 `lots_at`; settings_api.py:3 `serializers` | pyflakes, plus the importer and string check | none |
| R6-mfg | apps/manufacturing/models.py:12-121: 34 function re-exports nobody imports from `apps.manufacturing.models`: bom (`default_bom_for`, `byproduct_value`, `explode`, `material_balance`, `planned_cost`, `net_requirements`), demand (`consumed_lots`, `coverage`, `genealogy`, `runs_that_made`, `uncovered`), explain (`explains`, `measured`, `output_lots`), oee (`availability`, `by_machine`, `by_operator`, `by_shift`, `effectiveness`, `performance`), rolls (`by_machine as rolls_by_machine`, `metres_on_hand`, `rolls_at`, `unrolled_stock`, `weighed_gsm`), costing (`against_actual`, `explain`), `raise_change`, `machines_in`, `due_now`, `tools_for`, `wearing_out`, `capacity_report`, `routings_for` | A compatibility re-export nothing needs. The importer check (from-imports, sibling `.models` imports, `models.<name>`, tests included) finds 0. Keep every **model class** in that block (S2). | low: low value, and a shell script outside the repo could break |

### planning, hr, accounting, quality, gst, deploy
| # | file:line | what | evidence | risk |
|---|---|---|---|---|
| R5-plan | capacity.py:50 `ValidationError`; models.py:31 and mps.py:31 `transaction`; mrp.py:56 `Q`; promise.py:40 `calendar_offset`, :180 `safety_stock` | unused imports (pyflakes, plus the importer check) | none |
| R6-plan | planning/models.py:37 `coverage`, `forecast_demand` (keep `Forecast`) | re-exported, imported by nothing | none |
| R5-hr | payroll.py:59 `figure_as_of` (last use left in f987d8e); people_api.py:23 `Employee`; timesheets.py:29 `lock_rows`, :32 `parse_working_days`; views.py:2 `get_object_or_404` | unused imports | none |
| R5-acct | reports.py:21 `defaultdict`, :29 `round_money`; restatement.py:41 `settings` | unused imports | none |
| R5-qual | calibration.py:37 `transaction`; settings_api.py:3 `serializers` | unused imports | none |
| R5-gst | gstr2b.py:21 `re` | unused import | none |
| R5-deploy | deploy/scale_bridge/scale_bridge.py:32 `base64` | unused import in the station-PC bridge | none |
| R7 | sales/models.py:1198, 3352, 6125, 6204, 6606; purchasing/models.py:1427, 2386 | `x = Model.objects.create(...)` where `x` is never read (pyflakes "assigned to but never used") | cosmetic. **Drop the name only and keep the create() call.** |

## Merge (two implementations of one rule: mistake 5, "share it, so one fix closes both")

| # | locations | what | evidence | risk |
|---|---|---|---|---|
| M1 | hr/models.py:42, purchasing/models.py:130, sales/models.py:6322 (`_require_employee_role`, identical ×3); purchasing:135 `_require_vendor_role`; sales:136 `_require_customer_role` | one rule, "the party holds role X", written 5 times | dupes.py: same name and same body. Each is used once in its own file (`git grep -c` returns 2 per file). | low: one `require_role(party, role)` in core, which owns PartyRole |
| M2 | purchasing/models.py:140, sales/models.py:141 | `_names_another`, identical | identical-body hash | low |
| M3 | purchasing/models.py:6238 `BillPayment.save` and sales/models.py:3969 `InvoicePayment.save` | identical bodies (lock, full_clean, re-post FX on its own day) | identical-body hash. Both classes already share the mixin `RealisedOnItsOwnDay`. | medium (a money path): move it into the mixin and test both sides |
| M4 | purchasing/models.py:4268, sales/models.py:2930 | `days_overdue`, identical | hash | low |
| M5 | purchasing/views.py:167/181/236/246 and sales/views.py:218/227/282/292 | order viewset actions `approve`, `cancel`, `close_short`, `reopen`, identical (the approval-withdrawal hole of mistake 5 lived in exactly this kind of copy) | hash | low: an order-actions mixin |
| M6 | manufacturing/views.py:1976, purchasing/views.py:96, sales/views.py:722 | `pdf` action, identical ×3 | hash | low |
| M7 | manufacturing/inward.py:100, manufacturing/rebatch.py:98, planning/mps.py:96, quality/calibration.py:148, sales/third_party.py:105 | `_write(fields)`, the posted-guard bypass, identical ×5 | hash | low to medium: it is a guard bypass, so one shared copy is easier to audit |
| M8 | hr/appraisals.py:77, hr/expenses.py:108, hr/recruitment.py:167 | `_move`, identical ×3 | hash | low |
| M9 | manufacturing/alerts.py:104, manufacturing/complaints.py:127 | `_decide`, identical | hash | low |
| M10 | manufacturing/bom.py:220, quality/models.py:246 | `computed_by`, identical | hash | low |
| M11 | hr/calendars.py:221 `month_end`, planning/history.py:58 `month_end` (and the dead R2) | one date rule written three ways | read by hand | low |
| M12 | sales/models.py:4259 `committed_balance` | an alias that returns `committed_in_base(customer)` | made an alias in c6da931 (O416) | prod callers 0, test mentions 20, docs probes 7. **Test-only.** Point the tests at `committed_in_base`, then remove it. | low |
| M13 | sales/models.py:4269 `outstanding_in_base` | an alias of `outstanding_balance` ("the name the credit screens ask by") | both used in prod (5 mentions each) | low priority |

## Must stay

| # | file:line | what | why it stays | evidence |
|---|---|---|---|---|
| S1 | apps/core/trigram.py:25 `trigram_indexes` | vulture: "unused function" | **a migration imports it** | 8 mentions in migrations (accounting 0016, inventory 0033, purchasing 0044, …) |
| S2 | the import blocks at the foot of each models.py (~185 names pyflakes calls unused), and core/models.py:1994 `from . import session_limits` | model registration | the import loads the submodule, so Django registers its models. `session_limits` registers the sign-in receiver. | unused_imports.py class "models-registration" |
| S3 | core/roles.py:526, 552, 563, 576, 594; core/second_factor.py:362; core/session_limits.py:147 | vulture: "unused function" | `@receiver` signal handlers | read |
| S4 | 113 admin and AppConfig classes | vulture: "unused class" | `@admin.register` and INSTALLED_APPS | read |
| S5 | manufacturing/station_views.py:505, 539, 550, 565, 582, 671, 718 (`roll_off`, `void_tape`, …) | vulture: "unused method" | `@action` endpoints called from the station page | manufacturing/templates/manufacturing/station.html:223 `const API = "/api/manufacturing/stations/…"`; scale-readings from deploy/scale_bridge/scale_bridge.py:249 |
| S6 | routes core `api-keys`, `integrations`, `party-roles`; hr `gratuity-payouts`; gst `itc04`; manufacturing `stations`, `scale-readings` | no frontend string names them | API-only by design. apps/web/tests.py:445-456 lists them with reasons. | fe route check |
| S7 | apps/manufacturing/templates/registration/login.html | 0 references by name | LoginView's default `template_name`. `SignInView` (core/second_factor.py:392) sets none. | read |
| S8 | purchasing/models.py:8137-8250 `_legacy_consumption` | a "legacy" branch | reads records from before the fact was recorded ("posted is immutable") | read |
| S9 | apps/accounting/old_supply.py, gst/tests_old_supply.py, purchasing and sales migrations 0047_old_supply_notes | named "old" | a live feature: GST notes against old-system documents, shared by sales and purchasing | 6 code references |
| S10 | calc_audit2_floor/expected.py, calc_audit2_stores/fifo3.py (repo root) | stray scripts | where the tests' expected figures come from (CLAUDE.md: scenario script first) | cited by manufacturing/tests_audit2_floor.py:3 and inventory/tests_audit2_stores_money.py:171. They could move under docs/, but must not be deleted. |
| S11 | sales/models.py:4535 `render_statement_pdf` | a shim delegating to documents.py:463 | its one caller is `email_statement` (models.py:4565). A lazy import to avoid a cycle. | `git grep -w` |
| D1-D4 | above | | fix them; do not remove them | |

## Unsure (the owner's call)

| # | what | evidence | note |
|---|---|---|---|
| U1 | docs/handoff/reports/{probes,review_*_tests}/*.py: **39 archived probe and test copies, 9,308 lines**, "the old versions saved" | kept on purpose ("the audit rounds' probes kept beside their reports", 784af1c, ddc3ec2). Not under apps/, so no lane runs them. One test comment cites one of them (purchasing/tests_returns_and_landed.py:1173). | Safe for the product to remove, but that loses the audit trail. They also go stale, since they name helpers like `committed_balance`. |
| U2 | scripts/gate/nosentinel.py | `git grep nosentinel` returns 0 outside the file; not in README.md, lane.sh or CI. Moved there in e78fb3d. | a manual PostgreSQL race settings module: document it in the README or remove it |
| U3 | inventory/picking.py:136 `describe_plan` | prod callers 0, frontend 0. Exempted in audit_invariants `CALLED_FROM_OUTSIDE` "for a pick list a UI will render", and no UI does. | an inert helper: wire it or remove it |
| U4 | hr/timesheets.py:427 `hours_by_account` | callers 0. The exemption is keyed **`inventory.hours_by_account`**, the wrong app. The check passes only because the exemption string itself counts as a mention. The stale-exemption check covers 3-part method keys only. | fix the key or remove the helper |
| U5 | commands plan_run, roll_costs, can_we_promise (prod 0, tests 1, ops 0, docs 0) and run_mrp (docs 1) | CLI tools for planners. "A terminal is faster than a screen nobody has built yet." | screens may now exist |
| U6 | inventory/management/commands/fold_valuations.py | prod 0, tests 1, ops 0, docs 0. Migrations 0030/0037 refold the ledger at migrate time. | possibly superseded by those migrations; read before removing |
| U7 | function-local imports that repeat a module-level import (pyflakes "redefinition"): hr/models.py:333, 1057, 1059, 1090; assets/models.py:304, 452; accounting/models.py:630; inventory/models.py:1033, 1229; purchasing/models.py:5159, 5160; sales/views.py:1135; sales/models.py:6428 | harmless, and often a circular-import habit | low value |
| U8 | core/documents.py:411 `printed_text` | 15 test mentions, 0 in prod | **test-only, not dead.** It lives in a production module. |
| U9 | 47 frontend exports used only in their own file or in tests (`fe.py`) | no orphan files | `export` is superfluous. Nothing dead. |
| U10 | core/approvals.py:319 `in_full`, core/audit.py:123 `reads_only_some`, core/history.py:71 `_raw_delete` | used by tests only | test-only |

## Why the gate missed these (audit_invariants gaps, for whoever owns it)
- `uncalled_helpers` skips names beginning `_` (R2). It counts comment and docstring mentions as calls (R3) and
  exemption strings as calls (U4). The stale-exemption check ignores 2-part keys (U4).
- Nothing checks classes (R1), statements after `return` (D1-D3), or whether a cron-only command is
  scheduled (D4).

## The "remove" rows I am surest of
1. **R1** `BackorderMixin` (sales/serializers.py:231): an empty class, 1 mention in the tree.
2. **R2** `_month_end` (imports/cutover.py:198): 1 mention, dead from birth.
3. **R4** `if False: pass` (audit_invariants.py:1922).
4. **R3** `value_still_held` (inventory/costing.py:360): 0 code references. Change the docstring at
   purchasing/models.py:9579 to name `where_value_is`.
5. **R5** the 50 unused imports: pyflakes, with 0 importers and 0 dotted-string references for each.

Not removals, but more urgent than any of them: **D1, D2, D4.**
