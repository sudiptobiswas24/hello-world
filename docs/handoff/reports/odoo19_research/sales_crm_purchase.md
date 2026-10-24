# Odoo 19 Sales, CRM and Purchase against this ERP (woven polypropylene sack plant, India)

How to read this file. Every row is one concrete Odoo 19 feature, with the evidence for the Odoo side as inline links (labels in square brackets resolve to local files and URLs listed at the very end of the file) and the ERP side as file paths and line numbers under `/home/user/hello-world`.

Column codes.
- **New in 19?** `no` = already in the 18.0 docs and unchanged in the 18.0 to 19.0 docs diff. `NEW-d` = a docs page or section appears only in 19.0. `CHG-d` = a docs page was materially rewritten, renamed or trimmed. `NEW-s` / `CHG-s` = seen in the 19 community source with no 18 baseline available, so newness is **unproven**. `3p` = only a third-party blog says so (weak). `?` = could not establish. A docs diff proves the documentation changed, not necessarily the behaviour.
- **Ed.** `CE` = module directory exists in the Odoo 19 community addons tree ([m-tree], 638 modules). `EE` = docs say Enterprise. `EE?` = module is absent from the community tree, so inferred Enterprise. `CE+IAP` = community module whose data service is sold as paid In-App-Purchase credits.
- **ERP status.** `HAVE`, `HAVE+` (ERP deeper than Odoo community), `PARTIAL`, `MISSING`. "Not verified" means I read the model but did not exercise the screen.
- **Rel.** Relevance to this plant, my judgment: `G` needed before go-live, `S` soon after, `L` later or not relevant. This rates the Odoo feature for the plant, independent of whether the ERP already has it; a `G` row marked `PARTIAL` or `MISSING` is a go-live gap.

---

## 1. What Odoo 19 offers in Sales, CRM and Purchase, with edition, newness, ERP status and relevance

### Takeaway
Odoo 19 community covers quotation to cash, a lead and opportunity pipeline, and RFQ to bill with agreements, reordering and a new "Suggest" and "Group RFQ" pair; Enterprise adds commissions, subscriptions, rental, 3-way "Should be paid", WhatsApp, the new AI app and bill OCR. Against that list this ERP has the plant-critical selling and buying flows (and goes deeper on India tax, approvals, receipt inspection, price escalation), is thin on CRM and quotation presentation, and has no customer or vendor portal at all.

### Cited Findings

#### 1A. Sales (quotations, pricing, invoicing, portal, reporting)

