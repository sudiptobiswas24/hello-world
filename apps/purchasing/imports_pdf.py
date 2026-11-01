"""The bill of entry as the books hold it, on the plant's shared layout (apps/core/documents.py)."""

from django.http import HttpResponse

from apps.core.documents import counted, grouped, money, render_document


def bill_of_entry_pdf(entry):
    lines = entry.lines_in_order()
    totals = entry.totals()
    foreign = entry.currency
    meta = [
        ["BE number", entry.be_number],
        ["BE date", entry.be_date.strftime("%d %b %Y")],
        ["Port", entry.port_code],
        ["Our number", entry.number or "(draft)"],
        ["State", entry.get_status_display()],
        ["Customs rate", f"{foreign.code} 1 = {grouped(entry.customs_rate, places=4)}"],
        ["FOB", money(totals["fob"], foreign)],
        ["Freight", money(entry.freight_taken, foreign) + ("" if entry.freight is not None else " (taken)")],
        ["Insurance", money(entry.insurance_taken, foreign) + ("" if entry.insurance is not None else " (taken)")],
        ["Supplier's bills", ", ".join(f"{bill.number} ({bill.reference})" for bill in entry.bills())],
        ["Receipts cleared", ", ".join(receipt.number for receipt in entry.receipts())],
    ]
    if entry.supersedes_id:
        meta.append(["Re-assesses", f"{entry.supersedes.number} (BE {entry.supersedes.be_number})"])
    header = ["Goods", "Qty", f"FOB {foreign.code}", "Assessable", "BCD", "SWS", "IGST"]
    rows = [[f"{line.receipt_line.order_line.item.name}<br/>{line.receipt_line.receipt.number}",
             counted(line.quantity), grouped(line.fob, indian=False), grouped(line.assessable_value),
             f"{grouped(line.bcd)}<br/>@ {line.bcd_rate.normalize():f}%",
             f"{grouped(line.sws)}<br/>@ {line.sws_rate.normalize():f}%",
             f"{grouped(line.igst)}<br/>@ {line.igst_rate.normalize():f}%"] for line in lines]
    from reportlab.lib.units import mm

    widths = [44 * mm, 18 * mm, 22 * mm, 26 * mm, 22 * mm, 20 * mm, 22 * mm]
    blocks = []
    if entry.valuation_rule:
        blocks.append(("Valuation", entry.valuation_rule))
    if entry.void_reason:
        blocks.append(("Voided", f"{entry.voided_on:%d %b %Y}: {entry.void_reason}"))
    pdf = render_document(
        heading="Bill of Entry", document=entry, party=entry.customs_party,
        address=entry.customs_party.billing_address(), party_label="DUTY PAID TO", meta=meta,
        totals=[["Assessable value", totals["assessable_value"]], ["Basic customs duty", totals["bcd"]],
                ["Social welfare surcharge", totals["sws"]], ["IGST", totals["igst"]],
                ["Customs payable", totals["bcd"] + totals["sws"] + totals["igst"]]],
        table=(header, rows, widths), currency=entry.duty_bill.currency if entry.duty_bill_id else None,
        number=entry.number or entry.be_number, blocks=blocks)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{entry.number or "BE-" + entry.be_number}.pdf"'
    return response
