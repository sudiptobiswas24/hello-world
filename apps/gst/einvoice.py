"""
E-invoices: the payload an invoice is registered with, and the IRN the
portal answers with.

Nothing is sent from here. Registering needs credentials with the
invoice registration portal, through a GST Suvidha Provider, which this
system does not hold. So it builds the payload in the portal's schema
(version 1.1), and records what the portal returned: the IRN, the
acknowledgement and the signed QR code the printed invoice must carry.

**The same figures as the return.** The portal copies each registered
invoice into GSTR-1, so an e-invoice that disagreed with the return
compiled here would file the sale twice over, differently. Both are
built from one reading of the document (`returns._document`): the
figures it froze when it posted, turned into rupees at its own rate.

**What the portal said is checked before it is kept.** The signed QR
code carries the IRN, the document number, both GSTINs and the value.
An answer pasted against the wrong invoice would print one invoice's
QR code on another's paper, so each is compared with the invoice here.
The signature itself is not verified: that needs the portal's public
key, which is not in this system.

Which invoices: from `GstSettings.einvoicing_from`, to a registered
buyer, an SEZ or abroad. Not to an unregistered buyer, which the law
leaves out; not a down payment, which carries no tax. Credit notes to
the same buyers, yes.

**Cancelling an IRN** (O220). The portal cancels an IRN within 24 hours
of its acknowledgement, never after: past that a credit note, which is
always open, corrects the invoice. `cancellation()` builds the request
(the IRN, a reason code, a remark) and refuses outside the window, or
while anything settles the invoice (money, a deposit, TDS, a credit note,
a write-off or a discount: release it first) or an e-way bill stands on
it (the portal refuses an IRN whose e-way bill is live).
`record_cancellation()` keeps the portal's answer, and the books follow
it: a full credit note of the invoice, on the invoice's own date, so
the month nets to nothing as the portal's GSTR-1 does. That note is the
books' reversal, not a credit note issued under section 34: it is never
registered, never reported, and Table 13 counts both numbers as
cancelled. The invoice prints its IRN as cancelled and is not sent:
it is no longer a valid tax invoice. Nothing undoes a cancellation; the
supply, if there was one, is invoiced again under a new number.

Not built, and said here rather than left looking done:
- The 30-day window. A company with turnover of 10 crore or more cannot
  register an invoice more than 30 days old. Whether this one has that
  turnover is not known here, so the age is returned as a warning.
- Reverse charge, e-commerce operators, and the e-way bill inside the
  IRN request (see ewaybill.py, which builds it separately).
"""

import base64
import binascii
import datetime
import json
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import round_money
from apps.core.models import numbering_first, AuditModel, lock_rows, moment_that_has_come, serialised, to_date

from . import particulars as p

ZERO = Decimal("0")
LATE_AFTER_DAYS = 30
_NUMBER = re.compile(r"^[A-Z1-9][A-Z0-9/-]{0,15}$")
_IRN = re.compile(r"^[0-9a-f]{64}$")
_ACK = re.compile(r"^[0-9]{1,20}$")


CANCEL_WITHIN = datetime.timedelta(hours=24)


from apps.core.models import takes_back  # noqa: E402  (O354: a correction step)

class IrnCancelReason(models.TextChoices):
    # The invoice registration portal's codes, which are not the e-way bill
    # portal's: there 2 is "order cancelled" and 3 "data entry mistake".
    DUPLICATE = "1", "Duplicate"
    DATA_ENTRY = "2", "Data entry mistake"
    ORDER_CANCELLED = "3", "Order cancelled"
    OTHERS = "4", "Others"


