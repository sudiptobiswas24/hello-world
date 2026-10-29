"""
What a tape line was actually set to on a run: the draw ratio and the
temperatures that decide a tape's tenacity. The specification says what
was intended; this says what ran, so a strength complaint traces to the
setting, not only to the granule lot.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel


class TapeRunSetting(AuditModel):
    work_order = models.ForeignKey("manufacturing.WorkOrder", on_delete=models.PROTECT, related_name="tape_settings")
    machine = models.ForeignKey("manufacturing.Machine", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)
    draw_ratio = models.DecimalField(max_digits=6, decimal_places=2)
    quench_temperature_c = models.DecimalField(max_digits=5, decimal_places=1,
                                               help_text="The water bath the film is chilled in.")
    oven_temperature_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                             help_text="The orientation oven, where the tape is stretched.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["recorded_at", "id"]
        constraints = [
            models.CheckConstraint(check=Q(draw_ratio__gt=1), name="tape_draw_ratio_stretches"),
        ]

    def __str__(self):
        return f"{self.work_order} at {self.draw_ratio}:1"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("A setting is what the line ran at then; record the change as a new one.")
        if self.work_order.status not in ("released", "closed"):
            raise ValidationError("Settings are recorded on a run that has been released.")
        if self.machine_id:
            centres = {self.work_order.work_centre_id,
                       *self.work_order.operations.values_list("work_centre_id", flat=True)} - {None}
            if self.machine.work_centre_id not in centres:
                raise ValidationError({"machine": f"{self.machine.code} is not on a line this run uses."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A setting is what the line ran at then; it stays.")
