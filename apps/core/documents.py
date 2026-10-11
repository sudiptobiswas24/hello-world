"""
The plant's documents on paper: one A4 layout every printed document
shares (letterhead, the party, the details block, a table, totals, a
note, the footer), built on reportlab, which is pure Python. WeasyPrint
would give nicer typography but needs cairo and pango on the server, a
deployment burden for documents this plain.

Each module renders its own documents with `render_document`; what is
here knows nothing about invoices or challans.
"""

from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.graphics.barcode import code128
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


def address_block(address):
    return "" if address is None else address.formatted().replace("\n", "<br/>")


def money(amount, currency):
    symbol = currency.symbol if currency and currency.symbol else ""
    return f"{symbol}{amount:,.2f}"


def render_document(*, heading, document, party, address, meta, totals,
                     party_label="BILL TO", note=None, table=None, currency=None,
                     number="", qr=None):
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

    shown_number = number or getattr(document, "number", "") or ""
    title = [Paragraph(heading, style["title"])]
    if shown_number:
        # The number as Code 128, so the paper scans back into the system
        # (a purchase order at the gate, a challan at dispatch).
        title.append(code128.Code128(shown_number, barHeight=9 * mm, barWidth=0.3 * mm, humanReadable=False,
                                     quiet=False))
    story = [
        Table(
            [[Paragraph(letterhead(company), style["body"]), title]],
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
             [Paragraph(f"<b>{party.name}</b><br/>{address_block(address)}", style["body"]),
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
                Paragraph(money(line.unit_price, currency), style["right"]),
                Paragraph(
                    f"{line.discount_percent:,.0f}%" if line.discount_percent else "—",
                    style["right"],
                ),
                Paragraph(tax_names, style["right"]),
                Paragraph(money(line.net_amount(), currency), style["right"]),
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
         Paragraph(value if isinstance(value, str) else money(value, currency), style["right"])]
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

    if qr:
        story.append(Spacer(1, 6 * mm))
        story.append(qr_drawing(qr))

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


def qr_drawing(text, size=38 * mm):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    widget = QrCodeWidget(text)
    left, bottom, right, top = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (right - left), 0, 0,
                                             size / (top - bottom), 0, 0])
    drawing.add(widget)
    return drawing


def letterhead(company):
    """The company as the head of every page: name, then where it is and its GSTIN, where it has them."""
    lines = [f"<b>{company.name}</b>"]
    if company.address_id:
        lines.extend(company.address.formatted().splitlines())
    if company.tax_id:
        lines.append(f"GSTIN {company.tax_id}")
    return "<br/>".join(lines)


ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _below_thousand(number):
    hundreds, rest = divmod(number, 100)
    words = [f"{ONES[hundreds]} hundred"] if hundreds else []
    if rest:
        words.append(ONES[rest] if rest < 20 else f"{TENS[rest // 10]}{'-' + ONES[rest % 10] if rest % 10 else ''}")
    return " ".join(words)


def in_words(number):
    """A whole number in the Indian way: crore, lakh, thousand."""
    if number == 0:
        return "zero"
    crore, rest = divmod(number, 10_000_000)
    lakh, rest = divmod(rest, 100_000)
    thousand, rest = divmod(rest, 1_000)
    parts = []
    if crore:
        parts.append(f"{in_words(crore)} crore")
    if lakh:
        parts.append(f"{_below_thousand(lakh)} lakh")
    if thousand:
        parts.append(f"{_below_thousand(thousand)} thousand")
    if rest:
        parts.append(_below_thousand(rest))
    return " ".join(parts)


def rupees_in_words(amount):
    """What a tax invoice writes under its total: 'Rupees twelve lakh thirty thousand and fifty paise only'."""
    paise_total = int((Decimal(amount).copy_abs() * 100).to_integral_value(rounding="ROUND_HALF_UP"))
    rupees, paise = divmod(paise_total, 100)
    text = f"Rupees {in_words(rupees)}" + (f" and {in_words(paise)} paise" if paise else "") + " only"
    return f"Minus {text}" if Decimal(amount) < 0 else text
