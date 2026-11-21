# g22 brief: fix_purch — purchasing's open rows

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.
- Base: the commit your prompt names. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_purch <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 150 tool calls. Work in the order below; at the budget, commit what is done and report what is left.
- Report: $SP/reports/fix_purch.md.
- The plant buys PP granules, masterbatch and stores from Indian suppliers and imports some. Its accountant files GSTR-2B matching, TDS and the MSME return.

## Your rows, in order (read each row in docs/RISKS.md, "Open defects")

1. **O91.** The match's price check ignores the agreed discount: 10 @ 100 less 10% agreed, billed at 100 with tolerance 0, posts with 100 of price variance. Compare net of the agreed discount.
2. **O99.** A hand-typed price passes the approval rule whenever an agreed price exists, used or not.
3. **O167.** A confirmed purchase order's approval is withdrawn on any re-price, and only the budget is asked again. Sales fixed the same shape (O72, O141): read its fix and share it (mistake 5). Do not copy it.
4. **O95.** The same vendor invoice number in another case is accepted: INV-77 and inv-77 both post.
   - Decided: a vendor's invoice number is unique per vendor per financial year, compared without case and without spaces or the separators `-`, `/` and `.`. A GST invoice number is unique per supplier per year.
   - Keep it as a normalised key with a partial unique constraint. A data migration keys existing rows and reports collisions without merging them, as purchasing 0063 does for notes. Test the migration with MigrationExecutor.
5. **O154.** Debit notes raised by a receipt return post at once, so the supplier's credit-note number can never be recorded.
   - Decided: the supplier's credit-note number and date are a reference, not money. They may be set once after posting, by the role that posts bills, and never changed after.
   - GSTR-2B matching reads them.
6. **O188, O189: the MSME report and the bill's due date.**
   - A part settled wholly by a debit note or TDS has the paid-on day of that document.
   - `pay_by()` and the shown due date are per delivery when one delivery is objected to, as the payment run already is. Share one function for both.
7. **O211.** O94's ranges compare gross receipt quantities with bills in posting order. A return posted between two bills shifts which bill a later return finds. Work it out with a scenario table first.
8. **O190.** A debit note left unkeyed by migration 0063, edited as a draft, is refused by the constraint with a generic message. Refuse it in words naming the collision.
9. **O227.** `PurchaseOrderLine.save()` takes the blanket agreement after the order and the line. Prove it with `race()` on PostgreSQL, or refute it in words and with a test.
10. **O233.** TDS rates are a table of today's figures. Make them dated rows kept on each deduction as they stood, exactly as TCS's are (accounting/tcs.py TcsRate). Share the dated-rate mechanism; do not write a second one. Seed nothing as confirmed.
11. **O234.** TCS a supplier collects from the plant is not recorded. Needed:
    - a TCS line on a bill;
    - a TCS receivable account (credit the plant claims in its own return);
    - the bill's total agreeing with the supplier's;
    - its reverse on a debit note.

## Shared rules

- **Yours:** the vendor-invoice key, the per-delivery due date, the dated TDS rate (on TCS's mechanism).
- **Existing; use them:**
  - `settlement`, `correction_date()`, `@serialised`, `lock_rows`;
  - the challan mechanism (accounting/challans.py, shared by TDS and TCS since g21);
  - `AuditableViewSetMixin`;
  - the approval machinery sales uses.
- Another agent (fix_money) works in accounting, sales and gst at the same time. Keep to purchasing, purchasing's TDS, and what a bill posts. If you must touch accounting/challans.py or accounting/tcs.py, say so in the commit, and keep the change to what your row needs.
