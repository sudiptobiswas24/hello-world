"""
Sales documents on paper: the invoice and credit note, the quotation, the
customer statement and the delivery challan, each on the plant's shared
layout (apps/core/documents.py), differing in heading, details and how
their figures are summarised.
"""
from decimal import Decimal

from reportlab.lib.units import mm

from apps.accounting.trade_terms import freight_meta
from apps.core.documents import money, render_document, rupees_in_words
from apps.core.models import Company


def render_invoice_pdf(invoice):
    """Return the invoice as PDF bytes."""
    currency = invoice.currency
    meta = [
        ["Number", invoice.number or "(draft)"],
        ["Date", invoice.invoice_date.strftime("%d %b %Y") if invoice.invoice_date else ""],
        ["Due", invoice.due_date.strftime("%d %b %Y") if invoice.due_date else ""],
    ]
    if invoice.payment_terms_id:
        meta.append(["Terms", invoice.payment_terms.name])
    if invoice.sales_order_id:
        meta += freight_meta(invoice.sales_order)
    if invoice.reference:
        meta.append(["Reference", invoice.reference])
    if invoice.is_credit_note() and invoice.credits_id:
        meta.append(["Credits", invoice.credits.number])
    from .models import invoice_stamps

    qr = None
    for stamp in invoice_stamps(invoice):
        meta.extend([label, value] for label, value in stamp["rows"])
        qr = qr or stamp.get("qr")

    totals = [["Subtotal", invoice.subtotal()]]
    for tax, amount in sorted(invoice.tax_breakdown().items(), key=lambda pair: pair[0].code):
        totals.append([tax.name, amount])
    totals.append(["Total", invoice.total()])
    if invoice.amount_paid():
        totals.append(["Paid", f"-{money(invoice.amount_paid(), currency)}"])
    if invoice.amount_credited():
        totals.append(["Credited", f"-{money(invoice.amount_credited(), currency)}"])
    totals.append(["Amount due", invoice.amount_due()])

    note = total_in_words(invoice)
    if currency and not currency.is_base and invoice.exchange_rate:
        note = (
            f"Amounts shown in {currency.code}. Booked at {invoice.exchange_rate} "
            "to the reporting currency."
        )
    particulars = Company.get().bank_particulars()
    if particulars and not invoice.is_credit_note():
        note = f"{note or ''} Please remit to {particulars}.".strip()

    return render_document(
        heading="Credit Note" if invoice.is_credit_note() else "Invoice",
        document=invoice, party=invoice.customer, address=invoice.billing_address,
        meta=meta, totals=totals, note=note, qr=qr,
    )


def _ship_to(order):
    address = order.shipping_address
    return address.formatted().replace("\n", ", ") if address else ""


def order_note(order, proforma):
    """What the paper says under its total: what it is and, on a proforma, where to pay."""
    if proforma:
        note = ("Proforma invoice: not a tax invoice, and no supply has been made under it. The tax invoice "
                "follows the delivery.")
        particulars = Company.get().bank_particulars()
        if particulars:
            note += f" Please remit to {particulars}."
        return note
    return ("We acknowledge your order as set out above and will advise dispatch. Prices and taxes are as "
            "agreed; the tax invoice follows the delivery.")


def render_order_pdf(order, proforma=False):
    """The sales order as an acknowledgement, or as a proforma invoice asking for the money."""
    meta = [
        ["Number", order.number or "(draft)"],
        ["Date", order.order_date.strftime("%d %b %Y") if order.order_date else ""],
    ]
    if order.reference:
        meta.append(["Your order", order.reference])
    if order.payment_terms_id:
        meta.append(["Terms", order.payment_terms.name])
    meta += freight_meta(order)
    if _ship_to(order):
        meta.append(["Ship to", _ship_to(order)])
    totals = [["Subtotal", order.subtotal()]]
    for tax, amount in sorted(order.tax_breakdown().items(), key=lambda pair: pair[0].code):
        totals.append([tax.name, amount])
    totals.append(["Total", order.total()])
    return render_document(
        heading="Proforma Invoice" if proforma else "Order Acknowledgement", document=order,
        party=order.customer, address=order.billing_address, meta=meta, totals=totals,
        party_label="BILL TO" if proforma else "ORDERED BY", note=order_note(order, proforma),
    )


def total_in_words(document):
    """What a tax invoice writes under its total, for a rupee document; None in any other currency."""
    currency = document.currency
    if currency is None or currency.code != "INR":
        return None
    return f"Total in words: {rupees_in_words(document.total())}."


def render_quotation_pdf(quotation):
    """Return the quotation as PDF bytes."""
    meta = [
        ["Number", quotation.number or "(draft)"],
        ["Date", quotation.quotation_date.strftime("%d %b %Y") if quotation.quotation_date else ""],
    ]
    if quotation.valid_until:
        meta.append(["Valid until", quotation.valid_until.strftime("%d %b %Y")])
    if quotation.payment_terms_id:
        meta.append(["Terms", quotation.payment_terms.name])
    if quotation.reference:
        meta.append(["Reference", quotation.reference])

    totals = [["Subtotal", quotation.subtotal()]]
    for tax, amount in sorted(quotation.tax_breakdown().items(), key=lambda pair: pair[0].code):
        totals.append([tax.name, amount])
    totals.append(["Total", quotation.total()])

    note = None
    if quotation.valid_until:
        note = f"This quotation is valid until {quotation.valid_until:%d %b %Y}."

    return render_document(
        heading="Quotation", document=quotation, party=quotation.customer,
        address=quotation.billing_address, meta=meta, totals=totals,
        party_label="PREPARED FOR", note=note,
    )


