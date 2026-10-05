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

Not built, and said here rather than left looking done:
- Cancelling an IRN. A cancelled IRN takes the invoice off the portal
  and out of GSTR-1, so the return compiled here would have to leave it
  out too, and it does not yet. A credit note, which is always open,
  corrects an invoice instead.
- The 30-day window. A company with turnover of 10 crore or more cannot
  register an invoice more than 30 days old. Whether this one has that
  turnover is not known here, so the age is returned as a warning.
- Reverse charge, e-commerce operators, and the e-way bill inside the
  IRN request (see ewaybill.py, which builds it separately).
"""

import base64
import binascii
import json
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import round_money
from apps.core.models import AuditModel

from . import particulars as p

ZERO = Decimal("0")
LATE_AFTER_DAYS = 30
_NUMBER = re.compile(r"^[A-Z1-9][A-Z0-9/-]{0,15}$")
_IRN = re.compile(r"^[0-9a-f]{64}$")
_ACK = re.compile(r"^[0-9]{1,20}$")


class EInvoice(AuditModel):
    invoice = models.OneToOneField("sales.Invoice", on_delete=models.PROTECT,
                                   related_name="einvoice")
    payload = models.JSONField(editable=False)
    irn = models.CharField(max_length=64, blank=True, editable=False)
    ack_number = models.CharField(max_length=20, blank=True, editable=False)
    ack_date = models.DateTimeField(null=True, blank=True, editable=False)
    signed_qr = models.TextField(blank=True, editable=False)

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
        if ack_date is None or ack_date.date() < self.invoice.invoice_date:
            raise ValidationError(
                f"The acknowledgement must be dated on or after the invoice, "
                f"{self.invoice.invoice_date}."
            )
        _check_qr(signed_qr, irn, self.payload)
        self.irn, self.ack_number, self.ack_date = irn, ack_number, ack_date
        self.signed_qr = signed_qr.strip()
        super().save(update_fields=["irn", "ack_number", "ack_date", "signed_qr", "updated_at"])


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
    if not invoice.party_gstin and invoice.party_registration != "overseas":
        return False, (f"{invoice.number} is to an unregistered buyer; those are not "
                       "e-invoiced.")
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
                         invoice.is_credit_note())
    rate = invoice.exchange_rate or Decimal("1")
    seller = p.seller()
    overseas = document.registration == "overseas"
    buyer = invoice.customer
    billed_at = invoice.billing_address or buyer.billing_address()
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
        "DocDtls": {"Typ": "CRN" if document.is_note else "INV", "No": invoice.number,
                    "Dt": p.portal_date(invoice.invoice_date)},
        "SellerDtls": _block(seller, seller["gstin"], seller["legal_name"], seller["trade_name"]),
        "BuyerDtls": buyer_block,
    }
    # Shipped somewhere other than billed. Not for an export: the goods
    # leave by a port, and the block is optional there.
    shipped = invoice.shipping_address
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

    payload["ValDtls"] = {
        "AssVal": _money(sum((line.taxable for line in document.lines), ZERO)),
        "CgstVal": head("cgst"), "SgstVal": head("sgst"), "IgstVal": head("igst"),
        "CesVal": head("cess"), "StCesVal": 0.0, "Discount": 0.0, "OthChrg": 0.0,
        # Lines turned into rupees one by one can sum a paisa away from
        # the document's own total turned into rupees; the total is what
        # the ledger holds.
        "RndOffAmt": _money(document.value - running),
        "TotInvVal": _money(document.value),
    }
    if document.is_note and invoice.credits_id:
        original = invoice.credits
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
    existing = EInvoice.objects.select_for_update().filter(invoice=invoice).first()
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
    try:
        required, _ = needs_irn(invoice)
    except ValidationError:
        return None
    if not required:
        return None
    registered = EInvoice.objects.filter(invoice=invoice).exclude(irn="").first()
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
