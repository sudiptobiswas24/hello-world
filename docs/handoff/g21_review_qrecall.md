You are review_qrecall: an adversarial, read-only review for gate g21 of a Django 5.2 + DRF ERP for a woven polypropylene sack plant in West Bengal. Repository /home/user/hello-world; read its CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

Make a worktree of the integrated tip: `git -C /home/user/hello-world worktree add --detach $SP/review_qrecall aa49719`. Link `.venv` and `frontend/node_modules` from /home/user/hello-world. Work only there. Never edit product code; you write probes (tests that assert what SHOULD be true) and commit only them, in that worktree, unpushed.

What you review: the owner's decision 23 and O123, as built by fix_qrecall. These commits are in the tree, oldest first: 84d12ed (O204), d2428eb (O207), b2295e6 (O123), 3175e79 (O204, the rest), 59ac724 (O123, the lock), f9b086a (O207, the runs). The author's report is at $SP/reports/fix_qrecall.md and the brief at docs/handoff/g21_qrecall.md. The owner's words are in docs/RISKS.md "Decisions taken", items 22 and 23:
- a failure at the machines waits for the Quality Manager, whatever the plan;
- a recall holds what is still in stock of lots made from a batch rejected later, and the Quality Manager is told once;
- stock lists show a "Held because" column.

Try to break it. Lead with what would let a held lot move, or a recall be missed or made twice. In particular:
1. **Every way out of a station at the machines.** Doff, film roll, bag bundle, lamination, print, coating, and lab-to-finish. Can any off-limits batch still go on through a supervisor's PIN, a concession field, an older endpoint, the admin or a bulk path? Is the PIN still only the floor's note everywhere, with nothing it writes read as a decision?
2. **The recall's lineage.** It is registered both ways, through re-batches, subcontract assemblies and production runs, four levels deep. Look for:
   - a path that makes a lot from another one and is not registered (regrind, by-products, job work, a return to stock from a run, a split of a recalled lot);
   - a loop (regrind back into tape);
   - the depth limit hiding something without saying so.
3. **The hold is derived, not stored.** Prove every reverse a lot can come back from:
   - the source's rejection voided;
   - the source passed again;
   - the Quality Manager's own decision on the lot;
   - a second source rejected after the lot was released;
   - a lot split while held.
   For each, check what the gate, the "Held because" column and the certificate read.
4. **Told once.** One to-do per recall, closed when it lifts. Check two rejections at once, a rejection voided and posted again, and the escalation (`escalate_overdue`) of a recall whose source no longer holds it. Prove each race with `race()` on PostgreSQL (apps/e2e/tests_races.py; `DATABASE_URL=postgres://erp:erp@localhost:5432/review_qrecall`, drop test_review_qrecall first). SQLite proves nothing about races.
5. **Lock order.** The recall locks lots, then lifts `on_commit` in its own transaction. Look for a lock taken before what the order puts first, a lock outside a transaction, and the lift racing a new verdict.
6. **O123: `GoodsReceipt.accept`.** Every path out of a quarantine bay asks the gate under the batch's lock: a routed inspection step, a transfer, a subcontract assembly, the API, the admin.
7. **Who may.** Test as the roles over the API (apps/core/tests_roles.py): Quality Inspector, Quality Manager, Stores Manager, Production Supervisor. Can anyone but the Quality Manager decide a recall, void one, or lift it by voiding the source?
8. **The "Held because" column.** Is it filled from the gate's own words, for every hold, on every stock screen and API that lists lots on hand (not only the Batches screen)? Is it empty for a lot that may go, and does it show warehouse by warehouse? Is the cost bounded (O231 is registered as N+1; check nothing is worse than that)?

Method: a probe per claim, stated as a fact about stock or state, and PASS/FAIL per check. Number the findings in your report. For each one give file:line, a concrete scenario with numbers and names, and whether it blocks the gate. A finding needs a probe that fails on the tip; a suspicion without one is listed separately as unproven. Run audit_invariants and makemigrations --check on the tip and say what they print.

Budget: about 80 tool calls. Report to $SP/reports/review_qrecall.md, and 10 lines back: findings by severity, blockers first.
