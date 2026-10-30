"""
The gate: which vehicle came in, when it left, what it carried and what it weighed (L17, L18).

Polymer arrives and sacks leave by truck. Until this nothing recorded the vehicle, so a short
delivery was found only on the invoice, and a motor sent out for rewinding was tracked nowhere.

Three documents, all kept at the gate:

- **a vehicle visit** (GateVisit): numbered as it comes in, its in-time and out-time from the
  server's clock (shown in the plant's zone), never typed. It leaves only through check_out(), the
  out-entry; one still in after the company's set hours shows on the health check. A mistaken visit
  is cancelled, never deleted.
- **a weighing** (Weighing): gross and tare, each from the scale bridge (the latest settled reading
  of the weighbridge's scale, as a station weighs) or typed by somebody who may, with a reason. The
  net is gross less tare, derived. A wrong weighing is replaced: its predecessor is kept, marked
  replaced, its figures as they were. A goods receipt or a delivery names the weighing; the
  weighing names neither (the document that would be meaningless without the other holds the
  pointer).
- **a gate pass** (GatePass): returnable or not, numbered, listing items or assets. Approved by a
  role that may before its vehicle leaves; out with its vehicle; a returnable one is brought back
  in part or in full, and stays on the overdue list until it is. A pass moves no stock: an item that
  leaves for good names the stock movement that took it off the shelf (a delivery, a write-off, a
  job-work challan), which costed it with cost_of_removing(). An item out for repair is still ours,
  on the shelf's books, and the pass says where it is.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import (
    AuditModel, DocumentSequence, correction_date, lock_rows, numbered_in_one_transaction, serialised,
    takes_back, to_date,
)

from .models import Item, StockMovement


def _vehicle(number):
    """MH 12 AB 1234, mh12ab1234 and MH-12-AB-1234 are one lorry."""
    return "".join(ch for ch in (number or "").upper() if ch.isalnum())


class VisitPurpose(models.TextChoices):
    INWARD_ORDER = "inward_order", "Inward against a purchase order"
    INWARD_JOB_WORK = "inward_job_work", "Inward against a job-work challan"
    OUTWARD_DELIVERY = "outward_delivery", "Outward against a delivery"
    GATE_PASS = "gate_pass", "Against a gate pass"
    VISITOR = "visitor", "Visitor"


INWARD = {VisitPurpose.INWARD_ORDER, VisitPurpose.INWARD_JOB_WORK}
OUTWARD = {VisitPurpose.OUTWARD_DELIVERY, VisitPurpose.GATE_PASS}


class VisitStatus(models.TextChoices):
    IN = "in", "In"
    OUT = "out", "Left"
    CANCELLED = "cancelled", "Cancelled"


class GateVisit(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    vehicle_number = models.CharField(max_length=15)
    driver_name = models.CharField(max_length=100)
    driver_mobile = models.CharField(max_length=15, blank=True)
    transporter_name = models.CharField(max_length=100, blank=True,
                                        help_text="As the papers name them; a claim names the transporter's party.")
    purpose = models.CharField(max_length=20, choices=VisitPurpose.choices)
    reference = models.CharField(max_length=64, blank=True,
                                 help_text="The order, challan or delivery number its papers carry.")
    carried = models.TextField(blank=True, help_text="What it carried, as the gate saw it.")
    status = models.CharField(max_length=12, choices=VisitStatus.choices, default=VisitStatus.IN, editable=False)
    in_at = models.DateTimeField(editable=False, help_text="The gate's clock, as it came in.")
    out_at = models.DateTimeField(null=True, blank=True, editable=False, help_text="The gate's clock, as it left.")
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancel_reason = models.CharField(max_length=255, blank=True, editable=False)

    # What the guard keyed while the vehicle is in: anything else is the gate's clock or a step.
    KEYED = ("vehicle_number", "driver_name", "driver_mobile", "transporter_name", "purpose", "reference",
             "carried")

    class Meta:
        ordering = ["-in_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="gate_visit_number_unique"),
        ]

    def __str__(self):
        return f"{self.number or 'Visit'} {self.vehicle_number}"

    def hours_in(self, now=None):
        end = self.out_at or now or timezone.now()
        return Decimal((end - self.in_at).total_seconds()) / 3600

    def current_weighing(self):
        return self.weighings.filter(replaced_at__isnull=True).first()

    @numbered_in_one_transaction
    def save(self, *args, **kwargs):
        self.vehicle_number = _vehicle(self.vehicle_number)
        if not self.vehicle_number:
            raise ValidationError({"vehicle_number": "Write the vehicle's number."})
        if self._state.adding:
            self.in_at = timezone.now()
            self.status = VisitStatus.IN
            self.number = DocumentSequence.next_for(
                "inventory.gate_visit", timezone.localdate(), name="Gate register", prefix="GATE-")
        else:
            stored = GateVisit.objects.filter(pk=self.pk).first()
            if stored is not None and stored.status != VisitStatus.IN:
                raise ValidationError(f"{stored.number} is {stored.get_status_display().lower()}; "
                                      "what it recorded stands.")
            if stored is not None and stored.purpose != self.purpose and self.weighings.exists():
                raise ValidationError({"purpose": "It has been weighed as what it came for; cancel it and "
                                                  "record the visit again."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A visit in the gate register is cancelled, never deleted.")

    @serialised("status")
    def start_weighing(self, scale_code=""):
        """A weighing of this vehicle; one at a time (a wrong one is replaced, not weighed beside)."""
        if self.status != VisitStatus.IN:
            raise ValidationError(f"{self.number} is {self.get_status_display().lower()}; weigh a vehicle "
                                  "while it is in.")
        if self.purpose == VisitPurpose.VISITOR:
            raise ValidationError("A visitor is not weighed.")
        current = self.current_weighing()
        if current is not None:
            raise ValidationError(f"{self.number} is being weighed already ({current.number}); replace that "
                                  "weighing if it was wrong.")
        return Weighing.objects.create(visit=self, scale_code=(scale_code or "").strip())

    @serialised("status")
    def check_out(self):
        """The out-entry: the vehicle leaves, with the passes approved for it."""
        if self.status != VisitStatus.IN:
            raise ValidationError(f"{self.number} is {self.get_status_display().lower()}.")
        weighing = self.current_weighing()
        if weighing is not None and not weighing.is_complete():
            raise ValidationError(f"{weighing.number} has no {'tare' if weighing.gross_kg is not None else 'gross'}"
                                  " weight yet: weigh it, or replace the weighing, before the vehicle leaves.")
        # Held, and asked again as held: a pass approved or cancelled at this moment is seen as it ends.
        passes = list(self.gate_passes.order_by("pk"))
        lock_rows(*passes)
        passes = [each for each in passes if each.status != PassStatus.CANCELLED]
        waiting = [each.number for each in passes if each.status != PassStatus.APPROVED]
        if waiting:
            raise ValidationError(f"{', '.join(waiting)} is not approved; the vehicle cannot take it out.")
        self.out_at = timezone.now()
        self.status = VisitStatus.OUT
        super().save(update_fields=["out_at", "status", "updated_at"])
        for each in passes:
            each._go_out(self.out_at)
        return self

    @serialised("status")
    @takes_back
    def cancel(self, reason=""):
        """A visit recorded by mistake. Its weighings stay, read as cancelled with it."""
        if self.status == VisitStatus.CANCELLED:
            raise ValidationError(f"{self.number} is cancelled already.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why the visit is cancelled."})
        live = [each.number for each in self.gate_passes.exclude(status=PassStatus.CANCELLED)]
        if live:
            raise ValidationError(f"{', '.join(live)} is on this visit: cancel the pass first.")
        for weighing in self.weighings.all():
            named = weighing.named_by()
            if named:
                raise ValidationError(f"{weighing.number} is named by {named[0]}: that document weighs by it.")
        self.status = VisitStatus.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancel_reason = reason.strip()[:255]
        super().save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
        return self


def open_too_long(hours=None, now=None):
    """
    Visits still in after `hours`, by default the company's (gate_visit_open_hours): a vehicle that
    left with no out-entry, or one stood at the dock. The health check lists them.
    """
    from apps.core.models import Company

    hours = Company.get().gate_visit_open_hours if hours is None else hours
    now = now or timezone.now()
    return GateVisit.objects.filter(status=VisitStatus.IN,
                                    in_at__lt=now - timezone.timedelta(hours=hours)).order_by("in_at")


class WeightSource(models.TextChoices):
    SCALE = "scale", "From the scale"
    TYPED = "typed", "Typed"


TYPED_REASONS = [
    ("scale_offline", "Scale bridge not connected"),
    ("calibration", "Weighbridge under calibration"),
    ("power", "Power failure"),
    ("other", "Other"),
]


class Weighing(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    visit = models.ForeignKey(GateVisit, on_delete=models.PROTECT, related_name="weighings")
    scale_code = models.CharField(max_length=32, blank=True,
                                  help_text="The weighbridge's scale, as its bridge posts it.")
    gross_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, editable=False)
    gross_at = models.DateTimeField(null=True, blank=True, editable=False)
    gross_source = models.CharField(max_length=8, choices=WeightSource.choices, blank=True, editable=False)
    gross_reason = models.CharField(max_length=16, choices=TYPED_REASONS, blank=True, editable=False)
    gross_note = models.CharField(max_length=255, blank=True, editable=False)
    gross_typed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+", editable=False)
    tare_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, editable=False)
    tare_at = models.DateTimeField(null=True, blank=True, editable=False)
    tare_source = models.CharField(max_length=8, choices=WeightSource.choices, blank=True, editable=False)
    tare_reason = models.CharField(max_length=16, choices=TYPED_REASONS, blank=True, editable=False)
    tare_note = models.CharField(max_length=255, blank=True, editable=False)
    tare_typed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    replaced_at = models.DateTimeField(null=True, blank=True, editable=False)
    replace_reason = models.CharField(max_length=255, blank=True, editable=False)
    replaces = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="replaced_by", editable=False)

    STEPS = {"gross_kg", "gross_at", "gross_source", "gross_reason", "gross_note",
             "gross_typed_by", "tare_kg", "tare_at", "tare_source", "tare_reason", "tare_note",
             "tare_typed_by", "replaced_at", "replace_reason", "updated_at"}

    class Meta:
        ordering = ["-id"]
        permissions = [("type_weight", "Can type a weight the weighbridge's scale did not report")]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="weighing_number_unique"),
            # One weighing of a vehicle at a time; a wrong one is replaced, and the replaced one stays.
            models.UniqueConstraint(fields=["visit"], condition=Q(replaced_at__isnull=True),
                                    name="one_current_weighing_per_visit"),
        ]

    def __str__(self):
        return f"{self.number} {self.visit.vehicle_number}"

    def net_kg(self):
        if self.gross_kg is None or self.tare_kg is None:
            return None
        return self.gross_kg - self.tare_kg

    def is_complete(self):
        return self.gross_kg is not None and self.tare_kg is not None

    def typed(self):
        return WeightSource.TYPED in (self.gross_source, self.tare_source)

    def is_usable(self):
        return self.is_complete() and self.replaced_at is None and self.visit.status != VisitStatus.CANCELLED

    def named_by(self):
        """The receipts and deliveries that name this weighing (their pointer, read back)."""
        names = [str(receipt) for receipt in self.goods_receipts.filter(reverses__isnull=True)]
        return names + [str(delivery) for delivery in self.deliveries.filter(reverses__isnull=True)]

    @numbered_in_one_transaction
    def save(self, *args, **kwargs):
        if self._state.adding:
            self.number = DocumentSequence.next_for(
                "inventory.weighing", timezone.localdate(), name="Weighbridge slips", prefix="WB-")
        else:
            updating = set(kwargs.get("update_fields") or ())
            if not updating or not updating <= self.STEPS:
                # Its figures are written by record() and replace() alone: a weighed figure is a fact.
                raise ValidationError(f"{self.number} is weighed as the scale said or as typed with a reason; "
                                      "replace it if it was wrong.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self.number} is a weighed record: replace it, never delete it.")

    def locked_before_it(self):
        return [GateVisit(pk=self.visit_id)]

    def _check_side(self, side):
        if side not in ("gross", "tare"):
            raise ValidationError("Weigh the gross or the tare.")
        visit = GateVisit.objects.get(pk=self.visit_id)
        if visit.status != VisitStatus.IN:
            raise ValidationError(f"{visit.number} is {visit.get_status_display().lower()}; weigh a vehicle "
                                  "while it is in.")
        if self.replaced_at is not None:
            raise ValidationError(f"{self.number} was replaced; weigh on the weighing that replaced it.")
        if getattr(self, f"{side}_kg") is not None:
            raise ValidationError(f"{self.number} has its {side} weight; replace the weighing if it was wrong.")

    def _write(self, side, kg, at, source, reason="", note="", typer=None):
        other = self.tare_kg if side == "gross" else self.gross_kg
        if other is not None and (kg <= other if side == "gross" else kg >= other):
            raise ValidationError(f"The gross ({kg if side == 'gross' else other} kg) must be more than the tare "
                                  f"({other if side == 'gross' else kg} kg).")
        values = {"kg": kg, "at": at, "source": source, "reason": reason, "note": (note or "").strip()[:255],
                  "typed_by": typer}
        for name, value in values.items():
            setattr(self, f"{side}_{name}", value)
        super().save(update_fields=[f"{side}_{name}" for name in values] + ["updated_at"])
        return self

    @serialised("gross_kg", "tare_kg", "replaced_at", held_first=True)
    def record(self, side, source, kg=None, reason="", note="", by=None, at=None):
        """
        A typed gross or tare: by somebody who may, with a reason, and marked typed. A weight from the
        scale is not taken here: the scale bridge (apps.manufacturing.station_scale.weigh_from_bridge)
        locks its reading, refuses one already used, and calls from_scale(); the reading keeps the
        pointer to this weighing, and the weighing keeps the reading's figures as its own facts.
        """
        if source == WeightSource.SCALE:
            raise ValidationError("A weight from the scale is taken from the weighbridge's reading "
                                  "(the bridge's own action), not sent as a figure.")
        if source != WeightSource.TYPED:
            raise ValidationError("Say whether the weight is from the scale or typed.")
        self._check_side(side)
        if by is None or not by.has_perm("inventory.type_weight"):
            raise ValidationError("A typed weight needs somebody who may type one.")
        if reason not in dict(TYPED_REASONS):
            raise ValidationError({"reason": "Give the reason the scale was not used."})
        if reason == "other" and not (note or "").strip():
            raise ValidationError({"note": "Say what the other reason was."})
        try:
            kg = Decimal(str(kg))
        except Exception:  # noqa: BLE001 - anything not a number is refused in words
            raise ValidationError({"kg": "A weight is a number of kilogrammes."}) from None
        if not kg.is_finite() or kg <= 0:
            raise ValidationError({"kg": "A weight is a number of kilogrammes, more than nothing."})
        if kg != kg.quantize(Decimal("0.001")):
            raise ValidationError({"kg": "A weighbridge reads to the gramme at most."})
        return self._write(side, kg, at or timezone.now(), WeightSource.TYPED, reason, note, by)

    @serialised("gross_kg", "tare_kg", "replaced_at", held_first=True)
    def from_scale(self, side, kg, read_at):
        """
        The figures of a bridge reading, recorded as this weighing's own: its weight and the moment the
        scale reported it. Called by the scale bridge once it holds the reading (weigh_from_bridge).
        """
        self._check_side(side)
        if not self.scale_code:
            raise ValidationError("Say which scale the weighbridge is, or type the weight.")
        return self._write(side, kg, read_at, WeightSource.SCALE)

    @serialised("replaced_at", held_first=True)
    @takes_back
    def replace(self, reason=""):
        """A wrong weighing: kept as it was, marked replaced, and a fresh one begun for the same vehicle."""
        if self.replaced_at is not None:
            raise ValidationError(f"{self.number} was replaced already.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why the weighing is replaced."})
        visit = GateVisit.objects.get(pk=self.visit_id)
        if visit.status != VisitStatus.IN:
            raise ValidationError(f"{visit.number} is {visit.get_status_display().lower()}; a weighing is "
                                  "replaced while the vehicle is in.")
        named = self.named_by()
        if named:
            raise ValidationError(f"{named[0]} weighs by {self.number}: name another weighing there, or return "
                                  "it, first.")
        self.replaced_at = timezone.now()
        self.replace_reason = reason.strip()[:255]
        super().save(update_fields=["replaced_at", "replace_reason", "updated_at"])
        return Weighing.objects.create(visit=visit, scale_code=self.scale_code, replaces=self)


def check_weighing_for(weighing, inward):
    """What a receipt (inward) or a delivery asks of the weighing it names, under the visit's lock."""
    lock_rows(GateVisit(pk=weighing.visit_id), refresh=False)
    weighing.refresh_from_db()
    visit = GateVisit.objects.get(pk=weighing.visit_id)
    if visit.status == VisitStatus.CANCELLED:
        raise ValidationError(f"{weighing.number} is on {visit.number}, which was cancelled.")
    if weighing.replaced_at is not None:
        raise ValidationError(f"{weighing.number} was replaced; name the weighing that replaced it.")
    if not weighing.is_complete():
        raise ValidationError(f"{weighing.number} has no {'gross' if weighing.gross_kg is None else 'tare'} yet.")
    if inward and visit.purpose not in INWARD:
        raise ValidationError(f"{visit.number} came {visit.get_purpose_display().lower()}, not with goods in.")
    if not inward and visit.purpose not in OUTWARD:
        raise ValidationError(f"{visit.number} came {visit.get_purpose_display().lower()}, not for goods out.")
    return weighing