| ID | Odoo 19 feature | New in 19? | Ed. | ERP status and evidence | Rel | Why (sack plant) |
|---|---|---|---|---|---|---|
| S1 | Quotation to sales order: send by email with PDF, preview, confirm, cancel; states draft, sent, sale, cancel plus a `locked` flag [d-quote] [s-so] | no | CE | HAVE+: `Quotation` (apps/sales/models.py:5247) with `render_pdf`, `email_to_customer`, `mark_sent` :5478, `decline` :5487, `accept` :5495 to order, and `create_revision` :5419 (Odoo has no revisions) | G | Core selling flow |
| S2 | Quotation validity date; default validity setting; per-template validity [d-deadline] | no | CE | HAVE: `Quotation.valid_until`, `has_expired` :5398 | G | Rate quotes lapse when polymer moves |
| S3 | Quotation templates: reusable lines, sections, notes, T&C, validity, confirmation mail, journal, sign/pay flags [d-template] | no (the template Optional Products tab was removed in 19, see S5) | CE | MISSING: no template model in apps/sales (only `RecurringInvoice`, models.py:5767, is template-like) | S | Same bag family is quoted repeatedly |
| S4 | Sections and notes on lines; 19 adds a sub-total per section, nested subsections (`parent_id`), "collapse prices" and "collapse composition" [d-quote] [s-sol] (fields at sale_order_line.py:321-331) | CHG-d + CHG-s | CE | MISSING: `SalesOrderLine` (models.py:1372) has `description` and charge lines, no section or note line type | S | Multi-item quotes (bag, liner, print charge) read better grouped |
| S5 | Optional products: product-form "Upsell & Cross-sell" list kept; the quotation and template "Optional Products" tabs are replaced by optional sections ("Set Optional", lines default to quantity 0, portal user raises them) [d-optional] [x-sales-diff]; `sale.order.option` model is absent from 19 `sale_management/models` [s-sm-line] | CHG-d + CHG-s | CE | MISSING | L | B2B sack orders rarely cross-sell |
| S6 | Product catalog (card view with category and attribute filters) for adding lines [d-quote]; redesigned in 19 per [w-nalios] | 3p | CE | MISSING as a card catalog (not verified in the order form's item picker) | L | Item codes are typed or searched |
| S7 | Variants on lines: product configurator or grid entry (`sale_product_matrix`) [d-variants-so] [d-variants] | no (variants page rewritten) | CE | PARTIAL: variants are Items under a template ([e-variants] apps/inventory/variants.py); no grid entry or configurator on order lines | S | Size x GSM x colour x print gives many SKUs per order |
| S8 | Pricelists: rules by product, category or variant; min quantity; validity; discount, formula (rounding, extra fee) or fixed price; per-customer list; currency lists; "Update Prices" [d-pricing] | no (wording only) | CE | PARTIAL: `PriceList` models.py:121, `PriceListItem` :162, `resolve_price` ([e-sales-pricing]): fixed price per item with quantity breaks, validity, currency, customer list and default list; no percent, formula or category rule, no recompute button. Open O78 (EUR line priced from USD base) and O79 (price ignores the line's unit) in [e-risks] | G (exists, fix defects) / S (rules) | Price lists drive every quote |
| S9 | Discounts: per-line Disc.%; Discount wizard (all lines, global line, fixed amount) [d-discounts] [s-sale-settings] | no | CE | PARTIAL: `discount_percent` on `TaxedLineMixin` ([e-mixins] :228) and `ApprovalPolicy.max_discount_percent` (models.py:185); no global or fixed wizard | G (line) / L (wizard) | Trade discounts on big orders |
| S10 | Margins on quotation and order, from product cost, delivered stock cost (`sale_stock_margin`) or BoM cost (`sale_mrp_margin`) [d-margin] [m-tree] | no | CE | HAVE+: `SalesOrder.margin_percent` :852, `cost_total`, `costed_revenue`, `ApprovalPolicy.min_margin_percent` (models.py:185) routes low-margin orders to approval | G | Resin price swings eat margin |
| S11 | Down payments: fixed or percent from Create Invoice, deducted on the final invoice; income account editable on draft down-payment invoice only [d-dp] | no (wording; clarification that posted accounts cannot change) | CE | HAVE: `create_down_payment_invoice` models.py:1229, `DepositApplication` :3465, deposits on orders | G | Advance against order is normal in India |
| S12 | Invoicing policy: ordered vs delivered quantities (per product); milestone and timesheet policies for services [d-invpol] [d-milestone] [d-tm] | milestone page CHG-d (Delivered % per milestone) | CE | PARTIAL (order-level, not per item): `InvoicePolicy` models.py:482, `SalesOrder.invoice_policy`; milestones and timesheets not applicable | G (have) / L (services) | Invoice on dispatch is the norm |
| S13 | Pro-forma invoice from quotation or order [d-proforma] | no | CE | HAVE: `SalesOrder.render_pdf(proforma)` :924, `email_to_customer(proforma)` :930, apps/sales/documents.py:84 | G | Advance payment is asked against a proforma |
| S14 | Portal acceptance: sign (auto, draw, load) and/or pay online, decline, salesperson notified, signature stored; routes accept, decline, transaction, update_line_dict [d-sign] [d-pay] [s-sale-portal] | optional-section toggling is new (S5); rest no | CE (India providers `payment_razorpay`, `payment_payu` are in the tree [m-tree]) | MISSING: no customer portal; `grep portal` in apps hits only the GST portal; acceptance is the internal `Quotation.accept` :5495 | L | Buyers confirm by PO, mail or WhatsApp and pay by NEFT; a portal is also a security project |
| S15 | Customer portal pages: quotes, orders, invoices, PDF and EDI XML download [s-sale-portal] | no | CE | MISSING | L | Statements are emailed instead |
| S16 | Payment link wizard and prepayment percent on orders [s-sale-settings] | no | CE | MISSING | L | Bank transfer, not cards |
| S17 | PDF Quote Builder: header and footer PDFs, dynamic fields, per-product PDFs [d-pdfqb] | no (refs renamed) | CE (`sale_pdf_quote_builder` in tree) | MISSING (PDFs are generated in apps/sales/documents.py, no merged attachments) | L | Spec sheets and test certificates could ride along later |
| S18 | Sale warnings on customer or product [s-sale-settings] | no | CE | PARTIAL: `CustomerProfile.credit_hold` + reason (models.py:246) blocks; no free-text warning | L | Hold covers the main case |
| S19 | Lock confirmed sales; unlock [d-quote] | no | CE | PARTIAL: confirmed orders are guarded by approval and `figures_as_kept` (SalesOrder, models.py:557); no lock toggle (not verified in depth) | L | Approval already freezes the agreed figures |
| S20 | Credit limit warning on the order [s-so] (`partner_credit_warning`) | no | CE | HAVE: `credit_limit_breach` models.py:712, `refuse_going_over_the_credit_limit`; open O77 (currencies added at face value) | G | Credit control is a go-live item |
| S21 | Duplicate-order warning (`duplicated_order_ids`) [s-so] | NEW-s? (no 18 baseline) | CE | MISSING | L | Low risk with a customer PO reference field |
| S22 | Separate invoice and delivery addresses; customer reference [d-addr] | no | CE | HAVE: `billing_address`, `shipping_address`, `reference` on `SalesOrder` | G | Plants ship to sites, bill to head office |
| S23 | Delivery terms on the order: Incoterm and location, shipping policy (as soon as possible or when all ready), delivery date [d-quote] | no | CE | PARTIAL: `incoterm`, `port_of_discharge`, `freight_terms`, line `delivery_date`, `create_backorder` :4595; no ship-complete policy field | G | Partial dispatch by truck load is routine |
| S24 | Fiscal position (tax mapping by customer or region) and India GST report `l10n_in_sale` [d-quote] [m-tree] | no | CE | HAVE+: GST place of supply, HSN and recorded taxes on every document ([e-mixins] `PostedTaxDocumentMixin` :578; apps/gst) | G | GST is statutory |
| S25 | Payment terms and instalments on the order [d-quote] | no | CE | HAVE: `SalesOrder.payment_terms`, instalment terms (apps/sales/tests_installment_terms.py) | G | 30/60-day terms |
| S26 | Returns and refunds: reverse transfer then credit note [d-returns] | no | CE | HAVE+: `Delivery.create_return` :4643, customer-return action, credit notes, Claims screen ([e-registry]) | G | Torn or short-weight bale claims |
| S27 | Stock reservation and availability on lines; make-to-order route (`sale_stock`) [m-tree] | no | CE | HAVE: `reserve_stock` :1037, `pick_list` :4235, planning "When can we promise?" ([e-registry]) | G | Promise dates from the shelf and the plan |
| S28 | Sales teams, salesperson, invoicing target and progress [d-quote] [s-so] | no | CE | HAVE: `SalesTeam` apps/sales/teams.py:31, `SalesTarget` :45, "Targets against sales" screen | S | Reps and targets |
| S29 | Commissions: target-based and achievement-based plans, per person or team, levels up to 300 percent, metrics amount or quantity sold or invoiced, margin, MRR; by product or category; plan approval [d-commissions] | CHG-d (wording; 3p says commission can be moved between salespeople [w-cybrosys]) | EE? (`sale_commission` absent from tree though its toggle is in CE settings [s-sale-settings]) | PARTIAL: `CommissionPlan` models.py:5647 (flat percent on invoiced or collected), `SalesRep` :5677, Commission screen; no slabs, product or margin basis; open O74 (counts a bounced receipt), O75 (leaves out deposits), O76 (invoice currency, not base) | S (G for O74-O76 if commission runs at go-live) | Agents are often paid on collection |
| S30 | Referrer and commission plan on the quotation [d-quote] | no | EE? | MISSING | L | Direct B2B |
| S31 | Sales analysis (pivot, graph), invoice analysis, margin pivot; `spreadsheet_dashboard_sale` [d-margin] [m-tree] | no | CE (dashboard module in tree; the Dashboards app's edition not established) | PARTIAL: Sales report, Receivables by age, Targets, Commission, Claims, Bad debts screens ([e-registry]; `revenue` and `aging` actions in apps/sales/views.py); saved filters ([e-saved-filters]); no free pivot | S | Margin by customer and item |
| S32 | Subscriptions app: recurring plans, renewals, upsell, closing, auto-payment, MRR report; 19 adds a "Subscriptions with deliveries" page and removes the scheduled-actions and automatic-alerts pages [d-subs-deliv] [x-sales-diff] | CHG-d | EE? (`sale_subscription` absent) | PARTIAL: `RecurringInvoice` models.py:5767 (same invoice on a schedule, `auto_post`) | L | No recurring sales |
| S33 | Rental app: products, pricing, pick-up and return, deposits; docs reorganised in 19 [d-rental] | CHG-d | EE? (`sale_renting` absent) | MISSING | L | Not a rental business |
| S34 | Discount and loyalty programs, coupons, gift cards, eWallets; 19 adds a "Cheapest Product" reward in developer mode [d-loyalty] [d-ewallet] | CHG-d (minor) | CE | MISSING | L | B2B |
| S35 | Marketplace and print-on-demand connectors: Amazon, Shopee (features page added), Shopify, TikTok, Lazada (pages added in 19), Gelato [x-sales-diff] [d-gelato] | NEW-d (Shopify, TikTok, Lazada) | EE? (`sale_amazon`, `sale_shopee`, `sale_shopify` absent; `sale_gelato` is CE) | MISSING | L | Not retail |
| S36 | Carrier connectors (Shiprocket, DHL, FedEx, UPS and others) and "Add shipping" rating [s-sale-settings] | no | CE (needs carrier accounts) | PARTIAL: `Delivery.transporter`, `lr_number`, `vehicle_number`, `record_transport` (models.py:4190); freight as a charge line; no rate API | L | Full-truck freight, not parcels |
| S37 | EDI: import a sales order from a UBL XML or PDF RFQ (`sale_edi_ubl`), export PO as UBL BIS3 [d-edi] [m-tree] | no (link-only change) | CE | MISSING | L | Indian buyers do not send UBL |
| S38 | Chatter, activities and a deferred confirmation mail on the order (`pending_email_template_id` [s-so]) | 3p ("separate print and send" [w-cybrosys]) | CE | PARTIAL: notes and follow-ups on any record ([e-chatter]); `email_to_customer`; no confirmation-mail template | L | Notes exist |
| S39 | Mass cancel of quotations and orders from the list (19 labels it "Cancel") [d-template] | CHG-d (label) | CE | MISSING as a bulk action (not verified in the UI) | L | Rare |
| S40 | Units: line unit limited to the product's allowed UoMs; 19 drops UoM categories and adds product "Packagings" (`uom_ids`) [s-uom] [s-sol] | CHG-s | CE | PARTIAL: line `uom` with stock-unit conversion in the ERP, but price ignores unit (O79) | G (fix O79) | Sacks, bales, kg and tonnes all appear on one order |
| S41 | Combo products on lines (`combo_item_id`, `linked_line_id`) [s-sol] | NEW-s? | CE | MISSING | L | A bale is a unit conversion (`sacks_per_bale`), not a combo |

#### 1B. CRM (leads, pipeline, scoring, assignment, activities, channels, forecasting)

| ID | Odoo 19 feature | New in 19? | Ed. | ERP status and evidence | Rel | Why (sack plant) |
|---|---|---|---|---|---|---|
| C1 | Kanban pipeline with configurable stages, per-team pipelines [s-crm-team] | no | CE | PARTIAL: fixed stages New, Qualified, Quoted, Won, Lost (`Stage` apps/sales/crm.py:59, chance table :68), board screen ([e-registry] "Pipeline board"); not configurable; no team pipelines | S | Stages differ by customer type |
| C2 | Leads as a qualifying step; convert to opportunity, create or link customer [d-crm-convert] | CHG-d (restructured; a lead at 100 percent probability cannot be converted) | CE | HAVE: `Lead` crm.py:148, `convert` :209 makes the customer party and an opportunity | S | Enquiries via phone, IndiaMART, exhibitions |
| C3 | Similar-lead detection (email, phone) and merge [d-crm-merge] | no | CE | MISSING: [e-risks] #34 "two reps writing the same enquiry down make two leads" | S | Several reps, same buyer |
| C4 | Email alias per team creates leads, with accept-from rules [d-crm-email-manual] | no | CE | MISSING: outbound mail only ([e-mail]); #34 "Nothing reads mail" | S | Enquiry mailbox |
| C5 | Website contact form creates leads [d-crm-web-form] | no | CE (needs Website app) | MISSING (no website) | L | No website app |
| C6 | Mail plugin (Outlook, Gmail) and live chat `/lead` create leads [m-crm] | no | CE | MISSING | L | |
| C7 | Lead mining (country, state, industry, size filters; one IAP credit per lead) [d-crm-mining] | no (docs refreshed: credits not interchangeable) | CE+IAP | MISSING | L | India coverage unverified |
| C8 | Lead enrichment by email domain; partner autocomplete [d-crm-enrich] [d-crm-autocomplete] | no | CE+IAP | MISSING | L | Paid credits per lookup; Indian GSTIN data coverage not documented |
| C9 | Predictive lead scoring (naive Bayes over stage, team, plus optional state, country, source, language, tags, phone and email quality); manual override; 19 labels it "AI-computed probability", shows factors in a tooltip, and blocks 100 percent on a lead [d-crm-scoring] [s-crm-lead] | CHG-d + CHG-s (relabelled; tooltip parameter `is_tooltip`) | CE | PARTIAL: rule-of-thumb `Lead.score` crm.py:277 from source, reachability, interest, activity, age ([e-risks] #49); opportunity chance is the stage figure or typed (`Opportunity.chance`) | L | Needs hundreds of won and lost deals to learn |
| C10 | Rule-based lead assignment: members, domains, capacity per 30 days divided into a daily cap, pause assignment, auto or manual run [d-crm-teams] [s-crm-member] | CHG-d (workflow text) | CE | PARTIAL: `Lead.owner` with a "take" action (apps/sales/crm_api.py:136) and rep scoping; `SalesTeam` exists but is not tied to leads | L | Few reps |
| C11 | Activities: types (email, call, meeting, to-do, upload), scheduling, chaining (suggest or trigger next), activity plans [d-crm-activities] | CHG-d (page rewritten) | CE | PARTIAL: `Activity` crm.py:442 (kind, summary, due_on, done_on), "Calls and visits" screen, `follow_ups_due` :532; no custom types, plans or chaining; notes and follow-ups live beside it ([e-risks] #50) | S | Follow-up calls win repeat orders |
| C12 | Meetings from the opportunity, Google and Outlook calendar sync [m-crm] | no | CE | MISSING | L | |
| C13 | Email from the lead with templates; inbound threading in chatter | no | CE | PARTIAL: `Lead.email_them` crm.py:285 sends and logs an email activity; no inbound | S | |
| C14 | SMS (`crm_sms`, IAP credits), WhatsApp (templates e.g. quotations; Enterprise-only), VoIP with call transcription (3p) [d-whatsapp] [w-odooexperts] | WhatsApp doc CHG-d (troubleshooting only) | SMS CE+IAP; WhatsApp EE (doc says so); VoIP EE? | MISSING: [e-risks] #34 "Nothing reads mail or WhatsApp"; a call is logged by hand | S | Indian trade runs on WhatsApp, but Odoo's needs a Meta WhatsApp Business Platform account |
| C15 | Quotation from the opportunity; mark won or lost with a lost-reason master, restore, lost analysis [d-crm-send-quotes] [d-crm-lost] | no | CE | PARTIAL: `Opportunity.quote` crm.py:388, `win` :415, `lose` :433; lost reason is free text, no master list or restore | S | Why enquiries are lost drives pricing |
| C16 | Forecast report: opportunities by expected-closing month, drag to move, prorated revenue = expected revenue x probability [d-crm-forecast] | no | CE | PARTIAL: `pipeline()` crm.py:521 gives per-stage count, value, weighted value; `expected_on` exists but no month view | S | Monthly order book |
| C17 | Pipeline analysis: graph, pivot, cohort, list; win/loss, expected revenue, lead distribution, quality leads, unattended leads, marketing attribution reports [d-crm-winloss] [d-crm-distribution] [d-crm-quality] [d-crm-unattended] [d-crm-attribution] | CHG-d (win/loss page rewritten, cohort view) | CE | PARTIAL: `Campaign.results` (crm.py:103), pipeline view, due-activity list; no cohort or pivot | S | Manager review |
| C18 | Campaigns and UTM (campaign, medium, source) on leads, orders and invoices; link tracker [d-crm-attribution] | no | CE | PARTIAL: `Campaign` with results and `LeadSource` (exhibition, referral, web...) on leads; not carried onto orders | S | Trade-fair ROI (Plastivision-type shows) |
| C19 | Sales team features: leader, members, email alias, invoicing target bar [d-crm-teams] | no | CE | PARTIAL: `SalesTeam`/`SalesTarget` (teams.py); no alias | S | |
| C20 | Resellers / partner assignment with levels and activations (`website_crm_partner_assign`); Membership app replaced by the Membership/Partnership module (`partnership`) [d-crm-resellers] [d-crm-member] | NEW-d (explicit "Beginning with 19.0" for Partnership); resellers page CHG-d (Levels renamed, Commission Plans link dropped) | CE | MISSING | L | Dealer network later |
| C21 | CRM gamification: goals, challenges, badges [d-crm-gamification] | no | CE | MISSING | L | |
| C22 | Recurring revenue (MRR) fields on opportunities [s-crm-lead] | no | CE | MISSING | L | |
| C23 | Lead properties (custom fields per team) [s-crm-lead] (`lead_properties`) | NEW-s? | CE | MISSING on leads: `Extensible` custom fields ([e-core] :24) cover SalesOrder, Invoice, Party, Item, PurchaseOrder, Bill, not Lead or Quotation | L | |
| C24 | Priority stars, tags, colour on opportunities | no | CE | MISSING (no priority or tag field in crm.py) | L | |
| C25 | AI in CRM (new AI app): "Create Leads" agent topic, AI live-chat agent that qualifies and creates leads, AI-written email templates, "Ask AI" summaries and next steps [d-ai] [d-ai-agents] [d-ai-livechat] | NEW-d (the whole AI docs tree is added in 19) | EE? (`ai` absent from tree; document-sort needs Documents, also absent) | MISSING | L | Needs live chat and volume first |

#### 1C. Purchase (RFQ, agreements, vendor pricing, reordering, bills, portal, reporting)

| ID | Odoo 19 feature | New in 19? | Ed. | ERP status and evidence | Rel | Why (sack plant) |
|---|---|---|---|---|---|---|
| P1 | RFQ then PO: send RFQ by email with PDF template, print, confirm; states RFQ, RFQ sent, to approve, purchase, cancel [d-rfq] [s-po] (state list at purchase_order.py:105) | no | CE | PARTIAL: `PurchaseOrder` (apps/purchasing/models.py:1730) has `render_pdf` and `email_to_vendor` :1784; `RequestForQuotation` :290 is a separate document with no PDF or email | G (PO) / S (RFQ send) | RFQs to resin suppliers go out by mail or WhatsApp |
| P2 | Alternatives / call for tenders: create alternative RFQs, link existing, compare product lines, choose, cancel or keep alternatives [d-tenders] | no | CE (`purchase_requisition`) | HAVE (different shape): `RequestForQuotation` with `RfqInvitation` :524, `RfqQuote` :596, `comparison` :362, `award` :428 (its docstring explicitly rejects Odoo's modelling of an RFQ as a purchase-order state) | S | Price discovery for granules, masterbatch |
| P3 | Blanket orders: vendor, validity, lines with quantity and price, RFQs from the agreement, ordered quantity tracking, vendor line on product [d-blanket] | no | CE | HAVE: `BlanketOrder` :1102, `release` :1205, `refuse_calling_off_more` :1168; open O97 (a release drops the agreed discount) | S | Annual rate contracts |
| P4 | Purchase templates: agreement type for repeat RFQs across vendors [d-ptemplates] | no | CE | MISSING | L | |
| P5 | Vendor pricelist on product: price, quantity tiers, validity, lead time (19 relabels "Delivery Lead Time" to "Lead Time"), vendor code, discount %, currency; XLSX/CSV import and export with external id for update [d-pricelist] [x-pur-diff] | CHG-d (label) | CE | PARTIAL: `VendorPrice` :200 (quantity break, validity, `lead_time_days`, `vendor_item_code`, currency, `is_preferred`), `resolve_purchase_price` ([e-pur-pricing]); import is add-only ([e-imports] kind `vendor_prices`; [e-risks] #2); no discount %, no unit (O98); `award(record_prices=True)` records prices from an RFQ | G (price check) / S (update-in-place import, discount) | Agreed prices are what the 3-way check reads |
| P6 | Reordering rules: min, max, multiple, route Buy, scheduler; replenishment dashboard buttons Order, Automate, Snooze; temporary rule for out-of-stock products on an order [d-reorder] [d-temp-reorder] | CHG-d (Snooze button; Goods and Track Inventory wording) | CE | HAVE+: `ReorderRule` :1500 (minimum, target, multiple, MOQ, maximum, period), `suggested_quantity` :1650, "What to reorder" screen, `raise_reorder_requisition` (requires approval); MRP planned orders in apps/planning | G | Resin and film are the line stoppers |
| P7 | **Suggest**: in the RFQ catalog, quantity for "Replenish for N days" from forecast, last 7, 30 days, 3, 12 months or same month or quarter last year, scaled by a percent; demand = deliveries, MO components and subcontract resupply, less stock and incoming [d-suggest] [s-ps-product] (`suggested_qty` at product.py:39-52) | NEW-d + NEW-s | CE | PARTIAL: planning "Forecast from history" screen ([e-registry]) and `ReorderRule` suggestions exist, but nothing suggests an RFQ line from history | S | Seasonal demand (grain and fertiliser) |
| P8 | **Group RFQ by vendor**: vendor setting On Order, Daily, Weekly (+day), Always groups replenishment needs into one RFQ; never across warehouses, currencies or dropship [d-rfq] [s-ps-partner] [s-ps-rule] (stock_rule.py:370-386) | NEW-d + NEW-s (3p confirms [w-cybrosys]) | CE | PARTIAL: reorder suggestions go into one approved requisition, then `create_order(vendor, lines)` models.py:805 groups by the chosen vendor; no arrival-date rule | S | Fewer small POs to the same supplier |
| P9 | Merge selected RFQs of one vendor, currency, destination and agreement (`action_merge`) [s-po] (purchase_order.py:835) | ? | CE | MISSING | L | |
| P10 | Approval: one-step or two-step above an amount, manager group (`_approval_allowed`) [s-po] (purchase_order.py:1251) | no | CE | HAVE+: `PurchaseApprovalPolicy` :1305, `ApprovalTier` :1663 (group per amount band), `Budget` :1358 check; open O99 (typed price passes when an agreed price exists), O167 (approval withdrawn on re-price, only budget asked again) | G | Spend control |
| P11 | Lock confirmed orders [s-pur-settings] (`po_lock`) | no | CE | PARTIAL (as S19) | L | |
| P12 | Vendor acknowledgement of the PO and vendor date update through the portal; `/my/rfq`, `/my/purchase` [s-pur-portal] (routes at portal.py:111-196) | NEW-s? (`acknowledged` field, no docs) | CE | MISSING: no portal | L | Chasing is by phone |
| P13 | Receipt reminder email to the vendor N days before expected receipt (partner-level opt-in, cron) [s-pur-settings] [s-pur-partner] | no | CE | MISSING: no reminder; the scorecard is after the fact | S | Daily chasing of resin and film deliveries |
| P14 | Expected arrival from order deadline plus vendor lead time; Late filter [d-rfq] | no | CE | HAVE: `PurchaseOrderLine.expected_date`, `VendorPrice.lead_time_days`, `resolve_lead_time` ([e-pur-pricing]) | G | Inbound planning |
| P15 | Receipts, partial receipts, backorders, returns (`purchase_stock`) [m-tree] | no | CE | HAVE+: `GoodsReceipt` :6023, `ReceiptInspection` :7779 (quarantine), `create_return` :7163 | G | QC on incoming tape grade and fabric |
| P16 | Bill control policy: 19 moves it to the product (On ordered / On received quantities; goods default received, services ordered); no company-level default [d-control] [d-manage] [s-pur-settings] | CHG-d + CHG-s | CE | PARTIAL: `BillPolicy` :1719 on each `PurchaseOrder` (received or ordered), not per item | G | Bill-after-receipt discipline |
| P17 | Bill from PO, Auto-Complete from PO, batch payments, bill reference [d-manage] [d-vendor-bills] | no | CE | HAVE: `create_bill`, payment run report, duplicate-reference check (`Bill._check_duplicate_reference`; open O95 treats INV-77 and inv-77 as different) | G | Daily payables |
| P18 | Purchase matching: match PO lines to a draft bill, edit quantity and price, add to PO (`purchase.bill.line.match`) [d-vendor-bills] [s-bill-match] | no (in 18 docs) | CE | PARTIAL: bill lines link to PO lines and `_check_against_order` models.py:4195 enforces quantity and price; `match_report` :4275 was built and never called (O109) | G (the check) / S (the report) | Stops double billing |
| P19 | 3-way matching "Should Be Paid" flag on draft bills [d-control] | CHG-d (wording simplified) | EE? (`account_3way_match` absent from tree) | HAVE+: built-in refusal to bill beyond received (`bill_limit`, `_refuse_paying_for_goods_on_order`), price tolerance `Company.purchase_price_tolerance_percent` ([e-core] :1413); open O91 (ignores agreed discount) | G | Payables integrity |
| P20 | Down payments on a PO: bill lines turned into "Down Payments" PO lines (`bill_to_po_wizard`, `is_downpayment`) [s-bill2po] | NEW-s? | CE | HAVE: `create_prepayment_bill` :2288, `PrepaymentApplication` :5140, `apply_prepayment` | G | Advances for imports |
| P21 | Vendor bill digitisation: OCR and AI read uploaded or emailed bills, PO match, auto-post after three validated bills; one IAP credit per document; the OCR does not recognise taxes for Indian localisation [d-bill-digit] | no (AI wording only) | EE + IAP | MISSING (GSTR-2B matching exists as a control: apps/gst/gstr2b.py) | L | Low bill volume; Odoo's own doc says Indian taxes are not read |
| P22 | Purchase Analysis report (pivot, graph; measures Days to Confirm, Days to Receive, qty ordered, received, billed); 19 removes the "Comparison" option [d-procexp] [d-analyze] [d-vcosts] [x-pur-diff] | CHG-d | CE | PARTIAL: `vendor_performance` (models.py:5624) on-time, fill, return, price; Payables by age; MSME report; billed-not-received report; no free pivot | S | Vendor cost trends |
| P23 | **Purchase & Vendor analysis dashboard**: 8 KPI cards (purchased value, average order, orders, quantity, days to receive, days to confirm, supplier service level, on-time deliveries) and charts (top vendors, late receipts, by buyer) [d-pdash] | NEW-d | Dashboards app; edition unproven (`spreadsheet_dashboard_purchase*` absent from tree [m-tree]) | PARTIAL: Vendor scorecard screen ([e-registry]) covers on-time and fill | S | Supplier ranking |
| P24 | Product catalog inside the RFQ, filtered by vendor [d-rfq] | 3p redesign [w-nalios] | CE | MISSING | L | |
| P25 | Purchase grid entry for variants (`purchase_product_matrix`) [m-tree] | no | CE | MISSING | L | |
| P26 | Purchase warnings on vendor or product [s-pur-settings] | no | CE | PARTIAL: `VendorProfile.standing`, `payment_hold` (models.py:141) | L | |
| P27 | EDI: export PO as UBL BIS3; seller imports as sales order [d-edi] | no | CE | MISSING | L | |
| P28 | Drop-shipping and service outsourcing (`stock_dropshipping`, `sale_purchase`) [m-tree] | no | CE | HAVE: `create_for_drop_ship` :2106, `PurchaseOrderLine.sales_order_line` | S | Traded goods now and then |
| P29 | Subcontracting purchase (`mrp_subcontracting_purchase`) [m-tree] | no | CE | HAVE+: `SubcontractComponent` :1012, `issue_components` :2159 | G | Lamination or printing sent out |
| P30 | Landed costs (`stock_landed_costs`) [m-tree] | no | CE | HAVE: `LandedCostApplication` :7628, `Bill._record_landed_cost`; open O42, O48 in [e-risks] | G | Freight and duty on resin |
| P31 | Multi-currency PO, FX rate on bill | no | CE | HAVE: `Bill.exchange_rate`, settlement FX (apps/accounting/settlement.py) | G | Imported polymer |
| P32 | Buyer per order or vendor (`buyer_id` on partner [s-pur-partner]) | no | CE | PARTIAL: `PurchaseRequisition.requested_by`, `created_by`; no buyer field | L | |

#### 1D. Cross-cutting India items that touch these apps

| ID | Odoo 19 feature | New in 19? | Ed. | ERP status and evidence | Rel | Why |
|---|---|---|---|---|---|---|
| X1 | India localisation modules in the community tree: `l10n_in` (chart, GST), `l10n_in_sale`, `l10n_in_sale_stock`, `l10n_in_purchase_stock` (warehouse address on bill or invoice for e-invoice), `l10n_in_edi` (e-invoice through a GSP), `l10n_in_ewaybill` (+ `_stock`, `_irn`) [m-tree] | no (docs not diffed) | CE | HAVE+ for payloads: apps/gst builds GSTR-1, 3B, ITC-04, 2B, e-invoice and e-way bill payloads; nothing is submitted ([e-risks] known limit 8) | G | Statutory; the submission step is the open piece |
| X2 | TDS on vendor bills (Odoo has no CE TDS engine) | n/a | n/a | HAVE: apps/purchasing/tds.py and tds_api.py; sales side apps/sales/tds.py | G | Section 194Q, 194C etc. |

### Inferences
- Of the 41 Sales, 25 CRM and 32 Purchase rows, the plant-critical selling and buying documents are present; the ERP's gaps are in presentation (templates, sections, grid entry), CRM breadth and portals. See section 3 for counts.
- The 9 `G` rows that are only `PARTIAL` (S8, S9, S12, S23, S40, P1, P5, P16, P18) split two ways. Four are tied to open defects in a feature that exists: price list currency and unit (S8: O78, O79; S40: O79), vendor price unit and discount (P5: O98; P18: O91, O97, and the never-called `match_report`, O109). Five are differences of shape rather than defects: no global or fixed-amount discount wizard (S9), invoicing policy set per order rather than per item (S12), no ship-complete policy field (S23), RFQ has no PDF or email while the PO has (P1), bill policy per order rather than per item (P16). Other open defects touch `HAVE` rows: commission O74-O76 (S29), credit limit O77 (S20), blanket release O97 (P3), approval O99/O167 (P10).
- WhatsApp is the one Odoo-Enterprise capability a sack plant in India would plausibly want soon, but it needs a Meta WhatsApp Business Platform account and the Enterprise licence; the ERP already sends mail and PDFs and could add a WhatsApp send without Odoo's app.
- The new Purchase "Suggest" and "Group RFQ" are small features that map onto parts the ERP already holds (reorder rules, history forecasts, requisitions); they are cheap wins rather than new foundations.

### Gaps
- Behaviour of the ERP screens (as opposed to models) was not driven; "not verified" rows rest on reading the models only.
- Edition of the 19 Dashboards app and of the new Purchase & Vendor analysis dashboard is not established; the community tree has no `spreadsheet_dashboard_purchase*` module, so Enterprise is inferred, not documented.
- Whether S21, S41, P9, P12, P20, C23 are new in 19 could not be established: the 18.0 source is not available locally, only the 18.0 docs branch.
- Odoo's IAP lead-mining coverage for India was not found in the docs.

---

## 2. Which of these are new or materially changed in 19.0 versus 18.0

### Takeaway
The clearest, documented 19.0 changes are: Purchase "Suggest", "Group RFQ", the Purchase & Vendor analysis dashboard and the per-product bill control policy; Sales optional products rebuilt as optional sections with sub-totals and nested sections; the Membership app replaced by a Membership/Partnership module; the new AI app (CRM lead creation, live-chat agent); new marketplace connector pages. Much of the CRM docs churn is rewording of unchanged features (predictive lead scoring is relabelled "AI-computed probability").

### Cited Findings
- **Method.** `git diff --name-status origin/18.0 HEAD` on `content/applications/sales` and `content/applications/inventory_and_mrp/purchase` in the cloned Odoo docs lists the added, deleted and modified pages; per-page diffs were saved as [x-sales-diff], [x-crm-diff], [x-pur-diff]. The community source in [s-root] confirms 19-only code where a docs page exists. Odoo.com was unreachable (proxy returned 403 on a direct fetch), so release notes were not read.

**Purchase (documented in the 18.0 to 19.0 docs diff)**
- Suggest quantities from historical demand: page `advanced/suggest.rst` added [d-suggest]; code `suggested_qty`, `suggest_based_on`, `suggest_days`, `suggest_percent` context in [s-ps-product] (product.py:39-52). Third parties only found an add-on module for the same idea [w-apps-smart-rec], which does not contradict it.
- Group RFQs by vendor (On Order, Daily, Weekly, Always): section added to `rfq.rst` [d-rfq] [x-pur-diff]; fields `group_rfq`, `group_on` [s-ps-partner]; logic [s-ps-rule]; also named in [w-cybrosys] (weak).
- Purchase & Vendor analysis dashboard: page `advanced/purchase_dashboard.rst` added [d-pdash]; listed with eight KPI cards and charts; default visibility is Inventory admins.
- Bill control policy moves from a Purchase setting to each product (goods default to received quantities, services to ordered): `control_bills.rst` and `manage.rst` rewritten [x-pur-diff]; `purchase_method` is on the product [s-ps-product-model] and no longer in purchase settings [s-pur-settings].
- Purchase Analysis and vendor cost reports lose the Comparison (previous period or year) option [x-pur-diff] (analyze.rst, vendor_costs_report.rst).
- Vendor pricelist column "Delivery Lead Time" becomes "Lead Time" [x-pur-diff] (pricelist.rst).
- Replenishment dashboard buttons read Order, Automate, Snooze (previously Order Once, Automate); product type wording becomes Goods with a Track Inventory checkbox [x-pur-diff] (temporary_reordering.rst).
- Present in 19 source with no docs page and no 18 baseline: PO acknowledgement `acknowledged`/`action_acknowledge` [s-po], PO down-payment lines through the bill-to-PO wizard [s-bill2po], `action_merge` [s-po].

**Sales**
- Optional products: the "Optional Products" tab section on quotation templates and quotations is deleted from the 19 docs and replaced by "Setting optional product sections in quotations" (`Set Optional`) [d-optional] [x-sales-diff]; `sale_management/models` has no `sale_order_option` or template-option model, and template lines carry `is_optional` and `parent_id` [s-sm-tpl]. One third-party post presents optional products as new in 19 [w-ecosire]; the evidence above shows the change is the replacement of the tab by sections, not the feature's introduction.
- Sections show a sub-total; subsections and "collapse prices/composition" exist in code [d-quote] [s-sol].
- Milestone invoicing page rewritten around a "Delivered %" per milestone [x-sales-diff].
- Down-payment page reworded; states a posted down payment's income account cannot be changed [x-sales-diff].
- Docs pages added for Shopify, TikTok and Lazada connectors and a Shopee features page; Product-images page deleted; Subscriptions gains a deliveries page while scheduled-actions and automatic-alerts pages are deleted; Rental docs reorganised [x-sales-diff].
- Loyalty rewards gain a "Cheapest Product" option in developer mode [d-loyalty].
- Units of measure: 19 source has no UoM category (`relative_factor` on `uom.uom`) and products carry "Packagings" (`uom_ids`) [s-uom]; SO and PO lines use `allowed_uom_ids` [s-sol] [s-pol]. The docs for this were not examined.
- Third-party only (weak): partial down payment payable from the portal, default Quote Builder header and footer, moving commission between salespeople, separate print and send, easier combo products [w-cybrosys]; redesigned catalog with hide-prices per section [w-nalios].

**CRM**
- "Beginning with 19.0, the Membership app has been replaced by the Membership / Partnership module", activated from CRM settings; `partnership` module is in the community tree [d-crm-member] [m-tree].
- A lead at 100 percent probability cannot be converted (error image added); probability labelled "AI-computed", revert via an AI icon [d-crm-scoring] [x-crm-diff]; the code has a tooltip mode for the Bayes factors (`is_tooltip`) [s-crm-lead].
- Team member workflow rewritten: "Pause assignment", capacity divided by 30 as a daily cap, leads going unassigned when the cap is met [d-crm-teams] [x-crm-diff].
- Resellers page: "Partner Levels" renamed "Levels", Commission Plans entry dropped [d-crm-resellers] [x-crm-diff].
- Activities page rewritten as "CRM activities and activity plans" (Default Note, email templates on activity types, plans under Configuration) [d-crm-activities] [x-crm-diff].
- Win/loss report page rewritten with a cohort view [x-crm-diff].
- AI app: all pages under `productivity/ai` are added in 19, including a "Create Leads" agent topic (only if CRM is installed) and an AI live-chat agent that creates leads [d-ai-agents] [d-ai-livechat]; Ask AI cannot create leads or alter data [d-ai]. Edition not stated in the docs; `ai` is absent from the community tree [m-tree].
- Third-party only (weak): scoring factors visible, AI business-card capture, win-rate analysis [w-netframe] [w-odooexperts] [w-cybrosys-crm]; the same search flagged these as uncorroborated by Odoo's own notes.

**Enterprise-only items named by the docs**
- WhatsApp: "an Odoo Enterprise-only application"; can send Sales quotations as templates [d-whatsapp].

### Inferences
- Of the "new" items, only Suggest, Group RFQ and the vendor dashboard add Purchase behaviour a buyer sees; the rest of the Purchase docs diff is reorganisation.
- Optional sections matter to the plant only if quotations become section-structured (S4); the optional part is low value.
- The UoM overhaul is the 19 change most likely to touch this plant (sacks, bales, kg, tonnes), but it lives in the product and inventory layer outside this assignment and deserves a separate look by whoever covers Inventory.

### Gaps
- No Odoo 19 release notes or changelog could be read (odoo.com unreachable; direct fetch of a blog returned a proxy 403). Newness for source-only items is unproven.
- The 18.0 docs branch was used as the baseline; a docs diff cannot show a feature that shipped undocumented in either version.
- CRM changes in the Enterprise-only parts (VoIP transcription, voice-to-text) rest on third-party blogs only.

---

## 3. Where this ERP stands: have, partial, missing, with evidence

### Takeaway
Across the 98 Odoo feature rows in tables 1A to 1C (41 Sales, 25 CRM, 32 Purchase) the ERP is HAVE or HAVE+ on 26, PARTIAL on 34 and MISSING on 38 (Sales 12 / 13 / 16; CRM 1 / 10 / 14; Purchase 13 / 11 / 8). Nothing rated needed-before-go-live (30 rows, all in Sales and Purchase) is MISSING: 21 are HAVE or HAVE+ and 9 are PARTIAL. The 32 MISSING rows rated "later" are mostly portals, online payment, connectors, marketplaces, rental, loyalty, AI, OCR and lead-sourcing services, plus small items (combos, tags and priority, duplicate-order warning, RFQ merge, catalog card view, mass cancel) that a B2B sack plant can postpone. Only 6 MISSING rows are rated "soon": quotation templates (S3), section and note lines (S4), lead merge (C3), email-to-lead (C4), SMS and WhatsApp (C14) and vendor receipt reminders (P13). The CRM module is the thinnest area (1 HAVE of 25), and its 10 PARTIAL rows are mostly rated "soon".

### Cited Findings
- **Sales screens registered**: orders, quotations, leads, opportunities, pipeline, board, calls and visits, campaigns, deliveries, third-party releases, invoices, claims, money received, customers, receivables by age, sales report, call-offs, price lists, price indices, price clauses, variation bills, customer-supplied material, recurring invoices, payment reminders and levels, reps, teams, targets, commission plans and commission, bad debts [e-registry].
- **Purchasing screens registered**: purchase orders, goods in, receive by scan, bills, money paid, vendors, payables by age, requisitions, to quote, RFQs, blanket orders, vendor prices, vendor scorecard, what to reorder, reorder rules, budgets, purchase approval policies, freight not yet billed [e-registry].
- **Where the ERP is deeper than Odoo community** (Odoo has no equivalent in the Sales, CRM and Purchase modules read):
  - Price escalation: polymer price indices, clauses and variation bills, apps/sales/price_variation.py:50-184.
  - Call-offs against an order (apps/sales/call_offs.py:38), third-party inspection release (apps/sales/third_party.py:58), customer-supplied material for job work (`SuppliedItem`, apps/sales/models.py:1337), claims and write-offs.
  - Sales approval by discount, margin and order value (`ApprovalPolicy` :185); purchase approval tiers by group and amount plus budgets (apps/purchasing/models.py:1305, :1358, :1663).
  - Rep-scoped customers and orders (apps/sales/scoping.py:49).
  - Purchase requisitions with approval and RFQ award ([e-pur] :646, :428); blanket release with over-call refusal (:1168).
  - Receipt inspection and quarantine, landed cost, consignment, subcontract components, prepayments and deposits with FX ([e-pur] :7779, :7628, :1012, :5140).
  - India: GST place of supply on every document, e-invoice and e-way bill payloads, GSTR-1, 3B, ITC-04, 2B (apps/gst), TDS (apps/purchasing/tds.py, apps/sales/tds.py), MSME (apps/purchasing/msme.py).
  - Dunning levels and notices (`DunningLevel` :4966, `DunningNotice` :5012).
  - Quotation revisions (`create_revision` :5419).
- **Known limits and open defects that bear on rows above** ([e-risks]): #34 (CRM is a hand-kept notebook; no mail or WhatsApp reading; duplicate leads; stage chance is a stage figure), #44 (RFQ holds the need while out), #49 (lead score is a rule of thumb), #50 (notes and follow-ups sit beside CRM activities), limit 6 (permissions by model; rep scoping only), limit 8 (GST compiled, not filed); open rows O74-O76 (commission), O77 (credit limit across currencies), O78 and O79 (price list currency and unit), O91 and O97 (agreed discount ignored), O95 (vendor invoice number case), O98 (vendor price has no unit), O99 and O167 (purchase approval holes), O109 (built and never called: `match_report`, settlement discounts), O136 (`Opportunity.sales_order` not unique in the database).
- **Not in the ERP at all**: customer portal, vendor portal, online payment, e-signature, quotation templates, section lines, optional lines, order grid entry, percent or category price rules, lead merge, lead mining or enrichment, inbound email or WhatsApp, activity plans, configurable stages, team pipelines, forecast by month, RFQ PDF or email, receipt reminders, group-RFQ rule, vendor-price discount percent and update-in-place import, Suggest from history, bill OCR, purchase and sales pivots.
- **Other ERP facts used**: `Extensible` custom fields apply to SalesOrder, Invoice, Party, Item, PurchaseOrder, Bill, Employee, FixedAsset only ([e-core] :24 and class declarations); outbound mail goes through `deliver` and `send_document` ([e-mail] :36, :62); notes and follow-ups are generic ([e-chatter]); imports can add price lists and vendor prices but never update ([e-imports] :41-42; [e-risks] #2).

### Inferences
- **Go-live gaps (G):** no `G` Odoo feature is absent. The go-live work against this comparison is correcting thin spots: unit-aware and currency-aware pricing (O78, O79, O98), the discount carried through the three-way check and blanket release (O91, O97), commission correctness if used (O74-O76), credit limit across currencies (O77), and wiring `match_report` (O109) into a screen.
- **Soon (S), in order of leverage for a plant:**
  1. Quotation presentation: templates, section and note lines, order grid for variants (S3, S4, S7).
  2. Purchase chase and paper: RFQ PDF/email to vendors, receipt reminders, Group RFQ rule, vendor-price discount percent and update-in-place import (P1, P13, P8, P5).
  3. Pricing rules: percent and category rules on price lists (S8).
  4. CRM basics: lead merge, configurable stages, lost-reason master, month forecast, activity types and plans, email logging, WhatsApp send (C1, C3, C11, C13, C14, C15, C16).
  5. Reporting: sales and purchase pivots, vendor service level and days-to-confirm KPIs (S31, P22, P23).
  6. Commission slabs (S29).
- **Later or not relevant (L):** customer and vendor portals, e-signature and online payment (Razorpay and PayU modules exist in the community tree if wanted), subscriptions, rental, loyalty, marketplace and carrier connectors, UBL EDI, bill OCR (Odoo's own page says its OCR skips Indian taxes), AI app, lead mining and enrichment, predictive scoring (needs history), gamification, resellers, partnership, combos.
- Because CLAUDE.md treats a field nothing reads as worse than none, the cheapest rows to add are those that reuse existing models: RFQ PDF/email (PDF and mail helpers already exist for PO and quotation), Group RFQ and Suggest (reorder rules, requisitions, history forecast exist).

### Gaps
- Status for "not verified" rows (S6, S19, S39, C8) rests on model reading, not on driving the screens.
- I did not read the ERP's React forms; screen-level gaps (filters, smart buttons) are out of this note's scope.
- Relevance ratings are my judgment from the plant description (woven PP sacks, B2B, GST, TDS, MSME), not from plant interviews.

[d-quote]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/create_quotations.rst
[d-optional]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/optional_products.rst
[d-template]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/quote_template.rst
[d-margin]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/margin.rst
[d-deadline]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/deadline.rst
[d-sign]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/get_signature_to_validate.rst
[d-pay]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/get_paid_to_validate.rst
[d-variants-so]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/orders_and_variants.rst
[d-pdfqb]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/pdf_quote_builder.rst
[d-addr]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/sales_quotations/different_addresses.rst
[d-invpol]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/invoicing/invoicing_policy.rst
[d-dp]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/invoicing/down_payment.rst
[d-proforma]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/invoicing/proforma.rst
[d-milestone]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/invoicing/milestone.rst
[d-tm]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/invoicing/time_materials.rst
[d-pricing]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/products_prices/prices/pricing.rst
[d-discounts]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/products_prices/prices/discounts.rst
[d-loyalty]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/products_prices/loyalty_discount.rst
[d-ewallet]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/products_prices/ewallets_giftcards.rst
[d-returns]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/products_prices/returns.rst
[d-variants]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/products_prices/products/variants.rst
[d-commissions]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/commissions.rst
[d-gelato]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/sales/gelato.rst
[d-subs-deliv]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/subscriptions/subscriptions_deliveries.rst
[d-rental]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/rental.rst
[d-edi]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/advanced/edi.rst
[d-crm-convert]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/acquire_leads/convert.rst
[d-crm-mining]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/acquire_leads/lead_mining.rst
[d-crm-email-manual]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/acquire_leads/email_manual.rst
[d-crm-send-quotes]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/acquire_leads/send_quotes.rst
[d-crm-web-form]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/acquire_leads/opportunities_form.rst
[d-crm-merge]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/pipeline/merge_similar.rst
[d-crm-lost]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/pipeline/lost_opportunities.rst
[d-crm-teams]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/pipeline/manage_sales_teams.rst
[d-crm-scoring]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/track_leads/lead_scoring.rst
[d-crm-activities]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/optimize/utilize_activities.rst
[d-crm-enrich]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/optimize/lead_enrichment.rst
[d-crm-autocomplete]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/optimize/partner_autocomplete.rst
[d-crm-member]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/optimize/member_partner_module.rst
[d-crm-resellers]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/track_leads/resellers.rst
[d-crm-forecast]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/performance/forecast_report.rst
[d-crm-winloss]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/performance/win_loss.rst
[d-crm-distribution]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/track_leads/lead_distribution_report.rst
[d-crm-quality]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/track_leads/quality_leads_report.rst
[d-crm-unattended]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/track_leads/unattended_leads_report.rst
[d-crm-attribution]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/track_leads/marketing_attribution.rst
[d-crm-gamification]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/sales/crm/optimize/gamification.rst
[d-rfq]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/manage_deals/rfq.rst
[d-blanket]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/manage_deals/blanket_orders.rst
[d-tenders]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/manage_deals/calls_for_tenders.rst
[d-ptemplates]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/manage_deals/purchase_templates.rst
[d-control]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/manage_deals/control_bills.rst
[d-manage]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/manage_deals/manage.rst
[d-pricelist]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/products/pricelist.rst
[d-reorder]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/products/reordering.rst
[d-temp-reorder]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/products/temporary_reordering.rst
[d-suggest]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/advanced/suggest.rst
[d-pdash]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/advanced/purchase_dashboard.rst
[d-analyze]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/advanced/analyze.rst
[d-vcosts]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/advanced/vendor_costs_report.rst
[d-procexp]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/inventory_and_mrp/purchase/advanced/procurement_expenses_report.rst
[d-vendor-bills]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/finance/accounting/vendor_bills.rst
[d-bill-digit]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/finance/accounting/vendor_bills/invoice_digitization.rst
[d-ai]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/productivity/ai.rst
[d-ai-agents]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/productivity/ai/agents.rst
[d-ai-livechat]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/productivity/ai/live-chat.rst
[d-whatsapp]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/docs/content/applications/productivity/whatsapp.rst
[x-sales-diff]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/sales_diff_clean.txt
[x-crm-diff]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/crm_diff_clean.txt
[x-pur-diff]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/purchase.diff
[m-tree]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/src/addons
[m-crm]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo/src/addons/crm/__manifest__.py
[s-root]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons
[s-so]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/sale/models/sale_order.py
[s-sol]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/sale/models/sale_order_line.py
[s-sale-settings]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/sale/wizard/res_config_settings.py
[s-sale-portal]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/sale/controllers/portal.py
[s-sm-line]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/sale_management/models/sale_order_line.py
[s-sm-tpl]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/sale_management/models/sale_order_template_line.py
[s-uom]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/product/models/product_template.py
[s-po]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/models/purchase_order.py
[s-pol]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/models/purchase_order_line.py
[s-bill-match]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/models/purchase_bill_line_match.py
[s-bill2po]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/wizard/bill_to_po_wizard.py
[s-pur-portal]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/controllers/portal.py
[s-pur-settings]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/models/res_config_settings.py
[s-pur-partner]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/models/res_partner.py
[s-ps-partner]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase_stock/models/res_partner.py
[s-ps-rule]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase_stock/models/stock_rule.py
[s-ps-product]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase_stock/models/product.py
[s-ps-product-model]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/purchase/models/product.py
[s-crm-lead]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/crm/models/crm_lead.py
[s-crm-team]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/crm/models/crm_team.py
[s-crm-member]: /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/odoo19_src/odoo/addons/crm/models/crm_team_member.py
[w-cybrosys]: https://www.cybrosys.com/odoo/odoo-19-features/
[w-cybrosys-crm]: https://www.cybrosys.com/odoo/odoo-books/v19/crm/leads/
[w-nalios]: https://www.nalios.com/en/blog/what-s-new-in-odoo-6/odoo-19-sales-purchase-the-improved-catalog-feature-138
[w-ecosire]: https://ecosire.com/blog/odoo-19-sales-quotation-builder-walkthrough
[w-netframe]: https://www.netframe.org/blog/odoo-guide-3/odoo-19-crm-an-overview-of-new-features-for-your-business-31
[w-odooexperts]: https://www.odooexperts.nl/en_US/blog/blog-70/whats-new-in-odoo-19-296
[w-apps-smart-rec]: https://apps.odoo.com/apps/modules/19.0/purchase_smart_recommendation
[e-sales-pricing]: /home/user/hello-world/apps/sales/pricing.py
[e-pur-pricing]: /home/user/hello-world/apps/purchasing/pricing.py
[e-pur]: /home/user/hello-world/apps/purchasing/models.py
[e-mixins]: /home/user/hello-world/apps/accounting/mixins.py
[e-core]: /home/user/hello-world/apps/core/models.py
[e-mail]: /home/user/hello-world/apps/core/mail.py
[e-chatter]: /home/user/hello-world/apps/core/chatter.py
[e-saved-filters]: /home/user/hello-world/apps/core/saved_filters.py
[e-variants]: /home/user/hello-world/apps/inventory/variants.py
[e-imports]: /home/user/hello-world/apps/imports/api.py
[e-registry]: /home/user/hello-world/frontend/src/app/registry.ts
[e-risks]: /home/user/hello-world/docs/RISKS.md
