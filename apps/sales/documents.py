"""
Rendering sales documents as PDFs so they can actually reach the customer.

Built on reportlab, which is pure Python — WeasyPrint would give nicer
typography but needs cairo/pango system libraries, which is a deployment
burden for documents this plain.

Invoices and quotations share one layout: the same company header, party
block, line table and totals, differing only in their heading, the details
they show and how their totals are summarised.
"""
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.models import Company

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#666666")
RULE = colors.HexColor("#d4d4d4")
BAND = colors.HexColor("#f4f4f4")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Heading1"], fontSize=20, leading=24, textColor=INK, spaceAfter=2
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Normal"], fontSize=8, leading=11, textColor=MUTED,
            spaceAfter=3, fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12, textColor=INK),
        "right": ParagraphStyle(
            "right", parent=base["Normal"], fontSize=9, leading=12, alignment=TA_RIGHT, textColor=INK
        ),
        "muted": ParagraphStyle(
            "muted", parent=base["Normal"], fontSize=8, leading=11, textColor=MUTED
        ),
    }


def _address_block(address):
    return "" if address is None else address.formatted().replace("\n", "<br/>")


def _money(amount, currency):
    symbol = currency.symbol if currency and currency.symbol else ""
    return f"{symbol}{amount:,.2f}"