# -- gate passes ----------------------------------------------------------------------------------

class PassKind(models.TextChoices):
    RETURNABLE = "returnable", "Returnable"
    NON_RETURNABLE = "non_returnable", "Non-returnable"


class PassStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    OUT = "out", "Out"
    RETURNED = "returned", "Returned"
    CANCELLED = "cancelled", "Cancelled"


class GatePass(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    kind = models.CharField(max_length=16, choices=PassKind.choices)
    pass_date = models.DateField(default=timezone.localdate)
    party_name = models.CharField(max_length=120, help_text="Who it goes to: the repairer, the buyer of the scrap.")
    purpose = models.CharField(max_length=255, blank=True)
    expected_return_date = models.DateField(null=True, blank=True)
    visit = models.ForeignKey(GateVisit, null=True, blank=True, on_delete=models.PROTECT, related_name="gate_passes",
                              help_text="The vehicle it leaves on.")
    status = models.CharField(max_length=12, choices=PassStatus.choices, default=PassStatus.DRAFT, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    out_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancel_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-pass_date", "-id"]
        permissions = [("approve_gatepass", "Can approve a gate pass")]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="gate_pass_number_unique"),
        ]

    def __str__(self):
        return self.number or f"Gate pass {self.pk}"

    def is_overdue(self, today=None):
        today = today or timezone.localdate()
        return (self.kind == PassKind.RETURNABLE and self.status == PassStatus.OUT
                and self.expected_return_date is not None and self.expected_return_date < today)

    def locked_before_it(self):
        return [GateVisit(pk=self.visit_id) if self.visit_id else None]

    @numbered_in_one_transaction
    def save(self, *args, **kwargs):
        if self._state.adding:
            self.status = PassStatus.DRAFT
            returnable = self.kind == PassKind.RETURNABLE
            self.number = DocumentSequence.next_for(
                f"inventory.gate_pass_{'returnable' if returnable else 'non_returnable'}", to_date(self.pass_date),
                name="Returnable gate passes" if returnable else "Non-returnable gate passes",
                prefix="RGP-" if returnable else "NRGP-")
        else:
            stored = GatePass.objects.filter(pk=self.pk).first()
            if stored is not None and stored.status != PassStatus.DRAFT:
                raise ValidationError(f"{stored.number} is {stored.get_status_display().lower()}; it stands as "
                                      "approved.")
            if stored is not None and stored.kind != self.kind:
                raise ValidationError({"kind": "A pass is numbered as what it is; cancel it and write another."})
        if self.kind == PassKind.NON_RETURNABLE:
            self.expected_return_date = None
        if self.visit_id and GateVisit.objects.filter(pk=self.visit_id).exclude(status=VisitStatus.IN).exists():
            raise ValidationError({"visit": "That vehicle is not in."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A gate pass is numbered: cancel it, never delete it.")

    @serialised("status", "visit", held_first=True)
    def approve(self, by):
        """Signed off by a role that may, before the vehicle leaves."""
        if by is None or not by.has_perm("inventory.approve_gatepass"):
            raise ValidationError("Approving a gate pass needs somebody who may.")
        if self.status != PassStatus.DRAFT:
            raise ValidationError(f"{self.number} is {self.get_status_display().lower()}.")
        if self.visit_id and GateVisit.objects.get(pk=self.visit_id).status != VisitStatus.IN:
            raise ValidationError(f"{self.number}'s vehicle has left; a pass is approved before it goes.")
        if self.kind == PassKind.RETURNABLE:
            if self.expected_return_date is None:
                raise ValidationError({"expected_return_date": "Say when it is to come back."})
            if self.expected_return_date < to_date(self.pass_date):
                raise ValidationError({"expected_return_date": "It comes back after it goes."})
        lines = list(self.lines.order_by("pk"))
        if not lines:
            raise ValidationError(f"{self.number} lists nothing.")
        assets = [line.asset for line in lines if line.asset_id]
        lock_rows(*assets)
        for line in lines:
            line.check_can_go(self)
        self.status = PassStatus.APPROVED
        self.approved_by = by
        self.approved_at = timezone.now()
        super().save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        return self

    def _go_out(self, at):
        """Out with its vehicle (GateVisit.check_out, which holds this pass)."""
        self.status = PassStatus.OUT
        self.out_at = at
        super().save(update_fields=["status", "out_at", "updated_at"])

    @serialised("status", held_first=True)
    @takes_back
    def cancel(self, reason=""):
        if self.status not in (PassStatus.DRAFT, PassStatus.APPROVED):
            raise ValidationError(f"{self.number} is {self.get_status_display().lower()}: what went out went.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why the pass is cancelled."})
        self.status = PassStatus.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancel_reason = reason.strip()[:255]
        super().save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
        return self

    @serialised("status")
    def record_return(self, line, quantity, returned_on=None, condition="good", note="", visit=None):
        """What came back against a returnable pass, in part or in full; damaged is said, not hidden."""
        if self.kind != PassKind.RETURNABLE:
            raise ValidationError(f"{self.number} is non-returnable: nothing comes back on it.")
        if self.status != PassStatus.OUT:
            raise ValidationError(f"{self.number} is {self.get_status_display().lower()}; only what went out "
                                  "comes back.")
        line = GatePassLine.objects.get(pk=line.pk)
        if line.gate_pass_id != self.pk:
            raise ValidationError("That line is on another pass.")
        quantity = Decimal(str(quantity))
        if quantity <= 0:
            raise ValidationError({"quantity": "Say how many came back."})
        if quantity > line.outstanding():
            raise ValidationError({"quantity": f"Only {line.outstanding().normalize():f} of {line.label()} is out."})
        if condition not in dict(ReturnCondition.choices):
            raise ValidationError({"condition": "Say whether it came back good or damaged."})
        if condition == ReturnCondition.DAMAGED and not (note or "").strip():
            raise ValidationError({"note": "Say what the damage is."})
        day = correction_date(returned_on, to_date(self.out_at), f"{self.number} is not returned on", "it went out")
        made = GatePassReturn.objects.create(line=line, quantity=quantity, returned_on=day, condition=condition,
                                             note=(note or "").strip()[:255], visit=visit)
        self._settle_status()
        return made

    @serialised("status")
    @takes_back
    def cancel_return(self, entry, reason=""):
        """A return recorded against the wrong pass or line: kept, marked cancelled, and out again."""
        entry = GatePassReturn.objects.get(pk=entry.pk)
        if entry.line.gate_pass_id != self.pk:
            raise ValidationError("That return is on another pass.")
        if entry.cancelled_at is not None:
            raise ValidationError("That return is cancelled already.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why the return is cancelled."})
        entry.cancelled_at = timezone.now()
        entry.cancel_reason = reason.strip()[:255]
        entry.save(update_fields=["cancelled_at", "cancel_reason", "updated_at"])
        self._settle_status()
        return entry

    def _settle_status(self):
        back = all(line.outstanding() == 0 for line in self.lines.all())
        status = PassStatus.RETURNED if back else PassStatus.OUT
        if status != self.status:
            self.status = status
            super().save(update_fields=["status", "updated_at"])


