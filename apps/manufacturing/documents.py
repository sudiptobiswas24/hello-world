"""
The job-work challan on paper: the delivery challan of rule 55 that goes
with goods sent for job work under section 143, on the plant's shared
layout (apps/core/documents.py).
"""
from decimal import Decimal

from reportlab.lib.units import mm

from apps.core.documents import money, render_document
from apps.core.models import Company


def render_challan_pdf(challan):
    meta = [
        ["Number", challan.number or "(draft)"],
        ["Date", challan.challan_date.strftime("%d %b %Y") if challan.challan_date else ""],
        ["Purpose", "Job work"],
    ]
    if challan.job_worker_gstin:
        meta.append(["GSTIN", challan.job_worker_gstin])
    if challan.vehicle:
        meta.append(["Vehicle", challan.vehicle])
    due = sorted({line.due_back_by() for line in challan.lines.all()})
    if due:
        meta.append(["Due back by", due[0].strftime("%d %b %Y")])

    currency = Company.get().base_currency
    header = ["Description", "HSN", "Quantity", "Taxable value", "GST"]
    rows, total = [], Decimal("0")
    for line in challan.lines.all():
        rows.append([line.description, line.hsn_code, f"{line.quantity:,.3f}", money(line.value, currency),
                     f"{line.tax_rate:,.0f}%"])
        total += line.value
    if not rows:
        rows.append(["No lines", "", "", "", ""])
    note = challan.notes or None
    if challan.voided_at:
        note = "VOID. " + (note or "")
    return render_document(
        heading="Delivery Challan", document=challan, party=challan.job_worker,
        address=challan.job_worker.billing_address(), party_label="JOB WORKER", meta=meta,
        totals=[["Taxable value", total]], currency=currency, note=note,
        table=(header, rows, [74 * mm, 22 * mm, 26 * mm, 32 * mm, 20 * mm]),
    )
