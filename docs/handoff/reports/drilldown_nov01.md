# What is left, module by module (1 November 2026)

**Method.**
- Two sweeps in a real browser, on the g25 integration tip, with the demo plant loaded:
  - every screen offered to each of the ten demo people, plus the first record of each list (999 page
    opens);
  - every New form (144).
- The main flows driven end to end as the people who do them:
  - a rep's order, through stores' delivery and invoice, to the AR manager's posting;
  - purchase order, receipt and bill;
  - a stock adjustment;
  - work orders;
  - a pay run;
  - a customer receipt;
  - quality and maintenance.
- Every finding checked in the database or the code before it was written down.
- The 26 October Odoo 19 gap analysis re-checked against the code
  (docs/handoff/reports/odoo19_gap_analysis_oct26.md).

Odoo's documentation was not re-read today.

Register rows O392 to O408 are new today. "Fixed in g25" means fixed in the interface patch now under
test, not yet pushed.

## 1. The two findings that change go-live

1. **O395. A document typed on screen carries no GST.**
   - An item has no default taxes, and the line's tax picker is a native multi-select squeezed to 30
     pixels. Nobody had driven it: no test touches it.
   - A rep's order for 20,000 rice bags was confirmed, shipped, invoiced and posted as INV-2026-00016 at
     2,24,000.00 with no tax.
   - A polymer bill was posted the same way, which loses its input credit.
   - Nothing refuses a taxable line with no tax at confirmation or posting.
   - Fix (next, in g25):
     - default sales and purchase taxes on the item, taken by any line that names none;
     - a picker people can use;
     - under GST, an order, invoice or bill whose line has no tax is refused, unless the party is
       exempt. A nil-rated or zero-rated supply says so with its 0% tax row.
2. **O396. A bill posts with no vendor invoice number.**
   - The input credit is claimed against that number.
   - The check that refuses the same vendor invoice twice keys on it.

Both have green model and API tests. Both appear only when the screen is used as the plant will use it.

## 2. Module by module

### Sales
- **Works:**
  - the order, from customer and price list through confirmation and gap-free number;
  - stores' dispatch with a pick list;
  - the invoice drafted by the rep and posted by the AR manager (a sound split);
  - receipts applied to invoices;
  - credit notes;
  - the polymer price clause and dispatch schedule.
- **Defects:**
  - O395 (no tax).
  - O397: a delivery's quantity cannot be changed on its draft, so a part shipment cannot be made from
    the screen.
  - O400: an empty draft order reads "Dispatched: Done".
  - O401: a document must be created before its lines.
  - O406: no "receive payment" on an invoice.
  - The delivery address is filled from the customer and cannot be chosen on the order.
- **Odoo has, here missing:**
  - quotation templates;
  - section and note lines;
  - price rules by percent and category;
  - configurable pipeline stages and lost reasons;
  - lead merge and mail-to-lead;
  - a customer portal;
  - a variant grid;
  - cash rounding.

### Purchasing
- **Works:**
  - order with the vendor price filled in;
  - confirmation;
  - receipt into a warehouse;
  - bill from the order;
  - weight claims and gate links from round A.
- **Defects:**
  - O395 on the purchase side.
  - O396 (bill without vendor invoice number).
  - O398: a receipt's reference (the supplier's challan) cannot be typed, and its weighbridge choice is
    cut off.
- **Odoo has, here missing:**
  - RFQ and debit note sent by mail;
  - receipt reminders;
  - suggested quantities;
  - vendor discount percent.
- **Round B of g25 covers:**
  - landed cost by weight;
  - the bill of entry;
  - polymer bought on circulars.

### Stores
- **Works:**
  - stock on hand, movements, batches, bins, transfers, valuation against the books;
  - job-work challans;
  - the gate register and passes from round A.
- **Defects:**
  - O403: no adjustment reasons exist on a fresh plant, so no adjustment or count difference can be
    booked.
  - O408: a store keeper's "Vehicle in" opens as a blank read-only form.
  - O405: the demo has no Security Guard login, so nobody in it can work the gate.
- **Odoo has, here missing:**
  - item and bin barcodes with scan-driven picking and counting (round C);
  - cycle counts;
  - a putaway rule table;
  - warehouse dashboards.

### Production and How it's made
- **Works:**
  - planned and released runs;
  - the run board, schedule and Gantt (legend and labels fixed in g25);
  - issues and output at the station;
  - spec-driven BOMs;
  - costing and variances.
