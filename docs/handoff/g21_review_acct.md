You are review_acct: an adversarial, read-only review for gate g21 of a Django 5.2 + DRF ERP for a woven polypropylene sack plant in West Bengal. The plant sells PP sacks in India and abroad, and sells its PP scrap every month. Repository: /home/user/hello-world. Read its CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

**Setup**
- Make a worktree of the integrated tip: `git -C /home/user/hello-world worktree add --detach $SP/review_acct 027fb0b`.
- Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Work only there. Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only them, in that worktree, unpushed.

**What you review:** the go-live tax work, as fix_acct built it. Its commits are the ones in the tree whose subjects start with O218, O219, O220, O221 or "g21 item 5", their follow-ups included (`git log --oneline -25`).
- The author's report is $SP/reports/fix_acct.md.
- The brief is docs/handoff/g21_acct.md.
- The research behind it is docs/handoff/reports/odoo19_gap_analysis.md ("Accounts", "Indian law moved in 2025-26") and odoo19_research/accounting_india.md.

**What to break.** Lead with what puts a wrong figure in front of a buyer, a GST officer or the income-tax department, or posts the books wrong:

1. **The Rule 46 invoice (O218).**
   - Every field, read from what the invoice posted (`issued_as`), never from today's settings. Prove it by renaming the company, the buyer and the delivery address, changing a GSTIN, an HSN and a rate after posting, then reprinting.
   - Intra-state, inter-state, an export under LUT, an export with IGST, a supply to an SEZ if the code has one, and reverse charge.
   - Rounding: the printed tax per head and per line must foot to the posted entry to the paisa.
   - The three copies.
   - A credit note naming its invoice.
   - The LUT: what happens across 31 March / 1 April, and when a LUT is edited after an invoice used it.
2. **E-invoice cancellation (O220).**
   - The 24-hour window: at 23:59 and at 24:01, in Asia/Kolkata (`DJANGO_TIME_ZONE=Asia/Kolkata`).
   - Refused with a payment, a credit note or a TCS challan against the invoice.
   - The reversing credit note: on its own date, with GST and TCS both undone, and GSTR-1 and Table 13 right in the month of the invoice and in a later month.
   - The cancellation done twice.
   - A cancelled invoice used afterwards: paid, credited, delivered against, printed.
3. **TCS on scrap (O219).** Work it from a plain-Python scenario first.
   - Collected only on items flagged, and only at a rate that is confirmed and in force on the invoice date.
   - Check a rate change on the 1st and mid-month.
   - A Form 127 declaration: in force, expired, and given after the invoice.
   - The credit note gives TCS back in proportion, with the last one taking what is left.
   - A void before posting collects none.
   - The month's due amount foots to the TCS payable account, and the challan clears it to zero (audit shape 3).
   - A challan paid twice for one month; a challan voided; a TDS and a TCS challan in one month.
   - The quarter's return rows foot to what was collected less what was given back.
   - Check the Income-tax Act 2025 section and form numbers against the research notes.
4. **The shared TDS/TCS challan mechanism.** Mirror both ways (audit shape 1): every guard on a TDS challan holds for a TCS challan, and the reverse. Prove the race with `race()` on PostgreSQL: two challans for one month, and a challan against a credit note giving TCS back. Use `DATABASE_URL=postgres://erp:erp@localhost:5432/review_acct` and drop test_review_acct first. SQLite proves nothing about races.
5. **The backup (O221).** Run the backup and restore scripts in a scratch directory if they can run here. Does a restore bring back an attachment byte for byte, and does RUNBOOK.md say how to check one?
6. **setup_indian_books.** Run it twice. Run it on a company that already has accounts and taxes. Check that it overwrites nothing, that everything it seeds is listed for review, and that nothing is marked confirmed.
7. **Who may.** Test as the roles over the API (apps/core/tests_roles.py): Controller, Sales Rep, Stores Manager, and anyone else who reaches invoices.
   - Who may cancel an IRN, confirm a TCS rate, record a declaration, pay a challan, and run the setup command's endpoint if it has one?
   - Is anything printed or exported that a role may not read?
8. **Screens.** Drive the TCS month in the browser as the Controller (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium, `npm run build` in frontend first). Money must be shown with src/lib/format.ts, never Number().

**Method**
- One probe per claim, stated as a fact about money or a document, with PASS or FAIL per check.
- Number the findings. For each, give file:line, a concrete scenario with names and rupees, and whether it blocks the gate.
- A finding needs a probe that fails on the tip. List a suspicion without one separately, as unproven.
- Run audit_invariants and makemigrations --check on the tip, and say what they print.

**Budget and report:** about 80 tool calls. Report to $SP/reports/review_acct.md, with 10 lines back: findings by severity, blockers first.
