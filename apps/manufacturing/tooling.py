"""
The things a plant owns that wear out, and the artwork they carry.

A printing cylinder is not a machine and it is not a material. It is
engraved for one customer's design, it fits one press, it prints a
finite number of bags before the engraving goes and it has to be
re-chromed, and when a plant loses track of how much life is left in
one it finds out in the middle of a fifty-thousand-sack run. Cutting
dies, loom reeds and extruder screens are the same shape of thing.

**Life is derived from what was printed, never counted down.** A
stored remaining-impressions field is the classic drifting copy: it is
wrong the moment a run is voided, and nothing says so. Every use is a
row, the row hangs off the production booking that caused it, and
voiding that booking takes the wear back with it.

**A cylinder belongs to a design, and a design belongs to a
customer.** That is the whole reason the plant cannot simply buy more
printing capacity when a press is busy — the cylinders for Ultratech's
artwork do not print Ambuja's sacks. Modelling the design separately
from the cylinder is what lets a planner see that four of the six
cylinders for one customer are nearly worn while the press itself is
half idle.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

from apps.core.models import AuditModel

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


class PrintDesign(AuditModel):
    """
    One customer's artwork, as approved.

    Approval is a date and a person rather than a flag, because "who
    signed off the artwork" is the first question asked when fifty
    thousand sacks come back with the wrong shade of blue on them.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    customer = models.ForeignKey(
        "core.Party", on_delete=models.PROTECT, related_name="print_designs",
        help_text="Whose artwork it is. A design nobody owns is a design "
                  "nobody can approve.",
    )
    colours = models.PositiveSmallIntegerField(
        default=1,
        help_text="How many colours it prints in. One cylinder a colour, so "
                  "this is also how many cylinders a full set is.",
    )
    artwork_reference = models.CharField(
        max_length=255, blank=True,
        help_text="Where the approved file lives. A reference rather than the "
                  "file: this module has no business being a document store.",
    )
    approved_on = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "core.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Who at the customer signed it off.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["customer", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def is_approved(self):
        return self.approved_on is not None

    def cylinder_set(self):
        """
        The cylinders that print this design, and whether it is a full
        set.

        A four-colour design with three usable cylinders cannot be
        printed at all, and the plant that finds that out at the press
        has already changed over.
        """
        usable = tools_for(self)
        return {
            "design": self,
            "colours": self.colours,
            "tools": usable,
            "complete": len(usable) >= self.colours,
            "short_by": max(self.colours - len(usable), 0),
        }


class ToolKind(models.TextChoices):
    CYLINDER = "cylinder", "Printing cylinder"
    DIE = "die", "Cutting die"
    REED = "reed", "Loom reed"
    SCREEN = "screen", "Extruder screen"
    OTHER = "other", "Other"


class ToolStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    WORN = "worn", "Worn out"
    SERVICE = "service", "Away for service"
    RETIRED = "retired", "Retired"


