# g25 brief: fix_imports — landed cost by weight, the bill of entry, polymer bought on circulars (L23, L21, L16)

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it, especially the numbers
checklist (exchange differences, per-unit rates, cost_of_removing). Then L16, L21 and L23 in
docs/RISKS.md "Later items", apps/purchasing/models.py `allocate_landed_cost` and
`LandedCostApplication`, apps/sales/price_variation.py, and apps/gst/gstr2b.py.

## Setup
- Base: the g25 integration tip (round A, the gate register and the weighbridge, is in it). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_imports <tip>`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 200 tool calls; commit as you go, so a stop loses little. Report:
  /home/user/work/reports/fix_imports.md.
- You are the only agent running. Shared rules that exist, use them, never a second copy:
  - the numbering-first lock (O257), @serialised and lock_rows, correction_date(),
    in_a_transaction_it_opened, DocumentSequence;
  - the edit log (every model logged unless its class says why not);
  - settlement.in_base and settlement.post_drawdown for anything in a foreign currency;
  - allocate_landed_cost and its release, the only way a cost lands on received goods (shelf, run or
    cost of sales, by what is still there);
  - the weighing from round A (apps/inventory, a receipt may name one) and the unit conversion;
  - PriceIndex and PriceIndexValue (apps/sales/price_variation.py). purchasing may import sales;
    accounting and core may not import either (`imports_against_the_module_graph` checks it).

## Order, and why
L23 first: the bill of entry's duty and the freight on polymer both land through it. Then L21, then
L16, which needs neither but shares the revaluation path L21 settles.

## Decided (do not reopen)
1. **Landed cost by weight (L23).**
   - An allocation names its basis: value (today's, the default), quantity in the stocking unit, or
     weight in kilogrammes. The basis is kept on the application (a fact, mistake 4).
   - Weight is the weighed net where the receipt names a weighing, otherwise the received quantity
     converted to kg through the existing conversion.
   - A line with no weight under the weight basis refuses the allocation with a sentence naming it.
     Never a silent fall back to value (shape 7).
   - Total first, divide last; the last line takes the remainder. Release is unchanged and gives back
     exactly what each shelf took.
2. **The bill of entry (L21).** A numbered document of its own, linked to the overseas supplier's
   bill(s) and the receipts it cleared. It carries:
   - the BE number, date and port code;
   - the customs exchange rate (CBIC's notified rate for the date, typed and kept);
   - the assessable value at that rate, with the freight and insurance additions. Research the
     Customs Valuation Rules for when actuals are unknown, cite them, and load the figures as dated
     data, unconfirmed;
   - basic customs duty by line at its rate, social welfare surcharge on the duty, and IGST on
     assessable value plus duty plus surcharge. Each line is rounded as the BE prints it; the total
     is the sum of the lines.
   - **Posting.**
     - Duty and surcharge are capitalised onto the cleared receipt lines through
       allocate_landed_cost (basis chosen on the BE, default value).
     - IGST goes to input IGST.
     - The credit is to customs duty payable, settled by a payment like any payable.
     - The overseas supplier's payable stays at its bill's own rate. The customs rate is a valuation
       figure only and never makes an exchange difference.
   - **IMPG.** GSTR-2B's IMPG rows are matched to bills of entry by number, date and port. The IGST
     difference and the unmatched on either side show on the 2B reconciliation, and IGST credit
     taken on a BE absent from 2B is flagged as at risk.
   - **The reverse.** A BE is voided under correction_date(): its entry reversed and its
     allocations released. A re-assessment is a new BE that supersedes it; the old one is kept and
     marked superseded, never edited. Voiding a BE whose IGST is in a filed return is refused with a
     sentence.
3. **Polymer bought on circulars (L16).**
   - A purchase rate contract with a supplier: items, quantity and period. It is priced on a
     PriceIndex (the polymer maker's circular for the grade) at index less an agreed discount per kg.
   - An order line on the contract takes the index in force on its date. The receipt is costed at
     the order's price, as today.
   - **The circular revised back-dated** (the makers do this) changes the price of what was already
     received. The supplier's rate-difference note is entered against the contract for a window:
     - a rise is a supplementary bill;
     - a fall is a debit note against the supplier's latest bill;
     - each is drafted for review and taxed as the original.
     - Its value lands on the receipts in the window through allocate_landed_cost (quantity basis),
       so it goes to shelf, run or cost of sales by what is left of each.
     - A receipt is varied once, its index kept on the note. Cancelling a draft frees its receipts.
   - Share the variation arithmetic with sales price_variation rather than copying it (mistake 5).
     If the shared part needs a fix, the fix closes both.
4. **Both ways.** For each of these, a test with its numbers worked by hand first, in a plain-Python
   script in /home/user/work/calc_fix_imports/:
   - a weight allocation released after half the goods were used;
   - a BE voided after its duty landed and some stock was issued;
   - a BE superseded by a re-assessment;
   - a rate-difference note on receipts partly consumed and partly returned to the vendor (the
     return goes out at cost_of_removing);
   - a fall credited;
   - a second note for the same window refused.
5. **Screens and roles.**
   - The Purchase Manager makes contracts and rate-difference notes.
   - The Accountant (or whichever role setup_roles gives import accounting) makes the bill of entry.
   - The Controller reads all three.
   - Drive each flow in a browser test as those people, with no 403 or 404 in the sweep, and refuse
     each to a role without the right.

## Tests
Use the numbers from the hand script for:
- a USD 50,000 polymer bill at 83.2500, the customs rate 84.1000, BCD 7.5%, SWS 10% of BCD, IGST 18%,
  freight landed by weight across two grades of different kg;
- a back-dated circular moving 2.50 per kg on 18,000 kg received in three lots.
Each with a test that fails without the fix.

## Before reporting
Run purchasing, sales, inventory, accounting, gst, e2e and web (fastsettings, --exclude-tag migration,
browser tests after `npm run build`), and `--tag migration` for anything you migrated. On
PostgreSQL, using your own database (fix_imports) and `nice -n 19`, race() two allocations of one BE
at once, and a BE voided while its duty payable is being paid. Then audit_invariants and
makemigrations --check. Do not edit docs/RISKS.md. Anything you need the accountant or customs broker
to confirm goes in the report.