def overdue_passes(today=None):
    today = today or timezone.localdate()
    return GatePass.objects.filter(kind=PassKind.RETURNABLE, status=PassStatus.OUT,
                                   expected_return_date__lt=today).order_by("expected_return_date", "id")


class GatePassLine(AuditModel):
    gate_pass = models.ForeignKey(GatePass, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="gate_pass_lines")
    asset = models.ForeignKey("assets.FixedAsset", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="gate_pass_lines")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="In the item's stocking unit; an asset is one.")
    movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT, related_name="gate_pass_lines",
        help_text="An item leaving for good: the stock movement that took it off the shelf (a delivery, a "
                  "write-off, a challan). The pass moves no stock of its own.")

    class Meta:
        ordering = ["gate_pass", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="gate_pass_line_quantity_positive"),
            models.CheckConstraint(check=Q(item__isnull=True) ^ Q(asset__isnull=True),
                                   name="gate_pass_line_item_or_asset"),
        ]

    def __str__(self):
        return f"{self.label()} x {self.quantity.normalize():f}"

    def label(self):
        if self.asset_id:
            return f"{self.asset.number} {self.asset.name}"
        return f"{self.item.sku} {self.item.name}" if self.item_id else self.description

    def returned(self):
        return self.returns.filter(cancelled_at__isnull=True).aggregate(total=Sum("quantity"))["total"] or Decimal("0")

    def outstanding(self):
        if self.gate_pass.kind != PassKind.RETURNABLE or self.gate_pass.status not in (PassStatus.OUT,
                                                                                         PassStatus.RETURNED):
            return Decimal("0")
        return self.quantity - self.returned()

    def check_can_go(self, gate_pass):
        """Asked as the pass is approved, when every answer can be known."""
        if self.asset_id:
            from apps.assets.models import AssetStatus

            if self.quantity != 1:
                raise ValidationError(f"{self.label()}: an asset goes out one at a time.")
            if self.asset.status != AssetStatus.IN_SERVICE:
                raise ValidationError(f"{self.label()} is {self.asset.get_status_display().lower()}.")
            if gate_pass.kind == PassKind.NON_RETURNABLE:
                raise ValidationError(f"{self.label()} is an asset: one leaving for good is disposed of on the "
                                      "register; a pass for repair is returnable.")
            elsewhere = GatePassLine.objects.filter(
                asset_id=self.asset_id, gate_pass__status__in=[PassStatus.APPROVED, PassStatus.OUT]
            ).exclude(gate_pass=gate_pass)
            if any(other.gate_pass.status == PassStatus.APPROVED or other.outstanding() > 0 for other in elsewhere):
                raise ValidationError(f"{self.label()} is out on another pass already.")
            return
        if gate_pass.kind == PassKind.RETURNABLE:
            if self.movement_id:
                raise ValidationError(f"{self.label()} comes back: it stays on the books, and names no movement.")
            return
        # Leaving for good: through a stock path, never on the pass alone.
        if not self.movement_id:
            raise ValidationError(f"{self.label()} leaves for good: name the delivery, write-off or challan that "
                                  "took it off the shelf.")
        movement = StockMovement.objects.get(pk=self.movement_id)
        if movement.item_id != self.item_id or movement.quantity >= 0:
            raise ValidationError(f"{self.label()}: that movement did not take this item off the shelf.")
        covered = GatePassLine.objects.filter(movement_id=self.movement_id).exclude(
            gate_pass__status=PassStatus.CANCELLED).exclude(pk=self.pk).filter(
            gate_pass__status__in=[PassStatus.APPROVED, PassStatus.OUT]).aggregate(
            total=Sum("quantity"))["total"] or Decimal("0")
        if covered + self.quantity > -movement.quantity:
            raise ValidationError(f"{self.label()}: that movement took {(-movement.quantity).normalize():f} off the "
                                  f"shelf, and {covered.normalize():f} of it is on other passes.")

    def save(self, *args, **kwargs):
        if not GatePass.objects.filter(pk=self.gate_pass_id, status=PassStatus.DRAFT).exists():
            raise ValidationError("A pass's lines change only while it is a draft.")
        if self.asset_id and self.quantity is None:
            self.quantity = Decimal("1")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not GatePass.objects.filter(pk=self.gate_pass_id, status=PassStatus.DRAFT).exists():
            raise ValidationError("A pass's lines change only while it is a draft.")
        super().delete(*args, **kwargs)


class ReturnCondition(models.TextChoices):
    GOOD = "good", "Good"
    DAMAGED = "damaged", "Damaged"


class GatePassReturn(AuditModel):
    line = models.ForeignKey(GatePassLine, on_delete=models.PROTECT, related_name="returns")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    returned_on = models.DateField()
    condition = models.CharField(max_length=8, choices=ReturnCondition.choices, default=ReturnCondition.GOOD)
    note = models.CharField(max_length=255, blank=True)
    visit = models.ForeignKey(GateVisit, null=True, blank=True, on_delete=models.PROTECT, related_name="pass_returns",
                              help_text="The vehicle it came back on.")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["returned_on", "id"]
        constraints = [models.CheckConstraint(check=Q(quantity__gt=0), name="gate_pass_return_quantity_positive")]

    def __str__(self):
        return f"{self.quantity.normalize():f} of {self.line.label()} back {self.returned_on}"

    def save(self, *args, **kwargs):
        if not self._state.adding and set(kwargs.get("update_fields") or ()) != {
                "cancelled_at", "cancel_reason", "updated_at"}:
            raise ValidationError("What came back stands; cancel the entry if it was wrong.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("What came back stands; cancel the entry if it was wrong.")
