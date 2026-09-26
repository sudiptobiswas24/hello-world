"""
Work a vendor did on a run, and what it put into work in progress.

A laminated sack whose plant has no coating line goes out as woven
fabric and comes back coated, and nothing in between is an item: the
bag's routing is laminate, print, cut, stitch on one run. So the
vendor's charge cannot arrive as stock the way whole-item job work
does. It arrives as cost on the run.

The accounting:

    vendor's work comes back   Dr Work in progress   Cr GRNI
    sent back to the vendor    Dr GRNI               Cr Work in progress
    the vendor's bill          Dr GRNI               Cr Payables

The first two are here and the third is purchasing's, unchanged: the
bill clears the accrual at the agreed price and any difference goes to
purchase price variance, exactly as it does for polymer.

**Purchasing holds the pointer.** A purchase order line names the
operation it is for; the operation knows nothing of purchasing. The
line is the document that would be meaningless without the run, and
`manufacturing` may not import `purchasing`. The receipt therefore
calls in here, passing the account to credit, and this module owns the
posting — one place that writes work in progress for vendor work, and
one place that reverses it.

Without this a vendor's lamination was a service line on a purchase
order: it posted nothing when it came back and went straight to
expense when billed. The run's work in progress never saw it, its
planned cost never included it, and its variance at close was short by
the whole charge.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date


class OutsideMovement(AuditModel):
    """
    Work coming back from a vendor against an outside step — or going
    back to them.

    A return is its own row, not a void of the receipt, for the reason
    every correction here is a reversal: the vendor may send back six
    hundred of a thousand and take four hundred back for rework, and a
    void can only undo the whole.
    """

    number = models.CharField(max_length=32, blank=True)
    operation = models.ForeignKey(
        "manufacturing.WorkOrderOperation", on_delete=models.PROTECT,
        related_name="outside_movements",
    )
    movement_date = models.DateField()
    is_return = models.BooleanField(
        default=False,
        help_text="Sent back to the vendor. Takes the value back out of the "
                  "run at what it went in at.",
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="In the run's own unit. Always positive; `is_return` says "
                  "which way.",
    )
    value = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="What this movement is worth — the agreed price times the "
                  "quantity, for a receipt.",
    )
    credit_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="+",
        help_text="The accrual the other side of the entry lands in. Passed "
                  "in by whoever raised the movement, because it is theirs: "
                  "goods received not invoiced, for a purchase order.",
    )
    reference = models.CharField(max_length=64, blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="Signed: what this put into work in progress, negative for "
                  "a return. Frozen as the journal has it.",
    )
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+", editable=False,
    )
    voided_entry = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+", editable=False,
    )
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["movement_date", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="outside_movement_moves_something",
            ),
            models.CheckConstraint(
                check=Q(value__gte=0), name="outside_movement_value_not_negative",
            ),
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="outside_movement_number_unique",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft outside movement {self.pk}"

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = OutsideMovement.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    f"Cannot modify {self} once it is posted. Void it or "
                    "return the work to the vendor."
                )
        if self.operation_id is not None and not self.operation.is_outside:
            # Refused where it is first knowable. Vendor cost on one of
            # our own machine's steps would sit beside the time booked
            # there and double what the step cost.
            raise ValidationError(
                f"{self.operation} is done on our own machines. A vendor's "
                "charge belongs on the step they did."
            )
        super().save(*args, **kwargs)

    @transaction.atomic
    def post(self, memo=""):
        from .orders import (
            ManufacturingSettings,
            _check_order_is_open_for,
            _post_entry,
        )

        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        operation = self.operation
        order = operation.work_order
        _check_order_is_open_for(
            order, "take vendor work back" if self.is_return
            else "book vendor work against it",
        )
        self.movement_date = to_date(self.movement_date)
        if self.is_return:
            back = operation.quantity_back()
            if self.quantity > back:
                raise ValidationError(
                    f"{operation} has had {back} back from the vendor, so "
                    f"{self.quantity} cannot go back to them. Nothing can be "
                    "returned that never came."
                )
        else:
            # Against what the run was planned to start, with the same
            # allowance its output has: the vendor processes the spoiled
            # units too, and cannot have processed more than the run
            # ever held.
            ceiling = order.maximum_output()
            back = order.item.to_stock_quantity(
                operation.quantity_back(), order.uom
            )
            now = order.item.to_stock_quantity(self.quantity, order.uom)
            if back + now > ceiling:
                raise ValidationError(
                    f"{order} can make at most {ceiling}, and the vendor would "
                    f"have processed {back + now} of it."
                )
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.outside", self.movement_date,
                name="Outside Processing", prefix="OUT-",
            )
        label = memo or (
            f"{'Returned to vendor' if self.is_return else 'Back from vendor'}"
            f": {operation.name} on {order.number}, {self.number}"
        )
        wip = ManufacturingSettings.account(
            "wip", "a vendor's work is being booked to a run"
        )
        sign = Decimal("-1") if self.is_return else Decimal("1")
        self.journal_entry, released = _post_entry(
            self.movement_date, self.reference or self.number, label,
            [(wip, sign * self.value)], balance_to=self.credit_account,
        )
        self.posted = True
        self.posted_at = timezone.now()
        # Work in progress took the opposite of what the accrual took.
        self.posted_value = -released
        super().save(update_fields=[
            "number", "movement_date", "posted", "posted_at",
            "posted_value", "journal_entry", "updated_at",
        ])
        return self.journal_entry

    @transaction.atomic
    def void(self, on_date=None, memo=""):
        """
        Undo a movement entered in error. Written in the same sitting as
        `post()`, as every reverse here is.

        Not how work goes back to a vendor — that is a return, a
        movement of its own. This is for the receipt that should never
        have been keyed.
        """
        from .orders import _check_order_is_open_for

        if not self.posted:
            raise ValidationError(f"{self} was never posted.")
        if self.voided_at is not None:
            raise ValidationError(f"{self} is already void.")
        _check_order_is_open_for(self.operation.work_order, "void this movement")
        if not self.is_return:
            # Voiding a receipt that was later partly returned would
            # take back more than is left. The return has to go first.
            back = self.operation.quantity_back()
            if back - self.quantity < 0:
                raise ValidationError(
                    f"Some of {self} has already gone back to the vendor. "
                    "Void that return first."
                )
        on_date = to_date(on_date) or timezone.now().date()
        # A movement at no value writes no entry — `_post_entry` drops
        # zero rows — so there may be nothing to reverse, and the void
        # is still a real void of the quantity.
        if self.journal_entry_id:
            self.voided_entry = self.journal_entry.create_reversal(
                entry_date=on_date, memo=memo or f"Void of {self.number}"
            )
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_entry", "voided_at", "updated_at"])
        return self.voided_entry