def render_statement_pdf(statement):
    """Render a customer statement (from sales.customer_statement) as PDF."""
    currency = statement["currency"]
    customer = statement["customer"]

    meta = [["As at", statement["as_of"].strftime("%d %b %Y")]]
    if statement["since"]:
        meta.append(["From", statement["since"].strftime("%d %b %Y")])
    if customer.code:
        meta.append(["Account", customer.code])

    header = ["Date", "Type", "Reference", "Charges", "Credits", "Balance"]
    rows = []
    if statement["since"]:
        rows.append([
            statement["since"].strftime("%d %b %Y"), "Opening balance", "", "", "",
            money(statement["opening_balance"], currency),
        ])
    for entry in statement["entries"]:
        rows.append([
            entry.date.strftime("%d %b %Y"),
            entry.kind,
            entry.reference or entry.description or "—",
            money(entry.debit, currency) if entry.debit else "",
            money(entry.credit, currency) if entry.credit else "",
            money(entry.balance, currency),
        ])
    if not rows:
        rows.append(["—", "Nothing outstanding", "", "", "", money(Decimal("0"), currency)])

    totals = [["Balance due", statement["closing_balance"]]]
    if statement["overdue"] > 0:
        totals.insert(0, ["Of which overdue", statement["overdue"]])

    note = None
    if statement["overdue"] > 0:
        note = (
            f"{money(statement['overdue'], currency)} of this balance is past its due date. "
            "Please arrange payment, or contact us if any item is in dispute."
        )

    return render_document(
        heading="Statement", document=None, party=customer,
        address=customer.billing_address(), meta=meta, totals=totals,
        party_label="STATEMENT FOR", note=note, currency=currency,
        number=statement["as_of"].strftime("%Y-%m-%d"),
        table=(header, rows, [22 * mm, 30 * mm, 42 * mm, 26 * mm, 26 * mm, 28 * mm]),
    )


def render_delivery_pdf(delivery):
    """The delivery challan the lorry carries: what left, for whom, by which transporter and vehicle."""
    order = delivery.sales_order
    customer = order.customer
    meta = [
        ["Number", delivery.number or "(draft)"],
        ["Date", delivery.delivery_date.strftime("%d %b %Y") if delivery.delivery_date else ""],
        ["Order", order.number or ""],
    ]
    if delivery.reference:
        meta.append(["Reference", delivery.reference])
    if delivery.reverses_id:
        meta.append(["Returns", delivery.reverses.number])
    if delivery.transporter_id:
        meta.append(["Transporter", delivery.transporter.name])
    if delivery.lr_number:
        meta.append(["LR", f"{delivery.lr_number}" + (f" of {delivery.lr_date:%d %b %Y}" if delivery.lr_date else "")])
    if delivery.vehicle_number:
        meta.append(["Vehicle", delivery.vehicle_number])
    if delivery.received_on:
        meta.append(["Received", f"{delivery.received_on:%d %b %Y}" + (f" by {delivery.received_by}" if delivery.received_by else "")])

    header = ["Description", "Lot", "Quantity", "Unit"]
    rows, total = [], Decimal("0")
    for line in delivery.lines.select_related("order_line__item", "order_line__uom", "lot"):
        rows.append([line.order_line.label(), line.lot.code if line.lot_id else "—",
                     f"{line.quantity_shipped:,.2f}", line.order_line.uom.code if line.order_line.uom_id else ""])
        total += line.quantity_shipped
    if not rows:
        rows.append(["No lines", "", "", ""])
    return render_document(
        heading="Return" if delivery.reverses_id else "Delivery Challan", document=delivery, party=customer,
        address=delivery.shipping_address or customer.shipping_address(), party_label="DELIVER TO",
        meta=meta, totals=[["Total quantity", f"{total:,.2f}"]],
        table=(header, rows, [92 * mm, 32 * mm, 28 * mm, 22 * mm]),
        note="Received the above goods in good condition." if not delivery.reverses_id else None,
    )


def render_pick_list_pdf(rows, *, heading, document, meta, where):
    """The route a picker carries: bin, goods, batch, quantity and which delivery each is for, in walking order."""
    header = ["Bin", "Goods", "Batch", "Quantity", "For"]
    body = [[row["bin"].code if row["bin"] else "—", f"{row['item'].sku} {row['item'].name}",
             row["lot"].code if row["lot"] else "—",
             f"{row['quantity']:,.2f}" + (f" ({row['problem']})" if row["problem"] else ""),
             ", ".join(row["for"])] for row in rows]
    if not body:
        body.append(["Nothing to pick", "", "", "", ""])
    return render_document(
        heading=heading, document=document, party=where, address=None, party_label="PICK AT",
        meta=meta, totals=[["Lines", str(len(rows))]],
        table=(header, body, [22 * mm, 60 * mm, 26 * mm, 34 * mm, 32 * mm]),
    )
