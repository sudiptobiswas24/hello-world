"""
Sales documents on paper: the invoice and credit note, the quotation, the
customer statement and the delivery challan, each on the plant's shared
layout (apps/core/documents.py), differing in heading, details and how
their figures are summarised.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from reportlab.lib.units import mm

from apps.accounting.trade_terms import freight_meta
from apps.core.documents import money, render_document, rupees_in_words
from apps.core.models import Company


COPIES = {
    "original": "Original for recipient",
    "duplicate": "Duplicate for transporter",
    "triplicate": "Triplicate for supplier",
}
HEAD_NAMES = {"cgst": "CGST", "sgst": "SGST", "igst": "IGST", "cess": "Cess"}
HEAD_ORDER = ("cgst", "sgst", "igst", "cess")


def _address_lines(address):
    return address.formatted().splitlines() if address is not None else []


def _state_of(address):
    from apps.accounting.gst import state_code

    return (state_code(address) or "") if address is not None else ""


def issued_particulars(invoice):
    """
    Who issued the invoice to whom, and where, as it stands at posting: what
    CGST Rule 46 has the paper say, kept with the invoice so a reprint says
    what was issued. The company renamed, moved or re-registered, or the
    customer renamed, would otherwise reprint every invoice they ever had.
    """
    from apps.accounting.gst import GstSettings

    company = Company.get()
    registration = GstSettings.active()
    customer = invoice.customer
    billed = invoice.billed_to()
    shipped = invoice.shipping_address
    gstin = invoice.party_gstin
    if not invoice.taxes_recorded:
        # A draft, or a document from before taxes were kept: the customer's registration as it stands.
        profile = getattr(customer, "tax_profile", None)
        gstin = (profile.gstin or "") if profile else ""
    particulars = {
        "supplier": {
            "name": company.legal_name or company.name,
            "trade_name": company.name,
            "address": _address_lines(company.address),
            "gstin": registration.gstin if registration else "",
            "state": registration.state if registration else _state_of(company.address),
        },
        "recipient": {
            "name": customer.legal_name or customer.name,
            "address": _address_lines(billed),
            "gstin": gstin,
            "state": gstin[:2] if gstin else _state_of(billed),
        },
        "delivery": None,
    }
    if shipped is not None and (billed is None or shipped.pk != billed.pk):
        particulars["delivery"] = {"address": _address_lines(shipped), "state": _state_of(shipped)}
    return particulars


def _state_name(code):
    from apps.accounting.gst import OVERSEAS_PLACE, STATES

    if not code:
        return ""
    if code == OVERSEAS_PLACE:
        return f"Other country ({code})"
    return f"{STATES.get(code, code)} ({code})"


def _line_taxes(invoice, line):
    """[(head, rate, amount, reverse charge)] as the line posted; as it computes on a draft."""
    if invoice.taxes_recorded:
        return [(row.gst_head or row.tax.code, row.rate, row.amount, row.reverse_charge)
                for row in line.recorded_taxes.all()]
    return [(tax.gst_head or tax.code, tax.rate, amount, tax.reverse_charge)
            for tax, amount in line.tax_amounts()]


def _unit(line):
    item = getattr(line, "item", None)
    if item is None:
        return ""
    unit = getattr(getattr(line, "order_line", None), "uom", None) or item.uom
    return unit.code if unit else ""


def _rate(value):
    return f"{value.normalize():f}%"


def export_words(invoice):
    """What Rule 46's proviso has an export or SEZ invoice say, by whether it bore integrated tax."""
    registration = invoice.party_registration
    if registration not in ("overseas", "sez"):
        return None
    paid = any(head == "igst" and amount > 0
               for line in invoice.lines.all() for head, _, amount, _ in _line_taxes(invoice, line))
    if registration == "overseas":
        return ("SUPPLY MEANT FOR EXPORT ON PAYMENT OF INTEGRATED TAX" if paid else
                "SUPPLY MEANT FOR EXPORT UNDER BOND OR LETTER OF UNDERTAKING WITHOUT PAYMENT OF "
                "INTEGRATED TAX")
    return ("SUPPLY MEANT FOR SEZ UNIT OR SEZ DEVELOPER FOR AUTHORISED OPERATIONS ON PAYMENT OF "
            "INTEGRATED TAX" if paid else
            "SUPPLY MEANT FOR SEZ UNIT OR SEZ DEVELOPER FOR AUTHORISED OPERATIONS UNDER BOND OR "
            "LETTER OF UNDERTAKING WITHOUT PAYMENT OF INTEGRATED TAX")


