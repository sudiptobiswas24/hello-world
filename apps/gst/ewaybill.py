"""
E-way bills: the payload a consignment moves on, and the number the
portal answers with.

As with e-invoices, nothing is sent from here: the payload is built in
the portal's API shape and what it answered is recorded.

Two documents move goods out of this plant:
- an invoice, for goods sold (sub-type Supply, or Export);
- a job-work challan, for goods sent to be worked on (sub-type Job Work).
  Sent to another state, that needs an e-way bill whatever it is worth;
  inside the state, only above the limit like anything else;
- a delivery on a job-work sales order, for the customer's goods going
  back converted (sub-type Job Work Returns, on a delivery challan). No
  invoice carries them: the invoice bills the conversion, a service. The
  value is the goods' whole value, the customer's material included,
  which only the plant and the customer know, so it is stated; it may not
  be less than the conversion billed on those lines.

**Required is said, not enforced.** Under the limit an e-way bill is
optional and some plants make one anyway, so a payload is built either
way and says whether one was needed, and why.

**One standing per document.** The portal refuses a second e-way bill
on the same document, so this does too until the first is cancelled.
Cancelling is allowed for 24 hours from generation, as on the portal;
after that the bill expires unused or the recipient rejects it.

**The transport has to be there.** By road, a vehicle number, unless a
transporter is named and will add it (Part B). By rail, air or ship, the
transport document. The vehicle number is checked for the shapes a
registration takes: state series, Bharat series, temporary.

Not built: updating the vehicle en route (Part B changes), extending a
bill's validity, consolidated e-way bills, goods coming back (a
customer's return or a job worker's; the one sending them generates
it), and dispatch from an address other than the company's. A warehouse
holds its address as free text, which has no PIN to give.
"""

import datetime
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.gst import STATES
from apps.core.models import AuditModel

from . import particulars as p

ZERO = Decimal("0")
CANCEL_WITHIN = datetime.timedelta(hours=24)
MAX_DISTANCE_KM = 4000
_NUMBER = re.compile(r"^[0-9]{12}$")
_DOC_NUMBER = re.compile(r"^[A-Za-z0-9/-]{1,16}$")
_VEHICLE = [
    re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$"),   # KA25AB1234
    re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$"),            # 22BH1234AB
    re.compile(r"^TR[A-Z0-9]{6,13}$"),                        # temporary
]
_TRANSPORTER = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")


class TransportMode(models.TextChoices):
    ROAD = "1", "Road"
    RAIL = "2", "Rail"
    AIR = "3", "Air"
    SHIP = "4", "Ship"


class VehicleType(models.TextChoices):
    REGULAR = "R", "Regular"
    OVER_DIMENSIONAL = "O", "Over-dimensional cargo"


class CancelReason(models.TextChoices):
    DUPLICATE = "1", "Duplicate"
    ORDER_CANCELLED = "2", "Order cancelled"
    DATA_ENTRY = "3", "Data entry mistake"
    OTHERS = "4", "Others"


