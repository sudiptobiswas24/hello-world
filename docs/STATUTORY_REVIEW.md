# Statutory outputs: a pack for the accountant to check

Prepared 11 November 2026 from the demo plant (Deccan Polysacks Pvt Ltd, GSTIN 27AAECD4821K1ZL, Maharashtra),
for the month of **September 2026**. Every file was produced the way the office would produce it: signed in as
the demo accountant, payroll officer or purchase officer, through the application's own screens' endpoints.
Nothing was filed anywhere; the system files nothing by itself.

Until now each of these outputs had been checked only by the person who wrote the code. This pack is for someone
outside to check in about an hour. Each section below is one output: what the file is, the three or four figures
to compare with the old system or the portal, what an independent recomputation found, and the questions only
you can answer.

**How the figures were checked.** A separate script (not the application's code) rebuilt every headline total
from the document lines in the database: quantity x price x exchange rate, rounded per line; tax as taxable
value x the rate, rounded per line; payroll from the payslip lines. 77 figures were compared. 74 agree to the
paisa. The other three are not sums that failed: two show what provident fund wages would be under a different
*rule* (section 7), and one is the 2B match, where nothing was uploaded to match against (section 4).

**What is real and what is not.** The demo plant is invented. On top of it, five things had to be entered before
some files would come out at all, because the demo plant lacks them: a UAN and a date of birth for each of the
14 employees (invented numbers), a company address, the date e-invoicing began (1 April 2026), the dated
statutory figures from the `statutory/` folder, and a confirmation of the 194C rate. Three outputs could not be
produced with any content even so: the e-way bill, the export e-invoice and a TDS deduction with its challan.
Those sections say why.

Marks used below for the form of each file: **matches** (agrees with the format as the reviewer knows it),
**differs** (and how), **cannot verify** (needs the government's current specification, which the reviewer did
not have; rules change, so treat every "matches" as "matched what was known in mid-2026").

---

## 1. GSTR-1 (outward supplies), September 2026

Files: `gstr1_2026-09_summary.json` (the tables as the office reads them) and
`gstr1_2026-09_offline_tool.json` (the same figures in the shape meant for GSTN's offline tool).

| Compare | Figure in the file |
|---|---:|
| B2B invoices (table 4A), count | 4 |
| B2B taxable value | 33,88,200.00 |
| B2B tax: IGST / CGST / SGST | 3,18,816.00 / 1,45,530.00 / 1,45,530.00 |
| B2B invoice value | 39,98,076.00 |
| Exports without payment of tax (table 6A): 1 invoice, USD 17,100 at 83.45 | 14,26,995.00, IGST nil |
| HSN 63053300: quantity to registered buyers / exported | 2,97,000 nos / 90,000 nos |
| Documents issued (table 13) | INV-2026-00005 to INV-2026-00009: 5 issued, 0 cancelled |

By place of supply, all at 18%: Maharashtra (27) 16,17,000; Karnataka (29) 6,27,600; Madhya Pradesh (23)
2,73,600; Andhra Pradesh (37) 8,70,000. Recomputed independently: no difference on any figure.

Form:
- Section names `b2b`, `b2cl`, `b2cs`, `cdnr`, `cdnur`, `exp`, `at`, `txpd`, `nil`, `hsn`, `doc_issue`, and the
  keys inside a B2B invoice (`ctin`, `inum`, `idt` as dd-mm-yyyy, `val`, `pos`, `rchrg`, `inv_typ`, `itms` /
  `itm_det` with `rt`, `txval`, `iamt`, `camt`, `samt`, `csamt`); `fp` as `092026`: **matches**.
- The export under LUT is shown as `exp_typ: WOPAY`, zero-rated, IGST 0, as the owner decided: **matches**.
- HSN summary: the file writes `"hsn": {"b2b": [...], "b2c": [...]}`. The reviewer's recollection of the
  offline tool after the 2025 split of table 12 is `hsn_b2b` and `hsn_b2c`: **differs, to be confirmed in the
  tool**.
- Export invoice lines are written nested (`num`, `itm_det`). The reviewer's recollection is that export lines
  are flat (`txval`, `rt`, `iamt`, `csamt`): **differs, to be confirmed in the tool**.
- The export invoice carries no shipping bill number, date or port code (`sbnum`, `sbdt`, `sbpcode`); the system
  does not hold them: **differs** (they are optional to upload, but needed before a refund).
- The file has no `gt` / `cur_gt` (turnover), `version` or `hash` header: **cannot verify** whether the tool
  still wants them.
- HSN digits: every line carries 8 digits (63053300), enough at any turnover: **matches**. The return itself
  does not warn about a 4-digit code; only the e-invoice refuses fewer than 6.

Questions for you:
1. Please load `gstr1_2026-09_offline_tool.json` into the current offline tool. Does it import cleanly? If
   not, which sections does it refuse?
2. An export under LUT is reported here at rate 0% with IGST nil, in table 6A and in the HSN summary (B2C
   table). Do you report such a supply at 0%, or at the goods' own rate (18%) with the tax left nil?
3. The demo bills woven polypropylene sacks under HSN 6305 33 00 at 18%. The reviewer recalls a CBIC circular
   placing PP woven sacks under 3923 at 18%, and chapter 63 at other rates. Which heading and rate does the
   plant actually bill under?
4. Does the export's place in the HSN summary (the B2C table) agree with how you file it?

## 2. GSTR-3B (monthly summary), September 2026

File: `gstr3b_2026-09.json`.

| Compare | Figure in the file |
|---|---:|
| 3.1(a) taxable outward supplies | 33,88,200.00 |
| 3.1(a) IGST / CGST / SGST | 3,18,816.00 / 1,45,530.00 / 1,45,530.00 |
| 3.1(b) zero-rated (the export) | 14,26,995.00, IGST nil |
| 4(A)(5) input tax credit: IGST / CGST / SGST | 3,26,880.00 / 35,685.00 / 35,685.00 (3,98,250.00 in all) |

Recomputed independently from the purchase bills and sales invoices: no difference. The same figures stand in
the GST ledger accounts for the month (output 2201-2203, input 1311-1313).

The file itself lists what it does not build: imports (4(A)(1), (2)), credit reversed or ineligible (4(B),
4(D)), amendments, e-commerce operators, and the set-off in table 6.1 other than reverse charge. So it gives
the tax and the credit, not the cash to pay.

Questions for you:
1. The credit in 4(A)(5) is every posted bill from a registered regular supplier in the month. It is **not**
   limited to what GSTR-2B shows, and the file gives no warning: for September the 2B match (section 4 below)
   says none of the three bills (taxable 22,12,500, tax 3,98,250) is in a 2B statement, and 3B still shows the
   whole 3,98,250. Should 3B claim only what is in 2B, or warn, or stay as the books with the match read beside
   it?
2. Is it acceptable that the set-off (which credit pays which tax) is done by you on the portal and not here?

## 3. ITC-04 (goods sent to job workers), April to September 2026

File: `itc04_2026-04_to_2026-09.json`. It is empty: the demo plant sent nothing out for job work in the half
year. The file says it does not build table 5B (job worker to job worker), table 5C (supplied from the job
worker's premises) or components sent on a subcontract purchase order. **Cannot verify** the form on an empty
file.

Question: does the plant send material on subcontract purchase orders? If so, this return will understate what
went out, and you will need another list.

## 4. GSTR-2B match, September 2026

File: `gstr2b_match_2026-09.json`. No 2B statement was uploaded for the demo, so every bill is listed as "not
in 2B": six bills, taxable 41,56,000.00. The list runs from the start of the financial year to the month asked,
so August's three bills (19,43,500.00) appear beside September's three (22,12,500.00).

Question: when you ask for September, do you expect earlier months' unmatched bills in the same list?

## 5. E-invoice (one invoice)

Files: `einvoice_INV-2026-00005.json` (within Maharashtra, CGST + SGST) and `einvoice_INV-2026-00006.json` (to
Karnataka, IGST). The `payload` inside each is what would be sent to the invoice registration portal.

| Compare (INV-2026-00006) | Figure in the file |
|---|---:|
| Assessable value | 6,27,600.00 |
| IGST | 1,12,968.00 |
| Invoice value | 7,40,568.00 |
| Lines | 30,000 nos at 14.20 and 18,000 nos at 11.20, HSN 63053300, 18% |

Recomputed: no difference; the lines' values add to the invoice value.

Form, against schema version 1.1 as the reviewer knows it: `Version`, `TranDtls` (`TaxSch`, `SupTyp`),
`DocDtls` (`Typ`, `No`, `Dt` as dd/mm/yyyy), `SellerDtls` and `BuyerDtls` (`Gstin`, `LglNm`, `Addr1`, `Loc`,
`Pin`, `Stcd`, and `Pos` for the buyer), `ItemList` (`SlNo`, `IsServc`, `HsnCd`, `UnitPrice`, `TotAmt`,
`AssAmt`, `GstRt`, `TotItemVal`), `ValDtls` (`AssVal`, `TotInvVal`): all present, **matches**. The file warns
that the invoice is more than 30 days old.

Not produced: the e-invoice for the **export** (INV-2026-00007). The demo customer's address names no country,
the system refuses the payload without one, and an address already printed on a posted invoice cannot be
changed. From reading the code, an export under LUT would go as `SupTyp: EXPWOP` with `GstRt` 0 and IGST 0:
**cannot verify** against the portal.

Questions for you:
1. For an export under LUT, does the portal accept the item at rate 0, or does it want the goods' rate (18%)
   with the tax amount nil?
2. From which date, and above which turnover, does the plant e-invoice? The system only asks the date.

## 6. E-way bill (one invoice)

Not produced. Every demo address has no state, the e-way bill builder refuses a delivery address without one,
and the address of a posted invoice cannot be corrected afterwards. (The e-invoice for the same invoice was
built, because it reads the state from the buyer's GSTIN.) **Cannot verify** the form.

Open question already on the register, **O449**: when goods are billed to one party and shipped to another's
premises, the e-way bill names the buyer (GSTIN and name) at the third party's address, and gives no GSTIN or
name for the party the goods actually go to. Illustration: billed to a buyer in Pune, delivered to a job worker in
Mysuru; the bill would show the Pune buyer's GSTIN against the Mysuru address. The e-invoice does name the
consignee. What do the portal's "ship to" fields need: the consignee's own GSTIN and name, or the buyer's?

## 7. Provident fund ECR, September 2026

File: `ecr_2026-09.txt`. One line a member: UAN, name, gross wages, EPF wages, EPS wages, EDLI wages, employee
share, EPS share, employer's difference, NCP days, refund of advances, separated by `#~#`.

| Compare | Figure in the file |
|---|---:|
| Members | 14 |
| Gross wages / EPF wages | 5,75,500 / 5,75,500 |
| EPS wages and EDLI wages (15,000 a member) | 2,10,000 |
| Employee share (12%) | 69,060 |
| Pension share (8.33% of EPS wages) | 17,500 |
| Employer's difference | 51,560 |
| Total owed to the fund in the books (PF payable) | 1,38,120 |

The arithmetic agrees line by line: 12% of the EPF wages in the file, 8.33% of the EPS wages (1,249.50 rounds
to 1,250 a member), and the difference. Example, first line: gross 80,000, EPF wages 80,000, EPS wages 15,000,
employee 9,600, pension 1,250, difference 8,350.

Form: eleven fields, `#~#`, whole rupees, one member a line: **matches** the ECR layout as the reviewer knows
it. Not present: any header, or the establishment's totals; the reviewer believes the portal wants neither.

What differs is the **rule the demo plant is set up with**, not the sums:
- The demo takes provident fund on basic **plus house rent allowance**, with no wage ceiling. The first member
  has basic 62,000 and HRA 18,000 and pays 9,600. On basic alone it would be 7,440; on basic limited to 15,000
  it would be 1,800. For the whole plant: EPF wages 5,75,500 in the file, 4,47,500 on basic alone, 2,10,000
  on basic limited to 15,000. The plant's own setup command (as opposed to the demo) takes basic and dearness
  allowance up to the ceiling.
- The books owe 1,38,120. Nothing is calculated for EPF administration charges or the EDLI contribution. By
  the reviewer's arithmetic at 0.5% each: 2,877.50 on EPF wages and 1,050.00 on EDLI wages, so the challan
  would be about 1,42,048, not 1,38,120.

Questions for you:
1. On what wages does the plant pay provident fund: basic and DA only, and limited to the ceiling or on full
   wages? Is HRA ever included?
2. Should the system work out the administration charge and the EDLI contribution, or do you add them when
   paying the challan?
3. The figures on file say the wage ceiling rose from 15,000 to **25,000 on 17 September 2026** (S.O. 5109(E),
   noted as "Gazette not read", waiting for your confirmation), and that the system will pay the whole of
   September at whatever ceiling stands on the 30th. This ECR was worked at 15,000. Is the notification as
   described, and is September paid at 15,000, at 25,000, or split at the 17th? At 25,000 the pension share of
   a member earning more than that would be 2,082.50, not 1,250.
4. No member here is over 58. The system stops the pension share from the month after a member turns 58 and
   puts the whole employer share to EPF. Is that how you file?

## 8. ESI monthly contribution, September 2026

File: `esi_2026-09.csv`: IP number, name, days, wages, reason code, last working day.

| Compare | Figure |
|---|---:|
| Insured persons (wages up to 21,000) | 1 (wages 20,500, 30 days) |
| Employee 0.75% | 154 (153.75 rounded up) |
| Employer 3.25% | 667 (666.25 rounded up) |
| ESI payable in the books | 821 |

Recomputed: no difference. Two people on 22,200 and 23,500 are correctly left out.

Form: the six columns of the portal's upload template as the reviewer remembers them: **cannot verify** the
exact heading text, and the portal takes a spreadsheet, so this is to paste in.

Questions for you:
1. The wages (20,500) include HRA. Is that right for ESI at the plant?
2. Days are given as 30 for a month in which the payslip says 22 days paid. Which does the portal want?
3. A person who crosses 21,000 mid-way through a contribution period stays covered to its end. The system
   holds a six-month coverage rule; please test one such person against your records.

## 9. Professional tax (Maharashtra), September 2026

File: `professional_tax_2026-09.json`.

| Compare | Figure |
|---|---:|
| People taxed | 13 of 14 |
| Tax for the month | 2,600 (200 each) |
| Wages in the register | 5,52,700 |

Recomputed against the Maharashtra schedule as the reviewer knows it (men: nothing to 7,500, 175 to 10,000,
200 above; women: nothing to 25,000, 200 above; 300 in February): no difference. The one person not taxed is
a woman on 22,800. **Matches.** The system keeps no Form III-B; this is a register to fill it from.

Question: does every employee work in Maharashtra? The system reads the schedule from each person's work state.

## 10. Payslip

Files: `payslip_2026-09_first.pdf` (employee 101) and `payslips_2026-09.json` (all 14).

Employee 101: basic 62,000 + HRA 18,000 = 80,000; provident fund 9,600; professional tax 200; net 70,200.
Recomputed: no difference. The slip shows the employer's provident fund share (9,600) beside it.

Questions for you: does the slip need the UAN, the PF and ESI numbers, the bank account, and leave balances
printed on it for your inspector? It carries none of them. See also question 1 of section 7: the 9,600 is on
basic plus HRA.

## 11. TDS: the quarter's list and a challan, July to September 2026

Files: `tds_quarter_2026-07_to_2026-09.json`, `tds_challans.json`, `tds_deductions.json`. All three are
**empty**. The demo plant deducts no tax, and one could not be entered: it has no TDS sections until the
statutory figures are loaded, the loaded rates wait for the accountant's confirmation, and the demo's chart of
accounts has no TDS payable account for a section to owe into. The independent check of TDS by section could
therefore not be done.

What the empty file does show is its heading: TAN PNED04821F, "Income-tax Act 2025", "Form 140", "Challan ITNS
281N". The rates on file from 1 April 2026 cite section 393(1) of the 2025 Act (contractors 1% and 2%,
professional fees 10%, technical fees 2%, rent 2% and 10%, commission 2%, purchase of goods 0.1%), each from
secondary sources and each waiting for confirmation. **Cannot verify** any of these names or rates.

Questions for you:
1. Under the Income-tax Act 2025, what are the quarterly statement and the challan called, and are "Form 140"
   and "ITNS 281N" right?
2. Please confirm or correct each rate and threshold on the TDS rates screen; nothing can be deducted at a
   rate until you do.
3. The quarter's list is a working list (deductee, PAN, section, amount credited, rate, tax, challan, date
   paid). It is not the file the return utility takes. Is a list enough for your return preparer?

## 12. MSME payments list, April to September 2026

File: `msme_bills_2026-04_to_2026-09.json`.

| Compare | Figure |
|---|---:|
| Bills from micro and small vendors | 1 (BILL-2026-00002, Western Masterbatch, small, Udyam on file) |
| Value | 2,34,230.00 |
| Due under the Act / paid | 13 September 2026 / 8 September 2026 |
| Unpaid past its day at 30 September (section 43B(h)) | nil |

Recomputed: count and value agree. The due day is the agreed 30 days from the bill of 14 August, inside the 45.

Open question already on the register, **O500**, in two parts:
1. A bill with no payment terms is read as "nothing agreed", so the Act's 15 days apply. Illustration: goods
   accepted 1 June, no terms on the bill: due 16 June. If there was an unwritten understanding, the limit would
   be 45 days: 16 July. Which should the system assume when the bill is silent?
2. While a written objection to the goods stands on a bill with no terms, the system counts from the bill's
   own date, because the Act names no day. Is that the day you would use?

## 13. Tax invoice (PDF)

Files: `tax_invoice_INV-2026-00005.pdf` (Maharashtra), `tax_invoice_INV-2026-00006.pdf` (Karnataka),
`tax_invoice_INV-2026-00007.pdf` (export).

INV-2026-00005: 1,65,000 nos at 9.80, taxable 16,17,000.00, CGST 9% 1,45,530.00, SGST 9% 1,45,530.00, total
19,08,060.00, in words. It shows both GSTINs, both states with codes, place of supply, HSN, "Reverse charge
No", "Original for recipient" and a signatory line: **matches** rule 46 as the reviewer knows it.

The export invoice is in US dollars only (17,100.00 at 83.45), IGST 0%, with the words "Supply meant for
export under bond or letter of undertaking without payment of integrated tax" and the LUT's ARN and date.

Questions for you:
1. Should the export invoice also show the rupee value (14,26,995.00), the country of destination and the
   delivery address? It shows the port of discharge and the buyer's city only.
2. Before an IRN is recorded the invoice prints "IRN not yet registered". Is an invoice ever handed over in
   that state?

---

## 14. Built in g38, not yet seen by the accountant

These are choices the code makes and says. Each is to be confirmed or corrected; the figures are settings
marked "not confirmed" until the Controller confirms them.

**Labour welfare fund (Maharashtra).** 25 (employee) and 75 (employer) a half-year from 18 March 2024, under
the MLWF (Amendment) Act 2024, recalled from compliance summaries; the Gazette was not seen. Confirm the
figures, the day and the Act's number. Before it: 6 or 12 and 18 or 36, by wages. Also: is the half-year
deducted in June and December, or on those months' wages paid in July and January?

**Statutory bonus.**
- Paid leave counts as days worked (s.14, through the slip's paid days); lay-off and maternity days do not
  (they are unpaid days here). Confirm both.
- Wages counted are basic and DA within the calculation ceiling, and only months within 21,000; eligibility
  is judged month by month, not on the year's average.
- The payout is in no levy's base: confirm it is outside PF basic wages and ESI wages.
- Payment within eight months of the year's close (s.19): the statement shows "pay by" 30 November, but a
  later payout is not refused.
- Allocable surplus and set-on and set-off (ss.15-16) are not worked out; the per cent above the least is
  typed by the Controller.

**Gratuity.** 15/26 of basic and DA per counted year (s.4(2)); the 4.81% provision is that arithmetic, not
a notified rate, and understates once wages rise. No actuarial valuation (AS 15 / Ind AS 19). The ceiling
is a constant (O551).

**MSME interest (s.16).**
1. Days of interest: the day after the due day through the day of payment, both counted; 365 days a year.
2. Monthly rests are counted from the first day of interest, not by calendar month.
3. A bank rate changed during the delay applies from its own day.
4. "Bank rate" is the Reserve Bank's Bank Rate, not the repo rate.
5. Terms longer than 45 days are capped at 45 from acceptance, not treated as no agreement (15 days).
6. The payment terms on the bill are taken as the agreement in writing.
7. A payment goes to principal, oldest part first, never to interest first.
8. Interest paid to a supplier is not recorded, so s.22's "interest paid" reads "not recorded".
9. A debit note, tax deducted, a prepayment drawn or a discount settles its part on its own day.
10. Whether s.15 reaches a micro or small supplier that is only a trader is not asked.

**Reverse charge.**
1. The self-invoice is dated the day the bill posts, not the day of receipt, and is raised by whoever may
   post the bill.
2. The order in which credit is applied (s.49(5), s.49A, Rule 88A), the split of IGST credit and Rule 86B's
   1% in cash are left to the portal.
3. 3.1(a) + 3.1(b) is read as the whole of the tax credit may set off; interest and late fee are not in it.

**E-way bills for lots and job work.**
1. Portal sub-supply type 9 ("SKD/CKD/Lots") for each consignment of an invoice sent in lots, every lot but
   the last on its delivery challan and the last on the invoice (Rule 55(5)(b), (d)); for an export in lots it
   replaces sub-type 3. Confirm against the portal's current masters.
2. A lot's need of an e-way bill is judged by the lot's own value against the limit (Rule 138(1)), not the
   whole invoice's. Confirm.
3. Rule 138(1)'s third proviso is read as where the goods go: a job worker registered in another state taking
   goods at works inside this state is said in the bill's reason as not required. Confirm.
4. Rule 55(5) has the invoice issued before the first lot leaves; this plant ships, then invoices (O559).

## Decisions waiting on the register that touch these outputs

**O448. A goodwill credit of part of an invoice, then all the goods come back.** An invoice of 1,000; a
goodwill credit note of 600 is given; later the customer returns every item. Today the system refuses the
return's credit note ("has 400 left to give back") and with it the goods' return. Should the return give back
the remaining 400, or give back nothing more and only take the goods in? (Where the goodwill credit was for the
whole 1,000, it is already decided: the goods come back and nothing more is credited.) The same question
stands for a purchase bill.

**O449. E-way bill for goods shipped to a third party.** See section 6.

**O494. A charge line entered with no tax.** A freight or packing charge whose master says it carries GST can
be put on an order with its taxes left empty, and it then posts untaxed; an item line in the same state is
refused. Illustration (the reviewer's numbers): freight 5,000 at 18% entered with no tax leaves 900 of GST off the invoice and off GSTR-1.
Should a charge that declares GST be refused when entered with none, as an item is, or is a deliberate "no
tax" on a charge something you need?

**O500. MSME bills with no terms.** See section 12.

## What the reviewer could not check at all

- Any format against the government's current published specification; there was no access to one.
- The e-way bill, the export e-invoice, a TDS deduction and challan, ITC-04 with content, and a 2B match
  against a real statement: the demo plant cannot produce them.
- Credit notes, debit notes, B2C sales, advances, reverse charge, SEZ supplies and nil-rated or exempt
  supplies: September's demo documents contain none, so those tables of GSTR-1 and 3B are untested here.
- Part months, leavers, unpaid leave, a second pay run in a month, and anyone over 58: the demo's posted pay
  run has none.
- Labour welfare fund: the register is empty in the demo.