def tax_invoice(invoice, copy="original"):
    """
    What the printed tax invoice or credit note says, field by field (CGST
    Rule 46 and, for a note, Rule 53), read from what the document posted:
    its kept registrations and place of supply, each line's kept HSN and
    taxes. A draft reads as it would post, and says it is a draft.

    An invoice posted before `issued_as` existed prints the company and the
    customer as they stand, which is all it ever printed.
    """
    if copy not in COPIES:
        raise ValidationError(f"{copy!r} is not a copy; say {', '.join(COPIES)}.")
    issued = invoice.issued_as or issued_particulars(invoice)
    supplier, recipient = issued["supplier"], issued["recipient"]
    note = invoice.is_credit_note()
    if note:
        heading = "Credit Note"
    elif supplier["gstin"] and not invoice.is_down_payment:
        heading = "Tax Invoice"
    else:
        heading = "Invoice"

    lines, by_rate, reverse_charge = [], {}, False
    for line in sorted(invoice.lines.all(), key=lambda line: line.pk):
        taxes = _line_taxes(invoice, line)
        taxable = line.posted_net if line.posted_net is not None else line.net_amount()
        charged = []
        for head, rate, amount, reverse in sorted(
                taxes, key=lambda row: HEAD_ORDER.index(row[0]) if row[0] in HEAD_ORDER else 9):
            reverse_charge = reverse_charge or reverse
            charged.append((HEAD_NAMES.get(head, head), rate, amount))
            key = (HEAD_ORDER.index(head) if head in HEAD_ORDER else 9, HEAD_NAMES.get(head, head), rate)
            by_rate[key] = by_rate.get(key, Decimal("0")) + amount
        lines.append({
            "description": line.label(),
            "hsn": (line.hsn_code if invoice.taxes_recorded else line.current_hsn()) or "",
            "quantity": line.quantity, "unit": _unit(line), "unit_price": line.unit_price,
            "discount_percent": line.discount_percent, "taxable": taxable, "taxes": charged,
            "amount": taxable + sum((amount for _, _, amount in charged), Decimal("0")),
        })
    return {
        "heading": heading,
        "copy": COPIES[copy],
        "number": invoice.number or "(draft)",
        "date": invoice.invoice_date,
        "supplier": supplier | {"state_name": _state_name(supplier["state"])},
        "recipient": recipient | {"state_name": _state_name(recipient["state"])},
        "delivery": (issued["delivery"] | {"state_name": _state_name(issued["delivery"]["state"])}
                     if issued["delivery"] else None),
        "place_of_supply": _state_name(invoice.place_of_supply) if invoice.taxes_recorded else "",
        "reverse_charge": "Yes" if reverse_charge else "No",
        "credits": ({"number": invoice.credits.number, "date": invoice.credits.invoice_date}
                    if note and invoice.credits_id else None),
        "lines": lines,
        "taxable": sum((line["taxable"] for line in lines), Decimal("0")),
        "taxes": [(name, rate, amount) for (_, name, rate), amount in sorted(by_rate.items())],
        "export": export_words(invoice),
        "signatory": supplier["name"],
    }


def _party_text(recipient):
    lines = [f"<b>{recipient['name']}</b>", *recipient["address"]]
    lines.append(f"GSTIN {recipient['gstin']}" if recipient["gstin"] else "Unregistered")
    if recipient["state_name"]:
        lines.append(f"State {recipient['state_name']}")
    return "<br/>".join(lines)


def _letterhead_text(supplier):
    lines = [f"<b>{supplier['trade_name'] or supplier['name']}</b>"]
    if supplier["trade_name"] and supplier["name"] != supplier["trade_name"]:
        lines.append(supplier["name"])
    lines.extend(supplier["address"])
    if supplier["gstin"]:
        lines.append(f"GSTIN {supplier['gstin']}")
    if supplier["state_name"]:
        lines.append(f"State {supplier['state_name']}")
    return "<br/>".join(lines)


def render_invoice_pdf(invoice, copy="original"):
    """Return the invoice as PDF bytes: the tax invoice Rule 46 describes, or the credit note naming its invoice."""
    currency = invoice.currency
    printed = tax_invoice(invoice, copy)
    meta = [
        ["Number", printed["number"]],
        ["Date", printed["date"].strftime("%d %b %Y") if printed["date"] else ""],
    ]
    if printed["credits"]:
        meta.append(["Against", f"{printed['credits']['number']} dated "
                                f"{printed['credits']['date']:%d %b %Y}"])
    if printed["place_of_supply"]:
        meta.append(["Place of supply", printed["place_of_supply"]])
    meta.append(["Reverse charge", printed["reverse_charge"]])
    if invoice.due_date:
        meta.append(["Due", invoice.due_date.strftime("%d %b %Y")])
    if invoice.payment_terms_id:
        meta.append(["Terms", invoice.payment_terms.name])
    if invoice.sales_order_id:
        meta += freight_meta(invoice.sales_order)
    if invoice.reference:
        meta.append(["Reference", invoice.reference])
    from .models import invoice_stamps

    qr = None
    for stamp in invoice_stamps(invoice):
        meta.extend([label, value] for label, value in stamp["rows"])
        qr = qr or stamp.get("qr")

    header = ["Description", "HSN/SAC", "Qty", "Rate", "Taxable value", "Tax", "Amount"]
    rows = []
    for line in printed["lines"]:
        description = line["description"]
        if line["discount_percent"]:
            description += f"<br/>less {line['discount_percent'].normalize():f}%"
        rows.append([
            description, line["hsn"] or "—",
            f"{line['quantity']:,.2f} {line['unit']}".strip(),
            money(line["unit_price"], currency), money(line["taxable"], currency),
            "<br/>".join(f"{name} {_rate(rate)}<br/>{money(amount, currency)}"
                         for name, rate, amount in line["taxes"]) or "—",
            money(line["amount"], currency),
        ])
    table = (header, rows, [38 * mm, 20 * mm, 22 * mm, 20 * mm, 25 * mm, 24 * mm, 25 * mm])

    totals = [["Taxable value", printed["taxable"]]]
    totals += [[f"{name} @ {_rate(rate)}", amount] for name, rate, amount in printed["taxes"]]
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

    blocks = []
    if printed["delivery"]:
        delivery = printed["delivery"]
        text = "<br/>".join(delivery["address"])
        if delivery["state_name"]:
            text += f"<br/>State {delivery['state_name']}"
        blocks.append(("DELIVERED TO", text))
    if printed["export"]:
        blocks.append(("", f"<b>{printed['export']}</b>"))

    return render_document(
        heading=printed["heading"], subheading=printed["copy"],
        document=invoice, party=invoice.customer, address=invoice.billing_address,
        letterhead_text=_letterhead_text(printed["supplier"]),
        party_text=_party_text(printed["recipient"]),
        meta=meta, totals=totals, note=note, qr=qr, table=table, currency=currency,
        blocks=blocks, signatory=printed["signatory"],
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
