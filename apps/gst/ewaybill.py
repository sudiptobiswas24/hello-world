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

**One invoice on several trucks** (limit 20). A delivery records the invoice its goods
travelled under (`Delivery.invoice`), and an e-way bill is made for each such consignment
(`carried_on`): the truck's own transporter, lorry receipt and vehicle, and its own share of the
invoice's value, worked total first so the parts foot to the invoice to the paisa and the last
takes what rounding leaves. CGST Rule 55(5) has goods sent in lots move on a delivery challan for
each consignment but the last, which carries the invoice, and that is how each part is headed.

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

from collections import defaultdict

from apps.accounting.gst import STATES, state_delivered_in
from apps.core.models import numbering_first, AuditModel, moment_that_has_come, serialised, to_date

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
# The portal's sub-supply type for goods sent in lots on one invoice (its master code 9, "SKD/CKD/Lots"),
# the only outward type it takes a delivery challan under beside an invoice. NOT CONFIRMED against the
# portal's current masters: the plant's GST practitioner confirms it before the first such bill is uploaded.
LOTS = "9"
HEADS = ("taxable", "cgst", "sgst", "igst", "cess")


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
    carried_on = models.ForeignKey(
        "sales.Delivery", null=True, blank=True, on_delete=models.PROTECT, related_name="consignment_bills",
        help_text="On an invoice's bill: the delivery (truck) of that invoice this consignment is. Blank: "
                  "the invoice names no delivery and the bill covers it whole.")
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
            models.CheckConstraint(check=Q(carried_on__isnull=True) | Q(invoice__isnull=False),
                                   name="eway_bill_consignment_of_an_invoice"),
            models.UniqueConstraint(fields=["invoice"],
                                    condition=Q(cancelled_at__isnull=True, carried_on__isnull=True),
                                    name="one_standing_eway_bill_per_invoice"),
            models.UniqueConstraint(fields=["carried_on"], condition=Q(cancelled_at__isnull=True),
                                    name="one_standing_eway_bill_per_consignment"),
            models.UniqueConstraint(fields=["challan"], condition=Q(cancelled_at__isnull=True),
                                    name="one_standing_eway_bill_per_challan"),
            models.UniqueConstraint(fields=["delivery"], condition=Q(cancelled_at__isnull=True),
                                    name="one_standing_eway_bill_per_delivery"),
        ]

    def __str__(self):
        return f"E-way bill {self.number or '(not generated)'} for {self.document().number}"

    def document(self):
        return self.invoice or self.challan or self.delivery

    def document_date(self):
        """The date of the paper the goods move on: a lot's challan is its delivery's, not the invoice's."""
        if self.carried_on_id and (self.payload or {}).get("docType") == "CHL":
            return self.carried_on.delivery_date
        return _document_date(self.document())

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
    @serialised("number")
    def record(self, number, generated_at, valid_until=None):
        if self.number:
            raise ValidationError(f"This is already e-way bill {self.number}.")
        number = str(number or "").strip()
        if not _NUMBER.match(number):
            raise ValidationError("An e-way bill number is twelve digits.")
        if generated_at is None or to_date(generated_at) < self.document_date():
            raise ValidationError(
                f"An e-way bill cannot be generated before {self.payload.get('docNo') or self.document().number}, "
                f"dated {self.document_date()}."
            )
        moment_that_has_come(generated_at, "the portal answers with when it generated the e-way bill")
        if valid_until is not None and valid_until <= generated_at:
            raise ValidationError("It cannot expire before it was generated.")
        self.number, self.generated_at, self.valid_until = number, generated_at, valid_until
        super().save(update_fields=["number", "generated_at", "valid_until", "updated_at"])

    @transaction.atomic
    @serialised("number", "cancelled_at")
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


