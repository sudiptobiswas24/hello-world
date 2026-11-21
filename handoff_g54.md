**g54 is pushed ({PUSHED}, 21 November 2026), all six lanes green on one tip (gated as {GATED}).** Two commits on 28897ba:
O612, and the demo's books, payroll payments, tools and floor masters.

g54:
- O612 was registered as a screen problem: it said the server refuses quarantine, transit and consignment for a
  receipt, while posting refused only a customer's material. Probing what posting takes found two defects in the
  books behind it:
  - Another vendor's goods could go into a vendor's consignment store, as that vendor's stock at no value, and
    their order then billed what was never accrued. Posting refuses it.
  - A consignment vendor's own delivery could be billed on its order and again when drawn. bill_limit() now
    leaves out what came on consignment, and ordered_limit() keeps what approvals quote.
  On the screens, one rule (receivableWarehouse) says where a receipt may go: quarantine, and the order's own
  vendor's consignment store. Never transit, a customer's material or another vendor's store. The order page,
  the Scan screen and the line adder all use it.
- O613, narrowed and still open: the demo now has HDFC's statements from going live to October, matched. It has
  the posted run's salary file paid and its provident fund remitted. Tools are named on the sack and weave runs
  and wear as they book; there are a print design, the tape line's settings, setup families and changeovers,
  machine positions, crews, the power tariff, a transfer lane, party tags and two suppliers' bank details.
  55 of 266 lists are empty, down from 70. Seven of them a person would open (price clauses and variation bills,
  call-offs, test certificates, weight claims, rate differences, rebatches, job-work losses); the row names the
  rest as empty by nature.
- Registered, not fixed, both waiting on one design answer:
  - O614: a journal typed through the bank (the rent, the power bill, a bank charge entered by hand) can never
    explain a statement line, so no month with one can close. None of the demo's three statements can.
  - O615: a first statement has no cut-off, so every earlier payment reads as unpresented for good. The answer
    both want is a reconciled-from date on the bank account.

Counts: {COUNTS}
