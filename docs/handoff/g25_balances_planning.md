# g25 brief: fix_balances — balances and agings in base (O416), planning's warehouse (O415)

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it, especially mistake 5 and the
numbers checklist (an exchange difference is the base each side booked, each rounded, subtracted). Then O416
and O415 in docs/RISKS.md "Open defects", apps/accounting/settlement.py (`in_base`, `booked_by`,
`standing_on_account`), apps/accounting/restatement.py (its docstring), `ar_aging`, `outstanding_balance`,
`credit_exposure` and `credit_limit_breach` in apps/sales/models.py, `ap_aging` and `vendor_balance` in
apps/purchasing/models.py, and apps/planning/mrp.py (or wherever plan runs count supply and demand).

## Setup
- Base: the g25 integration tip, 7750c5a or later. Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_balances <tip>` (never under
  /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 200 tool calls; commit as you go. Report: /home/user/work/reports/fix_balances.md. Backup
  branch: claude/erp-g25-balances. Your PostgreSQL database: fix_balances.
- You are the only agent running. Shared rules that exist, use them, never a second copy: settlement.in_base
  and booked_by, standing_on_account, unapplied, @serialised and lock_rows, correction_date(), the edit log,
  refuse_untaxed_lines and the item's default taxes (1 November), the printed money helpers in
  apps/core/documents.py (`money`, `grouped`).

## Order, and why
O416 first: it is money, and a credit check reads it. O415 after: it is advice a planner acts on.

## O416. Decided (do not reopen)
1. **What is wrong.** ar_aging, ap_aging, outstanding_balance, vendor_balance and credit_exposure add each
   document's amount due in its own currency. An unpaid USD 50,000 bill counts 50,000 beside rupees where the
   payable holds 41,62,500.00. A USD customer owing 50,000 reads 50,000 against a rupee credit limit, so an
   order that should be held for credit is confirmed. Both sides have it (mistake 5).
2. **The rule.** Every figure these return is in the base currency. A document's due is converted at the
   rate it posted at (its kept exchange_rate, through in_base), never at today's rate: that is what the
   control account holds for it, since each settlement realised its own difference when it happened. An
   unapplied payment the same, at its own rate. A down payment or prepayment stays at the rate it came in at
   (restatement.py says why).
3. **One shared function, both sides.** The conversion and the summing live once in accounting (it may import
   neither trading side; register what each side needs, as restatement does), and sales and purchasing call
   it. A fix to one side that the other does not get is the defect again.
4. **Rows keep their own currency too.** Each aging row carries the document's currency and its due in that
   currency beside the base figure; the screens (frontend/src/modules/sales, purchasing and money Aging.tsx) show both, and the bucket totals in
   base. A customer statement sent to a USD customer stays in USD: it is their document, not the plant's
   books. Do not convert it.
5. **Restatement day.** A closing-rate restatement reverses the next day, so on any other day the ledger is at
   the booked rates and the aging foots to it. On a restatement's own day the aging shows the restatement as
   one more line per control account ("Restated at the closing rate of <day>"), so it still foots. Work out
   from restatement.py whether that is the right reading; if it is not, say why in the report.
6. **Credit.** credit_limit is in base. credit_exposure's open invoices come through the rule above; an
   uninvoiced order line has no posted rate, so it is weighed through in_base at the order's date, as
   approval limits already are (O293). A customer with no rate on that day is refused with in_base's own
   sentence, not counted as nothing (shape 7).
7. **The demo hides it.** Round B made the demo pay its import, which keeps the footing test green. Keep the
   payment (the plant does pay by TT), and give the demo one open foreign invoice and one open foreign bill,
   so the footing tests (aging against the control accounts, both sides) carry a foreign document.
8. **Both ways, worked by hand first** in /home/user/work/calc_fix_balances/:
   - a USD 50,000 bill at 83.2500, half paid at 84.0000: the half left is 20,81,250.00 in base, and the
     realised difference is already in the books;
   - the same bill debited 10,000 USD by a note;
   - a USD invoice to a customer with a 10,00,000 limit, refused for credit where it passed before;
   - a restatement day, and the day after it;
   - a vendor overpaid in USD (the credits group).

## O415. Decided (do not reopen)
The fix is the row's: an inventory-side default receiving warehouse (core may not point at a warehouse); a
supply or demand line that names no warehouse belongs to that warehouse only, never to every warehouse; the
purchase order line and requisition screens ask for the warehouse, defaulted to it. Lines already stored with
none are planned in the default receiving warehouse; a migration does not rewrite them (they are facts of how
they were typed). go_live_check fails while there are two or more active warehouses and no default receiving
warehouse. Test it with the planning battery's case: planning FG must no longer advise cancelling the raw
material requisitions or the polymer order.

## Screens and roles
Drive both in the browser on the demo plant as the people who use them: the AR Manager's and the AP Manager's
agings with a foreign document open, a Sales Rep's order to a foreign customer held for credit, the
Production Planner's plan run. No 403 or 404 in the sweep. Print the aging and read it back.

## Before reporting
Run sales, purchasing, accounting, planning, inventory, e2e and web (fastsettings, --exclude-tag migration,
browser tests after `npm run build`), the changed modules once without fastsettings, and `--tag migration`
for anything you migrated. On PostgreSQL (fix_balances, nice -n 19) run the credit and allocation races that
read these balances (apps/e2e/tests_races.py) and prove each new lock by reverting it. Then audit_invariants
and makemigrations --check. Do not edit docs/RISKS.md. Anything for the accountant goes in the report.
