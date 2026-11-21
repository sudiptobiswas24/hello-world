# g25 brief: fix_gate — the gate register, gate passes and the weighbridge (L18, L17)

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it. Then L17 and L18 in
docs/RISKS.md "Later items", and deploy/scale_bridge/RUNBOOK.md.

## Setup
- Base: c92c901 (g24 pushed, plus the gate-script fix). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_gate c92c901`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 160 tool calls; commit as you go, so a stop loses little. Report:
  /home/user/work/reports/fix_gate.md.
- You are the only agent running. Shared rules that exist, use them:
  - the numbering-first lock (O257): every locking transaction takes the company's numbering row
    first; DocumentSequence for numbers;
  - @serialised and lock_rows, correction_date(), in_a_transaction_it_opened;
  - the edit log (every model logged unless its class says why not);
  - the scale bridge (deploy/scale_bridge) and API keys, for reading a weight from a device;
  - the inventory receipt and the sales delivery (GoodsReceipt in apps/purchasing, Delivery in
    apps/sales), cost_of_removing(), StockMovement.value.

## Why
Polymer arrives and sacks leave by truck. Today nothing records which vehicle came in, when it left, or
what it weighed, so a short delivery is found only on the invoice, and a returnable item sent out for
repair is tracked nowhere.

## Decided (do not reopen)
1. **The gate register (L18).** A vehicle visit is a numbered document: vehicle number, driver, mobile,
   transporter, purpose (inward against a purchase order or a job-work challan, outward against a
   delivery, a gate pass, a visitor), in-time and out-time from the gate's own clock in Kolkata, and what
   it carried. A vehicle cannot leave without an out-entry, and a visit left open past a set number of
   hours shows on the health check. A visit is cancelled, never deleted.
2. **Gate passes (L18).** Returnable and non-returnable, each numbered, each listing items or assets
   with quantities.
   - A returnable pass has an expected return date.
   - Its return is recorded against it, in part or in full.
   - It shows on an overdue list.
   - An asset out on a returnable pass reads as out on the asset register.
   - A pass is approved by a role that may, before the vehicle leaves.
   - Stock that leaves on a non-returnable pass leaves through the existing stock paths
     (cost_of_removing), never on the pass alone.
3. **The weighbridge (L17).** A weighing has a gross and a tare, each with its time and how it was
   taken: from the scale bridge as a device reading, or typed with a reason. The net is gross less tare.
   - A typed weight needs a role that may, and is marked typed wherever it shows.
   - The weighing belongs to a vehicle visit.
   - A goods receipt and a delivery may name their weighing. The receipt compares the weighed net with
     the invoice or challan weight and the received quantity, in the item's stocking unit, through the
     existing unit conversion.
   - A shortage beyond the item's tolerance (a field on the item or its category, default 0.5%) is a
     claim: against the supplier or the transporter, chosen on the claim, with its quantity and value
     at the receipt's own cost.
   - A claim is settled through a debit note (supplier) or a recovery (transporter), never by editing
     the receipt.
4. **Both ways.** A visit cancelled after its weighing, a weighing replaced (its predecessor kept and
   marked replaced, never edited), a returnable pass whose item comes back damaged, a claim withdrawn.
   Each with a test, its numbers worked by hand first.
5. **Screens and roles.** Gate staff record visits and passes (setup_roles: a Security Guard role that
   reads only what the gate needs and sees no prices). Stores records receipts against a weighing. The
   Purchase Manager raises and settles claims. Drive each flow in a browser test as those people, with
   no 403 or 404 in the sweep.

## Before reporting
Run purchasing, sales, inventory, assets, core, e2e and web (fastsettings, --exclude-tag migration,
browser tests after `npm run build`), `--tag migration` for anything you migrated, a race() on
PostgreSQL for two weighings of one visit at once and a pass approved as the vehicle leaves (your own
database, fix_gate, `nice -n 19`), audit_invariants and makemigrations --check. Do not edit
docs/RISKS.md.