class EInvoice(AuditModel):
    invoice = models.OneToOneField("sales.Invoice", on_delete=models.PROTECT,
                                   related_name="einvoice")
    payload = models.JSONField(editable=False)
    irn = models.CharField(max_length=64, blank=True, editable=False)
    ack_number = models.CharField(max_length=20, blank=True, editable=False)
    ack_date = models.DateTimeField(null=True, blank=True, editable=False)
    signed_qr = models.TextField(blank=True, editable=False)
    cancel_reason = models.CharField(max_length=1, blank=True, choices=IrnCancelReason.choices,
                                     editable=False)
    cancel_remarks = models.CharField(max_length=100, blank=True, editable=False)
    cancel_payload = models.JSONField(null=True, blank=True, editable=False,
                                      help_text="The cancellation request, as built for the portal.")
    cancel_requested_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="When the cancellation request was built: the portal's answer cannot come before it.")
    cancelled_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="When the portal cancelled the IRN, as it answered. From then the invoice is no "
                  "valid tax invoice.")
    cancellation_note = models.OneToOneField(
        "sales.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="cancels_irn_of",
        editable=False, help_text="The full credit note the books reverse the invoice by.")

    class Meta:
        verbose_name = "e-invoice"
        constraints = [
            models.UniqueConstraint(fields=["irn"], condition=~Q(irn=""),
                                    name="einvoice_irn_unique"),
        ]

    def __str__(self):
        return f"E-invoice for {self.invoice.number}"

    def save(self, *args, **kwargs):
        if self.pk and EInvoice.objects.filter(pk=self.pk).exclude(irn="").exists():
            raise ValidationError(
                f"{self.invoice.number} is registered as {self.irn}; what was registered "
                "does not change. Correct it with a credit note."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.irn:
            raise ValidationError(f"{self.invoice.number} is registered; it cannot be deleted.")
        return super().delete(*args, **kwargs)

    def warnings(self):
        age = (timezone.localdate() - self.invoice.invoice_date).days
        if not self.irn and age > LATE_AFTER_DAYS:
            return [
                f"{self.invoice.number} is {age} days old. A company with turnover of 10 "
                f"crore or more cannot register an invoice older than {LATE_AFTER_DAYS} days."
            ]
        return []

    @transaction.atomic
    @serialised("irn")
    def record(self, irn, ack_number, ack_date, signed_qr):
        """Keep what the portal answered, once it is shown to be this invoice's."""
        if self.irn:
            raise ValidationError(f"{self.invoice.number} is already registered as {self.irn}.")
        irn = (irn or "").strip().lower()
        if not _IRN.match(irn):
            raise ValidationError("An IRN is 64 hexadecimal characters.")
        ack_number = str(ack_number or "").strip()
        if not _ACK.match(ack_number):
            raise ValidationError("The acknowledgement number is a number.")
        # The plant's day: 00:30 in Kolkata is still the day before in UTC.
        if ack_date is None or to_date(ack_date) < to_date(self.invoice.invoice_date):
            raise ValidationError(
                f"The acknowledgement must be dated on or after the invoice, "
                f"{self.invoice.invoice_date}."
            )
        moment_that_has_come(ack_date, "the portal answers with when it acknowledged the invoice")
        _check_qr(signed_qr, irn, self.payload)
        self.irn, self.ack_number, self.ack_date = irn, ack_number, ack_date
        self.signed_qr = signed_qr.strip()
        super().save(update_fields=["irn", "ack_number", "ack_date", "signed_qr", "updated_at"])

    def _refuse_cancelling(self, moment):
        """What stands between this IRN and its cancellation at `moment`: asked as it is built and as it is kept."""
        invoice = self.invoice
        if not self.irn:
            raise ValidationError(f"{invoice.number} has no IRN to cancel; delete the unregistered payload instead.")
        if invoice.is_credit_note():
            # The portal would cancel it and the books could not follow: a credit note is never
            # credited, so nothing here reverses one on its own date (review F2). Refused before
            # anything goes to the portal, so the two never part.
            raise ValidationError(
                f"{invoice.number} is a credit note. Its IRN is not cancelled from here: the books cannot take a "
                f"credit note back, and the portal and the books would part. To undo it, invoice the value again "
                f"(against {invoice.credits.number if invoice.credits_id else 'the invoice'}).")
        if self.cancelled_at is not None:
            raise ValidationError(f"{invoice.number}'s IRN was cancelled on "
                                  f"{timezone.localtime(self.cancelled_at):%d %b %Y %H:%M}.")
        if moment < self.ack_date:
            raise ValidationError(f"{invoice.number} was registered at "
                                  f"{timezone.localtime(self.ack_date):%d %b %Y %H:%M}; it cannot be cancelled "
                                  "before that.")
        if moment - self.ack_date > CANCEL_WITHIN:
            raise ValidationError(
                f"{invoice.number}'s IRN was acknowledged at {timezone.localtime(self.ack_date):%d %b %Y %H:%M}, "
                "more than 24 hours ago; the portal no longer cancels it. Correct the invoice with a credit "
                "note instead.")
        settled = invoice._settled_otherwise() + invoice.amount_credited()
        if settled:
            raise ValidationError(
                f"{invoice.number} has {settled} settled against it (payments, a deposit, TDS, a credit note, a "
                "write-off or a discount). Release them first: a cancelled invoice settles nothing.")
        paid_over = invoice.tcs_collected.filter(challan__isnull=False, challan__voided_entry__isnull=True).select_related(
            "challan").first()
        if paid_over is not None:
            # As a TDS deduction paid over is not reversed: a quarter filed on Form 143 would gain a
            # row taking back tax already paid (review F10).
            raise ValidationError(
                f"TCS of {paid_over.amount} on {invoice.number} was paid over by challan {paid_over.challan}; void "
                "the challan first, or correct the invoice with a credit note.")
        from .ewaybill import EwayBill

        standing = EwayBill.objects.filter(invoice=invoice, cancelled_at__isnull=True).exclude(number="").first()
        if standing is not None:
            raise ValidationError(f"E-way bill {standing.number} stands on {invoice.number}; cancel it first, as "
                                  "the portal asks.")

    @transaction.atomic
    @serialised("irn", "cancelled_at", "cancel_payload")
    def cancellation(self, reason, remarks="", now=None):
        """The request that cancels the IRN, within 24 hours of it. Kept until the portal has answered."""
        lock_rows(self.invoice)
        self._refuse_cancelling(now or timezone.now())
        if reason not in IrnCancelReason.values:
            raise ValidationError("Give a reason: 1 duplicate, 2 data entry mistake, 3 order cancelled, "
                                  "or 4 others.")
        remarks = " ".join((remarks or "").split())
        if not remarks:
            raise ValidationError("Say why it is cancelled: the portal asks for a remark.")
        if len(remarks) > 100:
            raise ValidationError(f"The remark is {len(remarks)} characters; the portal takes 100.")
        self.cancel_reason, self.cancel_remarks = reason, remarks
        self.cancel_payload = {"Irn": self.irn, "CnlRsn": reason, "CnlRem": remarks}
        self.cancel_requested_at = now or timezone.now()
        super().save(update_fields=["cancel_reason", "cancel_remarks", "cancel_payload", "cancel_requested_at",
                                    "updated_at"])
        return self.cancel_payload

    @transaction.atomic
    @serialised("irn", "cancelled_at", "cancel_payload")
    @takes_back
    def record_cancellation(self, cancelled_at):
        """
        Keep the portal's answer that the IRN is cancelled, and reverse the
        invoice in the books by a full credit note on its own date.
        """
        if not self.cancel_payload:
            raise ValidationError(f"No cancellation of {self.invoice.number} has been built; build one first.")
        if cancelled_at is None:
            raise ValidationError("Say when the portal cancelled it.")
        if self.cancel_requested_at and cancelled_at < self.cancel_requested_at:
            raise ValidationError(
                f"The request was built at {timezone.localtime(self.cancel_requested_at):%d %b %Y %H:%M}; the portal "
                "cannot have cancelled the IRN before it was asked to. Give the CancelDate it returned.")
        moment_that_has_come(cancelled_at, "the portal answers with when it cancelled the IRN")
        lock_rows(self.invoice)
        self._refuse_cancelling(cancelled_at)
        invoice = self.invoice
        if invoice.is_down_payment:
            raise ValidationError(f"{invoice.number} is a down payment, which is never registered.")
        note = invoice.create_credit_note(memo=f"IRN {self.irn} cancelled: {self.get_cancel_reason_display()}",
                                          on_date=invoice.invoice_date)
        self.cancelled_at, self.cancellation_note = cancelled_at, note
        super().save(update_fields=["cancelled_at", "cancellation_note", "updated_at"])
        return note


def _qr_claims(signed_qr):
    parts = (signed_qr or "").strip().split(".")
    try:
        if len(parts) != 3:
            raise ValueError
        body = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body))
        data = claims.get("data", claims)
        return json.loads(data) if isinstance(data, str) else data
    except (ValueError, binascii.Error, AttributeError, TypeError):
        raise ValidationError("That is not a signed QR code from the portal.")


