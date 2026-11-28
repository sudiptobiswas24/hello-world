"""
Money on paper: the remittance advice a vendor is sent with a payment
(which bills it settles, and the account it went to), and the receipt a
customer is given for money received, on the plant's shared layout.
"""

from decimal import Decimal

from reportlab.lib.units import mm

from apps.core.documents import money, render_document
from apps.core.models import Company


def render_payment_pdf(payment):
    """Remittance advice for money paid out; a payment receipt for money that came in."""
    currency = payment.currency or Company.get().base_currency
    out = payment.direction == "disbursement"
    meta = [
        ["Number", payment.number or "(draft)"],
        ["Date", payment.payment_date.strftime("%d %b %Y") if payment.payment_date else ""],
        ["Through", payment.bank_account.name],
    ]
    if payment.reference:
        meta.append(["Reference", payment.reference])
    account = payment.party.bank_accounts.filter(is_active=True).order_by("-is_primary").first()
    if out and account:
        meta.append(["Paid to", account.particulars()])
    rows, applied = [], Decimal("0")
    if out:
        header = ["Bill", "Their number", "Dated", "Bill total", "Paid"]
        widths = [40 * mm, 46 * mm, 26 * mm, 31 * mm, 31 * mm]
        for row in payment.bill_allocations.select_related("bill").order_by("bill__bill_date", "bill__number"):
            bill = row.bill
            rows.append([f"{bill.number}{' (debit note)' if bill.is_debit_note() else ''}", bill.reference,
                         bill.bill_date.strftime("%d %b %Y"), money(bill.total(), currency),
                         money(row.amount, currency)])
            applied += row.amount
    else:
        header = ["Invoice", "Dated", "Invoice total", "Received"]
        widths = [60 * mm, 32 * mm, 41 * mm, 41 * mm]
        for row in payment.invoice_allocations.select_related("invoice").order_by("invoice__invoice_date",
                                                                                   "invoice__number"):
            invoice = row.invoice
            rows.append([f"{invoice.number}{' (credit note)' if invoice.is_credit_note() else ''}",
                         invoice.invoice_date.strftime("%d %b %Y"), money(invoice.total(), currency),
                         money(row.amount, currency)])
            applied += row.amount
    if not rows:
        rows.append(["On account: applied to nothing yet", "", "", "", ""][: len(header)])
    totals = [["Applied", applied]]
    if payment.amount != applied:
        totals.append(["On account", payment.amount - applied])
    totals.append(["Amount paid" if out else "Amount received", payment.amount])
    note = ("This advises payment of the documents listed. No reply is needed; a query goes to the address below."
            if out else "With thanks. This acknowledges money received; it is not a tax invoice.")
    if payment.memo:
        note = f"{payment.memo} {note}"
    return render_document(
        heading="Remittance Advice" if out else "Payment Receipt", document=payment, party=payment.party,
        address=payment.party.billing_address(), meta=meta, totals=totals, party_label="PAID TO" if out else "RECEIVED FROM",
        table=(header, rows, widths), currency=currency, note=note,
    )
