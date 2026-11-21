**g53 is pushed (TIP, DATE November 2026), all six lanes green on one tip.** Twelve commits on g52's tip: O608, O605,
O606, O604, and O613 in seven parts.
The container restarted once more during its gate, at 00:04 with pg-a and pg-b twelve minutes in, and this time
with two watchers running. So g52's lesson is half of it: the watchers stop an idle session being reclaimed,
but the container is also restarted under a busy one. What saves a gate is the chain being restart-safe (each lane
skipped once its driver shows it green on the head) and someone starting it again; check uptime at every wake.

g53, what the demo still could not show, and three of the register's rows:
- O608: the Scan screen's Receive on a confirmed purchase order sent no warehouse, so an order whose line names
  none was refused on a screen with nowhere to say where. It is now the order page's own WarehouseChoice. It reads
  the order when pressed, not on sight, and asks only when a line still owed names no warehouse; one rule
  (owesWithoutAWarehouse) serves both order forms and the Scan screen. The mirror, Receive by scan, defaulted to
  the first warehouse by code, a quarantine bay where one is coded first; it now defaults as a line naming none is
  planned (defaultReceiving). Seen doing it, registered as O612: WarehouseChoice says the server refuses
  quarantine, transit and consignment for a receipt, and it refuses only a customer's material.
- O605: eight floor screens showed nothing on the demo, and its own loom-exit station could weigh no roll. The
  demo now weaves F-50 on LOOM-05 and weighs five rolls, one typed under calibration and one declaring metres its
  weight does not support. Two rolls are cut into atta sacks: the press counts, and the stitching tables count,
  weigh and scrap. The bundles are baled, the crews book their hours, and the floor keeps the week's attendance.
  All of it goes through the floor's own functions. The Rolls screen's Fabric column read an optional name and
  showed nothing for F-50; it reads the name, else the code.
- O606: a plant with no GST registration printed its own tax number as a GSTIN. issuer() keeps the label with the
  number, and a letterhead kept before that reprints as it was printed.
- O604: the control chart dropped a sentence for an empty answer that never comes. The server's refusal says
  which window held nothing, and its test now holds the words.
- O613: a sweep of every list route on the demo, read as the administrator, found 114 of 266 answering nothing.
  The pilot's own are filled, each through its own path:
  - stores: a count, a write-off, a move to the godown;
  - the gate: a weighed lorry, a returnable pass, a visitor;
  - planning: a year's shipment history, a forecast, a build-ahead and an MRP run;
  - the dealers' price list, given to nobody;
  - dunning, recorded for post;
  - a recurring charge that has issued nothing;
  - a blanket agreement with a draft call-off, and a reorder rule;
  - the latest invoice's e-invoice and e-way bill, waiting for the portals;
  - the month's rates, a frozen cost sheet and next year's standards in draft;
  - the import's bill of entry, as a draft for the accountant;
  - a customer's own granule in at no value and part of it back;
  - an advance against salary, recovered from this month's run, and the contract labour register.
  70 are left. Budgets and approval policies stay out: each would hold or refuse the orders other tests make on
  the demo, and they want a story that does not. The row lists the rest.

Counts: COUNTS
