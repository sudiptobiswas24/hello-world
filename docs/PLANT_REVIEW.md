# A woven-sack plant, end to end: what the system covers and what it does not

Walked as the material goes: granules in, tape, looms, lamination and
printing, conversion, baling, dispatch; then quality, maintenance,
people, money and the statutory side. Reviewed 2026-10-05 against the
code, not the documentation.

## What is covered well

Granules to bale are modelled in more depth than most ERPs for this
trade: bag specifications that compute weight, GSM and thread from
denier and mesh (`woven.py`), tape lines, looms as individual machines
with speeds from process parameters, lamination, BOPP and metallic
film, printing with cylinder lead times, liners, valve and block-bottom
bags, regrind back into stock, bales with labels and traceability back
to granule lots, station entry by PIN on the floor, scale bridges, OEE,
energy per machine, piece-rate wages, finite-capacity MRP and extruder
campaigns. Quality has test certificates, AQL sampling, SPC, calibration,
third-party inspection release, complaints with CAPA. Money has GST
returns, e-invoice and e-way bill payloads, ITC-04 for job work, price
variation clauses on polymer, deposits, prepayments, payroll with PF,
ESI and professional tax.

## What is missing, by what it costs the plant

### Statutory: money and notices if left out

1. **TDS and the TDS customers deduct.** Cement and fertiliser buyers
   deduct TDS (194Q on goods, 194C on job work) and pay the invoice
   short; today that remainder sits on the invoice as owed until
   someone writes it off, and nothing records it as TDS receivable to
   match against Form 26AS. On the buying side, nothing deducts TDS
   from contractors (194C), rent (194I), professionals (194J) or large
   goods purchases (194Q), books it payable, or lists it for the
   quarterly 26Q. *The most common gap an auditor would raise.*
2. **Reverse-charge GST.** Freight from a goods transport agency is
   under reverse charge, and every plant pays freight. The tax is not
   booked, and GSTR-3B 3.1(d) and 4(A)(3) are listed as not built.
3. **MSME vendors paid in 45 days.** Since 2024, a payment to an MSME
   vendor later than 45 days is not deductible for income tax (section
   43B(h)). Nothing marks a vendor as MSME or lists bills going past the
   45 days.

### The floor: what supervisors will ask for in the first week

4. **Attendance.** Payroll pays a month less unpaid leave; nothing
   records who came to which shift, late, absent or overtime, and no
   biometric file can be read in. Piece rates come from production
   counts, but day-rated workers' pay has no attendance behind it.
5. **Preventive maintenance: built, not reachable.** *Corrected: the
   first pass said this was missing, and it is not.* Schedules by
   calendar and by running hours (whichever comes first, hours derived
   from time bookings), jobs raised from them and from breakdowns,
   spares issued to a job and returned, reliability by machine
   (`maintenance.py`). What is missing is any screen in the office
   application, and anything that tells maintenance a job fell due.
6. **A daily production report in the office.** The station has a
   shift report, and OEE, energy per run and idle energy are API
   endpoints; the owner's morning question, "how many tonnes did each
   section make yesterday, at what wastage and what kWh a kilo", has no
   screen: tape, fabric, lamination, bags and bales, against plan, with
   waste percent.

### Gate and stores

7. **Gate entry and returnable gate passes.** Material reaches the
   system at the goods receipt; nothing records the vehicle at the gate
   before that, and nothing tracks dies, cylinders or motors sent out
   for repair on a returnable gate pass and whether they came back.
8. **Transport on the dispatch.** A delivery carries no transporter, LR
   number or vehicle (the e-way bill payload takes them as given); the
   freight bill cannot be matched to the deliveries it charges for.

### Smaller, or only if the plant does it

9. **Export incentives** (RoDTEP, drawback, advance authorisation
   export obligations, shipping bill and realisation): only if the
   plant exports.
10. **Incoming material tests by name** (MFI of granules, masterbatch
    dosage checks) and **BIS test sets** for cement and fertiliser sacks:
    possible today as inspection plans with characteristics, but none is
    set up; worth a seeded plan per material.
11. **Scrap sales** to recyclers: works through an ordinary invoice of
    the waste item regrind puts into stock; no special flow needed, but
    nobody has tried it.
12. **Cheque printing and post-dated cheques**: printing is cosmetic;
    post-dated cheques received matter where customers pay that way.

### A second pass: narrower, each checked against the code

Already there, so not gaps: artwork approval per customer design and
cylinder life by impressions (`tooling.py`), weighbridge weights,
consignment stock, regrind.

13. **Labour law beyond payroll.** No contract-labour register (CLRA),
    though a sack plant's looms and conversion run largely on
    contractors' workers; no statutory bonus (Payment of Bonus Act,
    8.33% minimum, paid yearly) and no gratuity provision as a liability.
    The bonus is a cash outflow the first October nobody planned for.
