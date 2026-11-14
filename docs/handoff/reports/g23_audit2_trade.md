# audit2_trade (sales and purchasing documents), base 0a6778f

Probes: apps/sales/tests_audit2_trade.py, tests_audit2_trade_races.py, apps/purchasing/tests_audit2_trade.py, tests_audit2_trade_races.py (committed, unpushed, in /home/user/work/audit2_trade).
audit_invariants: "No invariant findings" (exit 0). makemigrations --check: "No changes detected".

## Findings (each has a probe that fails on the base)

T1 security/rule, GO-LIVE BLOCKER. Sales customer_return (DeliveryViewSet.customer_return, sales/views.py ~711; Delivery.create_return, sales/models.py ~4844) posts credit notes for any login holding sales.post_delivery. Warehouse Staff hold post_delivery, not post_invoice, so a store hand taking back 10 sacks posts a 1,000 credit note that cuts the receivable. Purchasing's mirror (GoodsReceiptViewSet.return_receipt, post_notes=has_perm post_bill, review F8) leaves the debit notes as drafts; sales never got it. Probe S6 (WhoMayCreditByReturning).
T4 books/rule, GO-LIVE BLOCKER where any order is in a foreign currency. Approval policy is weighed in the document's currency against base-currency limits: SalesOrder.policy_breaches (max_order_value, sales/models.py ~840) and margin_percent (euro revenue against base average cost); PurchaseOrder.policy_breaches (max_order_value, max_line_value, purchasing/models.py ~1935) and check_approver tiers; Budget.spent/committed/available add line.net_amount() of any currency to a base amount. 10 x 5,000 EUR at 90 (4,500,000 base) under a 1,000,000 limit needs no approval on either side (S9, P5); a euro sale at 95.6% margin is held by a 20% floor as -300% (S10). Credit limit was fixed for this (O77); the policy was not. Budget not probed separately (same root).
T3 books/rule. commission_report, collected basis (sales/models.py ~5985) counts the payment, tax included; invoiced basis counts subtotal(). 1,000 + 20% VAT paid in full at 5%: 60.00, should be 50.00 (S8).
T2 rule. run_dunning (sales/models.py ~5340) picks the highest level NOT yet sent, not above the last sent. Levels 7/30/60, first run at 70 days: 60-day notice, then the 30-day, then the 7-day on the following runs (S7).
T5 crash/race, minor. Two dunning runs at once (scheduled plus a click): the second hits one_notice_per_invoice_and_level and the whole run aborts with a 500 (PostgreSQL race, R1). Needs a lock or get_or_create.
T6 rule, minor. Bill.create_debit_note with no on_date dates the note today even when the bill is dated later (purchasing/models.py ~4770: "a note made by hand, today"); sales credit notes go through correction_date. Debit note 10 Oct on a bill of 30 Oct (P4).
T7 rule, minor, code-only (credits/debits are read-only over the API). A credit or debit note keyed by hand in code posts on another control account, dated before its invoice or a year ahead, and with a typed line of 5,000 against a 1,000 invoice (S1, S2, S2b, S5, P1, P2, P2b, P3 all fail). Invoice._check_kind and Bill's lack the currency/account/date/ceiling checks the generated path has; same class as O249.
T8 information, minor. SalesOrderViewSet.approval (GET) returns margin_percent to every login that can read an order, Sales Rep included, while setup_roles keeps cost with the controller. Unprobed.

## Held (probed or read, no defect)
Cancelled-order guards on invoice, bill, delivery and receipt posting (mirrored); every custom action on every sales and purchasing viewset names a permission; rep scoping on documents, lines, reports and CRM; two blanket releases at once (R2 held); correction_date on credit notes, claims, write-offs; no timezone.now().date()/date.today() in sales, purchasing; MSME per-delivery days; approval self-approval rule; apply_available_deposits/prepayments are ordered.

## Not reached
Kolkata full-lane run (stopped on the integrator's CPU request; no Kolkata-specific code found by grep); budget currency probe; accept-with-approve by the approver of a quote; vendor reference duplicate race; browser drive of the screens; a month of thirty documents.
