"""
A customer's own inspector passes the goods before they leave.

Cement plants, fertiliser and food buyers, and government tenders send
an agency (SGS, RITES, Bureau Veritas, their own QA) to sample a batch
at the plant and sign an inspection certificate for so many sacks of
it. Nothing of theirs may be loaded without one. This is that
certificate as the plant receives it, and the check that holds a
delivery to it.

**Who needs it.** The customer's profile says whether they inspect, and
each order may say otherwise: a customer whose tender orders are
inspected and whose trial orders are not. An order takes the customer's
answer when it is written, and keeps it once anything has shipped on
it, because what has shipped was judged under it.

**What it releases.** So many of a batch, for one customer, and
optionally for one of their orders only: agencies sign against a
purchase order more often than not. A release for one order serves
that order first; one that names no order serves any of the customer's
inspected orders. A delivery on an inspected order passes when every
batch on it fits:

    sum over orders of  max(0, net shipped on it - released for it)
        <= released for no order in particular

Derived from the documents each time, never kept as a balance.

**What a return does.** Bags that went to the customer and came back
have been out of the plant's hands. By default they need inspecting
again: the returned quantity stays counted against the release. A
customer whose agency accepts returns back under their certificate is
set so on their profile; each return records which rule it came back
under when it is posted, so changing the setting later does not
rewrite what earlier returns meant.

**Withdrawn.** A release entered in error is voided. Not once goods it
vouched for have gone: they left on its word, and the word stands.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

ZERO = Decimal("0")


def _q(value):
    return format(Decimal(value).normalize(), "f")


class ThirdPartyRelease(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="+")
    sales_order = models.ForeignKey(
        "sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
        related_name="third_party_releases",
        help_text="The order the agency signed against, when it did. Left blank, the "
                  "release serves any of the customer's inspected orders.",
    )
    agency = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="+",
                               help_text="Who inspected.")
    inspector = models.CharField(max_length=128, blank=True)
    their_reference = models.CharField(
        max_length=64, help_text="The agency's inspection certificate number.")
    inspected_on = models.DateField()
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-inspected_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="third_party_release_number_unique"),
            models.UniqueConstraint(fields=["agency", "their_reference"],
                                    condition=Q(posted=True, voided_at__isnull=True),
                                    name="one_release_per_agency_certificate"),
        ]

    def __str__(self):
        return self.number or f"Draft third-party release {self.pk}"

    def save(self, *args, **kwargs):
        if self.pk and ThirdPartyRelease.objects.filter(pk=self.pk, posted=True).exists() \
                and not getattr(self, "_writing", False):
            raise ValidationError(f"{self} is posted. Void it and enter another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError(f"{self} is posted; void it.")
        return super().delete(*args, **kwargs)

    def _write(self, fields):
        self._writing = True
        try:
            self.save(update_fields=fields + ["updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._writing = False

    def is_standing(self):
        return self.posted and self.voided_at is None

    @transaction.atomic
    def post(self):
        from .models import _require_customer_role

        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        _require_customer_role(self.customer)
        if self.agency_id == self.customer_id:
            raise ValidationError("The customer does not inspect as their own agency here; "
                                  "name the agency that signed.")
        if self.sales_order_id and self.sales_order.customer_id != self.customer_id:
            raise ValidationError(f"{self.sales_order} is {self.sales_order.customer}'s, "
                                  f"not {self.customer}'s.")
        reference = (self.their_reference or "").strip()
        if not reference:
            raise ValidationError("Give the agency's certificate number.")
        self.inspected_on = to_date(self.inspected_on)
        if self.inspected_on > timezone.localdate():
            raise ValidationError("An inspection cannot be dated after today.")
        clash = ThirdPartyRelease.objects.filter(
            agency=self.agency, their_reference=reference, posted=True,
            voided_at__isnull=True).exclude(pk=self.pk).first()
        if clash is not None:
            raise ValidationError(f"{self.agency}'s certificate {reference} is already "
                                  f"entered as {clash}.")
        lines = list(self.lines.select_related("lot__item"))
        if not lines:
            raise ValidationError("A release of nothing releases nothing.")
        for line in lines:
            line._check()
        self.their_reference = reference
        self.number = DocumentSequence.next_for(
            "sales.third_party_release", self.inspected_on,
            name="Third-Party Releases", prefix="TPR-")
        self.posted, self.posted_at = True, timezone.now()
        self._write(["number", "their_reference", "inspected_on", "posted", "posted_at"])

    @transaction.atomic
    def void(self, reason):
        from apps.inventory.models import Lot

        if not self.is_standing():
            raise ValidationError(f"{self} is not a standing release.")
        if not (reason or "").strip():
            raise ValidationError("Say why the release is withdrawn.")
        lots = [line.lot for line in self.lines.select_related("lot")]
        list(Lot.objects.select_for_update().order_by().filter(pk__in=[lot.pk for lot in lots]))
        for lot in lots:
            fits, _ = _fits(self.customer, lot, exclude_release=self)
            if not fits:
                raise ValidationError(
                    f"{lot} has already shipped to {self.customer} on this release's word; "
                    "it cannot be withdrawn."
                )
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._write(["voided_at", "voided_reason"])


class ThirdPartyReleaseLine(AuditModel):
    release = models.ForeignKey(ThirdPartyRelease, on_delete=models.CASCADE,
                                related_name="lines")
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT, related_name="+")
    quantity_offered = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="What was put before the inspector, in the item's stocking unit.")
    quantity_released = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="What they passed. Nothing passed is a rejection, and is recorded "
                  "as one rather than left out.")
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity_offered__gt=0) & Q(quantity_released__gte=0)
                & Q(quantity_released__lte=models.F("quantity_offered")),
                name="third_party_release_quantities_in_order"),
            models.UniqueConstraint(fields=["release", "lot"],
                                    name="one_line_per_batch_per_release"),
        ]

    def __str__(self):
        return f"{self.lot}: {_q(self.quantity_released)} of {_q(self.quantity_offered)}"

    def save(self, *args, **kwargs):
        if self.release.posted:
            raise ValidationError(f"{self.release} is posted; its lines do not change.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.release.posted:
            raise ValidationError(f"{self.release} is posted; its lines do not change.")
        return super().delete(*args, **kwargs)

    def _check(self):
        offered, released = self.quantity_offered, self.quantity_released
        if offered is None or offered <= 0:
            raise ValidationError(f"{self.lot}: what was offered is more than nothing.")
        if released is None or released < 0 or released > offered:
            raise ValidationError(f"{self.lot}: {_q(released)} passed of {_q(offered)} "
                                  "offered is not a count the inspector could have signed.")
        on_hand = self.lot.on_hand_at()
        if offered > on_hand:
            raise ValidationError(f"{self.lot}: {_q(offered)} offered and {_q(on_hand)} on "
                                  "hand. An inspector samples what is there.")


def requires_inspection(order):
    """Whether this order's goods wait for the customer's inspector."""
    if order.third_party_inspection is not None:
        return order.third_party_inspection
    from .models import CustomerProfile

    profile = CustomerProfile.objects.filter(party=order.customer_id).first()
    return bool(profile and profile.third_party_inspection)


def _released(customer, lot, exclude_release=None):
    """(released for each order, released for any), from standing releases."""
    lines = ThirdPartyReleaseLine.objects.filter(
        lot=lot, release__customer=customer, release__posted=True,
        release__voided_at__isnull=True,
    ).select_related("release")
    if exclude_release is not None:
        lines = lines.exclude(release=exclude_release)
    specific, general = defaultdict(lambda: ZERO), ZERO
    for line in lines:
        if line.release.sales_order_id:
            specific[line.release.sales_order_id] += line.quantity_released
        else:
            general += line.quantity_released
    return specific, general


def _net_shipped(customer, lot, adding=None):
    """Per inspected order: shipped, less what came back under the release.
    `adding` is a delivery being posted, whose batches count already."""
    from .models import DeliveryAllocation

    rows = DeliveryAllocation.objects.filter(
        lot=lot, line__delivery__sales_order__customer=customer,
    ).select_related("line__delivery__sales_order")
    shipped = Q(line__delivery__posted=True)
    if adding is not None:
        shipped |= Q(line__delivery=adding)
    net = defaultdict(lambda: ZERO)
    inspected = {}
    for row in rows.filter(shipped):
        delivery = row.line.delivery
        order = delivery.sales_order
        if order.pk not in inspected:
            inspected[order.pk] = requires_inspection(order)
        if not inspected[order.pk]:
            continue
        if delivery.reverses_id is None:
            net[order.pk] += row.quantity
        elif delivery.returned_under_release:
            net[order.pk] -= row.quantity
    return net


def _fits(customer, lot, adding=None, exclude_release=None):
    specific, general = _released(customer, lot, exclude_release)
    net = _net_shipped(customer, lot, adding)
    excess = sum((max(ZERO, quantity - specific[order]) for order, quantity in net.items()),
                 ZERO)
    return excess <= general, general - excess


def available(customer, lot, order):
    """How much more of this batch may go on this order under standing releases."""
    specific, general = _released(customer, lot)
    net = _net_shipped(customer, lot)
    excess = sum((max(ZERO, quantity - specific[key]) for key, quantity in net.items()),
                 ZERO)
    own = max(ZERO, specific[order.pk] - net.get(order.pk, ZERO))
    return max(ZERO, own + general - excess)


def refuse_uncovered(delivery, lines):
    """Called as a delivery posts, once its batches are chosen."""
    from apps.inventory.models import Lot

    order = delivery.sales_order
    if not requires_inspection(order):
        return
    customer = order.customer
    lots, problems = {}, []
    for line in lines:
        item = line.order_line.item
        if item is None or not item.track_inventory:
            continue
        taken = [row.lot for row in line.allocations.select_related("lot")]
        if delivery.is_drop_ship or not taken or any(lot is None for lot in taken):
            problems.append(
                f"{item} goes out untraced to a batch, and {customer}'s inspector "
                "releases batches. Ship it by batch, or set this order as not inspected."
            )
            continue
        for lot in taken:
            lots[lot.pk] = lot
    list(Lot.objects.select_for_update().order_by().filter(pk__in=list(lots)))
    for lot in lots.values():
        fits, spare = _fits(customer, lot, adding=delivery)
        if not fits:
            problems.append(
                f"{lot} is {_q(-spare)} short of what {customer}'s inspector has released "
                "for it."
            )
    if problems:
        raise ValidationError(" ".join(dict.fromkeys(problems)))


def releases_for(customer, lot, order):
    """The standing releases a certificate can name for this batch on this order."""
    return list(ThirdPartyRelease.objects.filter(
        Q(sales_order__isnull=True) | Q(sales_order=order),
        customer=customer, posted=True, voided_at__isnull=True, lines__lot=lot,
    ).select_related("agency").distinct().order_by("inspected_on", "id"))


def coverage(customer, lot):
    """Released, shipped and returned, for a batch and a customer."""
    specific, general = _released(customer, lot)
    net = _net_shipped(customer, lot)
    fits, spare = _fits(customer, lot)
    return {
        "lot": lot.code, "customer": customer.name,
        "released_for_any_order": general,
        "released_for_orders": dict(specific),
        "net_shipped_by_order": dict(net),
        "spare_for_any_order": spare, "fits": fits,
    }