def _check_qr(signed_qr, irn, payload):
    claims = _qr_claims(signed_qr)
    expected = {
        "Irn": irn,
        "DocNo": payload["DocDtls"]["No"],
        "SellerGstin": payload["SellerDtls"]["Gstin"],
        "BuyerGstin": payload["BuyerDtls"]["Gstin"],
    }
    for key, value in expected.items():
        if str(claims.get(key, "")).lower() != str(value).lower():
            raise ValidationError(
                f"The signed QR code says {key} {claims.get(key) or 'nothing'}, not {value}: "
                "it is another document's."
            )
    total = claims.get("TotInvVal")
    if total is None or Decimal(str(total)) != Decimal(str(payload["ValDtls"]["TotInvVal"])):
        raise ValidationError(
            f"The signed QR code says the invoice is worth {total}, not "
            f"{payload['ValDtls']['TotInvVal']}."
        )


def needs_irn(invoice):
    """Whether this invoice must be registered, and why not when it need not."""
    registration = p.settings()
    if registration.einvoicing_from is None or invoice.invoice_date < registration.einvoicing_from:
        return False, f"{invoice.number} is dated before e-invoicing began."
    if invoice.is_down_payment or (invoice.credits_id and invoice.credits.is_down_payment):
        return False, f"{invoice.number} is a down payment, which carries no tax."
    if invoice.is_opening_balance or (invoice.credits_id and invoice.credits.is_opening_balance
                                      and not invoice.corrects_old_supply):
        return False, f"{invoice.number} is the old system's invoice, registered from there if at all."
    if not invoice.party_gstin and invoice.party_registration != "overseas":
        return False, (f"{invoice.number} is to an unregistered buyer; those are not "
                       "e-invoiced.")
    if invoice.pk and EInvoice.objects.filter(cancellation_note=invoice).exists():
        return False, (f"{invoice.number} is the books' reversal of an invoice whose IRN was cancelled; "
                       "it is no credit note to register.")
    return True, ""