def _render_document(*, heading, document, party, address, meta, totals,
                     party_label="BILL TO", note=None, table=None, currency=None,
                     number=""):
    """
    `meta` is [[label, value]] for the details block; `totals` is
    [[label, Decimal]] with the last row emphasised as the bottom line.

    `table` is (header, rows, col_widths) for documents whose body isn't a
    list of priced lines — a statement, for instance. Without it the body
    is built from `document.lines`.
    """
    company = Company.get()
    currency = currency or getattr(document, "currency", None)
    style = _styles()
    buffer = BytesIO()

    template = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{heading} {number or getattr(document, 'number', '')}".strip(),
        author=company.name,
    )

    story = [
        Table(
            [[Paragraph(f"<b>{company.name}</b>", style["body"]),
              Paragraph(heading, style["title"])]],
            colWidths=[95 * mm, 79 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]),
        ),
        Spacer(1, 2 * mm),
        Table(
            [[Paragraph(party_label, style["h2"]), "", Paragraph("DETAILS", style["h2"])],
             [Paragraph(f"<b>{party.name}</b><br/>{_address_block(address)}", style["body"]),
              "",
              Table(
                  [[Paragraph(label, style["muted"]), Paragraph(str(value), style["body"])]
                   for label, value in meta],
                  colWidths=[24 * mm, 50 * mm],
                  style=TableStyle([
                      ("VALIGN", (0, 0), (-1, -1), "TOP"),
                      ("LEFTPADDING", (0, 0), (-1, -1), 0),
                      ("TOPPADDING", (0, 0), (-1, -1), 1),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                  ]),
              )]],
            colWidths=[80 * mm, 20 * mm, 74 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]),
        ),
        Spacer(1, 8 * mm),
    ]

    if table is not None:
        header, body, col_widths = table
        rows = [[Paragraph(f"<b>{cell}</b>", style["muted"]) for cell in header]]
        for row in body:
            rows.append([
                Paragraph(str(cell), style["body" if index == 0 else "right"])
                for index, cell in enumerate(row)
            ])
    else:
        header = ["Description", "Qty", "Unit price", "Disc", "Tax", "Amount"]
        col_widths = [68 * mm, 18 * mm, 26 * mm, 14 * mm, 20 * mm, 28 * mm]
        rows = [[Paragraph(f"<b>{cell}</b>", style["muted"]) for cell in header]]
        for line in document.lines.all():
            tax_names = ", ".join(tax.code for tax, _ in line.tax_amounts()) or "—"
            rows.append([
                Paragraph(line.label(), style["body"]),
                Paragraph(f"{line.quantity:,.2f}", style["right"]),
                Paragraph(_money(line.unit_price, currency), style["right"]),
                Paragraph(
                    f"{line.discount_percent:,.0f}%" if line.discount_percent else "—",
                    style["right"],
                ),
                Paragraph(tax_names, style["right"]),
                Paragraph(_money(line.net_amount(), currency), style["right"]),
            ])

    story.append(
        Table(
            rows,
            colWidths=col_widths,
            repeatRows=1,
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BAND),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]),
        )
    )
    story.append(Spacer(1, 4 * mm))

    total_rows = [
        [Paragraph(label, style["right"]),
         Paragraph(value if isinstance(value, str) else _money(value, currency), style["right"])]
        for label, value in totals
    ]
    story.append(
        Table(
            total_rows,
            colWidths=[104 * mm, 70 * mm],
            hAlign="RIGHT",
            style=TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, len(totals) - 1), (-1, len(totals) - 1), 0.6, INK),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]),
        )
    )

    if note:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(note, style["muted"]))

    footer = [company.legal_name or company.name]
    if company.tax_id:
        footer.append(f"Tax ID {company.tax_id}")
    if company.email:
        footer.append(company.email)
    if company.phone:
        footer.append(company.phone)
    if company.website:
        footer.append(company.website)
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(" · ".join(footer), style["muted"]))

    template.build(story)
    return buffer.getvalue()


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
    if invoice.reference:
        meta.append(["Reference", invoice.reference])
    if invoice.is_credit_note() and invoice.credits_id:
        meta.append(["Credits", invoice.credits.number])

    totals = [["Subtotal", invoice.subtotal()]]
    for tax, amount in sorted(invoice.tax_breakdown().items(), key=lambda pair: pair[0].code):
        totals.append([tax.name, amount])
    totals.append(["Total", invoice.total()])
    if invoice.amount_paid():
        totals.append(["Paid", f"-{_money(invoice.amount_paid(), currency)}"])
    if invoice.amount_credited():
        totals.append(["Credited", f"-{_money(invoice.amount_credited(), currency)}"])
    totals.append(["Amount due", invoice.amount_due()])

    note = None
    if currency and not currency.is_base and invoice.exchange_rate:
        note = (
            f"Amounts shown in {currency.code}. Booked at {invoice.exchange_rate} "
            "to the reporting currency."
        )

    return _render_document(
        heading="Credit Note" if invoice.is_credit_note() else "Invoice",
        document=invoice, party=invoice.customer, address=invoice.billing_address,
        meta=meta, totals=totals, note=note,
    )


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

    return _render_document(
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
            _money(statement["opening_balance"], currency),
        ])
    for entry in statement["entries"]:
        rows.append([
            entry.date.strftime("%d %b %Y"),
            entry.kind,
            entry.reference or entry.description or "—",
            _money(entry.debit, currency) if entry.debit else "",
            _money(entry.credit, currency) if entry.credit else "",
            _money(entry.balance, currency),
        ])
    if not rows:
        rows.append(["—", "Nothing outstanding", "", "", "", _money(Decimal("0"), currency)])

    totals = [["Balance due", statement["closing_balance"]]]
    if statement["overdue"] > 0:
        totals.insert(0, ["Of which overdue", statement["overdue"]])

    note = None
    if statement["overdue"] > 0:
        note = (
            f"{_money(statement['overdue'], currency)} of this balance is past its due date. "
            "Please arrange payment, or contact us if any item is in dispute."
        )

    return _render_document(
        heading="Statement", document=None, party=customer,
        address=customer.billing_address(), meta=meta, totals=totals,
        party_label="STATEMENT FOR", note=note, currency=currency,
        number=statement["as_of"].strftime("%Y-%m-%d"),
        table=(header, rows, [22 * mm, 30 * mm, 42 * mm, 26 * mm, 26 * mm, 28 * mm]),
    )
