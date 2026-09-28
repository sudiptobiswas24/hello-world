"""
A test certificate for a shipment: what was measured on the batches the
customer received, and on what they were made from.

**Only what was measured.** Every figure is a reading from a posted,
standing inspection, judged against the limits frozen on the reading
when it was taken. A batch nobody inspected says so; a batch taken by
concession says that too. A certificate that fills a silence with the
specification is a certificate that lies.

The plan's target is not reported: a reading freezes the limits it was
judged against, not the target, and a target read from today's plan
beside limits frozen last month would be two different plans on one line.

**Frozen when issued.** The customer holds what they were sent. An
inspection voided next week does not reach back into a certificate
issued this week: the certificate is voided and issued again, and both
are kept.

**Not for goods that should not have gone.** A batch of an item whose
inspection is mandatory, with no accepted inspection standing, is not
certified: it should not have shipped, and a certificate would make the
mistake official. The same holds for what it was made from: every batch
in its ancestry answers the same question, and one that is listed is
one that was measured and accepted.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence

MAX_DEPTH = 4


class TestCertificate(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    delivery = models.ForeignKey("sales.Delivery", on_delete=models.PROTECT,
                                 related_name="test_certificates")
    issued_on = models.DateField()
    content = models.JSONField(editable=False,
                               help_text="What the certificate said, as issued.")
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-issued_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="test_certificate_number_unique"),
            models.UniqueConstraint(fields=["delivery"], condition=Q(voided_at__isnull=True),
                                    name="one_standing_certificate_per_delivery"),
        ]

    def __str__(self):
        return self.number or f"Draft certificate for {self.delivery}"

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_voiding", False):
            raise ValidationError("A certificate is what the customer was sent. Void it "
                                  "and issue another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A certificate is what the customer was sent; void it.")

    @transaction.atomic
    def void(self, reason):
        if self.voided_at is not None:
            raise ValidationError(f"{self} is already void.")
        if not reason.strip():
            raise ValidationError("Say why the certificate is withdrawn.")
        self.voided_at = timezone.now()
        self.voided_reason = reason.strip()
        self._voiding = True
        try:
            self.save(update_fields=["voided_at", "voided_reason", "updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._voiding = False


def _number(value):
    if value is None:
        return None
    # Fixed-point: normalize() alone writes 600 as 6E+2, which is no figure to print.
    return format(Decimal(value).normalize(), "f")


def _measured(lot):
    """One batch: its standing inspection's characteristics, or why there are none."""
    from apps.quality.models import Disposition
    from apps.quality.release import latest_inspection, plan_for

    inspection = latest_inspection(lot)
    row = {"lot": lot.code, "item": lot.item.sku, "item_name": lot.item.name}
    if inspection is None:
        plan = plan_for(lot.item)
        if plan is not None and plan.is_mandatory:
            raise ValidationError(
                f"Batch {lot.code} must be inspected and has no standing inspection; it "
                "should not have been used or shipped, and nothing made from it is "
                "certified."
            )
        row.update(inspection=None, status="not inspected", characteristics=[])
        return row
    if inspection.disposition not in (Disposition.ACCEPT, Disposition.CONCESSION):
        raise ValidationError(
            f"Batch {lot.code}'s standing inspection {inspection} was not accepted; "
            "nothing made from it is certified."
        )
    characteristics = []
    for line in inspection.readings_by_line():
        characteristic = line["characteristic"]
        frozen = inspection.readings.filter(plan_line=line["plan_line"]).first()
        characteristics.append({
            "code": characteristic.code, "name": characteristic.name,
            "unit": characteristic.uom.code if characteristic.uom_id else "",
            "lower": _number(frozen.lower_limit), "upper": _number(frozen.upper_limit),
            "mean": _number(line["mean"]),
            "readings": len([v for v in line["values"] if v is not None]),
            "passed": line["passed"],
        })
    row.update(
        inspection=inspection.number, inspected_on=str(inspection.inspected_on),
        status=("accepted by concession" if inspection.disposition == Disposition.CONCESSION
                else "passed" if inspection.result == "pass" else "accepted"),
        characteristics=characteristics,
    )
    return row


def _ancestry(lot):
    """The measured batches a shipped batch was made from, nearest first."""
    from .demand import genealogy

    rows, seen = [], {lot.pk}
    for step in genealogy(lot, depth=MAX_DEPTH):
        source = step["from_lot"]
        if source.pk in seen:
            continue
        seen.add(source.pk)
        measured = _measured(source)
        if measured["inspection"] is not None:
            measured["level"] = step["level"]
            rows.append(measured)
    return rows


def _specification(item, on_date):
    from apps.core.windows import covers

    from .woven import BagSpecification

    for spec in BagSpecification.objects.filter(bag_item=item, is_active=True):
        if covers(spec.valid_from, spec.valid_to, on_date):
            return {
                "code": spec.code, "construction": spec.construction(),
                "width_cm": _number(spec.bag_width_cm), "length_cm": _number(spec.bag_length_cm),
                "gusset_cm": _number(spec.gusset_cm),
                "weight_g": _number(spec.target_grams if spec.target_grams is not None
                                    else spec.bag_grams().quantize(Decimal("0.01"))),
                "weight_tolerance_percent": _number(spec.weight_tolerance_percent),
                "contracted": spec.target_grams is not None,
            }
    return None


@transaction.atomic
def issue(delivery, on_date=None):
    """Certify a posted shipment, frozen."""
    if not delivery.posted:
        raise ValidationError(f"{delivery} has not shipped; there is nothing to certify.")
    if delivery.is_return():
        raise ValidationError(f"{delivery} is goods coming back, not a shipment.")
    if TestCertificate.objects.filter(delivery=delivery, voided_at__isnull=True).exists():
        raise ValidationError(f"{delivery} is already certified. Void that certificate to "
                              "issue another.")
    on_date = on_date or timezone.localdate()
    lines = []
    for line in delivery.lines.select_related("order_line__item"):
        item = line.order_line.item
        batches = []
        for allocation in line.allocations.select_related("lot__item"):
            if allocation.lot_id is None:
                continue
            batch = _measured(allocation.lot)
            batch["quantity"] = _number(allocation.quantity)
            batch["made_from"] = _ancestry(allocation.lot)
            batches.append(batch)
        lines.append({
            "item": item.sku, "item_name": item.name,
            "quantity": _number(line.quantity_shipped),
            "uom": line.order_line.uom.code if line.order_line.uom_id else item.uom.code,
            "specification": _specification(item, delivery.delivery_date),
            "batches": batches,
        })
    order = delivery.sales_order
    content = {
        "customer": order.customer.name, "customer_code": order.customer.code,
        "delivery": delivery.number, "delivery_date": str(delivery.delivery_date),
        "order": order.number, "customer_reference": order.reference,
        "lines": lines,
    }
    certificate = TestCertificate(delivery=delivery, issued_on=on_date, content=content)
    certificate.number = DocumentSequence.next_for(
        "manufacturing.test_certificate", on_date, name="Test Certificates", prefix="TC-",
    )
    certificate.save()
    return certificate