def _supply_type(document):
    paid = document.igst() > ZERO
    if document.registration == "sez":
        return "SEZWP" if paid else "SEZWOP"
    if document.registration == "overseas":
        return "EXPWP" if paid else "EXPWOP"
    return "B2B"


def _money(value):
    return float(value)


def _item(number, recorded, rate):
    line = recorded.source
    what = f"Line {number} ({line.label()})"
    if not recorded.hsn:
        raise ValidationError(f"{what} has no HSN code.")
    if len(recorded.hsn) < 6:
        raise ValidationError(
            f"{what} has HSN {recorded.hsn}. A company that e-invoices reports six digits "
            "or more."
        )
    service = recorded.hsn.startswith("99")
    total = round_money(line.gross_amount() * rate)
    taxes = recorded.igst + recorded.cgst + recorded.sgst + recorded.cess
    row = {
        "SlNo": str(number),
        "PrdDesc": p.text(line.label(), f"{what}'s description", 3, 300),
        "IsServc": "Y" if service else "N",
        "HsnCd": recorded.hsn,
        "Qty": p.quantity(line.quantity, f"{what}'s quantity"),
        "UnitPrice": p.three(line.unit_price * rate),
        # Before discount, at the unrounded price: the discount is what
        # takes it down to the taxable value the return reports.
        "TotAmt": _money(total),
        "Discount": _money(total - recorded.taxable),
        "AssAmt": _money(recorded.taxable),
        "GstRt": float(recorded.rate),
        "IgstAmt": _money(recorded.igst),
        "CgstAmt": _money(recorded.cgst),
        "SgstAmt": _money(recorded.sgst),
        "CesRt": float(recorded.rates.get("cess", ZERO)),
        "CesAmt": _money(recorded.cess),
        "TotItemVal": _money(recorded.taxable + taxes),
    }
    if not service:
        row["Unit"] = recorded.uqc
    return row, recorded.taxable + taxes


