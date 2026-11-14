# review_yearend (in progress)
Tip 7eb8ac1. Probes: apps/accounting/tests_review_yearend.py (committed on the review worktree, unpushed).
F1 (blocks): two restatements in one period stack: 15 Mar @83.9 + 31 Mar @84.1 on a USD 1,000 invoice booked 83,336.70 leaves AR at 31 Mar 84,663.30 (expected 84,100.00); probe P01 FAILS. Cause restatement.py document_item carries booked_by (own entries) so each restatement posts the difference to the booked base; the uniqueness constraint is on as_of only. Bank_item reads the ledger, so EEFC nets, documents do not.
F2 (blocks): restating a day before the period end, then paying before the period end: restate 25 Mar, pay 28 Mar: AR at 31 Mar 763.30 with nothing open, FX gain at 31 Mar 1,326.60 vs 563.30 realised; probe P02 FAILS. Reversal is at period end+1 not as_of+1.
F3 (blocks, tax): sole asset in block (opening 0, cost 100,000, in service 1 Jun 2025) scrapped 1 Feb 2026 for 0: schedule reads ceased False, closing 60,000, gain 0 (expected ceased, loss 100,000). tax.py _block_year `row.ceased = bool(row.sales) and remaining == 0` needs sales > 0; dispose() defaults proceeds=0. Probe apps/assets/tests_review_yearend.py T01 FAILS.
F4 (medium, tax): year 2025-26 filed with 2024-25 empty; asset then put to use 1 Oct 2024 (unfiled year): filed opening 100,000 vs 2024-25 closing 65,450, schedule does not foot; the asset never reaches a filed year. Guards only test the event's own year. Probe T03 FAILS.

## Clean results
- Probes P03-P09 (part-paid across reversal 485.32+727.98, credit note then paid, payable -1,213.30, EEFC, unapplied receipt applied after reversal 563.30, void after payment) PASS. P10: void into a closed period refused whole (by design). P11 refuses in words. P12 = author's limit 4 (known).
- Roles X1/X2 PASS: no role but Controller reaches any restatement/tax endpoint; a PATCHed confirmed rate is not left confirmed at the new figure.
- Races on PostgreSQL (race_in_order, both orders): R1 restatement-then-allocation and R2 allocation-then-restatement PASS.
- R3 (restatement vs payment void) written, NOT run to completion (test DB migration exceeded the time); unproven.
- File-vs-disposal race (file() locks blocks only, dispose takes no block lock): unproven, no probe.
- audit_invariants: "No invariant findings." makemigrations --check: "No changes detected."
- Not covered: Controller can create and confirm a rate (no segregation), accountant's call.
