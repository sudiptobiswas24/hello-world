"""
Where on a machine a spare goes: the drive-side main bearing, the screen
pack, the winder motor. A job that issues a bearing says which position
took it, so "how long does a bearing last on loom 14" is the gap between
two placements at the same position, and a machine lists the spares it
cannot run without, to keep on the shelf.

A placement is the fact of what went where, read off the spare issue
that drew it; an issue returned whole takes its placements with it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

from apps.core.models import AuditModel

ZERO = Decimal("0")


class MachinePosition(AuditModel):
    machine = models.ForeignKey("manufacturing.Machine", on_delete=models.PROTECT, related_name="positions")
    code = models.CharField(max_length=32, help_text="Short, as the fitter says it: BRG-DS, SCREEN, WINDER.")
    name = models.CharField(max_length=128, blank=True)
    spare_item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                   help_text="The spare that goes here, where it is one stock item.")
    is_critical = models.BooleanField(
        default=False, help_text="The machine stands without it: its spare is kept on the shelf.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["machine", "code"]
        constraints = [models.UniqueConstraint(fields=["machine", "code"], name="one_position_code_a_machine")]

    def __str__(self):
        return f"{self.machine.code} {self.code}"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        if not self.code:
            raise ValidationError({"code": "Name the position."})
        if self.is_critical and self.spare_item_id is None:
            raise ValidationError({"spare_item": "A critical position names the spare it takes, or nothing can be kept."})
        super().save(*args, **kwargs)

    def placements(self):
        return list(self.spare_placements.select_related("item", "issue__adjustment").order_by(
            "issue__adjustment__adjustment_date", "pk"))

    def history(self):
        """[(date, item, quantity, days since the one before)], oldest first."""
        rows, previous = [], None
        for placement in self.placements():
            day = placement.issue.adjustment.adjustment_date
            rows.append((day, placement.item, placement.quantity, (day - previous).days if previous else None))
            previous = day
        return rows

    def life_days(self):
        """Mean days between one placement and the next; None until there are two."""
        gaps = [days for _, _, _, days in self.history() if days is not None]
        return Decimal(sum(gaps)) / len(gaps) if gaps else None


class SparePlacement(AuditModel):
    issue = models.ForeignKey("manufacturing.SpareIssue", on_delete=models.CASCADE, related_name="placements")
    position = models.ForeignKey(MachinePosition, on_delete=models.PROTECT, related_name="spare_placements")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta:
        ordering = ["issue", "pk"]
        constraints = [models.CheckConstraint(check=Q(quantity__gt=0), name="spare_placement_positive")]

    def __str__(self):
        return f"{self.quantity} {self.item.sku} at {self.position}"


def on_the_jobs_machine(job, position):
    """A position takes a spare only from a job on its own machine."""
    if job.machine_id is None or position.machine_id != job.machine_id:
        raise ValidationError(f"{position} is not on {job.machine or job.work_centre}, which this job is for.")


def place(issue, rows):
    """Record which position took each spare of an issue, checked against the job's machine."""
    for item, quantity, position in rows:
        if position is None:
            continue
        on_the_jobs_machine(issue.job, position)
        SparePlacement.objects.create(issue=issue, position=position, item=item, quantity=Decimal(str(quantity)))


def critical_spares():
    """Every critical position, with how much of its spare is on any shelf, and whether that is nothing."""
    from apps.inventory.models import StockMovement

    positions = list(MachinePosition.objects.filter(is_critical=True, machine__is_active=True)
                     .select_related("machine", "spare_item"))
    held = {row["item_id"]: row["total"] for row in StockMovement.objects.filter(
        item_id__in={position.spare_item_id for position in positions}).values("item_id").annotate(total=Sum("quantity"))}
    rows = []
    for position in positions:
        on_hand = held.get(position.spare_item_id) or ZERO
        rows.append({"position": position, "machine": position.machine, "item": position.spare_item,
                     "on_hand": on_hand, "short": on_hand <= 0, "life_days": position.life_days()})
    return rows