def _block(row, gstin, legal_name, trade_name=None):
    block = {"Gstin": gstin, "LglNm": legal_name}
    if trade_name:
        block["TrdNm"] = trade_name
    block |= {"Addr1": row["addr1"]}
    if "addr2" in row:
        block["Addr2"] = row["addr2"]
    block |= {"Loc": row["location"], "Pin": row["pin"], "Stcd": row["state"]}
    return block


def build(invoice):
    """The payload, in the portal's schema. Refused, with the reason, where it cannot be."""
    from .returns import _document

    if not invoice.posted:
        raise ValidationError(f"{invoice} is not posted; there is nothing to register.")
    if invoice.is_opening_balance or (invoice.credits_id and invoice.credits.is_opening_balance
                                      and not invoice.corrects_old_supply):
        raise ValidationError(
            f"{invoice.number} is an opening balance from the old system, which registered "
            "the supply; it is not registered again.")
    if not invoice.taxes_recorded:
        raise ValidationError(f"{invoice.number} posted before taxes were recorded.")
    required, reason = needs_irn(invoice)
    if not required:
        raise ValidationError(reason)
    if not _NUMBER.match(invoice.number):
        raise ValidationError(
            f"{invoice.number} cannot be registered: the portal takes up to 16 capitals, "
            "digits, / and -, not starting with 0, / or -."
        )
    registration = p.settings()
    document = _document(invoice, invoice.invoice_date, registration.state,
                         invoice.is_credit_note() or invoice.is_debit_note())
    rate = invoice.exchange_rate or Decimal("1")
    seller = p.seller()
    overseas = document.registration == "overseas"
    buyer = invoice.customer
    billed_at = invoice.billing_address or buyer.billing_address()
    shipped = invoice.shipping_address
    if (not overseas and shipped is not None and billed_at is not None and shipped.pk == billed_at.pk
            and p.state_code(billed_at) not in (None, document.gstin[:2])):
        # Billed and delivered at the buyer's site in another state (O161
        # places the supply there, O181): the buyer is the registration's,
        # at its own address, and the site is where it was shipped.
        billed_at = buyer.billing_address()
    billed = p.place(billed_at, f"{buyer}",
                     registered_state=None if overseas else document.gstin[:2],
                     domestic=not overseas)
    buyer_block = _block(billed, p.UNREGISTERED if overseas else document.gstin,
                         p.party_name(buyer))
    buyer_block["Pos"] = document.place
    payload = {
        "Version": "1.1",
        "TranDtls": {"TaxSch": "GST", "SupTyp": _supply_type(document), "RegRev": "N",
                     "IgstOnIntra": "N"},
        # A debit note is DBN and names its invoice, as a credit note is CRN (O235).
        "DocDtls": {"Typ": "DBN" if invoice.is_debit_note() else "CRN" if document.is_note else "INV",
                    "No": invoice.number,
                    "Dt": p.portal_date(invoice.invoice_date)},
        "SellerDtls": _block(seller, seller["gstin"], seller["legal_name"], seller["trade_name"]),
        "BuyerDtls": buyer_block,
    }
    # Shipped somewhere other than billed. Not for an export: the goods
    # leave by a port, and the block is optional there.
    if not overseas and shipped is not None and shipped.pk != billed_at.pk:
        ship = p.place(shipped, f"{buyer}'s delivery address")
        payload["ShipDtls"] = _block(ship, document.gstin, p.party_name(buyer))
    items, running = [], ZERO
    for number, recorded in enumerate(document.lines, start=1):
        row, value = _item(number, recorded, rate)
        items.append(row)
        running += value
    if not items:
        raise ValidationError(f"{invoice.number} has no lines.")
    payload["ItemList"] = items

    def head(name):
        return _money(sum((getattr(line, name) for line in document.lines), ZERO))

    # TCS collected with the sale is an other charge in the invoice's value, not GST (sales.tcs).
    other = round_money(invoice.tcs_amount * rate)
    payload["ValDtls"] = {
        "AssVal": _money(sum((line.taxable for line in document.lines), ZERO)),
        "CgstVal": head("cgst"), "SgstVal": head("sgst"), "IgstVal": head("igst"),
        "CesVal": head("cess"), "StCesVal": 0.0, "Discount": 0.0, "OthChrg": _money(other),
        # Lines turned into rupees one by one can sum a paisa away from
        # the document's own total turned into rupees; the total is what
        # the ledger holds.
        "RndOffAmt": _money(document.value - running - other),
        "TotInvVal": _money(document.value),
    }
    if document.is_note and (invoice.credits_id or invoice.debits_id):
        original = invoice.credits or invoice.debits
        payload["RefDtls"] = {"PrecDocDtls": [{
            "InvNo": original.reference if original.is_opening_balance else original.number,
            "InvDt": p.portal_date(original.invoice_date),
        }]}
    if overseas:
        country = billed_at.country
        if country is None:
            raise ValidationError(f"{buyer}'s address has no country; an export names one.")
        payload["ExpDtls"] = {"ForCur": invoice.currency.code if invoice.currency_id else "INR",
                              "CntCode": country.code}
    return payload


