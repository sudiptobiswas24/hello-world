"""
Rendering invoices as PDFs so they can actually reach the customer.

Built on reportlab, which is pure Python — WeasyPrint would give nicer
typography but needs cairo/pango system libraries, which is a deployment
burden for a document this plain.
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
    if address is None:
        return ""
    return address.formatted().replace("\n", "<br/>")


def _money(amount, currency):
    symbol = currency.symbol if currency and currency.symbol else ""
    return f"{symbol}{amount:,.2f}"


def render_invoice_pdf(invoice):
    """Return the invoice as PDF bytes."""
    company = Company.get()
    currency = invoice.currency
    style = _styles()
    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{'Credit note' if invoice.is_credit_note() else 'Invoice'} {invoice.number}",
        author=company.name,
    )

    heading = "Credit Note" if invoice.is_credit_note() else "Invoice"
    story = [
        Table(
            [[
                Paragraph(f"<b>{company.name}</b>", style["body"]),
                Paragraph(heading, style["title"]),
            ]],
            colWidths=[95 * mm, 79 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]),
        ),
        Spacer(1, 2 * mm),
    ]

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

    story.append(
        Table(
            [[
                Paragraph("BILL TO", style["h2"]),
                "",
                Paragraph("DETAILS", style["h2"]),
            ], [
                Paragraph(
                    f"<b>{invoice.customer.name}</b><br/>{_address_block(invoice.billing_address)}",
                    style["body"],
                ),
                "",
                Table(
                    [[Paragraph(label, style["muted"]), Paragraph(str(value), style["body"])]
                     for label, value in meta],
                    colWidths=[22 * mm, 52 * mm],
                    style=TableStyle([
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ]),
                ),
            ]],
            colWidths=[80 * mm, 20 * mm, 74 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]),
        )
    )
    story.append(Spacer(1, 8 * mm))

    header = ["Description", "Qty", "Unit price", "Disc", "Tax", "Amount"]
    rows = [[Paragraph(f"<b>{cell}</b>", style["muted"] if index else style["muted"])
             for index, cell in enumerate(header)]]
    for line in invoice.lines.all():
        tax_names = ", ".join(tax.code for tax, _ in line.tax_amounts()) or "—"
        rows.append([
            Paragraph(line.description or str(line.item), style["body"]),
            Paragraph(f"{line.quantity:,.2f}", style["right"]),
            Paragraph(_money(line.unit_price, currency), style["right"]),
            Paragraph(
                f"{line.discount_percent:,.0f}%" if line.discount_percent else "—", style["right"]
            ),
            Paragraph(tax_names, style["right"]),
            Paragraph(_money(line.net_amount(), currency), style["right"]),
        ])

    story.append(
        Table(
            rows,
            colWidths=[68 * mm, 18 * mm, 26 * mm, 14 * mm, 20 * mm, 28 * mm],
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

    totals = [["Subtotal", _money(invoice.subtotal(), currency)]]
    for tax, amount in sorted(invoice.tax_breakdown().items(), key=lambda pair: pair[0].code):
        totals.append([tax.name, _money(amount, currency)])
    totals.append(["Total", _money(invoice.total(), currency)])
    if invoice.amount_paid():
        totals.append(["Paid", f"-{_money(invoice.amount_paid(), currency)}"])
    if invoice.amount_credited():
        totals.append(["Credited", f"-{_money(invoice.amount_credited(), currency)}"])
    totals.append(["Amount due", _money(invoice.amount_due(), currency)])

    total_rows = [
        [Paragraph(label, style["right"]), Paragraph(value, style["right"])]
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

    if currency and not currency.is_base and invoice.exchange_rate:
        story.append(Spacer(1, 6 * mm))
        story.append(
            Paragraph(
                f"Amounts shown in {currency.code}. Booked at {invoice.exchange_rate} "
                "to the reporting currency.",
                style["muted"],
            )
        )

    footer = [company.legal_name or company.name]
    if company.tax_id:
        footer.append(f"Tax ID {company.tax_id}")
    if company.email:
        footer.append(company.email)
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(" · ".join(footer), style["muted"]))

    document.build(story)
    return buffer.getvalue()
