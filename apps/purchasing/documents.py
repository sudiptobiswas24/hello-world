"""The purchase order on paper, for the vendor: on the plant's shared layout (apps/core/documents.py)."""

from apps.accounting.trade_terms import freight_meta
from apps.core.documents import render_document
from apps.core.models import Company

from .models import VendorProfile


def render_purchase_order_pdf(order):
    meta = [
        ["Number", order.number or "(draft)"],
        ["Date", order.order_date.strftime("%d %b %Y") if order.order_date else ""],
    ]
    if order.reference:
        meta.append(["Reference", order.reference])
    profile = VendorProfile.objects.filter(party_id=order.vendor_id).first()
    if profile and profile.our_account_number:
        meta.append(["Our account", profile.our_account_number])
    if order.payment_terms_id:
        meta.append(["Terms", order.payment_terms.name])
    meta += freight_meta(order, "Port of loading")
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
        letterhead_text=_buyer(company), party_text=_vendor(order.vendor),
    )


def _buyer(company):
    """
    The plant as the supplier must bill it (O410): its legal name, address, GSTIN and state. The supplier's
    tax invoice names the buyer's GSTIN and the place of supply, and the order named neither.
    """
    from apps.accounting.gst import STATES, GstSettings

    lines = [f"<b>{company.name}</b>"]
    if company.legal_name and company.legal_name != company.name:
        lines.append(company.legal_name)
    if company.address_id:
        lines.append(company.address.formatted().replace("\n", "<br/>"))
    gst = GstSettings.active()
    if gst is not None:
        lines.append(f"GSTIN {gst.gstin}")
        lines.append(f"State {STATES.get(gst.state, gst.state)} ({gst.state})")
    return "<br/>".join(lines)


def _vendor(vendor):
    """The vendor, with the GSTIN the order is placed under, so a wrong one is seen before the invoice comes."""
    from apps.core.documents import address_block

    profile = getattr(vendor, "tax_profile", None)
    text = f"<b>{vendor.name}</b><br/>{address_block(vendor.billing_address())}"
    if profile is not None and profile.gstin:
        text += f"<br/>GSTIN {profile.gstin}"
    return text