def consignments(invoice):
    """
    The deliveries an invoice's goods went on, oldest first (`Delivery.invoice`). A delivery since
    returned in full, for which no e-way bill was generated, was a shipment put right and is no
    consignment; one a bill was generated for went, whatever came back.
    """
    from django.db.models import Sum

    from apps.sales.models import Delivery, DeliveryLine

    found = []
    for delivery in Delivery.objects.filter(invoice=invoice, posted=True, reverses__isnull=True).order_by(
            "delivery_date", "pk"):
        shipped = delivery.lines.aggregate(total=Sum("quantity_shipped"))["total"] or ZERO
        back = DeliveryLine.objects.filter(delivery__reverses=delivery, delivery__posted=True).aggregate(
            total=Sum("quantity_shipped"))["total"] or ZERO
        if back >= shipped and not EwayBill.objects.filter(carried_on=delivery).exclude(number="").exists():
            continue
        found.append(delivery)
    return found


def consignment_shares(invoice, goods, other, tcs, parts):
    """
    Each consignment's share of an invoice, in the order the trucks left: [{"delivery", "lines":
    {index of the goods line: (quantity, {head: amount})}, "other", "tcs", "final", "lots"}].

    Total first, divide last (CLAUDE.md, numbers): a line's taxable value and each tax on it are
    the invoice's own recorded figures times the quantity this truck carried over the quantity
    billed, rounded once; the truck that completes the line takes what is left of each, so the
    parts foot to the line to the paisa. What the invoice bills beside the goods (freight, its tax,
    TCS, rounding) follows the goods' taxable value the same way and the last truck takes the rest.
    A truck's share never moves when a later truck is tied: only the one that completes is "what is left".
    """
    from apps.accounting.models import round_money
    from apps.core.api import plain
    from apps.sales.models import DeliveryLine

    order_lines = [line.source.order_line_id for line in goods]
    if None in order_lines or len(set(order_lines)) != len(order_lines):
        raise ValidationError(
            f"{invoice.number} bills goods from no order line, or one order line twice, so which truck carried "
            "what cannot be worked out. Take its deliveries off it (the delivery's page) and make one e-way "
            "bill for the whole invoice, typing the transport.")
    carried = defaultdict(Decimal)
    for delivery_id, order_line_id, quantity in DeliveryLine.objects.filter(
            delivery__in=parts, order_line__in=order_lines).values_list("delivery", "order_line", "quantity_shipped"):
        carried[delivery_id, order_line_id] += quantity
    done = [dict.fromkeys(("quantity",) + HEADS, ZERO) for _ in goods]
    whole_taxable = sum((line.taxable for line in goods), ZERO)
    spread = {"other": ZERO, "tcs": ZERO}
    shares = []
    for position, delivery in enumerate(parts):
        lines = {}
        for index, line in enumerate(goods):
            quantity = carried[delivery.pk, line.source.order_line_id]
            if not quantity:
                continue
            billed, so_far = line.source.quantity, done[index]
            if so_far["quantity"] + quantity > billed:
                raise ValidationError(
                    f"{invoice.number} bills {plain(billed)} of {line.source.label()}, and its deliveries up to "
                    f"{delivery.number} carried {plain(so_far['quantity'] + quantity)}. A delivery billed on two "
                    "invoices cannot be split here: take it off this invoice and type the transport.")
            completes = so_far["quantity"] + quantity == billed
            amounts = {}
            for head in HEADS:
                whole = getattr(line, head)
                amounts[head] = whole - so_far[head] if completes else round_money(whole * quantity / billed)
                so_far[head] += amounts[head]
            so_far["quantity"] += quantity
            lines[index] = (quantity, amounts)
        if not lines:
            raise ValidationError(f"{delivery.number} travels on {invoice.number} and carried none of the goods it "
                                  "bills. Take it off the invoice (the delivery's page).")
        final = position == len(parts) - 1 and all(
            row["quantity"] == line.source.quantity for row, line in zip(done, goods))
        taxable = sum((amounts["taxable"] for _, amounts in lines.values()), ZERO)
        share = {"delivery": delivery, "lines": lines, "final": final}
        for name, whole in (("other", other), ("tcs", tcs)):
            share[name] = (whole - spread[name] if final
                           else round_money(whole * taxable / whole_taxable) if whole_taxable else ZERO)
            spread[name] += share[name]
        shares.append(share)
    for share in shares:
        share["lots"] = len(shares) > 1 or not shares[0]["final"]
    return shares


