# review_money2: fix_money2 at 3d7dc23

Probes committed at b0710bc in /home/user/work/review_money2 (apps/{accounting,gst,sales,purchasing,e2e}/tests_review_money2.py).
Ran: probes (SQLite fastsettings, roles over the API: Bookkeeper, Controller, AR Manager, GST Officer), BankRaces on PostgreSQL
(review_money2, --keepdb, then dropped), MigrationExecutor test apps.sales.tests_b2cl_migration (OK), audit_invariants ("No
invariant findings"), makemigrations --check ("No changes detected"). Not run: whole suites, web browser tests, Kolkata lane.

## Blocks

1. apps/accounting/models.py:318-334 (CORRECTING_STEPS, in_a_correction), used at :481 and in JournalLine.save.
   A return is a reverse path and is not exempt: it goes by no name the regex knows. Delivery.create_return
   (apps/sales/models.py:4953 -> post -> post_inventory_entry -> entry.post()) with the inventory or COGS account switched off:
   ValidationError "1200 - Inventory is switched off; it takes nothing new". Purchasing GoodsReceipt.create_return with GRNI off:
   same refusal. Probes apps.sales.tests_review_money2.Returns.test_return_with_cogs_and_inventory_switched_off and
   apps.purchasing.tests_review_money2.ReturnToVendor. Retiring an account makes goods already shipped or received
   impossible to take back (mistake 2). The author's note admits it ("a return ... is not exempt"); the brief lists return
   as a correction door. Same family: release_exchange_difference, reclaim_stock, take_back_landed are also not matched
   by name unless called from a void. Blocks, unless the owner decides returns are new business (they are not: they carry
   `reverses`-like intent with no marker).

## Does not block (fix or register)

2. apps/accounting/models.py:318-334. The exemption is a stack-frame name match, so any new posting made under a function whose
   name starts void/revers/cancel/undo is exempt (probe: a local `reverse_charge_booking()` calling post() on an entry with a
   switched-off account posts; apps.accounting.tests_review_money2.SwitchedOffDoors.test_a_name_on_the_stack_exempts_a_new_posting
   fails its assertRaises). Names here are real English words in GST (reverse charge), and `cancel` is 25 methods.
   I found no live path that books new business under such a name; but nothing stops the next. A marker argument
   (post(correcting=True), which already exists for notes) would close both 1 and 2.
3. apps/core/recurrence.py:63-76 (run_date) with apps/accounting/recurring.py:92 and apps/sales/models.py RecurringInvoice.generate_one.
   A schedule whose start_date is edited to a day later than its next_run_date is refused on every run ("RJ is not taken on
   2026-06-01: the schedule starts on 2026-08-01") and next_run_date is read-only in the API, so the only way out is to edit
   start_date back. The run list reports it as "refused" every night. Probe RecurringDates.test_schedule_moved_later_after_a_run
   and Recurring.test_edit_start_after_run_sticks. Before O284 it ran. Minor; refuse the start_date edit past next_run_date
   in _check_terms, or take the later of the two.
4. apps/gst/gstr2b.py:424-431 (O288). A bill matched to a line with itc_available False now leaves totals.matched but is in no other
   bucket (not waiting, not not_booked, not filed): booked = matched + waiting no longer holds by exactly the blocked amount, and
   only a note says so (result["notes"], one per line; also fires for rows in "differs", wording "not counted as matched").
   Wants its own total ("blocked"), so the five totals foot. Minor.
5. apps/sales/models.py:committed_in_base (O342). in_base raises for any other confirmed order with no rate on its own date
   ("No exchange rate for EUR on 01-03-2026 ... weigh this document"), so one old order (confirmed before O293, or its rate row
   later deleted) blocks every new order of that customer at the credit check, and the message names a date, not the order.
   Probe Exposure.test_no_rate_for_old_confirmed_order_blocks_the_next. Before, today's rate was read, which only fails with no rate
   at all. Minor; name the order, or skip it as the new order's own gap is skipped ("not weighed here").
6. Register (CLAUDE.md "Auditing"): the three defects the report lists as seen and not fixed (next_run_date advance from on_date,
   switched-off account on a draft line offered by the pickers, and items 1 and 3 above) are not in docs/RISKS.md; the commits only
   took rows off. Add rows with ids.
7. apps/gst/ewaybill.py:cancel, einvoice.py: with the five-minute skew a bill generated 4:59 ahead can be cancelled (plant clock)
   before its own generated_at; harmless to money, noted. A naive datetime from code (not the API, whose _moment makes it
   aware) gives TypeError in moment_that_has_come (apps/core/models.py:377); the old record_cancellation line had the same.
8. apps/accounting/models.py JournalLine.save: a CostCentre query on every line save that names a centre (n+1 over an import or a
   recurring run). Perf only.

## Tried and held (no defect found)

- O281: journal (Controller; Bookkeeper has no post right, 403), invoice (AR Manager), payment (post_payment) dated 31 March against
  opening 1 April: all 400 with the sentence; on the opening day posts. Re-dating a draft to the day before: 400. Invoice/Bill
  is_opening_balance is read-only in both serializers, so no one can claim the exemption over the API (create with the flag: 201
  and False). Credit note against a switched-off revenue and receivable account via API: 200.
- O282: PATCH and PUT of currency before posting 200; after: 400 for both roles; to the base (already unset) 200; a USD account to
  unset after posting: refused; save(update_fields=["currency"]): refused. Admin goes through save()/clean(). queryset.update is not
  guarded (nothing can).
- O283: switched-off account at post_entry: refused (Controller); line with switched-off centre: refused beside its field;
  reverse of a posted entry with account and centre both off: 200; credit note with revenue and receivable off: 200; new invoice
  with receivable off: refused.
- O284: month-end chain from 31 January 2024 monthly: 31 Jan, 29 Feb, 31 Mar, 30 Apr, 31 May (anchored); run API with as_of in
  2030: refused per schedule, no entry made, schedule not advanced; on_date 400 days ahead / before start: refused, nothing created.
- O285/O287: SEZ note and B2CL month recompiled after a limit change (their tests, and the code read: a note takes its invoice's limit
  because source is the original). Migration 0079: MigrationExecutor test OK; fills posted only, leaves drafts blank.
- O289: ack 4:59 ahead accepted, 5:01 refused, backwards (before the invoice) refused; e-way 4:59 accepted.
- O290: PostgreSQL race(), same file twice (their test) and my two: overlapping files A+B at once = 3 lines (not 4);
  import vs close at once = import done, close refused "2 lines unexplained", statement stays open with 2 lines. Both
  serialise on the statement row.
- O291: 18 garbage periods (99999-01, 0000-01, +2026-01, fullwidth and Arabic digits, 20-digit years...) on gstr1, gstr1/json, gstr3b,
  gstr2b/match: only 200 or 400, never 500; itc04 400.
- O342: EUR at 1.10 on 1 March and 1.30 on 1 June, GBP at 1.25 and 1.50 on two dates, four orders: committed_in_base 5,150.00, equal to
  the hand figure (each order at the rate in force on its own date).
- O286: debit notes on opening invoices stay in table 13 and in _invoices alike (consistent).