@transaction.atomic
def prepare(invoice):
    """Build the payload and keep it. Built again until the portal has answered."""
    payload = build(invoice)
    numbering_first()  # a raw row lock, after the company's numbering row (O257)
    existing = EInvoice.objects.select_for_update(no_key=True).filter(invoice=invoice).first()
    if existing is None:
        return EInvoice.objects.create(invoice=invoice, payload=payload)
    if existing.irn:
        raise ValidationError(f"{invoice.number} is already registered as {existing.irn}.")
    existing.payload = payload
    existing.save(update_fields=["payload", "updated_at"])
    return existing


def invoice_stamp(invoice):
    """
    What the printed invoice carries, for sales to print without knowing
    about GST: the IRN and QR code once registered, and a refusal to send
    it before.
    """
    if not invoice.posted:
        return None
    cancels = EInvoice.objects.filter(cancellation_note=invoice).select_related("invoice").first()
    if cancels is not None:
        # Not a credit note under section 34: never registered, never reported, never sent (review F4).
        return {
            "rows": [("Reverses", cancels.invoice.number), ("IRN cancelled",
                     timezone.localtime(cancels.cancelled_at).strftime("%d %b %Y %H:%M"))],
            "qr": None,
            "heading": "Internal reversal: not a credit note",
            "refuse_sending": (f"{invoice.number} is the books' reversal of {cancels.invoice.number}, whose IRN was "
                               "cancelled; it is no credit note under section 34 and is not sent to the buyer."),
        }
    try:
        required, _ = needs_irn(invoice)
    except ValidationError:
        return None
    if not required:
        return None
    registered = EInvoice.objects.filter(invoice=invoice).exclude(irn="").first()
    if registered is not None and registered.cancelled_at is not None:
        when = timezone.localtime(registered.cancelled_at).strftime("%d %b %Y %H:%M")
        return {
            "rows": [("IRN", registered.irn), ("IRN cancelled", when)],
            "qr": None,
            "heading": "CANCELLED: not a tax invoice",
            "refuse_sending": (f"{invoice.number}'s IRN was cancelled on {when}; it is no longer a valid "
                               "tax invoice."),
        }
    if registered is None:
        return {
            "rows": [("IRN", "not yet registered")],
            "qr": None,
            "refuse_sending": (
                f"{invoice.number} must be e-invoiced and has no IRN yet; without one it is "
                "not a valid tax invoice."
            ),
        }
    return {
        "rows": [("IRN", registered.irn), ("Ack no.", registered.ack_number),
                 ("Ack date", timezone.localtime(registered.ack_date).strftime("%d %b %Y %H:%M"))],
        "qr": registered.signed_qr,
        "refuse_sending": None,
    }
