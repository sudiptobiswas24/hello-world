# g24 brief: fix_rcm — GST reverse charge: the self-invoice, cash payment, GSTR-3B table 6

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it. Then
docs/handoff/reports/odoo19_gap_analysis_oct26.md, item 7, and RISKS known limit 16.

## Setup
- Base: d76ef6f (g23b pushed). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_rcm d76ef6f`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 140 tool calls. Report: /home/user/work/reports/fix_rcm.md.
- You own reverse charge in apps/gst and purchasing, and the GST cash ledger. fix_audit owns
  apps/core/history.py and audit.py; fix_statutory owns hr/statutory.py, the seeds and the TDS/TCS
  names. Do not touch their files. Shared rules that exist, use them: the numbering-first lock
  (O257), DocumentSequence for numbers (consecutive, GST Rule 46), correction_date(),
  @serialised and lock_rows, settlement.in_base, gst/returns.py's 3.1(d) and 4(A)(3).

## Why
The plant pays reverse charge on freight from transporters (GTA) and on any supply from an
unregistered person that the law places under reverse charge. Booking into GSTR-3B 3.1(d) and
4(A)(3) exists. Three things do not: the self-invoice for a supply from an unregistered supplier,
the payment of the reverse-charge tax in cash (it cannot be paid from input credit), and table 6.1's
cash column. Research each rule from the CGST Act, the Rules and the CBIC notifications and circulars
in force on 26 October 2026, and cite each. Anything you cannot confirm goes to the report for the
tax adviser.

## Decided (do not reopen)
1. **The self-invoice** (CGST s.31(3)(f), and the time limit the Rules now set; verify it) is a
   numbered document of its own series, made from the bill or the receipt of the supply, within the
   time limit. It is refused after the limit with a sentence, and a health line lists every
   reverse-charge supply with no self-invoice yet. Its reverse path is a cancellation, never an edit
   or a delete, under correction_date(). The payment voucher (s.31(3)(g)) follows the payment to the
   supplier, if the law still requires it.
2. **The tax is paid in cash.** The reverse-charge liability is booked to its own payable account and
   cleared only by a GST cash payment (a challan, PMT-06, with its CPIN and date), never set off
   against input credit. The credit (4(A)(3)) is taken only once the tax is paid, if that is the law
   (verify it). A cash ledger, debits and credits by head, foots to its account.
3. **GSTR-3B table 6.1** shows reverse-charge tax paid in cash, by head. The return reads the payments,
   not a recomputation (mistake 4).
4. **Both ways:** a bill with reverse charge voided after its self-invoice; a cash payment voided; a
   debit note on a reverse-charge bill; a supplier who registers mid-month. Each with a test and its
   numbers worked by hand first.

## Before reporting
Run gst, purchasing, accounting, sales, e2e and web (fastsettings, --exclude-tag migration, browser
tests after `npm run build`), `--tag migration` for anything you migrated, and a race() on PostgreSQL
for two self-invoices numbered at once (your own database, fix_rcm). Then audit_invariants and
makemigrations --check. Do not edit docs/RISKS.md.
