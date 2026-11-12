"""
Reverse charge on paper: the self-invoice (CGST Act s.31(3)(f), Rule 46)
and the payment voucher (s.31(3)(g), Rule 52), on the plant's shared layout.

Each prints what it was issued as: the figures kept on the document, and
the bill's lines with the tax recorded on them as the bill posted. Nothing
is worked out again here. A cancelled document says so across its heading.
"""

from decimal import Decimal

from reportlab.lib.units import mm

from apps.accounting.gst import GstSettings
from apps.core.documents import address_block, bold, counted, lines, money, render_document
from apps.core.models import Company

from .reverse_charge import HEADS

ZERO = Decimal("0")


def _supply_lines(bill, currency):
    """[description, HSN/SAC, quantity, taxable value, rate, tax] of each line of the bill under reverse charge."""
    rows = []
    for line in bill.lines.select_related("item", "charge").prefetch_related("recorded_taxes"):
        taxes = [row for row in line.recorded_taxes.all() if row.reverse_charge and row.gst_head in HEADS]
        if not taxes:
            continue
        rate = sum((row.rate for row in taxes), ZERO)
        # In base, as the document's own figures are: a bill in another currency at the rate it posted at.
        base = bill.exchange_rate or Decimal("1")
        taxable = (line.net_amount() * base).quantize(Decimal("0.01"))
        tax = sum(((row.amount * base).quantize(Decimal("0.01")) for row in taxes), ZERO)
        rows.append([line.description or (line.item.name if line.item_id else line.charge.name if line.charge_id else ""),
                     # Rule 46(g): the HSN of goods, or the SAC of a service billed as a charge (freight).
                     line.item.hsn_code if line.item_id else (line.charge.hsn_code if line.charge_id else ""),
                     counted(line.quantity),
                     money(taxable, currency), f"{rate.normalize():f}%", money(tax, currency)])
    return rows


def _supplier(document, bill):
    """Who supplied, as the document says: its kept name where it keeps one, and the bill's kept GSTIN."""
    name = getattr(document, "supplier_name", "") or bill.vendor.name
    registered = f"GSTIN {bill.party_gstin}" if bill.party_gstin else "Not registered under GST"
    return lines([bold(name), address_block(bill.vendor.billing_address()), registered])


def _place(code):
    return f"State code {code}" if code else ""


def _tax_totals(document):
    rows = [[head.upper(), getattr(document, head)] for head in HEADS if getattr(document, head)]
    return rows + [["Tax payable by the recipient", sum((getattr(document, head) for head in HEADS), ZERO)]]


def render_self_invoice_pdf(self_invoice):
    """The invoice the company issues itself for a reverse-charge supply from a supplier not registered."""
    bill, currency = self_invoice.bill, Company.get().base_currency
    settings = GstSettings.active()
    meta = [
        ["Number", self_invoice.number],
        ["Date", self_invoice.date.strftime("%d %b %Y")],
        ["Supply received", self_invoice.received_on.strftime("%d %b %Y")],
        ["Place of supply", _place(self_invoice.place_of_supply)],
        ["Our bill", f"{bill.number}{f' ({bill.reference})' if bill.reference else ''}"],
        ["Recipient's GSTIN", settings.gstin if settings else ""],
    ]
    if self_invoice.cancelled_on:
        meta.append(["Cancelled", f"{self_invoice.cancelled_on:%d %b %Y}: {self_invoice.cancellation_reason}"])
    header = ["Description", "HSN/SAC", "Quantity", "Taxable value", "Rate", "Tax"]
    widths = [58 * mm, 22 * mm, 22 * mm, 30 * mm, 14 * mm, 28 * mm]
    return render_document(
        heading="Self-Invoice (cancelled)" if self_invoice.cancelled_on else "Self-Invoice",
        subheading="Issued by the recipient under section 31(3)(f) of the CGST Act: tax payable on reverse charge",
        document=self_invoice, number=self_invoice.number, party=bill.vendor, address=bill.vendor.billing_address(),
        party_label="SUPPLIER", party_text=_supplier(self_invoice, bill), meta=meta,
        totals=[["Taxable value", self_invoice.taxable]] + _tax_totals(self_invoice),
        table=(header, _supply_lines(bill, currency), widths), currency=currency,
        note="Tax on this supply is paid by the recipient under reverse charge and is not charged by the supplier.",
        signatory=Company.get().name,
    )


def render_payment_voucher_pdf(voucher):
    """The voucher issued at the time of paying the supplier of a reverse-charge supply."""
    bill, payment, currency = voucher.bill, voucher.payment, Company.get().base_currency
    settings = GstSettings.active()
    meta = [
        ["Number", voucher.number],
        ["Date", voucher.date.strftime("%d %b %Y")],
        ["Payment", payment.number or ""],
        ["Our bill", f"{bill.number}{f' ({bill.reference})' if bill.reference else ''}"],
        ["Place of supply", _place(bill.place_of_supply)],
        ["Recipient's GSTIN", settings.gstin if settings else ""],
    ]
    if voucher.cancelled_on:
        meta.append(["Cancelled", f"{voucher.cancelled_on:%d %b %Y}: the payment was voided"])
    header = ["Description", "HSN/SAC", "Quantity", "Taxable value", "Rate", "Tax on the bill"]
    widths = [58 * mm, 22 * mm, 22 * mm, 30 * mm, 14 * mm, 28 * mm]
    return render_document(
        heading="Payment Voucher (cancelled)" if voucher.cancelled_on else "Payment Voucher",
        subheading="Issued by the recipient under section 31(3)(g) of the CGST Act: tax payable on reverse charge",
        document=voucher, number=voucher.number, party=bill.vendor, address=bill.vendor.billing_address(),
        party_label="PAID TO", party_text=_supplier(voucher, bill), meta=meta,
        totals=_tax_totals(voucher)[:-1] + [["Tax on what was paid", sum((getattr(voucher, head) for head in HEADS), ZERO)],
                                            ["Amount paid", voucher.amount]],
        table=(header, _supply_lines(bill, currency), widths), currency=currency,
        note="Tax on this supply is paid by the recipient under reverse charge; the tax shown is the part of the "
             "bill's that goes with this payment.",
        signatory=Company.get().name,
    )
