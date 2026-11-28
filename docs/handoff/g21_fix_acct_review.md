Integrator here. Your tax work has had its independent review: $SP/reports/review_acct.md. It has 4 blockers, 12 more findings and 9 unproven. The probes are commits 5cc6cda and 3c607d8 in $SP/review_acct, based on 027fb0b. Read the report first.

The tree has moved. Work in a NEW worktree from the integrated tip:
  git -C /home/user/hello-world worktree add --detach $SP/fix_acct2 d05ba67
Link .venv and node_modules into it, then cherry-pick 5cc6cda and 3c607d8 so the probes are in. Your commits are in that tip already, rehashed. One addition since you last worked: the demo plant now records its LUT (load_demo.py). Your LUT refusal stopped the demo's export invoice, and apps.web.tests_load_demo failed. Run the whole of apps.web, not only apps.web.tests.

Commits:
- `Gate: {GATE}` (literal) and the two trailers.
- Each commit names its row (O218, O219, O220 or O221) and the finding ("review F3").

Decided by the integrator; build to these:
- **F1.** A Form 127 declaration without the buyer's PAN exempts nothing. Refuse to record one without a PAN of valid shape, and say why. A declaration already recorded without one is not honoured at posting.
- **F2.** The portal and the books must never part. A credit note's IRN can be cancelled only if the books can record that cancellation (the note reversed on its own date, GSTR-1 and Table 13 right). Otherwise building its cancellation request is refused in words. Pick the one the code can do soundly, and say which.
- **F3, the TCS and TDS payable accounts read live.** This is mistake 4. Each collection and each deduction keeps the account it posted to (`posted_account`). The challan, the month's due amount, the return rows and any reversal read that kept account, never the section's current one. Fix TDS the same way: one rule for both (mistake 5). Then make `audit_invariants` see this shape. Extend `kept_settings_read_live` (or its sibling) until it reports the old code, and say what it now reports.
- **F4.** A cancelled invoice is never printed or sent as a tax invoice. Its print says "Cancelled" plainly, or is refused. An e-way bill for it is refused. The books' reversal note is not a section 34 credit note: it prints as an internal reversal and is never offered to email to the buyer.
- **F7.** A supply to an SEZ unit or developer is inter-state whatever the states (IGST Act section 7(5)(b)). It takes IGST, or goes without it under the LUT, never CGST and SGST.
- **F8.** Recording a cancellation needs the portal's acknowledgement of it (the cancel date the portal returned), and the right to cancel. A GST Officer, or anyone who may not, is refused.
- **F10.** Refuse an IRN cancellation once a TCS challan has paid the invoice's TCS over, as the brief asked.
- **F12.** `setup_indian_books` wires a tax only into an account it created, or into one whose type and purpose match. A code collision never makes CGST collect into "Rent payable". A collision is reported for the accountant, and nothing is wired.
- **F13.** Seed the section by date: 206C(1) until 31 March 2026, 394(1) from 1 April 2026. Both rates stay unconfirmed, and the review list names the 1% or 2% question for the accountant.
- **F14.** An LUT covers exports from the later of valid_from and filed_on.

Fix in this order, with every probe passing at the end, renamed by what it protects (drop "review" from names):
1. F1 to F4
2. F7, F8 and F10
3. F6 (no settlement discount on the TCS)
4. F5 (the footer's company name and Tax ID, the item name and unit code, from what the invoice posted)
5. F9 (only the accountant's roles set an item's TCS section)
6. F11 (a voided challan's rows do not read "Paid over")
7. F12 to F14
8. F15 (the backup writes a sha256 manifest of the attachments, and the restore checks it)
9. F16

Then the unproven ones. Each either gets a probe and a fix, or a line saying why it is not a defect:
- **TCS on an advance received for scrap.** Section 206C(1) collects at the debit or the receipt, whichever is earlier, so sales/tcs.py:119 skipping advances looks wrong.
- **Freight on the scrap invoice and the TCS base.**
- **206CCA.** As far as I know it was omitted from 1 April 2025. Check this against the notes, and do not model it if so.
- **The LockOrderViolation in `record_taxes` on a 13-line invoice on PostgreSQL.** Find the order, fix it, and prove it on PostgreSQL (`postgres://erp:erp@localhost:5432/fix_acct2`; drop test_fix_acct2 first).

Before reporting:
- Run accounting, gst, sales, core, purchasing, inventory and apps.web.
- Run the browser tests (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium; `npm run build` first; vitest).
- Then run audit_invariants and makemigrations --check.
- Read each result.
- fix_people2 works in hr at the same time; keep out of apps/hr.

Budget: about 120 tool calls. If it runs out, stop after item 7 and list what is left. Append "Review fixes" to your report, and send 10 lines back.