class Tool(AuditModel):
    """
    Something that wears out, with a life measured in what it makes.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=12, choices=ToolKind.choices)
    design = models.ForeignKey(
        PrintDesign, null=True, blank=True, on_delete=models.PROTECT,
        related_name="tools",
        help_text="The artwork this cylinder carries. Required on a cylinder "
                  "and refused on anything else: a cylinder with no design is "
                  "a cylinder nobody can find when that customer orders, which "
                  "is the same as not having it.",
    )
    work_centre = models.ForeignKey(
        "WorkCentre", null=True, blank=True, on_delete=models.PROTECT,
        related_name="tools",
        help_text="The machine it fits, where it only fits one.",
    )
    life_limit = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="How much it is good for before it has to be re-engraved or "
                  "replaced, in `life_uom`. Empty means nobody has said, which "
                  "is reported rather than treated as unlimited.",
    )
    life_uom = models.ForeignKey(
        "core.UnitOfMeasure", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="What the life is counted in — bags for a cylinder or a die, "
                  "metres or hours for a reed. Output is converted into this "
                  "unit, and refused rather than guessed when it will not "
                  "convert.",
    )
    status = models.CharField(
        max_length=12, choices=ToolStatus.choices, default=ToolStatus.AVAILABLE
    )
    acquired_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["kind", "code"]
        constraints = [
            models.CheckConstraint(
                check=Q(life_limit__isnull=True) | Q(life_limit__gt=0),
                name="tool_life_limit_positive",
            ),
            # A life with no unit is a number nobody can read output
            # against, and a unit with no life is a unit for nothing.
            models.CheckConstraint(
                check=Q(life_limit__isnull=True, life_uom__isnull=True)
                | Q(life_limit__isnull=False, life_uom__isnull=False),
                name="tool_life_needs_a_unit",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        self._check_design()

    def save(self, *args, **kwargs):
        self._check_design()
        super().save(*args, **kwargs)

    def _check_design(self):
        if self.kind == ToolKind.CYLINDER and self.design_id is None:
            raise ValidationError(
                f"{self.code} is a printing cylinder and carries no design. A "
                "cylinder nobody can match to a customer's artwork is a "
                "cylinder nobody finds when that customer orders."
            )
        if self.kind != ToolKind.CYLINDER and self.design_id is not None:
            raise ValidationError(
                f"{self.code} is a {self.get_kind_display().lower()} and names "
                f"{self.design}. Only a cylinder carries artwork."
            )

    # -- life, derived ---------------------------------------------------

    def used(self):
        """
        What this tool has made, summed off its usage rows.

        Derived rather than counted down. A stored remaining figure is
        wrong the moment a run is voided and nothing says so; these
        rows hang off the booking that caused them and go back with it.
        """
        total = self.usages.filter(
            entry__posted=True, entry__voided_at__isnull=True
        ).aggregate(total=Sum("quantity"))["total"]
        return total or ZERO

    def remaining(self):
        """What is left, or None when nobody has said what it is good for."""
        if self.life_limit is None:
            return None
        return self.life_limit - self.used()

    def used_percent(self):
        if not self.life_limit:
            return None
        return (self.used() / self.life_limit * ONE_HUNDRED).quantize(
            Decimal("0.01")
        )

    def is_worn(self):
        """
        Whether it has run past its life.

        None when no life is stated. Deliberately not False: an unknown
        life is not an unlimited one, and answering no would let a
        cylinder nobody has rated run for ever.
        """
        remaining = self.remaining()
        if remaining is None:
            return None
        return remaining <= 0

    def is_usable(self):
        """Fit to go on a press today."""
        if self.status != ToolStatus.AVAILABLE:
            return False
        return self.is_worn() is not True

    def check_usable(self, what="go on a run"):
        if self.status != ToolStatus.AVAILABLE:
            raise ValidationError(
                f"{self} is {self.get_status_display().lower()} and cannot "
                f"{what}."
            )
        if self.is_worn():
            raise ValidationError(
                f"{self} has run {self.used()} of its {self.life_limit} "
                f"{self.life_uom}, so it is worn out and cannot {what}. Send "
                "it for re-engraving, or raise the limit if the plant has "
                "decided it is still good."
            )

    def quantity_for(self, produced, uom):
        """
        `produced` restated in the unit this tool's life is counted in.

        Refused rather than converted when the two do not measure the
        same kind of thing. A cylinder rated in bags, asked how much
        life a tonne of tape took out of it, has no answer — and the
        plausible wrong one is a cylinder that reads half worn and is
        not.
        """
        if self.life_uom_id is None:
            return None
        if uom is None or uom.pk == self.life_uom_id:
            return Decimal(produced)
        try:
            return uom.convert_to(Decimal(produced), self.life_uom)
        except ValidationError:
            raise ValidationError(
                f"{self} is rated in {self.life_uom} and this run is counted "
                f"in {uom}, which is not the same kind of thing. Wear read "
                "against the wrong unit gives a tool that looks fresh and is "
                "finished."
            )


class ToolUsage(AuditModel):
    """
    What one production booking took out of one tool.

    Hangs off the booking rather than off the work order, so that
    voiding the booking takes the wear back with it and `used()` needs
    no correction of its own.
    """

    tool = models.ForeignKey(Tool, on_delete=models.CASCADE, related_name="usages")
    entry = models.ForeignKey(
        "ProductionEntry", on_delete=models.CASCADE, related_name="tool_usages"
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="In the tool's own life unit, converted on the way in.",
    )

    class Meta:
        ordering = ["entry", "tool"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="tool_usage_quantity_positive"
            ),
            models.UniqueConstraint(
                fields=["tool", "entry"], name="one_tool_usage_per_entry"
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.tool.life_uom} on {self.tool}"


def tools_for(design):
    """Usable cylinders carrying this artwork."""
    return [tool for tool in design.tools.all() if tool.is_usable()]


def wearing_out(threshold=Decimal("90"), kind=None):
    """
    Tools past a stated share of their life, worst first.

    The report that stops a changeover happening mid-run. A threshold
    rather than a flag, because how close to the end a plant is willing
    to start a fifty-thousand-sack run is a judgement it makes for
    itself.
    """
    rows = Tool.objects.exclude(status=ToolStatus.RETIRED).filter(
        life_limit__isnull=False
    )
    if kind is not None:
        rows = rows.filter(kind=kind)
    found = []
    for tool in rows.select_related("design", "life_uom", "work_centre"):
        share = tool.used_percent()
        if share is not None and share >= threshold:
            found.append((tool, share, tool.remaining()))
    return sorted(found, key=lambda row: row[1], reverse=True)
