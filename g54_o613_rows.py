"""O613 narrowed, O614 and O615 registered: applied to g54's docs/RISKS.md after the demo suite."""
import sys
p = sys.argv[1] + "/docs/RISKS.md"
s = open(p).read()
lines = s.split("\n")
i = [n for n, line in enumerate(lines) if line.startswith("| O613 |")]
assert len(i) == 1
lines[i[0]] = (
    "| O613 | demo | Read as the administrator by every list route, the demo answers 55 of 266 lists with nothing "
    "(sweep of 2026-11-21, g54; calc_g53/probe_empty.py, output probe_empty_out_g54.txt). Since g54 it also holds "
    "bank statements, a paid salary file, a provident fund remittance, tools and their wear, a print design, the "
    "tape line's settings, setup families and changeovers, machine positions, crews, the power tariff, a transfer "
    "lane, party tags and suppliers' bank details. Wanted still, as lists a person would open: price clauses and "
    "their variation bills, call-offs, test certificates, weight claims, rate differences, rebatches, job-work "
    "losses. Empty by nature or out of scope, to stay so: keys, errors, integrations, licences, saved filters, role "
    "proposals, custom fields, search (it asks for words), my payslips (the administrator is nobody's employee); "
    "figures a demo cannot invent (bank rates; income-tax blocks, rates, years and openings; GST returns filed); "
    "what would move other demo tests' figures or hold their orders (tax groups, TCS and customer TDS, reverse "
    "charge's self-invoices, payment vouchers and cash payments, purchasing's approval policies, tiers and budgets, "
    "vendor profiles O500, customs valuation defaults); hr's bonus (O550), gratuity payouts, shortfall settlements "
    "and timesheets (O312); item templates, attributes and alternative units, BOM substitutes, liner "
    "specifications, packing lines, by-products, restatements, invoice write-offs, supplied items, third-party "
    "releases | demo | minor |")
lines.insert(i[0] + 1, (
    "| O614 | accounting | A journal typed through a bank account (a recurring journal's rent, a power bill, a bank "
    "charge entered by hand) can never explain a statement line: match_entry takes only movements a document "
    "registered (bank_movements), and post_to would book it a second time. The line stays unresolved, so "
    "is_reconciled() is False and close() refuses that statement for good. Seen on the demo's August to October "
    "statements (g54), none of which can close. Either register hand-typed bank journals as movements (and then "
    "decide what an opening balance is, O615) or refuse a journal line on a money account outside a document | "
    "books | decide |"))
lines.insert(i[0] + 2, (
    "| O615 | accounting | A bank account's first statement has no cut-off: every posted payment dated before it "
    "lists as unpresented for good (_unseen reads every payment up to the statement's end), so a plant that "
    "starts reconciling months after going live sees a difference no line explains. On the demo, October alone "
    "read a difference of -1,716,200; the loader now enters every month from going live. Wants a reconciled-from "
    "date (or an opening reconciliation listing what was outstanding then) on the bank account; O614's fix "
    "depends on the same answer | books | decide |"))
open(p, "w").write("\n".join(lines))
print("rows written")
