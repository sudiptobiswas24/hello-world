# g21 brief: fix_acct — go-live tax invoice, TCS, e-invoice cancel, backups

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names, not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_acct <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Budget: about 160 tool calls. Work in the order below.
- Report: $SP/reports/fix_acct.md.
- Background: docs/handoff/reports/odoo19_gap_analysis.md ("Accounts",
  "Indian law moved in 2025-26") and odoo19_research/accounting_india.md.

A rate or threshold the law sets is DATA with the day it applies from,
kept on the document as it stood. Build the mechanism; seed nothing as if
it were confirmed by the plant's accountant.

## In order

1. O218: the tax invoice print carries what CGST Rule 46 asks: the
   supplier's and recipient's names, addresses and GSTINs, the invoice
   number and date, HSN per line, quantity and unit, taxable value, the
   rate and amount of each tax head (CGST and SGST, or IGST, and cess),
   the place of supply with state name and code, the delivery address
   where it differs, whether tax is on reverse charge, the signatory, and
   the copy it is ("Original for recipient", "Duplicate for transporter",
   "Triplicate for supplier"). Credit and debit notes the same, naming the
   invoice. Read the fields from what the document posted (kept facts),
   never from today's settings. Test the printed text, not only that a
   PDF comes out.
2. O220: e-invoice cancellation: within the portal's window (24 hours
   from the IRN), a cancellation payload with a reason code, and the
   document's state after it (the IRN cancelled, the invoice no longer
   a valid tax invoice, a credit note the way to undo after the window);
   refused in words outside the window.
3. O219: TCS on the sale of scrap (section 206C(1) of the 1961 Act;
   section 394 of the Income-tax Act 2025 from 1 April 2026): which items
   are goods TCS applies to (an item flag, scrap), a rate that is data by
   the day it applies from (the accountant confirms 1% or 2%), collected
   on the sale invoice and shown on its print, a TCS payable account, the
   buyer's declaration that exempts (Form 127, was 27C), what is due for
   the month, the challan that pays it, and the quarterly return's data
   (Form 143, was 27EQ). Name the reverses: a credit note on a scrap
   invoice gives its TCS back; a void before posting collects none.
4. O221: the backup copies the attachments (the `media` volume) as well
   as the database; the restore puts both back; RUNBOOK.md says what is
   in a backup and how to check it. Test a restore that brings an
   attachment back.
5. A command that sets up a new company's Indian books: chart of
   accounts, GST taxes and tax groups for the current slabs, intra- and
   inter-state fiscal positions, the TDS and TCS accounts. Everything it
   seeds is marked as a default for the accountant to review; it never
   overwrites what exists.

## Shared rules

Yours: the invoice print's tax content, TCS, the e-invoice's
cancellation, the setup command. Existing, use them: `apps.core.documents`
(printing), apps/gst (e-invoice, e-way bill payloads, place of supply),
the TDS machinery in purchasing and sales (TCS is its mirror: read it
first and share what can be shared, mistake 5), `settlement`,
`AccountingPeriod.refuse_closed()`, `correction_date()`, `@serialised`,
`lock_rows`, `AuditableViewSetMixin`.

Another agent (fix_people) works at the same time on hr and payroll;
another (fix_qrecall) on quality. Keep to accounting, gst, the sales
invoice and its print, and the backup scripts.