14. **EPR for plastic packaging.** Woven PP sacks are plastic packaging
    under the Plastic Waste Management Rules: the plant registers with
    the CPCB and files annual returns of tonnes placed on the market by
    category and buyer. Nothing records which sales are packaging, to
    whom, and whether that buyer is EPR-registered, so the return is
    compiled by hand from invoices. Whether the obligation sits with the
    plant or the cement buyer depends on the buyer's registration;
    settle that with the plant's consultant before building.
15. **Customer deductions as money.** Complaints with CAPA exist, but a
    complaint carries no amount and leads to no credit note. Cement and
    fertiliser buyers pay short for torn bags, short weight or a
    disputed rate; that shortfall sits on the invoice as owed (the same
    symptom as TDS in 1, a different cause), and nobody can say what
    quality cost the plant last quarter.
16. **Bank stock statement.** A plant on a cash-credit limit sends the
    bank stock and receivables each month for drawing power (stock by
    class, debtors under 90 days, less creditors). Every figure exists
    here; the statement does not.
17. **Packing material at baling.** A bale takes a cover, straps and a
    label; making a bale consumes none of them, so stores' stock of
    covers never moves and their cost never reaches the bale.
18. **Proof of delivery.** A delivery has no received-on date or
    acknowledged copy; cement plants pay from their own receipt, and a
    disputed delivery is argued from paper.
19. **Tape line settings.** Draw ratio and quench temperature are not
    recorded per run, so a tenacity complaint cannot be traced to the
    setting that caused it, only to the granule lot.
20. **Licence calendar.** Factory licence, pollution consent to
    operate, fire NOC, weights-and-measures stamping: nothing reminds
    anyone before they lapse. Calibration due dates exist; these do not.

Only if the plant does it: a second GSTIN or depot in another state
(branch transfers), letters of credit and bank guarantees for tenders
and exports, proforma invoices for advance-paying buyers, diesel for
the DG set against kWh made.

### A third pass: the office cannot reach what the plant has built

**The largest gap is not a missing module.** The server has 173 API
endpoints; the office application has screens for about forty, all in
sales, purchasing, stores, accounts, payroll and production planning.
Maintenance, energy, OEE, downtime, quality (inspection, SPC, AQL,
calibration, complaints), forecasts, the master schedule, tooling and
lot trace are built, tested and reachable only through the API or the
Django admin. To the people who would use them, they do not exist.

21. **Screens for what is built**, in this order: production losses
    (OEE by machine and shift, downtime by reason), maintenance (what is
    due, open jobs, spares on them), meter readings and kWh a kilogramme,
    quality, forecasts.
22. **Nothing runs by itself.** There is no scheduler. Every check is
    asked for, never told: a maintenance job falling due, an instrument
    out of calibration, a shift's meter left unread, stock under safety
    level, an invoice past due, a draft nobody posted. Wanted: one job
    that runs the checks each morning and an inbox per role ("yours to
    act on today"), then mail.
23. **Search finds screens, not records.** The command palette goes to
    a screen by name; typing a bale, lot, invoice or vehicle number
    lands nowhere. Each list searches itself, one at a time.
24. **No reports leave the system.** Nothing but GST exports a file. A
    department head wanting a figure the screen does not show has no
    way out except asking someone with database access. Analytics is
    better served by a read-only replica and an off-the-shelf tool
    (Metabase, Superset) than by charts built here.
25. **Forecasting has no history to start from.** The seasonal
    forecast needs 12 months of shipments and the importer brings in no
    history, so for a year after go-live it proposes nothing. Importing
    two or three years of monthly shipments by item fixes that, and is
    a far smaller job than anything else in this list.
26. **Meters on the floor.** Readings are entered through the API; the
    station has no meter screen, so the shift supervisor who walks past
    the meter cannot record it. Maximum demand (kVA) and power factor,
    which set the demand charge and penalty on the bill, are not
    recorded at all.
27. **Machine counters.** Loom picks, extruder kg and bag counts come
    from people typing at the station. Short stops never reach OEE, so
    it overstates availability. Reading the counters a loom already
    has is where productivity data stops being opinion.
28. **What changed on a machine.** A job says spares were issued; it
    does not say to which position (which bearing, which screen pack),
    so "how long does a bearing last on loom 14" cannot be answered,
    and no machine lists its critical spares to keep in stock.

An assistant for users: worth it only after 21 and 23, and only
read-only, acting with the user's own permissions and answering with
links to the record. Most of what people would ask it ("where is",
"how much did we make") is a missing screen or a missing search, and
those should be built first.

## Order to build in, if the plant wants them

The statutory three first (1-3): they cost money each month they are
missing and an auditor will find them. Then attendance (4), because pay
depends on it from the first month. Then the daily production report
(6) and preventive maintenance (5), which the supervisors will ask for.
Gate and transport (7, 8) after the pilot shows how the gate works
today. Export (9) only if it applies.

From the second pass: labour law (13) and customer deductions (15)
sit with the statutory three, because they cost money every month; the
bank statement (16) as soon as the bank asks; EPR (14) once the
consultant says whose obligation it is.

From the third pass, before any of the rest: screens for what is built
(21) and the morning checks (22), because each is weeks of work already
paid for and unused; the shipment history import (25), because it is
small and the forecast is useless for a year without it.
