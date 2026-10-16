# Review of the trading fixes O137-O141 (63d2b34, 364cd8b, 72d98ef, 016ef46, 88a8dd9)

Reviewed at 88a8dd9 in a detached worktree. Nothing was fixed. Probes: `probes/tests_probe_trade2_sales.py`, `probes/tests_probe_trade2_purchasing.py` in this folder (copies of apps/sales/tests_probe_trade2.py and apps/purchasing/tests_probe_trade2.py).

## Baseline
- The earlier reviewer's probes (apps/sales and apps/purchasing tests_probe_review.py): 9 tests, all pass on SQLite (2 skipped as races) and all 9 pass on PostgreSQL. So the five rows O137-O141 as first described are closed.
- Touched modules once, fastsettings, nice 19, --parallel 2, `--exclude-tag migration`: `apps.sales apps.purchasing apps.core apps.accounting`, 2224 tests, 11 failures, 4 skipped. All 11 failures are in `apps/sales/tests_probe_audit.py` (the earlier audit's probe file I copied in so the review probes import; it asserts open defects such as O69). No product test fails. Log: trade2_suite.log.
- `audit_invariants`: "No invariant findings."

## Defects

### D1. A return that credits a multi-order invoice takes the orders in the wrong order: deadlock (race) - PROVEN on PostgreSQL
- Commits 63d2b34 + 016ef46. Files: apps/sales/models.py:4100 (Delivery.locked_before_it holds only the delivery's own order), :4519 create_return, :4436 _credit_returned_goods, :2491 Invoice.locked_before_it (the invoice's orders, sorted by pk). Purchasing mirror: apps/purchasing/models.py:5765 (receipt), :6504 create_return, :6411 _debit_returned_goods, :3524 Bill.locked_before_it.
- Scenario: one posted invoice bills lines of orders O1 and O2 (an invoice with no sales_order whose lines name different orders of one customer; rule B allows it and Invoice.post locks "every order a line bills"). Return a delivery of O1 and a delivery of O2 at the same moment. Each return holds only its own order (and the stock position), then create_credit_note takes [O1, O2] in pk order. One holds O1 and wants O2, the other holds O2 and wants O1 (or the stock position the first holds).
- Proof: `TwoReturnsOnAConsolidatedInvoice.test_two_returns_on_two_orders_of_one_invoice_do_not_deadlock` with race((StockMovement, Invoice), ...) on PostgreSQL: `['done', 'refused: deadlock detected ... while locking tuple (0,1) in relation "inventory_stockposition"']`. Log: trade2_pg_race.log. The written order promises "orders, then documents"; a return does not take the orders of the document it is about to correct before it starts. The same shape: Invoice.post of another invoice that bills O1 and O2 (holds O1 then O2) against a return of O2 (holds O2, wants O1 through the credit note).
- Kind: race (the O137 shape again, one level down; the test added for O137 uses one order).

### D2. A note against a note posts (books, rule)
- Commit 016ef46 (rule B's correcting half). apps/sales/models.py:2476 `_check_kind` does not refuse `credits` being itself a credit note; only create_credit_note() refuses (2760). apps/accounting/mixins.py:90 refuse_correcting_another_line only asks that the line is on the document the note names. Purchasing: apps/purchasing/models.py:3151 `_check_kind`, same.
- Scenario: invoice 10 x 100; credit note N1 for 5; a typed Invoice with credits=N1 and a line with credits_line=N1's line, quantity 5, posts. AR 0 on an invoice of 1,000 that was credited once for 500 and again for 500, and the order line reads net invoiced 0. Debit note against a debit note posts the same (payable 0 on a bill of 50).
- Probes: `CreditByValueProbes.test_a_note_against_a_credit_note_is_refused`, `DebitNoteAgainstANote.test_a_debit_note_against_a_debit_note`.

### D3. A typed note is not held to the price of the line it gives back (books)
- Commit 016ef46. apps/accounting/mixins.py:123 refuse_correcting_past_what_it_holds counts quantity only. A note line with credits_line = the 10 x 100 line, quantity 10, unit_price 1,000 posts: AR -9,000 on an invoice of 1,000 (credit of 10,000). "By value, not quantity" is unguarded; only claims (credit_claim, capped by `left`) and the default path (copies the price) are safe.
- Probe: `CreditByValueProbes.test_a_typed_note_at_ten_times_the_price_is_refused`. Related and already registered: O69 (a claim then a full quantity credit gives back 1,300 of 1,000; reproduced again: AR -100 on 1,000 after claim 100 + whole credit).

### D4. A typed note line may name no order line, so the order line stays billed (books)
- Commit 016ef46. apps/accounting/mixins.py:111 `if line.order_line_id and line.order_line_id != corrected.order_line_id` only checks a line that names one. A typed non-claim note line with credits_line and no order_line posts; SalesOrderLine.quantity_invoiced() (apps/sales/models.py:1771) nets notes by the note line's own order_line, so the order line still reads invoiced 10 after a full credit (AR 0). It cannot be re-billed and the order never reads uninvoiced. The note should take the order line of the line it gives back.
- Probe: `TypedNoteWithoutItsOrderLine`.

### D5. A drop-ship purchase order can be re-pointed at another sales order after its receipt (books)
- Commit 72d98ef froze `PurchaseOrderLine.sales_order_line` but not `PurchaseOrder.drop_ship_for` (apps/purchasing/models.py:1714 field, :1776 FROZEN_ONCE_MOVED = vendor, currency only; writable in apps/purchasing/serializers.py:70). The return looks the delivery up by `delivery__sales_order=self.purchase_order.drop_ship_for` (:6156).
- Scenario: receive the 10 of a drop-ship; set order.drop_ship_for to another confirmed sales order and save (accepted); return the receipt. The return finds no delivery to reverse: the customer's line still reads shipped 10 and the shelf and ledger part. This is O139's own symptom through the neighbouring field.
- Probe: `DropShipRepointProbes`.

### D6. Orders approved before 0063 lose their approval's cover after any cut (rule, migration)
- Commit 88a8dd9. apps/sales/migrations/0063_salesorder_approved_figures.py is an AddField only; apps/sales/models.py:699 `_covered()` returns nothing when approved_figures is NULL. An order approved (and confirmed, or approved and awaiting confirmation) before the migration is weighed by `before` only: an approved 20% line cut to 10% cannot go back to 20% ("needs approval beyond what it has") while the same order approved after 0063 can. approve() refuses a second approval ("already been approved"), so the only way out is cancel and remake. The migration could seed approved_figures from the current figures for orders that are approved and have had no change since (updated_at on the lines against approved_at).
- Probe: `ApprovedFiguresProbes.test_cut_and_restore_on_an_order_approved_before_0063` (post-0063 twin `test_cut_and_restore_within_its_approval` passes).
- Kind: rule / migration. Medium to low.

### D7. lock_for_change and save() take a moved line's orders in different orders (race, low)
- Commit 63d2b34. apps/core/models.py:152-176 lock_for_change(instance) calls `instance.locked_before_it()` on the stored object (the line's old order only), then the line. The line's save() then holds old and new order, sorted by pk (apps/sales/models.py:1499, 1545). Two API edits moving a line between the same two draft orders in opposite directions (A to B, B to A): each holds its old order and the line, then wants the other order. The O138 freeze closed this for confirmed orders; draft orders still move. Same for PurchaseOrderLine (purchasing/models.py:2469, 2507). Not run on PostgreSQL.

### D8. apply_deposit takes the invoice and the deposit in pk order, post() takes the invoice first (race, low)
- Not a change of these commits, but they wrote the order down without the pair. apps/sales/models.py:2244 apply_deposit does `lock_rows(self, deposit)` (sorted by pk: the deposit, made first, comes first); Invoice.post (2615) holds the invoice, then apply_available_deposits (2324) wants the deposit. A standalone apply_deposit(A, D) racing Invoice.post(A) deadlocks (A is refused afterwards as unposted, after the lock). Contrived; not run.

## Checked and found sound
- O137 core: serialised(held_first) and lock_in_turn take order, then invoice or bill, then the note's original, then the note; a return and a credit note on one invoice stand on PostgreSQL (earlier probe passes there). close_short and reopen: order, line, customer. SalesOrderLine.save: order, line, customer. confirm: order, customer. Payment.void, InvoicePayment.save: payment then invoice, consistent; neither takes an order. Call-off save takes the line only and reaches no order.
- O138: a typo in a confirmed order's customer is refused (by design) but cancel and remake works: with a posted deposit, cancel is refused with "credit it back", the deposit credit note then lets the cancel through, and the same with the deposit paid and allocated. A draft order's customer still changes and confirms. A drop-ship purchase order awaiting delivery counts draft too, so a sales line's item cannot be edited with a draft drop-ship PO open; cancelling that PO frees it.
- O141: approve, change within, change again (qty 12, discount 19, then 21 refused) behaves; a deleted and retyped approved line needs approval again (new pk), acceptable. The delete path is atomic.
- Not verified (budget): purchasing prepayment cancel-and-remake; the customer row lock in confirm() against FK inserts for that customer (FOR UPDATE on the party blocks inserts that reference it).
