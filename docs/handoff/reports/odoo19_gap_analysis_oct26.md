# Gap analysis against Odoo 19, redone on the g23b tree (26 October 2026)

Code read: /home/user/work/g23btree at ea1c8d4 (g23b, gate in flight). Paths below are under it.
Odoo evidence: odoo.com was blocked from this machine (CONNECT 403), so Odoo 19.0 claims are not freshly fetched. They come from (a) the 14 October research notes in docs/handoff/reports/odoo19_research/*.md, which cite the 19.0 page for every row (the URL is repeated here where it matters), and (b) the 19.0 documentation source read from raw.githubusercontent.com/odoo/documentation/19.0/content/applications/... (India localisation and India payroll page headings re-read today). Edition (C = Community, E = Enterprise) is inferred from the module tree, as in the old report. Release notes were not reachable. Indian-law figures are the old report's secondary-source figures and are not re-verified.
Rating: G = needed before the first real month, S = soon after, L = later. "H:" = what HANDOFF section 2 already schedules (L-numbers are RISKS "Later items"; g24 = L16 L17 L18 L23 L28 L21 L9; g25 = L25 L27 L11 L29; g26 = L19 L20 L22 L6 L30-L35 L31; pilot+ = L8 L13 L10 L7 L26 L37 L15; L14 throughout; L12 and L36's backup half done).

## 1. The 16 go-live items against today's code

| # | Item | Status | Evidence | What is missing |
|---|---|---|---|---|
| 1 | TCS on scrap | Built | apps/sales/tcs.py:40 TcsCollection, :447 TcsChallan; apps/accounting/tcs.py:33 TcsSection (dated rates, PAN / no-PAN rate), :110 TcsDeclaration (Form 127); invoice prints TCS lines sales/documents.py:236,315,350; screens registry.ts:885-901 (collected, challans, return, declarations); rates seeded 1% to 31 Mar 2026, 2% from 1 Apr 2026, flagged as a question: setup_indian_books.py:56-66 | Rate and base are the adviser's call (HANDOFF item 3). Form names still 27EQ-era in places |
| 2 | Rule 46 tax invoice | Built | sales/documents.py:161-203 (Rule 46/53/50/51 content), :247 recipient GSTIN, :277 place of supply, :294 HSN/SAC and taxable-value columns, :18-20 original/duplicate/triplicate, :143 export/SEZ wording | O218 (invoices before g21 reprint current names, test data only); O319 open: text in angle brackets dropped on the print (RISKS) |
| 3 | IRN cancellation | Built | gst/einvoice.py:218 cancellation(), :240 record_cancellation(), reasons :84-87, cancelled IRN prints as cancelled documents.py:192 | O152 open: ShipDtls carries buyer GSTIN for third-party delivery. Sent by hand, not by API (RISKS limit 8) |
| 4 | TDS rates, sections, forms for FY26-27 | Partly built | Dated rates exist: accounting/tds.py:129 TdsRate, :99 rate_on() refuses with no confirmed row; TDS return and challan purchasing/tds.py:246 | No rate seeded (nothing in setup_indian_books.py or load_demo for TdsRate), so TDS deducts nothing until typed. No TAN field anywhere in apps (grep). Section codes still 1961-Act style (194Q, 194C; TdsSection.code help text tds.py:64), screen text still "26Q" and "challan 281" (frontend/src/modules/accounts/TdsReturn.tsx:10, registry.ts:877). No FVU file (RISKS limit 15). Salary TDS (392 / 24Q to Form 138 / Form 130) not built (HANDOFF item 3 g23 P6) |
| 5 | Indian chart, GST taxes, fiscal positions, LUT | Built | accounting/management/commands/setup_indian_books.py:79 Command (chart, taxes, positions, EXPORT-LUT :105, TDS/TCS accounts :72); accounting/lut_api.py | TDS rates and payroll components not seeded (see 4, 13) |
| 6 | Edit log that cannot be bypassed | Partly built | core/history.py:27 RecordEvent written by the API layer (core/audit.py:175); admin made read-only for posted documents (core/admin_mixins.py:25-31) | History keeps field names, not old and new values (audit.py:175 `summary=", ".join(sorted(request.data))`). An admin or script edit of a master or draft leaves only updated_by (history.py:8-10; RISKS limit 35). Odoo forces the restrictive trail on for India companies (l10n_in res_config_settings_views.xml, cited in the old report) |
| 7 | Reverse-charge freight: cash payment and self-invoice | Partly built | gst/returns.py:97-98,192 reverse_charge into 3.1(d) and 4(A)(3) | No self-invoice for unregistered supplier, no table 6 / cash-ledger payment (RISKS limit 16). Unchanged since g20 |
| 8 | EPF and EPS ceiling as dated data | Built | hr/statutory.py:20-47 StatutoryFigure (kind, state, effective_from, value, source); defaults :30-34; kept on each line hr/payroll.py:1372; read by statutory_files.py:91-94 | statutory_files.py:35 EPS_CEILING constant remains only as fallback for lines kept before O222. The 15,000 vs 25,000 figure is still unconfirmed (HANDOFF item 3) and nobody has entered one |
| 9 | Employee bank, IFSC, PAN, date of birth, missing-account check | Built | hr/pay_details.py:32-71 EmployeePayDetails (account, IFSC, PAN, DOB validated); bank file refuses anyone without details and names them (hr/bank_file.py docstring) | Gender, dependants, nominee still absent (not G) |
| 10 | Bank advice file, one settlement per run | Built | hr/bank_file.py:34 SalaryTransferFile, :147 SalaryTransfer, mark_paid, return_transfer, void guard :213 | Format is a plain bulk CSV; a bank-specific layout is not known |
| 11 | Printed payslip | Built | hr/payslip_print.py:10 payslip_pdf; hr/pay_api.py:264; own-payslip screen registry.ts:983 | none |
| 12 | Advance and loan ledger | Built | hr/advances.py:47 SalaryAdvance (recovery stops at the amount, :85-100); screen registry.ts:993; leaver case O262 fixed | O372 open (owed_elsewhere counts unrecorded debits) |
| 13 | Professional tax with effective dates and state | Built (engine); data not entered | hr/payroll.py:332 PayComponentSlab with state and effective_from (:308), slab_on() :289; Employee.work_state hr/models.py:143; PT register hr/pay_api.py:81, statutory_files.py | West Bengal schedule of 1 Oct 2026 not seeded: no PT/LWF rows anywhere but the demo (load_demo.py:881) |
| 14 | Labour Welfare Fund | Built (engine); data not entered | hr/payroll.py:96-97 LWF and LWF_EMPLOYER kinds; statutory_files.py:287 lwf_register | Must be entered before the December run; no seed |
| 15 | Bonus ceiling (higher of 7,000 and minimum wage), dated | Partly built | Bonus is a hand-made component with a flat base_ceiling (hr/payroll.py:193,245,1415) | No bonus FigureKind (statutory.py:32-34 has only EPF, EPS, overtime), no minimum-wage table (no hit for minimum wage in apps), nothing dated |
| 16 | Overtime rate derived from the ordinary wage | Built | hr/payroll.py:77 OVERTIME_ORDINARY, multiplier dated statutory.py:34 and read payroll.py:1685; approved_hours on attendance (hr/models.py:1240) | Legacy PER_OVERTIME_HOUR kept (payroll.py:75). Multiple of 2 unconfirmed |

Count: 12 Built (1, 2, 3, 5, 8, 9, 10, 11, 12, 13, 14, 16; 13 and 14 need data), 4 Partly built (4, 6, 7, 15), 0 Not built.
Beyond the 16, also built since the old report: 2FA and session limits (core/second_factor.py:81 SecondFactor; config/settings.py:103-105 idle 30 min, absolute 12 h), API keys (core/api_keys.py:57 ApiKey, :196 ApiKeyAuthentication), attachments in the nightly backup (deploy/backup.sh:4-9, 48-49), the period-close checks for depreciation and unposted pay runs (assets/apps.py:15, hr/apps.py:56, so old O29 is closed), asset register footed to the ledger in Health (web/health.py:248, assets/models.py:885, old O34), setup_indian_books, the scale-bridge client (deploy/scale_bridge/scale_bridge.py), the plant month (apps/e2e/tests_plant_month.py).

## 2. The old "soon after" table, item by item

| Module | Item | Status | Evidence |
|---|---|---|---|
| Sales and CRM | Quotation templates | Not built | no template model in apps/sales (grep) |
| | Section and note lines | Not built | no display-type or section field on lines (grep) |
| | Order grid for variants | Not built | variants are Items (inventory/variants.py); no grid in frontend (not re-greped; unchanged per old report) |
| | Percent and category price rules | Not built | sales/models.py:191 PriceListItem is a fixed price per item with quantity breaks |
| | Lead merge, email-to-lead | Not built | no merge or inbound-mail code (grep); RISKS limit 34 |
| | Configurable stages, lost-reason master | Not built | fixed `Stage` choices sales/crm.py:60; lost_reason is free text crm.py:167,313 |
| | Month-by-month forecast | Not built | pipeline() per stage only (not re-greped) |
| | WhatsApp send | Not built | only a source label crm.py:37 |
| | Commission slabs | Not re-checked | CommissionPlan sales/models.py:6007 exists; whether it has slabs was not read |
| Purchasing | RFQ PDF and mail | Not built | only render_purchase_order_pdf purchasing/documents.py:10; send_document callers list PO only (purchasing/models.py:1893) |
| | Receipt reminders, Group RFQ rule | Not built | no hit |
| | Suggest from history | Not re-checked | planning has a history forecast; no RFQ-side suggest |
| | Vendor KPIs | Partly built | scorecard on time, in full, price purchasing/models.py:6246; no pivot |
| Stores | Item and bin barcodes | Not built | StorageBin inventory/bins.py:32 has no barcode field; no GTIN (grep) |
| | Scan-driven receiving, counts, labels | Partly built | frontend/src/views/ScanBox.tsx (typed, wedge or camera); scans only PO number, bale, roll (manufacturing/barcode.py) |
| | Assigned and cycle counts | Not built | no hit for cycle count or frequency |
| | Putaway rules | Partly built | suggest_putaway inventory/bins.py:188, plan_putaway picking.py:115; no rule table |
| | Weight-basis landed cost | Not built | allocation spreads by value only, purchasing/models.py:5249-5254 (L23) |
| | Fill-rate and on-time dashboard | Partly built | vendor scorecard only; no warehouse dashboard |
| | Automatic requisitions from reorder rules | Not re-checked | old report: button-driven |
| Production, Plant, Quality | Operator step instructions | Not built | no hit in manufacturing or quality |
| | Block-or-warn on over-issue | Not re-checked | |
| | Photo and worksheet checks | Not built | no hit |
| | ECO stages and approvals | Partly built | manufacturing/changes.py:25 ChangeStatus; single-decision approvals (core/approvals.py:131 check_approver) |
| | Equipment master data | Not built | Machine manufacturing/machines.py:49 has no vendor, serial, warranty; serial only on gauges quality/calibration.py:49 |
| | Operator-raised maintenance requests | Not built | MaintenanceJob/Schedule only (maintenance.py:77,246) |
| | Machine counters | Not built | counts typed at the station (L25) |
| | Granulating master data | Not built | regrind loop in BOM only (bom.py:30) (L26) |
| Accounts | Bank rules, XLSX import | Not built | accounting/bank_import.py is CSV with auto_match one-candidate |
| | Vendor payment file | Not built | bank_file.py is payroll only; purchasing payment_run lists dues |
| | Cheque register | Not built | cheque is free text (accounting/bank_import.py:5) (L19) |
| | Unrealised exchange | Not built | no hit |
| | Written-down-value depreciation | Not built | assets/models.py:40 DepreciationMethod: straight line (and none) |
| | Deferrals | Not built | no hit |
| | Cash flow, day book, Schedule III | Not built | reports.py has P&L, balance sheet (:158); none of the three (L30) |
| | GSTR-2B link, e-invoice API | Partly built | 2B matched from an uploaded file (gst/gstr2b.py); payloads by hand (L31) |
| | TAN, per-PAN thresholds | Not built | no TAN; thresholds per party (RISKS limit 15) |
| | MSME-1, GSTR-9 and 9C | Not built | no hit (L30) |
| | Period-close check for depreciation (O29) | Built | assets/apps.py:15 |
| People | Employment terms, probation | Not built | no hit in hr |
| | Onboarding and exit checklists, departure reason | Not built | no hit |
| | Work-entry types, rotating weekly offs | Not built | calendars.py holds working pattern and holidays only |
| | Off-cycle runs | Not built | no hit |
| | Leave accrual milestones, compensatory leave | Not built | LeavePolicy hr/models.py:504 has monthly accrual and carry-over only |
| | Overtime approval | Built | approved_hours hr/models.py:1240 |
| | Final settlement, leave encashment | Not built | no hit (L34); leaver shortfall recovery exists (payroll.py:675) |
| | Salary register, statutory registers | Partly built | PT and LWF registers (pay_api.py:81); muster, wages, overtime registers not found |
| | Minimum-wage table | Not built | no hit (L33) |
| | Contract-labour register re-check | Open | O224 |
| | Payroll starter set | Not built | no management command seeds components |
| | Own-payslip view | Built | registry.ts:983 |
| Settings | API tokens | Built | core/api_keys.py:57 |
| | Session timeout and 2FA | Built | config/settings.py:103; core/second_factor.py:81 |
| | Inbound mail, WhatsApp | Not built | no IMAP or provider code; config/settings.py:303 is SMTP only |
| | Pivot and .xlsx layer | Not built | Grouped.tsx single group-by; csv only |
| | Record scoping beyond reps | Not built | core/scoping.py is parties/reps only |
| | Old and new values in history | Not built | see item 6 |
| | Filterable custom fields | Not built | core/customfields.py; RISKS limit 42 |
| | Installable tablet app | Not built | no web manifest or service worker in frontend |
| | Scale-bridge client | Built | deploy/scale_bridge/scale_bridge.py |
| | Off-machine copy of attachments | Partly built | backed up with the database (backup.sh:48); no offsite target (grep) |

## 3. What Odoo 19 offers that the plant would use and the ERP lacks

URLs are Odoo 19.0 documentation pages (base https://www.odoo.com/documentation/19.0/applications/). "C/E" as in the header. HANDOFF column: item that schedules it, or "none".

### Sales and CRM
| ID | Gap | Odoo 19 (page, edition) | Rate / reason | H: |
|---|---|---|---|---|
| S1 | Quotation templates | sales/sales/sales_quotations/quotation_templates.html (C) | S: same sack families quoted repeatedly | L13 (pilot+) |
| S2 | Section and note lines | sales/sales/sales_quotations/create_quotations.html (C) | S: multi-item quotes read better | L13 |
| S3 | Lead merge, mail-to-lead | sales/crm/optimize/... and crm email alias (C) | S | L13, L6 (inbound mail) |
| S4 | Configurable stages, lost-reason master, month forecast | sales/crm/pipeline.html, crm/track_leads/lost_opportunities (C) | S | L13 (stages, lost reason not named) |
| S5 | Order-line variants grid | sales/sales/products_prices/products/variants.html (C) | S | none |
| S6 | Price rules by percent and category | sales/sales/products_prices/prices/pricing.html (C) | S: price list is fixed price per item (sales/models.py:191) | none |
| S7 | Ship-complete policy, billing policy per item | sales/sales/invoicing/ (C) | S | L22 (g26) |
| S8 | Customer portal: orders, invoices, statements, acknowledgement | sales/sales/sales_quotations/get_paid_to_validate.html, general/users/portal (C) | S: buyers ask for statements and POD | none |
| S9 | WhatsApp send | productivity/whatsapp.html (E, needs Meta account) | S | L7 (pilot+) |
| S10 | Confirmation mail template, duplicate-order warning | sales/sales/sales_quotations (C) | L | none |
| S11 | Subscriptions, rental, e-commerce, loyalty, marketplace, Peppol/UBL | not relevant: no recurring or retail sales, Indian buyers do not send UBL | | |

### Purchasing
| ID | Gap | Odoo 19 | Rate / reason | H: |
|---|---|---|---|---|
| P1 | RFQ as PDF and mail | inventory_and_mrp/purchase/manage_deals/rfq.html (C) | S: RFQs to resin suppliers | L6 (g26) |
| P2 | Debit note and remittance advice printed and mailed | accounting/vendor_bills (C) | S: PDF only for PO, payment and challan (grep of render_*_pdf) | L6 |
| P3 | Receipt reminders to vendors | purchase/manage_deals/reminders (C) | S | none |
| P4 | Suggest quantities, Group RFQ by vendor | purchase/advanced/suggest.html; manage_deals/rfq.html (C, new in 19) | S: planning already forecasts | none |
| P5 | Vendor price discount percent, update-in-place price import | purchase/products/vendor_pricelist (C) | S | L16 (g24) covers circular-linked prices only |
| P6 | Landed cost split by weight, volume, equal | inventory_valuation/landed_costs.html (C) | S for polymer freight; purchasing/models.py:5249 values only | L23 (g24) |
| P7 | Merge RFQs, purchase agreements templates | purchase/manage_deals (C) | L | none |
| P8 | Bill OCR | accounting/vendor_bills/invoice_digitization.html (E) | not relevant: Odoo's OCR does not read Indian taxes, low volume | |

### Stores and inventory
| ID | Gap | Odoo 19 | Rate / reason | H: |
|---|---|---|---|---|
| I1 | Item, bin, lot barcodes and labels, scan receive/pick/count | inventory_and_mrp/barcode/setup/software.html, barcode/operations/* (barcodes C, Barcode app E) | S: the gate and stores run on paper without it | L9 (g24) |
| I2 | Cycle counts, assigned counts | inventory/warehouses_storage/inventory_management/cycle_counts.html, count_products.html (C) | S | L9 partly (counts by scan); frequency not named |
| I3 | Putaway rules table | shipping_receiving/daily_operations/putaway.html (C) | S | L9 not explicit |
| I4 | Weighbridge gross/tare/net | not in Odoo Community (IoT scales E) | G if the plant buys polymer by truck weight: stock and payables turn on net weight | L17 (g24) |
| I5 | Gate register, returnable passes | not in Odoo core | S | L18 (g24) |
| I6 | Inventory dashboards: fill rate, on-time, ageing, negative lines | inventory/warehouses_storage/reporting/ (C, new dashboards in 19) | S | L8 (pilot+) |
| I7 | Per-item projected stock graph, by-bin report | reporting/forecast.html, locations.html (C) | S | L8 |
| I8 | Pack-in-pack, wave/cluster picking, GS1, RFID, carrier connectors, repairs, unbuild | not relevant: sack plant ships bales and full trucks. (Unbuild is L: void and reversal cover it) | | |
| I9 | Negative stock valued at last cost and trued up | inventory_valuation/operations_valuation.html (C) | L: contradicts replayed ledger, do not copy | |

### Manufacturing, MRP, work centres, quality, maintenance
| ID | Gap | Odoo 19 | Rate / reason | H: |
|---|---|---|---|---|
| M1 | Operator step instructions on the work order | manufacturing/workflows/use_work_instructions + Shop Floor (E; worksheet fields removed from Community 19) | S | L11 (g25) |
| M2 | Photo, measure, worksheet check types | quality/quality_management/quality_checks.html (E) | S | L11 |
| M3 | Equipment master: vendor, serial, warranty, cost, MTBF | maintenance/maintenance_setup.html (C) | S | none (L11 names an equipment register: covered) |
| M4 | Operator-raised maintenance request, team email alias | maintenance/maintenance_requests.html (C) | S | L11 |
| M5 | Machine counters feeding OEE and short stops | IoT (E); not documented as a counter feature | S | L25 (g25) |
| M6 | ECO stages, multi-approver | plm/management/approvals.html, engineering_change_orders.html (E) | S, G only if customers audit recipes | L10 (pilot+) |
| M7 | BoM from what a run used; split and merge MO | manufacturing/workflows/split_merge.html, MO "generate BoM" (C, new in 19) | L | L27 (g25) |
| M8 | Flexible consumption (block/warn/allow) per BoM | manufacturing/basic_setup/bill_configuration.html (C) | S: over-issue of tape/granules | none |
| M9 | Quality analytics: pivot of checks and alerts, quality teams | quality (E) | S | L8 |
| M10 | BIS test plans, ISI marking register | not in Odoo | G if BIS licence inspection falls inside the first year | L29 (g25) |
| M11 | Supplier certificate of analysis vs incoming plan | not in Odoo | S | L28 (g24) |
| M12 | Recycling line (granule lots, melt-flow, recycled content) | not in Odoo (no recycling doc) | S, firm once the plant commits | L26 (pilot+) |
| M13 | Landed freight on a run | mrp_landed_costs (C) | closed: decision 20, O48 fixed in g20 | |
| M14 | MPS, repair orders, IoT box, PLM design files | not relevant: ERP already has MPS (planning/mps.py); repairs, box not needed | | |

### Accounting
| ID | Gap | Odoo 19 | Rate / reason | H: |
|---|---|---|---|---|
| A1 | Audit trail with old and new values, admin and script edits logged | accounting/reporting/year_end.html, l10n_in setting (setting C, report E) | G: MCA edit-log rule; Odoo forces it for India | none |
| A2 | TAN, 2025-Act section and form names (394/393, Forms 138/140/143/144/130/127) on TDS/TCS, challans and returns | fiscal_localizations/india.html (TDS/TCS threshold alert; section groups, C) | G: names on every challan and return from 1 Apr 2026 | L14 (law as data) in principle; not scheduled |
| A3 | Seeded statutory data: TDS rates, EPF/ESI, WB PT and LWF, bonus, TCS confirmation | india payroll page seeds PF, PT, LWF, ESIC rules (E) | G: engines exist, nothing is entered, so the first run deducts nothing | item 3 waits on accountant; nobody owns the seed |
| A4 | Reverse-charge self-invoice, 3B table 6 / cash payment | fiscal_localizations/india.html (RCM, C) | G: GTA freight and unregistered purchases | none |
| A5 | GSTR-3B completeness: 4(B)/4(D) reversals, rule 37 180-day ITC reversal, imports, IMS action | india.html "Filing GSTR-1", "GSTR-2B reconciliation" (E) | S | L21 (import IGST only) |
| A6 | E-invoice and e-way bill sent by GSP, rejection read back | india.html e-Invoice, E-Way bill sections (C) | S | L31 (g26) |
| A7 | GST filing from the system | india.html (E) | S: compiled returns upload by hand | L31 partly |
| A8 | Export papers: shipping bill, packing list, eBRC, refund | india.html (partial) | G if an export ships in the first quarter | L20 (g26) |
| A9 | Import bill of entry, IGST credit matched to IMPG | india.html (import via vendor bill) | G if an import lands in the first quarter | L21 (g24) |
| A10 | Post-dated cheques, cheque register and printing | accounting/payments/ (C) | S | L19 (g26) |
| A11 | Vendor payment file (NEFT bulk) | accounting/payments/batch_payment.html, India payment order (E) | S | none |
| A12 | Bank reconciliation rules, XLSX/OFX import, Bank Matching view | accounting/bank/reconciliation.html (E) | S: bank charges and interest recur | none |
| A13 | Unrealised exchange at year-end | accounting/reporting (E) | S, G before the first year-end if foreign balances exist | L30 not listing it: none |
| A14 | Schedule III statements, cash flow, day book, GSTR-9/9C, MSME-1, year-end checklist | reporting/year_end.html, financial reports (E) | S before the first year-end | L30, L32 (g26) |
| A15 | Declining-balance depreciation, asset revaluation, deferrals/prepaid | accounting/assets, deferred (E) | S | none |
| A16 | Cash rounding, round-off line | accounting/customer_invoices/cash_rounding (C) | S: some buyers insist on rounded totals; none in sales code | none |
| A17 | Loans schedule, follow-up by WhatsApp | accounting loans, follow-ups (E) | L | L7 for WhatsApp |
| A18 | Export incentives, RoDTEP, advance authorisation, EPCG | not in Odoo | S, before the first claim | L37 (pilot+) |
| A19 | Plastic EPR returns | not in Odoo | L, adviser to say | L15 |
| A20 | Multi-company, consolidation, bank sync (no Indian provider named), online payment, Peppol, UPI QR, Avatax | not relevant: one entity, B2B bank transfer | | |

### People and payroll
| ID | Gap | Odoo 19 | Rate / reason | H: |
|---|---|---|---|---|
| H1 | Final settlement and leave encashment inside two working days | hr/payroll/... (Odoo India page has none) | G for the first leaver | L34 (g26, "before the first leaver") |
| H2 | Minimum-wage table by schedule, zone, skill, checked on every wage | not in Odoo | G: wages below the schedule are an offence; also bonus base | L33 (g26) |
| H3 | Bonus ceiling and calculation as dated figures; statutory bonus run | india payroll page has none | G | L33/L14 in part; bonus not named |
| H4 | Salary TDS section 392, Form 24Q to Form 138, Form 130 | india payroll page mentions TDS once | S, G if any pay passes the exemption | none scheduled (HANDOFF item 3 only notes it) |
| H5 | Statutory registers: muster, wages, overtime; Form formats under the Codes | india payroll page: six reports | S before inspection | L35 (g26); O224 waits on adviser |
| H6 | Work-entry types, rotating weekly offs, sandwich leave, comp-off | hr/payroll/work_entries.html; hr_holidays (C/E); l10n_in_hr_holidays | S | L35 names sandwich leave and comp-off |
| H7 | Leave accrual plans with milestones | hr/time_off/accrual_plans.html (C) | S | none |
| H8 | Off-cycle payslip | hr/payroll/payslips.html (E) | S | none |
| H9 | Onboarding and offboarding plans, departure reason, probation | hr/employees/onboarding.html, offboarding.html (C) | S | none |
| H10 | Employee master: gender, dependants, nominee, emergency contact, documents with expiry | hr/employees/new_employee.html (C) | S: PF nomination and ESI dependants need it | none |
| H11 | Live attendance feed from a biometric reader | hr/attendances (E for kiosk and RFID) | S | L35 names device import |
| H12 | Timesheets for hourly pay | hr/payroll (E) | L until the owner says (O312) | waits on owner |
| H13 | Appraisals, skills, recruitment pipeline | hr/appraisals (E), hr_skills (C) | L (appraisal and recruitment code exist) | none |
| H14 | Expense cards, referrals, fleet, eLearning | not relevant | | |

### Settings, platform, reports, portal, mail, mobile, integrations
| ID | Gap | Odoo 19 | Rate / reason | H: |
|---|---|---|---|---|
| X1 | Pivot, graph and .xlsx export on any list; owner dashboard | productivity/spreadsheet, general/dashboards (pivot C, spreadsheet E) | S: first monthly review will ask | L8 (pilot+) |
| X2 | Inbound mail on records | general/email_communication/email_servers (C) | S | L6 (g26) |
| X3 | Outgoing document mail from every screen | same | S: five documents already mail (core/mail.py send_document callers), RFQ and debit note missing | L6 |
| X4 | WhatsApp provider | productivity/whatsapp.html (E) | S | L7 (pilot+) |
| X5 | Customer and vendor portal | general/users/portal (C) | S | none |
| X6 | Automation rules and approvals in stages set by the owner | productivity/studio/automations, general approval rules (C/E) | S | L10 (pilot+) |
| X7 | Record rules beyond reps, e.g. purchasing by vendor | general/users/access_rights.html (C) | S | none |
| X8 | Filterable, exportable, printable custom fields | Studio / properties (E/C) | S | none |
| X9 | Installable floor-tablet app (PWA), offline read | general/offline_mode.html (C) | S | none |
| X10 | Off-machine copy of attachments | cloud storage for attachments (C) | S: backup.sh archives media to the same host | L36 (only the in-backup half done) |
| X11 | Webhooks, JSON-2 | developer/reference/external_api (C) | L: API keys exist | |
| X12 | Idle timeout, 2FA, API keys | general/users/access_rights.html, auth_timeout (C) | closed in g22 | L12 done |
| X13 | Studio, Documents, Knowledge, Sign, AI, SSO, VoIP, SMS, Discuss, multi-language, multi-company | not relevant: developer changes screens, few users, no Hindi screens, one entity | | |

## 4. Counts and the gaps HANDOFF does not cover

Rated gaps in section 3 (rows marked closed or not relevant not counted): G 11, S 60, L 9. Of the 11 G, 7 are scheduled by a g24-g26 item (I4, M10, A8, A9, H1, H2 and H3 in part), 4 are not scheduled at all (A1, A2, A3, A4).

G gaps that HANDOFF's plan does not cover, in order of risk:
1. A1 Edit log: old and new values, plus admin and script edits (no item anywhere).
2. A3 Seed the statutory data (TDS rates, WB PT of 1 Oct 2026, LWF before the December run, EPF/ESI, bonus, TCS rate) as a startup command and checklist. The engines exist and are empty. HANDOFF item 3 only waits on the accountant; nobody builds the seed or the go_live_check line that refuses to start without it.
3. A2 TAN and the 2025-Act section and form names on TDS and TCS challans and returns; "26Q" and "challan 281" remain on screens.
4. A4 Reverse-charge self-invoice, cash payment and 3B table 6.
5. H3 Bonus: dated ceiling and calculation (L33 covers minimum wages; the bonus rule is not named).
6. A13 Closing-rate restatement of foreign balances (G before the first year-end; the plant exports and imports; L30 does not list it).
7. A5 GSTR-3B reversals (4(B), 4(D), Rule 37 180-day) and imports; L21 covers IGST on imports only. Rated S, but it makes the first 3B wrong once a supplier goes unpaid.
8. H4 Salary TDS 392 / Forms 138 and 130: noted in HANDOFF item 3, not scheduled. G only if pay passes the exemption.
9. S8/X5 Portal for customers and vendors (S, no item).
10. P3/P4, A11, A12, A16, M3, M8, H7 to H10, X7 to X9 (S, no item): see section 3 "H: none".

## 5. Where this ERP is deeper than Odoo for this plant

| Area | This ERP | Odoo 19 | Evidence |
|---|---|---|---|
| Ledger integrity | Posted is immutable, corrections are reversals, `audit_invariants` gate, lock order sentinel, numbering row for gap-free Rule 46 numbers | Restrictive audit trail optional (forced for India), no lock-order or replay checks documented | core/lock_order.py, management/commands/audit_invariants.py, HANDOFF g23b decision |
| Costing | Replayed on-hand and weighted average, four methods per item and warehouse, as-of valuation reconciled to ledger, `cost_of_removing` on every outbound path | 19 moves valuation to invoice-level entries with manual Adjust Valuation | inventory/costing.py, valuation.py |
| Woven-sack BOM and run costing | BOM computed from bag, fabric, tape specs, regrind loop, changeovers, finite scheduling, operation costing with variances | One work order at a time into the first free slot; no spec-driven BOM | manufacturing/woven.py, bom.py:30, changeover.py, planning/capacity.py |
| Quality statistics | SPC, Cpk, AQL sampling, certificates, calibration register with suspect inspections, failure waits for Quality Manager (decision 23) | No SPC or AQL in quality docs (Quality is E) | quality/spc.py, sampling.py, calibration.py |
| Job work and ITC-04 | Challans with one-year clock, ITC-04 return | No ITC-04, no GSTR-9/9C in Odoo India modules | manufacturing/jobwork.py, gst/itc04.py |
| TCS and TDS | TCS with PAN/no-PAN rate, Form 127 declarations, challans, return listing; TDS threshold modes, certificate rate, customer TDS receivable | TCS tax with threshold alert; no TCS return or challan object | sales/tcs.py, accounting/tcs.py, purchasing/tds.py |
| MSME 45-day | Schedule and acceptance rule | none documented | purchasing/msme.py |
| Payroll integrity | Rate change inside a period paid per day, frozen figures, dues per month tied to bank payments, advances, bank file with returned transfers, PF ECR and ESI files, PT/LWF registers | India payroll is Enterprise; no F&F, bonus, quarterly return documented | hr/payroll.py, hr/bank_file.py, hr/statutory_files.py |
| Races and two-person acts | `@serialised`, `lock_rows`, role-giving by two people, maker-cannot-approve, PostgreSQL race tests | not documented | core/models.py, apps/e2e/tests_races.py |
| Price escalation | Polymer indices, clauses, variation bills, call-offs | none | sales/price_variation.py, call_offs.py |
| Proof | Plant month test, 46 tests through the API as roles | | apps/e2e/tests_plant_month.py |

## 6. Ledger of ratings (section 3 rows)

G (11): I4 weighbridge (H: L17), M10 BIS (L29), A1 edit log (none), A2 TAN and names (none), A3 statutory seed (none), A4 RCM (none), A8 export papers (L20), A9 import bill of entry (L21), H1 final settlement (L34), H2 minimum wages (L33), H3 bonus (partial L33).
Not covered at all by HANDOFF: A1, A2, A3, A4, and partly H3. Also A13 (conditional G) and H4 (conditional G).
S and L counts are taken from the rows above, one per row: see section 3 tables. Rows marked "not relevant" are not counted.