class EwayBill(AuditModel):
    invoice = models.ForeignKey("sales.Invoice", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="eway_bills")
    challan = models.ForeignKey("manufacturing.JobWorkChallan", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="eway_bills")
    delivery = models.ForeignKey("sales.Delivery", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="eway_bills")
    mode = models.CharField(max_length=1, choices=TransportMode.choices,
                            default=TransportMode.ROAD)
    distance_km = models.PositiveIntegerField(
        default=0, help_text="0 lets the portal work it out from the two PINs.")
    transporter_id = models.CharField(max_length=15, blank=True)
    transporter_name = models.CharField(max_length=100, blank=True)
    vehicle_number = models.CharField(max_length=15, blank=True)
    vehicle_type = models.CharField(max_length=1, choices=VehicleType.choices,
                                    default=VehicleType.REGULAR)
    transport_doc_number = models.CharField(max_length=15, blank=True)
    transport_doc_date = models.DateField(null=True, blank=True)
    payload = models.JSONField(editable=False)
    required = models.BooleanField(editable=False)
    required_because = models.CharField(max_length=255, editable=False)
    number = models.CharField(max_length=12, blank=True, editable=False)
    generated_at = models.DateTimeField(null=True, blank=True, editable=False)
    valid_until = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancel_reason = models.CharField(max_length=1, choices=CancelReason.choices, blank=True,
                                     editable=False)
    cancel_remarks = models.CharField(max_length=100, blank=True, editable=False)

    class Meta:
        verbose_name = "e-way bill"
        constraints = [
            models.CheckConstraint(
                check=(Q(invoice__isnull=False) & Q(challan__isnull=True)
                       & Q(delivery__isnull=True))
                | (Q(invoice__isnull=True) & Q(challan__isnull=False)
                   & Q(delivery__isnull=True))
                | (Q(invoice__isnull=True) & Q(challan__isnull=True)
                   & Q(delivery__isnull=False)),
                name="eway_bill_one_document"),
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="eway_bill_number_unique"),
            models.UniqueConstraint(fields=["invoice"], condition=Q(cancelled_at__isnull=True),
                                    name="one_standing_eway_bill_per_invoice"),
            models.UniqueConstraint(fields=["challan"], condition=Q(cancelled_at__isnull=True),
                                    name="one_standing_eway_bill_per_challan"),
            models.UniqueConstraint(fields=["delivery"], condition=Q(cancelled_at__isnull=True),
                                    name="one_standing_eway_bill_per_delivery"),
        ]

    def __str__(self):
        return f"E-way bill {self.number or '(not generated)'} for {self.document().number}"

    def document(self):
        return self.invoice or self.challan or self.delivery

    def save(self, *args, **kwargs):
        if (self.pk and not getattr(self, "_cancelling", False)
                and EwayBill.objects.filter(pk=self.pk).exclude(number="").exists()):
            raise ValidationError(
                f"E-way bill {self.number} is generated. Cancel it and make another."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.number:
            raise ValidationError(f"E-way bill {self.number} is generated; cancel it.")
        return super().delete(*args, **kwargs)

    @transaction.atomic
    def record(self, number, generated_at, valid_until=None):
        if self.number:
            raise ValidationError(f"This is already e-way bill {self.number}.")
        number = str(number or "").strip()
        if not _NUMBER.match(number):
            raise ValidationError("An e-way bill number is twelve digits.")
        if generated_at is None or generated_at.date() < _document_date(self.document()):
            raise ValidationError(
                f"An e-way bill cannot be generated before {self.document().number}, dated "
                f"{_document_date(self.document())}."
            )
        if valid_until is not None and valid_until <= generated_at:
            raise ValidationError("It cannot expire before it was generated.")
        self.number, self.generated_at, self.valid_until = number, generated_at, valid_until
        super().save(update_fields=["number", "generated_at", "valid_until", "updated_at"])

    @transaction.atomic
    def cancel(self, reason, remarks="", now=None):
        if not self.number:
            raise ValidationError("It was never generated; delete it instead.")
        if self.cancelled_at is not None:
            raise ValidationError(f"E-way bill {self.number} is already cancelled.")
        now = now or timezone.now()
        if now - self.generated_at > CANCEL_WITHIN:
            raise ValidationError(
                f"E-way bill {self.number} was generated more than 24 hours ago; the portal "
                "no longer cancels it. It expires unused, or the recipient rejects it."
            )
        if reason not in CancelReason.values:
            raise ValidationError("Give a reason: duplicate, order cancelled, data entry "
                                  "mistake, or others.")
        remarks = " ".join((remarks or "").split())
        if reason == CancelReason.OTHERS and not remarks:
            raise ValidationError("Say what the other reason is.")
        self.cancelled_at, self.cancel_reason, self.cancel_remarks = now, reason, remarks[:100]
        self._cancelling = True
        try:
            self.save(update_fields=["cancelled_at", "cancel_reason", "cancel_remarks",
                                     "updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._cancelling = False


def _document_date(document):
    for name in ("invoice_date", "challan_date", "delivery_date"):
        value = getattr(document, name, None)
        if value:
            return value
    return None


def normalise_vehicle(number):
    return re.sub(r"[\s-]", "", (number or "")).upper()


def _transport(mode, distance_km, transporter_id, transporter_name, vehicle_number,
               vehicle_type, transport_doc_number, transport_doc_date, document_date):
    if mode not in TransportMode.values:
        raise ValidationError("Say how the goods go: road, rail, air or ship.")
    distance_km = int(distance_km or 0)
    if not 0 <= distance_km <= MAX_DISTANCE_KM:
        raise ValidationError(f"The portal takes a distance of 0 to {MAX_DISTANCE_KM} km.")
    transporter_id = (transporter_id or "").strip().upper()
    if transporter_id and not (_TRANSPORTER.match(transporter_id)
                               and transporter_id[:2] in STATES):
        raise ValidationError(f"{transporter_id} is not a GSTIN or transporter ID.")
    vehicle_number = normalise_vehicle(vehicle_number)
    transport_doc_number = (transport_doc_number or "").strip()
    if mode == TransportMode.ROAD:
        if vehicle_number and not any(shape.match(vehicle_number) for shape in _VEHICLE):
            raise ValidationError(
                f"{vehicle_number} is not the shape of a vehicle registration."
            )
        if not vehicle_number and not transporter_id:
            raise ValidationError(
                "By road, give the vehicle number, or name the transporter who will add it."
            )
    else:
        if not transport_doc_number or transport_doc_date is None:
            raise ValidationError(
                f"By {TransportMode(mode).label.lower()}, give the transport document's "
                "number and date."
            )
        if transport_doc_date < document_date:
            raise ValidationError("The transport document cannot predate the goods' document.")
    if vehicle_type not in VehicleType.values:
        raise ValidationError("A vehicle is regular or over-dimensional.")
    return {
        "mode": mode, "distance_km": distance_km, "transporter_id": transporter_id,
        "transporter_name": " ".join((transporter_name or "").split())[:100],
        "vehicle_number": vehicle_number, "vehicle_type": vehicle_type,
        "transport_doc_number": transport_doc_number, "transport_doc_date": transport_doc_date,
    }


def _transport_payload(transport):
    row = {
        "transMode": transport["mode"],
        "transDistance": str(transport["distance_km"]),
        "transporterId": transport["transporter_id"],
        "transporterName": transport["transporter_name"],
        "transDocNo": transport["transport_doc_number"],
        "transDocDate": (p.portal_date(transport["transport_doc_date"])
                         if transport["transport_doc_date"] else ""),
        "vehicleNo": transport["vehicle_number"],
        "vehicleType": transport["vehicle_type"] if transport["vehicle_number"] else "",
    }
    return row


def _from(seller):
    row = {
        "fromGstin": seller["gstin"], "fromTrdName": seller["trade_name"],
        "fromAddr1": seller["addr1"], "fromAddr2": seller.get("addr2", ""),
        "fromPlace": seller["location"], "fromPincode": seller["pin"],
        "fromStateCode": int(seller["state"]), "actFromStateCode": int(seller["state"]),
    }
    return row


def _to(gstin, name, place, billed_state):
    return {
        "toGstin": gstin, "toTrdName": name,
        "toAddr1": place["addr1"], "toAddr2": place.get("addr2", ""),
        "toPlace": place["location"], "toPincode": place["pin"],
        "toStateCode": int(billed_state), "actToStateCode": int(place["state"]),
    }


def _limit_test(value, registration):
    if value > registration.eway_bill_limit:
        return True, f"Worth {value}, above the limit of {registration.eway_bill_limit}."
    return False, f"Worth {value}, not above the limit of {registration.eway_bill_limit}."


def _invoice_payload(invoice):
    from .returns import _document

    if not invoice.posted:
        raise ValidationError(f"{invoice} is not posted.")
    if invoice.is_credit_note():
        raise ValidationError(
            f"{invoice.number} is a credit note. Goods coming back move on the sender's "
            "e-way bill, which is not built here."
        )
    if invoice.is_down_payment:
        raise ValidationError(f"{invoice.number} is a down payment; no goods move on it.")
    if not invoice.taxes_recorded:
        raise ValidationError(f"{invoice.number} posted before taxes were recorded.")
    registration = p.settings()
    document = _document(invoice, invoice.invoice_date, registration.state, False)
    goods = [line for line in document.lines if not line.hsn.startswith("99")]
    for line in goods:
        if not line.hsn:
            raise ValidationError(f"{line.source.label()} has no HSN code.")
    if not goods:
        raise ValidationError(f"{invoice.number} bills only services; nothing moves.")
    overseas = document.registration == "overseas"
    buyer = invoice.customer
    going_to = invoice.shipping_address or invoice.billing_address or buyer.shipping_address()
    # For an export, where the goods go in India is the port.
    place = p.place(going_to, f"{buyer}'s delivery address" + (" (the port)" if overseas else ""))
    if overseas:
        billed_state = p.OTHER_COUNTRY
    else:
        billed_state = document.gstin[:2] if document.gstin else document.place
        if not billed_state:
            raise ValidationError(f"{invoice.number} records no place of supply.")
    billed_at = invoice.billing_address or buyer.billing_address()
    two_places = billed_at is not None and going_to is not None and going_to.pk != billed_at.pk
    items = []
    for number, line in enumerate(goods, start=1):
        items.append({
            "itemNo": number,
            "productName": p.text(line.source.label(), f"Line {number}'s description", 1, 100),
            "productDesc": p.text(line.source.label(), f"Line {number}'s description", 1, 100),
            "hsnCode": int(line.hsn),
            "quantity": p.quantity(line.source.quantity, f"Line {number}'s quantity"),
            "qtyUnit": line.uqc,
            "taxableAmount": float(line.taxable),
            "cgstRate": float(line.rates.get("cgst", ZERO)),
            "sgstRate": float(line.rates.get("sgst", ZERO)),
            "igstRate": float(line.rates.get("igst", ZERO)),
            "cessRate": float(line.rates.get("cess", ZERO)),
            "cessNonadvol": 0,
        })

    def head(name):
        return sum((getattr(line, name) for line in goods), ZERO)

    taxable = head("taxable")
    taxes = head("cgst") + head("sgst") + head("igst") + head("cess")
    seller = p.seller()
    payload = {
        "supplyType": "O", "subSupplyType": "3" if overseas else "1", "subSupplyDesc": "",
        "docType": "INV", "docNo": invoice.number,
        "docDate": p.portal_date(invoice.invoice_date),
        **_from(seller),
        **_to(p.UNREGISTERED if overseas or not document.gstin else document.gstin,
              p.party_name(buyer), place, billed_state),
        "transactionType": 2 if two_places else 1,
        "totalValue": float(taxable),
        "cgstValue": float(head("cgst")), "sgstValue": float(head("sgst")),
        "igstValue": float(head("igst")), "cessValue": float(head("cess")),
        "cessNonAdvolValue": 0,
        # Services billed alongside, their tax, and any paisa of rounding:
        # on the invoice, not goods on the lorry.
        "otherValue": float(document.value - taxable - taxes),
        "totInvValue": float(document.value),
        "itemList": items,
    }
    required, because = _limit_test(document.value, registration)
    return payload, required, because


def _challan_payload(challan):
    if not challan.posted or challan.voided_at is not None:
        raise ValidationError(f"{challan} is not an issued challan.")
    registration = p.settings()
    worker = challan.job_worker
    if not challan.job_worker_state:
        raise ValidationError(
            f"{worker} had no state on its tax profile when {challan.number} was issued."
        )
    place = p.place(worker.shipping_address(), f"{worker}",
                    registered_state=challan.job_worker_state if challan.job_worker_gstin
                    else None)
    items, total = [], ZERO
    for number, line in enumerate(challan.lines.select_related(
            "operation__work_order__uom__gst_uqc"), start=1):
        unit = getattr(line.operation.work_order.uom, "gst_uqc", None)
        items.append({
            "itemNo": number,
            "productName": p.text(line.description, f"Line {number}'s description", 1, 100),
            "productDesc": p.text(line.description, f"Line {number}'s description", 1, 100),
            "hsnCode": int(line.hsn_code),
            "quantity": p.quantity(line.quantity, f"Line {number}'s quantity"),
            "qtyUnit": unit.code if unit else "OTH",
            "taxableAmount": float(line.value),
            "cgstRate": 0, "sgstRate": 0, "igstRate": 0, "cessRate": 0, "cessNonadvol": 0,
        })
        total += line.value
    payload = {
        "supplyType": "O", "subSupplyType": "4", "subSupplyDesc": "",
        "docType": "CHL", "docNo": challan.number,
        "docDate": p.portal_date(challan.challan_date),
        **_from(p.seller()),
        **_to(challan.job_worker_gstin or p.UNREGISTERED, p.party_name(worker), place,
              challan.job_worker_state),
        "transactionType": 1,
        "totalValue": float(total),
        "cgstValue": 0, "sgstValue": 0, "igstValue": 0, "cessValue": 0,
        "cessNonAdvolValue": 0, "otherValue": 0,
        "totInvValue": float(total),
        "itemList": items,
    }
    if challan.job_worker_state != registration.state:
        return payload, True, (f"Sent to a job worker in {STATES[challan.job_worker_state]}: "
                               "across a state line, whatever it is worth.")
    required, because = _limit_test(total, registration)
    return payload, required, because


def _delivery_payload(delivery, declared_value):
    from apps.accounting.models import PartyTaxProfile, round_money

    if not delivery.posted:
        raise ValidationError(f"{delivery} has not shipped.")
    if delivery.is_return():
        raise ValidationError(
            f"{delivery} is goods coming back; the customer sending them generates the "
            "e-way bill."
        )
    order = delivery.sales_order
    if not order.is_job_work:
        raise ValidationError(
            f"{delivery} is a sale: its goods move on the invoice's e-way bill."
        )
    if declared_value is None:
        raise ValidationError(
            "State the goods' value going back: the customer's material and the "
            "conversion together."
        )
    try:
        declared_value = Decimal(str(declared_value))
    except (ArithmeticError, ValueError):
        raise ValidationError(f"{declared_value} is not an amount.")
    if declared_value <= 0:
        raise ValidationError("Goods going back are worth something; state what.")
    lines = list(delivery.lines.select_related("order_line__item", "order_line__uom__gst_uqc"))
    conversion = []
    for line in lines:
        order_line = line.order_line
        # What the line bills after its discount: the floor is the
        # conversion actually charged, not the list price of it.
        gross = line.quantity_shipped * (order_line.unit_price or ZERO)
        conversion.append(round_money(
            gross * (Decimal("100") - order_line.discount_percent) / Decimal("100")))
    base = order.currency is None or order.currency.is_base
    if base and declared_value < sum(conversion, ZERO):
        raise ValidationError(
            f"The conversion billed on these goods alone is {sum(conversion, ZERO)}; they "
            f"cannot be worth {declared_value}."
        )
    registration = p.settings()
    customer = order.customer
    profile = PartyTaxProfile.objects.filter(party=customer).first()
    gstin = profile.gstin if profile else ""
    going_to = delivery.shipping_address or order.shipping_address or customer.shipping_address()
    place = p.place(going_to, f"{customer}'s delivery address",
                    registered_state=gstin[:2] if gstin else None)
    items, spread, total = [], ZERO, sum(conversion, ZERO)
    for number, (line, share) in enumerate(zip(lines, conversion), start=1):
        item = line.order_line.item
        if not item.hsn_code:
            raise ValidationError(f"{item} has no HSN code.")
        # The stated value laid on the lines as the conversion is; the last
        # takes what rounding leaves, so the lines foot to the whole.
        value = (declared_value - spread if number == len(lines) else
                 round_money(declared_value * share / total) if total else ZERO)
        spread += value
        unit = getattr(line.order_line.uom, "gst_uqc", None)
        items.append({
            "itemNo": number,
            "productName": p.text(item.name, f"Line {number}'s description", 1, 100),
            "productDesc": p.text(line.order_line.label(), f"Line {number}'s description", 1, 100),
            "hsnCode": int(item.hsn_code),
            "quantity": p.quantity(line.quantity_shipped, f"Line {number}'s quantity"),
            "qtyUnit": unit.code if unit else "OTH",
            "taxableAmount": float(value),
            "cgstRate": 0, "sgstRate": 0, "igstRate": 0, "cessRate": 0, "cessNonadvol": 0,
        })
    payload = {
        "supplyType": "O", "subSupplyType": "6", "subSupplyDesc": "",
        "docType": "CHL", "docNo": delivery.number,
        "docDate": p.portal_date(delivery.delivery_date),
        **_from(p.seller()),
        **_to(gstin or p.UNREGISTERED, p.party_name(customer), place,
              gstin[:2] if gstin else place["state"]),
        "transactionType": 1,
        "totalValue": float(declared_value),
        "cgstValue": 0, "sgstValue": 0, "igstValue": 0, "cessValue": 0,
        "cessNonAdvolValue": 0, "otherValue": 0,
        "totInvValue": float(declared_value),
        "itemList": items,
    }
    required, because = _limit_test(declared_value, registration)
    return payload, required, because


@transaction.atomic
def prepare(document, mode=TransportMode.ROAD, distance_km=0, transporter_id="",
            transporter_name="", vehicle_number="", vehicle_type=VehicleType.REGULAR,
            transport_doc_number="", transport_doc_date=None, declared_value=None):
    """Build the payload for an invoice, a challan or a job-work delivery, and keep it."""
    from apps.sales.models import Delivery, Invoice

    is_invoice = isinstance(document, Invoice)
    is_delivery = isinstance(document, Delivery)
    if not is_invoice and not is_delivery:
        challan_vehicle = normalise_vehicle(document.vehicle)
        given = normalise_vehicle(vehicle_number)
        if challan_vehicle and given and given != challan_vehicle:
            raise ValidationError(
                f"{document.number} says it went on {challan_vehicle}, not {given}."
            )
        vehicle_number = given or challan_vehicle
    transport = _transport(mode, distance_km, transporter_id, transporter_name,
                           vehicle_number, vehicle_type, transport_doc_number,
                           transport_doc_date, _document_date(document))
    if is_invoice:
        payload, required, because = _invoice_payload(document)
    elif is_delivery:
        payload, required, because = _delivery_payload(document, declared_value)
    else:
        payload, required, because = _challan_payload(document)
    if not _DOC_NUMBER.match(payload["docNo"] or ""):
        # Defined with the other portal limits and, until now, checked by
        # nothing: a longer number built a payload the portal refuses.
        raise ValidationError(
            f"{payload['docNo']} cannot go on an e-way bill: the portal takes up to 16 "
            "letters, digits, / and -."
        )
    payload |= _transport_payload(transport)
    key = ({"invoice": document} if is_invoice else {"delivery": document} if is_delivery
           else {"challan": document})
    existing = EwayBill.objects.select_for_update().filter(
        cancelled_at__isnull=True, **key).first()
    if existing is not None and existing.number:
        raise ValidationError(
            f"{document.number} already moves on e-way bill {existing.number}. Cancel it "
            "first."
        )
    bill = existing or EwayBill(**key)
    for name, value in transport.items():
        setattr(bill, name, value)
    bill.payload, bill.required, bill.required_because = payload, required, because
    bill.save()
    return bill


def refuse_challan_void(challan):
    """A challan whose goods are on the road on an e-way bill is not withdrawn
    from under it: the bill is cancelled first."""
    standing = EwayBill.objects.filter(challan=challan, cancelled_at__isnull=True).first()
    if standing is not None:
        raise ValidationError(
            f"{challan} has e-way bill {standing.number or '(not yet generated)'} standing. "
            "Cancel or delete it first."
        )
