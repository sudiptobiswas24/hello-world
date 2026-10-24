# g22 brief: fix_money: open rows in sales, accounting and GST

Read docs/handoff/fix_common.md first; it binds you. Then read CLAUDE.md, all of it.

**Setup**
- Base: the commit your prompt names.
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/fix_money <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad).
- Budget: about 150 tool calls. Work in the order below. At the budget, commit what is done and report what is left.
- Report: $SP/reports/fix_money.md.

**The plant.** It sells woven sacks in India and abroad, under LUT, and sells PP scrap every month (TCS applies). It takes advances, and takes in job work.

## Your rows, in order

Read each row in docs/RISKS.md, "Open defects".

1. **O209.** After g20's settlement slices, deleting or re-pointing a middle allocation re-states only its own slice, so a paisa may reappear on the control account.
   - Prove it with a scenario table and a probe, or refute it.
   - The control account must clear after every order of delete and re-point (audit shape 3).
2. **O230.** A payment voided while a bank statement line is matched to it.
   - Decided:
     - The deposit line stays matched to the payment, because the deposit happened.
     - The bank's return line (a bounced cheque) is matched to the void's reversing entry.
     - The reconciliation counts both, and the statement foots.
   - So:
     - The bank statement must be able to match a line to a void's reversal and to a direct entry; this is also O156's second half.
     - Voiding must leave the reconciliation true.
   - Test a cheque deposited, matched, bounced, voided and its return line matched, on one statement and across two.
3. **O77.** The credit limit adds currencies at face value. Measure exposure in base currency, as the row's probe says (1,018.00, not 980).
4. **O109.** Settlement discounts are built and never called, on both sides.
   - Decided: wire them. On a payment or receipt within the discount days, an explicit "take the settlement discount" step books it as a financial discount with no change to GST.
   - The other way, a GST credit note reducing the value under section 15(3)(b) when the discount was agreed before supply, is the accountant's to choose. Build the switch on the payment term, default off, and add the question to docs/HANDOFF.md's accountant list. That doc edit is yours.
   - Mirror sales and purchasing, with one function for both (mistake 5).
5. **O243.** TCS on an advance received for scrap.
   - Section 206C(1) (394 from April 2026) collects at the debit or the receipt, whichever is earlier.
   - Collect it on the advance receipt, and count it against the invoice so it is never collected twice.
   - Then lift g21's refusal of down payments on scrap orders.
   - Reverses: the advance refunded, the invoice credited, the advance drawn down across two invoices.
6. **O235.** There is no sales debit note, the supplementary invoice under CGST section 34(3). Build:
   - its posting;
   - its Rule 53 print (as O218's invoice print does, read from what it posted);
   - GSTR-1 (CDNR/CDNUR);
   - the e-invoice (document type DBN);
   - its reverse.
7. **O238.** An advance received for job work prints as an "Invoice". It needs a Rule 50 receipt voucher, and a Rule 51 refund voucher for its refund.
8. **O245.** The quarter's TCS return rows read `section.form_code` live, and a challan shows `section.code` live. Keep both at the event (mistake 4).
9. **O24.** `Account.currency` is read by nothing.
   - Decided: wire it. A payment through a bank account that has a currency (an EEFC account) must be in that currency, and is refused in words otherwise.
10. **O239.** The Letter of Undertaking and the setup command's list of seeded defaults have admin screens only.
    - Give each an office screen, with permissions in src/app/registry.ts matching the server's.
    - Add a browser test as the Controller.
11. **O244.** Probe each tax path the review did not drive:
    - a payment landing between an IRN cancellation's request and the portal's answer;
    - a TDS and a TCS challan for one month on one bank;
    - an attachment added while a backup runs;
    - an IRN cancelled in a closed period.
    For each, fix it or say in a test why it holds.
12. **O156.** The TDS return screen has no "reverses" column. The statement screen half is item 2.
13. **O81.** The dunning mail asks for the whole balance and the last due date, not what the notice records as overdue (mistake 1).

## Shared rules

- **Yours:**
  - statement matching to entries;
  - the settlement-discount function;
  - TCS on advances;
  - the sales debit note;
  - the receipt and refund vouchers.
- **Existing; use them:**
  - `settlement` and `post_drawdown`;
  - the challan mechanism (accounting/challans.py);
  - the TCS machinery (sales/tcs.py, accounting/tcs.py);
  - `Invoice.issued_as` and the Rule 46 print;
  - the e-invoice and GSTR-1 builders in apps/gst;
  - `correction_date()`, `@serialised`, `lock_rows`;
  - `AuditableViewSetMixin`.
- **Another agent (fix_purch)** works in purchasing at the same time. Keep out of apps/purchasing, except where a mirror needs one shared function. Then say so in the commit.
