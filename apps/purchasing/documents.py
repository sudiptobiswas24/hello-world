"""The purchase order on paper, for the vendor: on the plant's shared layout (apps/core/documents.py)."""

from apps.core.documents import render_document
from apps.core.models import Company


def render_purchase_order_pdf(order):
    meta = [
        ["Number", order.number or "(draft)"],
        ["Date", order.order_date.strftime("%d %b %Y") if order.order_date else ""],
    ]
    if order.reference:
        meta.append(["Reference", order.reference])
    expected = sorted({line.expected_date for line in order.lines.all() if line.expected_date})
    if expected:
        meta.append(["Deliver by", expected[0].strftime("%d %b %Y") if len(expected) == 1
                     else f"{expected[0]:%d %b %Y} to {expected[-1]:%d %b %Y}"])
    company = Company.get()
    if order.subcontract_warehouse_id:
        meta.append(["Deliver to", str(order.subcontract_warehouse)])
    elif company.address_id:
        meta.append(["Deliver to", company.address.formatted().replace("\n", ", ")])

    totals = [["Subtotal", order.subtotal()]]
    for tax, amount in sorted(order.tax_breakdown().items(), key=lambda pair: pair[0].code):
        totals.append([tax.name, amount])
    totals.append(["Total", order.total()])
    return render_document(
        heading="Purchase Order", document=order, party=order.vendor, address=order.vendor.billing_address(),
        party_label="TO", meta=meta, totals=totals, note=order.shipping_note or None,
    )