def _invoice_payload(invoice, carried_on=None):
    from .returns import _document

    if not invoice.posted:
        raise ValidationError(f"{invoice} is not posted.")
    if invoice.is_credit_note():
        raise ValidationError(
            f"{invoice.number} is a credit note. Goods coming back move on the sender's "
            "e-way bill, which is not built here."
        )
    if invoice.is_debit_note():
        # A supplementary invoice bills more for goods that moved on the invoice's own bill (review F5).
        raise ValidationError(f"{invoice.number} is a debit note: no goods move on it, so it has no e-way bill.")
    if invoice.is_down_payment:
        raise ValidationError(f"{invoice.number} is a down payment; no goods move on it.")
    from .einvoice import EInvoice

    cancelled = EInvoice.objects.filter(invoice=invoice, cancelled_at__isnull=False).first()
    if cancelled is not None:
        raise ValidationError(f"{invoice.number}'s IRN was cancelled on "
                              f"{timezone.localtime(cancelled.cancelled_at):%d %b %Y %H:%M}: it is no tax invoice, "
                              "and no goods move on it.")
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
    # Where the goods go, as the invoice's place of supply read it.
    going_to = invoice.delivered_to()
    billed_at = invoice.billed_to()
    # Where the lorry goes is a fact the invoice kept as it posted (Invoice.delivery_state, .port), or
    # that was recorded on it since (record_where_it_went): never the address as it reads today.
    kept_state, kept_port = invoice.where_it_went()
    if overseas:
        # An export goes to a place in India first: the port, ICD or CFS it named as it posted (O545).
        # The buyer's address abroad was read for it, and refused for having no Indian PIN.
        if not kept_port:
            raise ValidationError(
                f"{invoice.number} is an export and records no place in India its goods left the plant for "
                "(the port, ICD or CFS), which this bill is made out to. It posted before one was asked for: "
                "record it on the invoice (Record where it went).")
        place = p.kept_place(kept_port, f"{invoice.number}'s port")
    else:
        # From before the state was kept: the one rule's answer, as posting placed it (O533). An address
        # with no state that is the one billed to reads the place of supply the invoice posted at.
        state = kept_state or state_delivered_in(going_to, billed_at, document.place)
        if going_to is not None and state is None:
            raise ValidationError(
                f"{invoice.number}'s delivery address ({going_to.one_line()}) says no GST state, and a posted "
                "invoice's address is not edited. Record the state its goods were delivered in on the invoice "
                "(Record where it went).")
        place = p.place(going_to, f"{buyer}'s delivery address", standing=state)
        if kept_state:
            place["state"] = kept_state
    if overseas:
        billed_state = p.OTHER_COUNTRY
    else:
        # The place of supply the invoice was taxed at: where the goods go when
        # they go to the buyer's own site, the buyer's own state when they go to
        # someone else on its direction (accounting.gst.place_of_supply). Read
        # from the registration instead, a registered buyer's goods taken to its
        # own site in another state bore integrated tax while this said its own.
        billed_state = document.place
        if not billed_state:
            raise ValidationError(f"{invoice.number} records no place of supply.")
    two_places = overseas or (billed_at is not None and going_to is not None and going_to.pk != billed_at.pk)
    from apps.accounting.models import round_money

    tcs = round_money(invoice.tcs_amount * (invoice.exchange_rate or Decimal("1")))

    def whole(name):
        return sum((getattr(line, name) for line in goods), ZERO)

    whole_other = document.value - sum((whole(name) for name in HEADS), ZERO)
    share = None
    if carried_on is not None:
        shares = consignment_shares(invoice, goods, whole_other, tcs, consignments(invoice))
        share = next(row for row in shares if row["delivery"].pk == carried_on.pk)
    items = []
    for index, line in enumerate(goods):
        if share is not None and index not in share["lines"]:
            continue
        number = len(items) + 1
        quantity, amounts = share["lines"][index] if share is not None else (line.source.quantity, None)
        items.append({
            "itemNo": number,
            "productName": p.text(line.source.label(), f"Line {number}'s description", 1, 100),
            "productDesc": p.text(line.source.label(), f"Line {number}'s description", 1, 100),
            "hsnCode": int(line.hsn),
            "quantity": p.quantity(quantity, f"Line {number}'s quantity"),
            "qtyUnit": line.uqc,
            "taxableAmount": float(amounts["taxable"] if amounts else line.taxable),
            "cgstRate": float(line.rates.get("cgst", ZERO)),
            "sgstRate": float(line.rates.get("sgst", ZERO)),
            "igstRate": float(line.rates.get("igst", ZERO)),
            "cessRate": float(line.rates.get("cess", ZERO)),
            "cessNonadvol": 0,
        })

    def head(name):
        """This consignment's share of the head, or the invoice's whole."""
        if share is None:
            return whole(name)
        return sum((amounts[name] for _, amounts in share["lines"].values()), ZERO)

    taxable = head("taxable")
    taxes = head("cgst") + head("sgst") + head("igst") + head("cess")
    other = share["other"] if share is not None else whole_other
    value = taxable + taxes + other
    seller = p.seller()
    lots = share is not None and share["lots"]
    # In lots, each consignment but the last moves on its delivery challan and the last carries the
    # invoice (CGST Rule 55(5)(b) and (d)).
    on_challan = lots and not share["final"]
    payload = {
        "supplyType": "O", "subSupplyType": LOTS if lots else "3" if overseas else "1", "subSupplyDesc": "",
        "docType": "CHL" if on_challan else "INV", "docNo": carried_on.number if on_challan else invoice.number,
        "docDate": p.portal_date(carried_on.delivery_date if on_challan else invoice.invoice_date),
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
        "otherValue": float(other),
        "totInvValue": float(value),
        "itemList": items,
    }
    # The consignment's value is the supply's (Rule 138) with its GST: TCS collected with it is
    # income tax, not part of the value of supply (CBIC Circular 76/50/2018-GST as corrected on
    # 7 March 2019), so it counts in totInvValue as an other value but never towards the limit.
    # A lot is judged by its own value, as the paper it moves on states it (Rule 138(1), Explanation 2).
    required, because = _limit_test(value - (share["tcs"] if share is not None else tcs), registration)
    if lots:
        because = (f"Consignment {carried_on.number} of {invoice.number}, sent in lots. " + because)
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
    going_to = worker.shipping_address()
    billed_at = worker.billing_address()
    # Where the lorry goes is the address's own state, by the rule a sale reads (O533, O546): the
    # worker's own billing address with no state reads the state the challan recorded at issue; any
    # other site with none is nobody's to guess. The registration's state is whom it is sent to
    # (toStateCode), never where: a laminator registered in Karnataka with works at Kolhapur was
    # refused, and with no state on the site it built 29 over a Maharashtra PIN.
    place = p.place(going_to, f"{worker}",
                    standing=state_delivered_in(going_to, billed_at, challan.job_worker_state))
    # Bill-to and ship-to differ where the addresses do, or where the one address is in another
    # state than the registration it is sent to.
    two_places = ((billed_at is not None and going_to is not None and going_to.pk != billed_at.pk)
                  or place["state"] != challan.job_worker_state)
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
        "transactionType": 2 if two_places else 1,
        "totalValue": float(total),
        "cgstValue": 0, "sgstValue": 0, "igstValue": 0, "cessValue": 0,
        "cessNonAdvolValue": 0, "otherValue": 0,
        "totInvValue": float(total),
        "itemList": items,
    }
    # CGST Rule 138(1), third proviso: goods sent by a principal in one state to a job worker in
    # another need a bill whatever they are worth. Read as where the goods go; a worker registered in
    # another state taking them at works in this one is said, and is the plant's practitioner's to
    # confirm (not confirmed).
    if place["state"] != registration.state:
        return payload, True, (f"Sent to a job worker in {STATES[place['state']]}: "
                               "across a state line, whatever it is worth.")
    required, because = _limit_test(total, registration)
    if challan.job_worker_state != registration.state:
        because += (f" The worker is registered in {STATES[challan.job_worker_state]} and takes the goods in "
                    f"{STATES[place['state']]}: read here as no state line crossed.")
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
    billed_at = order.billing_address or customer.billing_address()
    # Its own billing address with no state reads the state on its record, as a sale's does (O533);
    # a site of its own in another state is where the goods go, and the registration's state is only
    # whom they go back to (O546).
    place = p.place(going_to, f"{customer}'s delivery address",
                    standing=state_delivered_in(going_to, billed_at, profile.place_of_supply() if profile else None))
    two_places = ((billed_at is not None and going_to is not None and going_to.pk != billed_at.pk)
                  or bool(gstin and place["state"] != gstin[:2]))
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
        "transactionType": 2 if two_places else 1,
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
            transport_doc_number="", transport_doc_date=None, declared_value=None, carried_on=None):
    """
    Build the payload for an invoice, a challan or a job-work delivery, and keep it. `carried_on`,
    for an invoice: which of its deliveries (trucks) this bill is for; left out where it went on one.
    """
    from apps.sales.models import Delivery, Invoice

    is_invoice = isinstance(document, Invoice)
    is_delivery = isinstance(document, Delivery)
    if carried_on is not None and not is_invoice:
        raise ValidationError("Only an invoice's e-way bill names the delivery it went on.")
    carried = document if is_delivery else None
    if is_invoice:
        trucks = consignments(document)
        if carried_on is not None and carried_on.pk not in [truck.pk for truck in trucks]:
            raise ValidationError(
                f"{carried_on.number or 'That delivery'} is not a shipment travelling on {document.number}. "
                "Record the invoice it went on, on the delivery's page.")
        if carried_on is None and len(trucks) > 1:
            raise ValidationError(
                f"{document.number} went on {len(trucks)} deliveries ({', '.join(truck.number for truck in trucks)}). "
                "The portal wants an e-way bill for each consignment: make one for each delivery.")
        carried_on = carried_on or (trucks[0] if trucks else None)
        carried = carried_on
    if is_invoice and carried is None and document.sales_order_id:
        # A sale moves on its invoice's e-way bill; the truck is on the
        # delivery. Taken from it only when there is just the one.
        shipped = list(Delivery.objects.filter(sales_order_id=document.sales_order_id, posted=True,
                                               reverses__isnull=True, transporter__isnull=False)[:2])
        carried = shipped[0] if len(shipped) == 1 else None
    if carried is not None:
        # What the delivery says carried it, unless told otherwise.
        carrier = carried.transporter
        profile = getattr(carrier, "tax_profile", None) if carrier else None
        transporter_id = transporter_id or (profile.gstin if profile else "")
        transporter_name = transporter_name or (carrier.name if carrier else "")
        vehicle_number = vehicle_number or carried.vehicle_number
        transport_doc_number = transport_doc_number or carried.lr_number
        transport_doc_date = transport_doc_date or carried.lr_date
    if not is_invoice and not is_delivery:
        challan_vehicle = normalise_vehicle(document.vehicle)
        given = normalise_vehicle(vehicle_number)
        if challan_vehicle and given and given != challan_vehicle:
            raise ValidationError(
                f"{document.number} says it went on {challan_vehicle}, not {given}."
            )
        vehicle_number = given or challan_vehicle
    goods_dated = _document_date(document)
    if carried_on is not None:
        # The lorry receipt is the truck's: a delivery billed two days later was loaded before its invoice.
        goods_dated = min(goods_dated, carried_on.delivery_date)
    transport = _transport(mode, distance_km, transporter_id, transporter_name,
                           vehicle_number, vehicle_type, transport_doc_number,
                           transport_doc_date, goods_dated)
    if is_invoice:
        payload, required, because = _invoice_payload(document, carried_on)
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
    key = ({"invoice": document, "carried_on": carried_on} if is_invoice else {"delivery": document} if is_delivery
           else {"challan": document})
    numbering_first()  # a raw row lock, after the company's numbering row (O257)
    if is_invoice:
        # One bill for the whole invoice, or one for each truck: never both standing.
        other = EwayBill.objects.select_for_update(no_key=True).filter(
            invoice=document, cancelled_at__isnull=True, carried_on__isnull=carried_on is not None).first()
        if other is not None:
            raise ValidationError(
                f"{document.number} has e-way bill {other.number or '(not yet generated)'} standing, made "
                + ("for the whole invoice before its deliveries were recorded" if carried_on is not None
                   else f"for {other.carried_on.number}")
                + ". Cancel or delete it first.")
    existing = EwayBill.objects.select_for_update(no_key=True).filter(
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


def refuse_retying(delivery):
    """
    A delivery is not moved to another invoice, or off its invoice, while an e-way bill made for it
    as that invoice's consignment stands: the bill says which invoice the truck carried. Asked by
    Delivery.travel_on for tying and untying alike.
    """
    standing = EwayBill.objects.filter(carried_on=delivery, cancelled_at__isnull=True).first()
    if standing is not None:
        raise ValidationError(
            f"{delivery.number or 'This delivery'} has e-way bill {standing.number or '(not yet generated)'} "
            f"standing, as a consignment of {standing.invoice.number}. Cancel or delete it first.")


def refuse_changing_where_it_went(invoice):
    """
    Where an invoice's goods went, recorded after it posted, stands once something reads it: a
    generated e-way bill, or a registered IRN. Asked by Invoice.record_where_it_went and its
    withdrawal alike.
    """
    from .einvoice import EInvoice

    bill = EwayBill.objects.filter(invoice=invoice, cancelled_at__isnull=True).exclude(number="").first()
    if bill is not None:
        raise ValidationError(f"{invoice.number} moves on e-way bill {bill.number}, which says where it went. "
                              "Cancel the bill first.")
    registered = EInvoice.objects.filter(invoice=invoice, cancelled_at__isnull=True).exclude(irn="").first()
    if registered is not None:
        raise ValidationError(f"{invoice.number} is registered under IRN {registered.irn[:12]}..., which says where "
                              "it went; that stands.")


def lacking_where_they_went(queryset=None):
    """
    [(invoice, why)]: posted invoices of goods for which no e-way bill or e-invoice ship-to can be
    built, because nothing says the state they were delivered in or, for an export, the place in
    India they left for (Invoice.lacks_where_it_went). The go-live check and the office list read it.
    """
    from django.db.models.functions import Lower, Trim

    from apps.sales.models import Invoice

    # Asked every morning: an invoice from before the state was kept whose ship-to says a state, as
    # accounting.gst.state_code reads one, lacks nothing and is passed over in the query, so only the
    # few that might lack it are opened.
    says_a_state = [name.lower() for name in STATES.values()] + list(STATES)
    rows = (queryset if queryset is not None else Invoice.objects.all()).filter(
        posted=True, credits__isnull=True, debits__isnull=True, is_opening_balance=False, is_down_payment=False,
        taxes_recorded=True, delivery_state="").annotate(
            ship_to_state=Lower(Trim("shipping_address__state"))).exclude(
            Q(ship_to_state__in=says_a_state) & ~Q(party_registration="overseas")).select_related(
            "customer", "shipping_address", "billing_address", "sales_order").order_by("invoice_date", "pk")
    found = []
    for invoice in rows:
        why = invoice.lacks_where_it_went()
        if why:
            found.append((invoice, why))
    return found
