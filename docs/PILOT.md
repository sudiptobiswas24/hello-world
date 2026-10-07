# The pilot: a fortnight before anything is switched off

Every flow in this system has been driven in a browser by tests, as each
role. None of that says whether the people at Deccan Polysacks can do
their day's work in it. The pilot finds that out while the old system
still carries the business, so a problem costs a day, not a month-end.

## Before it starts

1. Install on the server (RUNBOOK.md) and restore nothing yet.
2. Import a **copy** of the old system's records as they stand on the
   pilot's first day (docs/IMPORT.md), employees and logins included,
   and which rep carries each customer: a rep sees only their own.
3. `python manage.py go_live_check` until it reports no failure, and
   read its warnings aloud to whoever owns each.
4. Pick the people: two or three per desk, the ones who will be asked
   when something goes wrong later.

| Desk | Role to give them | Their flows to run every day |
|---|---|---|
| Sales | Sales Rep, AR Manager | order → delivery → invoice → money received; a credit note; aging; a new customer's delivery address and contact, on its page; a price list's breaks; a polymer clause billed at the month's index; a call-off against a schedule; the reminders as they fall due; a buyer's short payment for torn bags credited as a claim, and the quarter's claims by reason; before the order: an enquiry written down as a lead, the call logged with its follow-up, the lead converted to a customer and its first opportunity, valued and quoted from there, won with the order or lost with why; the pipeline by stage at its chance; what each campaign brought; follow-ups due on the home page; before the order: an enquiry written down as a lead, the call logged with its follow-up, the lead converted to a customer and its first opportunity, valued and quoted from there, won with the order or lost with why; the pipeline by stage at its chance; what each campaign brought; follow-ups due on the home page; a confirmed order's acknowledgement and a proforma invoice (with where to pay, once Settings → Company names the bank) printed and emailed from the order |
| Dispatch | Warehouse Staff | ship an order, choosing batches; a customer return; the transporter, LR and vehicle on each delivery, before the e-way bill; the customer's receipt on each delivery (when, by whom, their GRN number), and the deliveries still unacknowledged |
| Purchasing | Purchasing Clerk | purchase order → confirm; close a short line; a requisition approved and ordered; an RFQ put to two vendors and awarded; a release against a blanket order; the agreed vendor prices and reorder rules; what to reorder, from the rules against stock, orders and commitments |
| Stores | Warehouse Staff, Stores Manager | goods in with batch numbers; stock on hand and its movements; a count of one rack, posted by the manager; a transfer between warehouses; fabric out to the laminator on a job-work challan, and what it lost; a customer's own granules received and the unused sent back; a new item and the other units it is bought or sold in; each item's stock class (raw material, packing, stores and spares, semi-finished, finished) for the bank statement; covers and straps drawn off the shelf as each bale is pressed, under the packing reason |
| Maintenance | Maintenance | a service raised from Maintenance due and completed; a breakdown raised from its stoppage; the day's meter readings; Electricity shows each meter's highest demand and latest power factor in the window, the figures the board bills and penalises on; each machine's positions (the bearing seat, the screen pack) with the spare each takes; a spare issued to a job names the position it went to, so how long a bearing lasts on loom 14 is read off the gaps; the critical spares with none on any shelf; a job that fell due is on the home page and in the morning mail, not only on the Due list |
| Accounts | AP Manager, Bookkeeper | bill from an order → payment; a journal; trial balance; a fixed asset bought and the asset register; the month's bank statement matched, its charges posted and closed by the Controller; a debt written off with its reason; TDS deducted on a contractor's bill and the month paid over by challan; what a cement buyer deducted, recorded from its remittance; the quarter's TDS list against the old system's 26Q; the MSME payments list each week, paying what is near its 45 days first; a transporter's freight bill matched to the deliveries it charges for, and what is not yet billed; the bank's monthly stock statement (stock by class at cost, work in progress, creditors, debtors within ninety days) and the drawing power at the bank's margins, read at any date; the cost centres, a centre named on a bill's expense lines and on a department, and the month's costs by centre footing to the profit and loss; the month closed from the periods screen and a late entry refused; the quarter's budget read against the books; the rent accrual taken from its schedule each month; the bank's own export imported onto the statement; a vendor's remittance advice (the bills it settles, the account it went to) and a customer's payment receipt, printed and emailed from the payment |
| Production | Production Planner, Supervisor | plan → firm → release; the machine schedule; material issued to a run and the day's output and time reviewed against the stations'; the month's forecast and any build-ahead weeks in the master schedule; what the plan says to expedite, defer or cancel; the draw ratio and quench and oven temperatures each tape run actually ran at, and each change as it is made; the shift register marked each day (present, half a day, absent, how late, overtime agreed), by hand or read in from the reader's punch file; yesterday's production by section first thing: made, scrap, waste %, kWh and kWh a kilogramme, and which meter went unread; two or three years of the old system's monthly shipments brought in (import_csv shipment_history), so the seasonal forecast proposes from the first month |
| Quality | Quality Inspector, Quality Manager | each batch's inspection read and posted; batch status before dispatch; a complaint and its actions; calibration due; goods held at receipt passed to a shelf or failed back to the vendor; an upheld complaint settled by credit, so it shows what it cost; a complaint's batches traced to what made them and to the tape line settings on those runs |
| Floor | Station | a shift's entries at one loom and one extruder; the meter's three dials (kWh, maximum demand in kVA, power factor) copied at the end of the shift from the station, against the meter on that station's line |
| How it's made | Process Engineer | the recipes, routings, machines and work-centre rates checked against the old system's before the first run is costed; any change to one before it is used; each sack's tape, fabric and bag specification and its print design; each sack's packing recipe (the cover, straps and label a bale takes), on the item |
| Costing | Controller | the polymer rate sheet and conversion rates against the old costing sheet; the quotation policy; a standard-cost version rolled up, read and published; a sack costed and put on a quotation |
| Settings | Controller | the month's exchange rates; units, payment terms and taxes as the old system had them; document numbering, charge types and the manufacturing accounts as the old system had them; the TDS sections with this year's rates and thresholds, and each vendor's PAN and section; each micro and small vendor's Udyam number; the plant's licences (factory, consent to operate, fire NOC, stamping) with when to start renewing each, and each renewal recorded as it comes; Ctrl+K and a number, code, name or lorry registration lands on the record itself, among the kinds the login may read; every list and report leaves as a CSV of its columns; every login's home page opens on what fell due for them (the morning checks), and the cron line in RUNBOOK.md mails the same at six; the old system's records brought in from Bring old records in, a kind at a time, checked and then kept; a login made for the new clerk with its role, and the leaver's deactivated (HR Admin); Health shows the books agreeing with themselves (trial balance, every entry, each control account against its documents, stock against the ledger, closed months, invoice numbers) and the server's backup and disk, and Problems people hit holds every failure under the reference the person was shown, dealt with and noted |
| GST | GST Officer | the month's GSTR-1 and 3B, compared with the old system's; each new customer's GSTIN, checked on the portal and entered on its page before its first invoice; an e-invoice and an e-way bill prepared for an invoice; a transporter's freight bill under reverse charge, and its 3.1(d) and 4(A)(3) in the month's 3B; the month's GSTR-2B kept from the portal's file and read against the bills (Input credit against GSTR-2B): which credit is matched, which bills still wait on their suppliers, which filed invoices have no bill here |
| HR | Payroll Officer, Controller | a pay run worked out, posted, and its statutory dues, with the month's PF ECR and ESI files taken off the posted run; somebody taken on, with their department and pay; the statutory bonus and gratuity accrued in the month's pay run, and gratuity owed set against the provision; each labour contractor's licence and the workers it brought; the punch file checked before it is kept, the unmarked days of day-rated workers before the run, and absences and overtime read onto the slip |

## Every day of the pilot

- Each person enters **the same day's real work** in both systems.
- At the end of the day, one person compares three numbers between the
  old system and this one, and writes them in a shared sheet:
  receivables owed, payables owing, and stock value. A difference that
  is not explained by something the old system does differently is a
  defect: write down the document that caused it.
- Anything that took longer than it should, was refused for a reason
  nobody understood, or could not be done at all goes in the same sheet
  with who, what screen, and what they were trying to do. A screenshot
  is better than a description.

## At the end of the fortnight

The pilot passes when, for the last five working days:

- the three numbers agree with the old system, or every difference is
  explained;
- each desk has done every flow in the table at least once without
  help;
- the month's GSTR-1 and 3B compiled here agree with what the old
  system would have filed;
- a backup was restored into a scratch database and read (RUNBOOK.md,
  "Proving a backup restores").

Then choose the go-live date: a month's first day, so the old system
closes a whole month. On that morning, import the opening position as
it stood at the old system's close, run `go_live_check`, and switch.

## What the pilot cannot tell you

Speed with years of data (the pilot has a fortnight's), and what
happens at a year-end close. Both were measured on generated data and
are in docs/RISKS.md.