- **Defects:**
  - O404: /production/work-orders/new hung on "Opening..." (fixed in g25).
  - A run cannot be made by hand, only from the plan. That is a design choice; Odoo allows it.
  - O405: the demo has no fabric, tape, film or bag specifications, so its distinctive feature cannot be
    shown.
- **Odoo has, here missing:**
  - operator step instructions;
  - block or warn on over-issue per BOM;
  - split and merge of a run.
- **g26 covers:** equipment records, machine counters and BIS test plans.

### Plant (maintenance and energy)
- **Works:**
  - maintenance jobs, schedules and calendar;
  - reliability (breakdowns are jobs);
  - energy meters and tariffs.
- **Missing:**
  - equipment master data (serial, vendor, warranty);
  - operator-raised maintenance requests.
- The demo has no meters (O405).

### Quality
- **Works:**
  - inspections with plans;
  - SPC, AQL sampling, certificates, calibration;
  - decisions waiting on the Quality Manager.
- **Gaps:**
  - O405: the demo has no plans and no inspections, so none of it can be shown.
  - Missing against Odoo: photo, measure and worksheet check types, and quality teams.
- **Round C of g25 covers:** supplier certificates of analysis against the specification.

### Accounts
- **Works:**
  - chart, journals, periods and closing;
  - trial balance, P&L, balance sheet;
  - GST returns, GSTR-2B matching, e-invoice and e-way payloads;
  - reverse charge;
  - TDS and TCS;
  - assets with tax depreciation by block;
  - closing-rate restatement;
  - the edit log.
- **Gaps:**
  - Missing against Odoo:
    - bank reconciliation rules and XLSX import;
    - a vendor payment file (NEFT bulk);
    - a cheque register;
    - Schedule III statements, cash flow and day book;
    - GSTR-9 and 9C;
    - MSME-1;
    - deferrals;
    - e-invoice sent through a GSP rather than by hand.
  - Export papers (shipping bill, eBRC) are a must-have before the first export (g26).

### People and payroll
- **Works:**
  - employees, attendance, leave and claims;
  - pay runs worked out by HR and posted by the Controller;
  - bank file, PF and ESI files, PT and LWF registers;
  - advances;
  - statutory figures as dated, confirmed data.
- **Defects:**
  - O402: the go-live check said a missing minimum wage stops posting, but a pay run posts. The minimum
    wage is read only by the bonus; no wage is checked against it. Wording fixed in g25; the check
    itself is must-have H2, in g26.
  - With no statutory rows loaded, PF and overtime work at the code's defaults (by design; the go-live
    check fails until confirmed figures exist).
- **Must-haves still open:**
  - final settlement within two working days (H1);
  - the minimum-wage check (H2).
- **Missing against Odoo:**
  - onboarding and exit checklists;
  - accrual plans;
  - off-cycle payslips;
  - nominee and dependants;
  - salary TDS (if the accountant says pay passes the limit).

### Settings and platform
- **Defects:**
  - O403: no countries on a fresh plant.
  - O399: history shows stored values ("ex_works") rather than labels.
  - O407: 83 of 144 New forms start with a blank date.
- **Fixed in g25:**
  - O392: screens grouped into menus per module;
  - the phone module bar;
  - three dead links, with a check over every link;
  - O394: loading rows.
- **Missing against Odoo:**
  - pivot tables and .xlsx export on any list;
  - an owner's dashboard;
  - inbound mail on records;
  - a customer and vendor portal;
  - record rules beyond reps;
  - an installable tablet app.

## 3. What to do, in order

1. **O395 and O396, now in g25.** They make the books wrong on the first day of real use.
2. **O403: seed countries and standard adjustment reasons** in setup_indian_books, and say so in the
   empty choice.
3. **O397, O398 and O401:** part shipment, the receipt's reference, and lines before the first save.
   These are the daily friction of stores and sales.
4. **O405: a demo that shows the plant's own features.** The specifications, inspections and a gate
   login are what a buyer of this system asks to see first.
5. **The rest of g25 (rounds B and C), then g26's must-haves:** export papers, final settlement, the
   minimum wage, BIS.
6. **Then the "Odoo has" lists above, pivot and .xlsx first:** the first monthly review will ask for a
   spreadsheet.

The suite passing said none of this. The two books defects were found by using the screens, which is
the one test that had not been run.
