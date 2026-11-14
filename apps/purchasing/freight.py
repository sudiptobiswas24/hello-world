"""
A transporter's freight bill matched to the deliveries it charges for, so
a delivery is charged for once and those not yet charged can be listed.
"""

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.models import AuditModel, lock_rows


class FreightDelivery(AuditModel):
    bill = models.ForeignKey("purchasing.Bill", on_delete=models.CASCADE, related_name="carried")
    # One bill to a delivery: charged twice, freight is paid twice.
    delivery = models.OneToOneField("sales.Delivery", on_delete=models.PROTECT, related_name="freight_charge")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.delivery} on {self.bill}"


def carry(bill, delivery):
    with transaction.atomic():
        lock_rows(delivery)
        if not delivery.posted or delivery.reverses_id:
            raise ValidationError("Only a posted delivery out is carried and charged for.")
        if bill.is_debit_note():
            raise ValidationError("A debit note corrects the freight bill; name the deliveries on the bill.")
        if delivery.transporter_id and delivery.transporter_id != bill.vendor_id:
            raise ValidationError(f"{delivery.number} went with {delivery.transporter}, not {bill.vendor}.")
        taken = FreightDelivery.objects.filter(delivery=delivery).select_related("bill").first()
        if taken:
            raise ValidationError(f"{delivery.number} is charged for on {taken.bill.number or 'another bill'} already.")
        return FreightDelivery.objects.create(bill=bill, delivery=delivery)


def uncarry(bill, delivery):
    """Take a delivery off a freight bill it was charged for on; refused where it was not."""
    with transaction.atomic():
        lock_rows(delivery)
        gone, _ = FreightDelivery.objects.filter(bill=bill, delivery=delivery).delete()
        if not gone:
            raise ValidationError({"delivery": f"{delivery.number} is not on this bill."})


def unbilled_freight(transporter=None):
    """Posted deliveries out, with a transporter, that no freight bill names yet."""
    from apps.sales.models import Delivery

    rows = Delivery.objects.filter(posted=True, reverses__isnull=True, transporter__isnull=False,
                                   freight_charge__isnull=True).select_related("transporter", "sales_order__customer")
    if transporter is not None:
        rows = rows.filter(transporter=transporter)
    return [{"delivery": row.pk, "number": row.number, "date": row.delivery_date, "transporter": row.transporter.name,
             "transporter_id": row.transporter_id, "customer": row.sales_order.customer.name,
             "lr_number": row.lr_number, "vehicle_number": row.vehicle_number}
            for row in rows.order_by("delivery_date", "pk")]
