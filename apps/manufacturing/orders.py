"""
Work orders: what a run consumed, what it produced, and the difference.

The bill of materials says what a sack ought to take. This says what it
took. In a woven sack plant those two numbers are the business —
material is three quarters of the cost, the regrind loop hides the
leak, and a plant that cannot put kilos in against kilos out per run
cannot tell a bad loom from a bad blend from a bad count.

The accounting is perpetual and ordinary:

    issue material      Dr Work in progress   Cr Inventory
    receive production  Dr Inventory          Cr Work in progress
    by-product          Dr Inventory          Cr Work in progress
    scrap               Dr Scrap              Cr Work in progress
    close the order     Dr/Cr Variance        Cr/Dr Work in progress

Two decisions in there are worth defending.

**Production is received at a planned cost, not at what the run
actually cost.** The actual cost is not known until the order closes,
and the lorry will not wait — a sack made on Tuesday ships on Tuesday
and has to be worth something when it does. So the BOM's cost is frozen
onto the order at release, every receipt goes in at that, and whatever
is left in work in progress when the order closes is a variance for
somebody to explain. The alternative, revaluing the output at close,
means restating stock that has already been sold.

**Work in progress is an account, not a warehouse.** The fabric rolls
waiting at the cutting table are not work in progress: fabric is a
stocked item with its own cost and its own work order, and it sits on a
shelf and is counted. What is in work in progress is the material
already fed into a run that has not yet been accounted for — which has
no location, because it is no longer anywhere.

The ledger entries here are written out rather than put through
`post_inventory_entry`. That helper's counterpart is goods received not
invoiced going in and cost of sales going out, and no argument to it
spells work in progress; this project has already posted one document
backwards by bending it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import JournalEntry, JournalLine, round_money
from apps.core.models import AuditModel, DocumentSequence, to_date
from apps.hr.calendars import WorkingCalendar, parse_working_days
from apps.inventory.availability import check_available
from apps.inventory.costing import cost_of_removing
from apps.quality.release import check_released
from apps.inventory.locking import lock_positions
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.inventory.valuation import inventory_account_for

from .bom import BillOfMaterials, ByproductValuation, byproduct_value, planned_cost
from .shifts import Downtime, DowntimeReason, Shift


class ManufacturingSettings(AuditModel):
    """
    Where the manufacturing side of the ledger lands.

    On this side rather than on `Company`, which already carries the
    goods-received accrual and the purchase price variance. That was the
    pattern and it does not scale: by the tenth module the kernel holds
    thirty account fields and knows what every one of them is for. A
    module's settings belong to the module.
    """

    wip_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Holds what has been fed into open runs. An asset, and it "
                  "should be near zero once the orders that fed it are closed — "
                  "a work-in-progress balance that only grows is orders nobody "
                  "ever closed.",
    )
    variance_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Where a closed run's over- or under-consumption lands. This "
                  "is the number the plant manages: a run that ate more polymer "
                  "than the specification says shows up here and nowhere else.",
    )
    conversion_absorbed_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Credited when machine time is charged to a run. A contra "
                  "account against the power, wages and depreciation posted "
                  "elsewhere: its balance is what the plant over- or "
                  "under-absorbed, which is the question 'did the machines run "
                  "as many hours as we costed them at'.",
    )
    conversion_variance_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Where a run's time overrun lands at close, kept apart from "
                  "the material variance. A blend that ran heavy and a loom "
                  "that ran slow are different problems with different owners, "
                  "and one number for both names neither.",
    )
    scrap_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Where production that failed is written off. Separate from "
                  "variance, because a thousand mis-stitched sacks and a blend "
                  "that ran heavy are different problems with different owners.",
    )

    class Meta:
        verbose_name_plural = "manufacturing settings"

    def __str__(self):
        return "Manufacturing settings"

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()

    NAMES = {
        "wip": "work in progress",
        "variance": "material variance",
        "conversion_absorbed": "conversion absorbed",
        "conversion_variance": "conversion variance",
        "scrap": "production scrap",
    }

    @classmethod
    def account(cls, name, why):
        account = getattr(cls.get(), f"{name}_account")
        if account is None:
            raise ValidationError(
                f"Manufacturing has no {cls.NAMES[name]} account configured, "
                f"and {why}."
            )
        return account


class WorkCentre(AuditModel):
    """
    A machine or a line, and what a run of it is booked against.

    One circular loom runs one fabric for days, and "which loom" is the
    identity of a run rather than a note on it: two looms set to the
    same mesh do not waste the same, and a plant that cannot say which
    one ate the polymer has a number it cannot act on.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    capacity_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="The nominal rate, in `capacity_uom`. A routing operation "
                  "that does not state its own rate for the product falls back "
                  "to this; one that runs slower than the line's nominal speed "
                  "says so on the operation.",
    )
    capacity_uom = models.ForeignKey(
        "core.UnitOfMeasure", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    available_hours_per_day = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("24"),
        help_text="Hours this machine can run on a day it runs at all. "
                  "Twenty-four for a continuous line; less where a shift "
                  "pattern or a maintenance window says so.",
    )
    working_days = models.CharField(
        max_length=7, default="1234567",
        help_text="Which days this machine runs, as ISO weekday numbers — "
                  "1 for Monday through 7 for Sunday. '1234567' is a "
                  "continuous line; '123456' is six days; '12345' is weekdays.",
    )
    holiday_region = models.CharField(
        max_length=32, blank=True,
        help_text="Which public holiday list applies here. Blank takes the "
                  "company-wide one only.",
    )
    machine_rate_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"),
        help_text="Power and depreciation an hour. Twelve looms drawing three "
                  "phase all night is not a rounding error on a sack.",
    )
    labour_rate_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"),
        help_text="The crew this machine needs, an hour. A rate rather than a "
                  "headcount: one operator minding four looms costs a quarter "
                  "of themselves to each.",
    )
    overhead_rate_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"),
        help_text="Everything else the hour carries — supervision, the "
                  "building, maintenance. Three rates rather than one because "
                  "a plant manager argues about them separately.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=Q(machine_rate_per_hour__gte=0)
                & Q(labour_rate_per_hour__gte=0)
                & Q(overhead_rate_per_hour__gte=0),
                name="work_centre_rates_not_negative",
            ),
            models.CheckConstraint(
                check=Q(available_hours_per_day__gt=0)
                & Q(available_hours_per_day__lte=24),
                name="work_centre_hours_in_a_day",
            ),
            models.CheckConstraint(
                check=~Q(working_days=""), name="work_centre_works_some_day",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        # Parsed at the point somebody types it, not at the point a plan
        # divides by it. A pattern nobody can read is a pattern that
        # silently schedules the wrong days.
        parse_working_days(self.working_days)

    def calendar(self):
        """
        Which days this machine runs, holidays included.

        A pattern rather than a count of days a week, which is what
        this used to be. Six-sevenths of a window is the right average
        and the wrong answer to every question actually asked of it: a
        three-day window over a weekend has no capacity at all, not
        three sevenths of a week's worth, and a plant shut for Diwali
        has none for a fortnight. The company-wide holiday list already
        existed in `hr`; this module declined to read it and computed
        an average instead.
        """
        return WorkingCalendar(self.working_days, self.holiday_region)

    def days_a_week(self):
        """Derived from the pattern, never stored beside it."""
        return self.calendar().days_a_week()

    def conversion_rate_per_hour(self):
        """
        What an hour of this machine costs the run.

        Zero for a plant that has chosen not to absorb conversion into
        stock, which is a real choice and not an oversight: it then
        carries power and labour as period cost and its finished goods
        are worth their materials. Nothing here forces the other way —
        but a plant that leaves this at zero should know that a sack on
        its shelf is worth about three quarters of what it cost.
        """
        return (
            self.machine_rate_per_hour
            + self.labour_rate_per_hour
            + self.overhead_rate_per_hour
        )

    def capacity(self, start, end):
        """What this machine is being asked to do in a window, against what it can."""
        from .routing import capacity_report

        return capacity_report(self, start, end)


class WorkOrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    RELEASED = "released", "Released"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class WorkOrder(AuditModel):
    """
    One run: make this much of this, to this bill of materials.

    Released is the moment the arithmetic stops being an opinion. The
    requirements are copied off the BOM and frozen, and the planned cost
    is frozen with them, because the BOM is computed from a
    specification and the specification will change — and when it does,
    a run already on the floor must still read against what it was
    actually started on.
    """

    number = models.CharField(max_length=32, blank=True)
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="work_orders"
    )
    bom = models.ForeignKey(
        BillOfMaterials, on_delete=models.PROTECT, related_name="work_orders"
    )
    quantity_ordered = models.DecimalField(max_digits=18, decimal_places=4)
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="work_orders",
        help_text="Where the output lands and where the material is drawn from.",
    )
    work_centre = models.ForeignKey(
        WorkCentre, null=True, blank=True, on_delete=models.PROTECT,
        related_name="work_orders",
        help_text="The machine a single-operation run sits on. A run with a "
                  "routing spans several, and each operation names its own.",
    )
    routing = models.ForeignKey(
        "Routing", null=True, blank=True, on_delete=models.PROTECT,
        related_name="work_orders", editable=False,
        help_text="Copied off the bill of materials at release and frozen "
                  "there, like everything else about a released run.",
    )
    sales_order_line = models.ForeignKey(
        "sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT,
        related_name="work_orders",
        help_text="The customer order this run is for, when it is one. The "
                  "pointer is here rather than on the sales line because a "
                  "make-to-order run is meaningless without the order and the "
                  "order is perfectly meaningful without the run — and it "
                  "keeps sales from having to know that manufacturing exists.",
    )
    scheduled_start = models.DateField(null=True, blank=True)
    scheduled_end = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=WorkOrderStatus.choices,
        default=WorkOrderStatus.DRAFT,
    )
    backflush = models.BooleanField(
        default=False,
        help_text="Frozen off the bill of materials at release. Whether output "
                  "draws its own components, or a storeman issues them.",
    )
    rework_of = models.ForeignKey(
        "inventory.Lot", null=True, blank=True, on_delete=models.PROTECT,
        related_name="rework_orders",
        help_text="The batch this run is putting right. Required on a rework "
                  "recipe and refused on any other: rework is always of a "
                  "particular roll that a particular inspection failed, and a "
                  "rework order that does not say which is a licence to draw "
                  "good stock and call it salvage.",
    )
    time_allowance_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("50"),
        help_text="How far past its planned time an operation may be booked. "
                  "Wide on purpose — a breakdown, a bad batch of polymer or a "
                  "new crew all cost real hours and none of them is an error. "
                  "Forty-two times the plan is a typed zero, and it had put "
                  "three hundred and sixty thousand rupees into work in "
                  "progress.",
    )
    over_production_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("10"),
        help_text="How far past the ordered quantity this run may book output. "
                  "A loom runs long and a shift books what it made, so some "
                  "allowance is right; unlimited is how a mistyped 100,000 kg "
                  "takes a million off work in progress and puts it on a shelf.",
    )
    planned_unit_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What one unit was expected to cost, worked out from the BOM "
                  "and the shelf at the moment of release and frozen there. "
                  "Every receipt against this order goes in at it. Recomputing "
                  "it later would price Monday's sacks at Friday's polymer.",
    )
    planned_material_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The material side of that plan before the by-products are "
                  "credited back, frozen with it. A by-product valued at a share "
                  "of the run takes its share of this — a fixed number — rather "
                  "than of what has been issued so far, which would value the "
                  "same regrind differently on Tuesday and Thursday.",
    )
    planned_conversion_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The machine-time side of the plan, frozen. Kept apart so "
                  "the close can tell a blend that ran heavy from a loom that "
                  "ran slow.",
    )
    released_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    close_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="The entry that cleared work in progress to variance.",
    )
    reopened_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="The reversal raised when a closed order was reopened, so a "
                  "reader sees what undid the close rather than inferring it.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="work_order_number_unique",
            ),
            models.CheckConstraint(
                check=Q(quantity_ordered__gt=0),
                name="work_order_quantity_positive",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft work order {self.pk}"

    # -- state ----------------------------------------------------------

    def is_open(self):
        return self.status == WorkOrderStatus.RELEASED

    def posted_issues(self):
        return self.issues.filter(posted=True, voided_at__isnull=True)

    def posted_entries(self):
        return self.entries.filter(posted=True, voided_at__isnull=True)

    def quantity_produced(self):
        total = self.posted_entries().aggregate(
            total=models.Sum("quantity_produced")
        )["total"]
        return total or Decimal("0")

    def quantity_scrapped(self):
        total = self.posted_entries().aggregate(
            total=models.Sum("quantity_scrapped")
        )["total"]
        return total or Decimal("0")

    def planned_minutes(self):
        """How long this run was planned to hold machines, setup included."""
        total = self.operations.aggregate(
            total=models.Sum("planned_minutes")
        )["total"]
        return total or Decimal("0")

    def bottleneck(self):
        """The operation this run waits on, or None where it has no routing."""
        return self.operations.order_by("-planned_minutes", "sequence").first()

    def maximum_output(self):
        """The most this run may book, in the item's stocking unit."""
        ordered = self.item.to_stock_quantity(self.quantity_ordered, self.uom)
        return ordered * (
            Decimal("1") + (self.over_production_percent or Decimal("0"))
            / Decimal("100")
        )

    def check_output(self, additional):
        """Refuse output past the allowance, counting what is already booked."""
        booked = self.item.to_stock_quantity(
            self.quantity_produced() + self.quantity_scrapped(), self.uom
        )
        allowed = self.maximum_output()
        if booked + additional > allowed:
            raise ValidationError(
                f"{self} is for {self.quantity_ordered} {self.uom} and allows "
                f"{self.over_production_percent}% over. Booking {additional} on "
                f"top of {booked} would take it past {allowed}. Raise the "
                "allowance on the order if the run really went that long."
            )

    def material_cost(self):
        """
        What has gone in, less what came back, at the values posted.

        Summed from what each document froze rather than recomputed from
        today's shelf. A run that drew polymer in March against a ledger
        that has since been corrected would otherwise cost a different
        amount every time somebody looked at it, and none of those
        amounts would be the one in the journal.
        """
        total = Decimal("0")
        for issue in self.posted_issues():
            total += issue.posted_value or Decimal("0")
        return total

    def posted_time(self):
        return self.time_bookings.filter(posted=True, voided_at__isnull=True)

    def conversion_cost(self):
        """Machine time charged to this run, at the values posted."""
        total = Decimal("0")
        for booking in self.posted_time():
            total += booking.posted_value or Decimal("0")
        return total

    def minutes_booked(self):
        total = self.posted_time().aggregate(total=models.Sum("minutes"))["total"]
        return total or Decimal("0")

    def output_value(self):
        """What has come out, at the values posted."""
        total = Decimal("0")
        for entry in self.posted_entries():
            total += entry.posted_value or Decimal("0")
        return total

    def unaccounted(self):
        """
        What went in and has not come back out as stock or scrap.

        Positive means the run is either unfinished or it ate more than
        the specification said — in polymer, in machine hours, or in
        both. This is the figure the close splits between the two and
        sends to variance, and it goes on reading the same after the
        close: the right number to explain a finished run by, and the
        wrong one to report as a balance.
        """
        return (
            self.material_cost() + self.conversion_cost() - self.output_value()
        )

    def wip_balance(self):
        """
        What this run is actually still holding, which after a close is
        nothing.

        A closed order's work in progress was cleared to variance, and
        an order that says otherwise is telling a reader money is in an
        account it has left. `unaccounted()` is the figure behind the
        close; this is the one that has to agree with the ledger.
        """
        if self.status == WorkOrderStatus.CLOSED:
            return Decimal("0")
        return self.unaccounted()

    def conversion_earned(self):
        """
        What the output this run booked was worth in machine time.

        Per unit made rather than per unit ordered: a run that made half
        of what it was for has earned half the machine hours, and the
        rest is an overrun whether the loom was slow or simply stopped.
        Scrap counts — the machine ran to make it.
        """
        if self.planned_conversion_cost is None:
            return Decimal("0")
        ordered = self.item.to_stock_quantity(self.quantity_ordered, self.uom)
        if not ordered:
            return Decimal("0")
        made = self.item.to_stock_quantity(
            self.quantity_produced() + self.quantity_scrapped(), self.uom
        )
        return self.planned_conversion_cost / ordered * made

    def conversion_variance(self):
        """Machine time charged against machine time earned."""
        return self.conversion_cost() - self.conversion_earned()

    def time_variance_minutes(self):
        """
        The same question in hours, which is the one a shift manager can
        answer. Positive means the run took longer than it was planned
        at.
        """
        ordered = self.item.to_stock_quantity(self.quantity_ordered, self.uom)
        if not ordered:
            return Decimal("0")
        made = self.item.to_stock_quantity(
            self.quantity_produced() + self.quantity_scrapped(), self.uom
        )
        earned = self.planned_minutes() / ordered * made
        return self.minutes_booked() - earned

    def material_variance(self):
        """
        Kilo for kilo, what the run took against what it should have.

        Returned per item as (item, expected, actual, difference), in
        the item's stocking unit — the number a plant manager reads
        before the money one, because it says which material moved.
        """
        expected = {}
        for component in self.components.select_related("item", "uom"):
            key = component.item_id
            expected[key] = [
                component.item,
                component.item.to_stock_quantity(
                    component.quantity_required, component.uom
                ),
                Decimal("0"),
            ]
        for issue in self.posted_issues():
            for line in issue.lines.select_related("item", "uom"):
                row = expected.setdefault(
                    line.item_id, [line.item, Decimal("0"), Decimal("0")]
                )
                row[2] += line.stock_quantity() * issue.sign()
        return [
            (item, want, got, got - want) for item, want, got in expected.values()
        ]

    # -- the forward path -----------------------------------------------

    @transaction.atomic
    def release(self, on_date=None):
        """
        Freeze the arithmetic and let material be drawn against it.

        Everything that could be wrong about this order is knowable now
        — before a loom runs for three days — so it is all asked now:
        whether the BOM makes what the order says, whether every
        by-product can be valued, whether the components convert into
        the units they are stocked in.
        """
        if self.status != WorkOrderStatus.DRAFT:
            raise ValidationError(
                f"{self} is {self.get_status_display().lower()}; only a draft "
                "order can be released."
            )
        if self.bom.item_id != self.item_id:
            raise ValidationError(
                f"{self.bom} makes {self.bom.item}, and this order is for "
                f"{self.item}."
            )
        if (
            self.sales_order_line_id is not None
            and self.sales_order_line.item_id != self.item_id
        ):
            raise ValidationError(
                f"This run makes {self.item} and is against a customer line "
                f"for {self.sales_order_line.item}. A run covers the line it "
                "is for, or it covers nothing."
            )
        if not self.bom.is_active:
            raise ValidationError(f"{self.bom} is not active.")
        self._check_rework()
        if not self.bom.components.exists():
            raise ValidationError(
                f"{self.bom} has no components, so nothing would ever be issued "
                "against this order and its whole cost would fall to variance."
            )
        scale = self.bom.scale_for(self.quantity_ordered, self.uom)
        for byproduct in self.bom.byproducts.select_related("item"):
            # Asked here rather than at the first production entry: by
            # then the run is on the floor and the answer has not changed.
            byproduct_value(byproduct, byproduct.quantity * scale, Decimal("0"))

        batch_quantity = self.quantity_ordered
        if self.uom_id != self.bom.uom_id:
            batch_quantity = self.uom.convert_to(self.quantity_ordered, self.bom.uom)
        operations = []
        if self.bom.routing_id is not None:
            if not self.bom.routing.is_active:
                raise ValidationError(
                    f"{self.bom.routing} has been retired. Releasing against it "
                    "would plan this run at times the plant no longer keeps."
                )
            operations = list(
                self.bom.routing.operations.select_related("work_centre")
            )
        for operation in operations:
            # Asked here, where a rate that nobody has stated can still be
            # stated and a machine that has been taken out can still be
            # swapped. By the first shift the loom has been running a day.
            if not operation.work_centre.is_active:
                raise ValidationError(
                    f"{operation.work_centre} is not in service, and "
                    f"{operation} would put this run on it."
                )
            operation.minutes_for(batch_quantity, self.bom.uom)

        self.components.all().delete()
        for index, component in enumerate(
            self.bom.components.select_related("item", "uom"), start=1
        ):
            WorkOrderComponent.objects.create(
                work_order=self, item=component.item,
                quantity_required=component.gross_quantity() * scale,
                uom=component.uom, waste_percent=component.waste_percent,
                line_number=index,
            )

        self.routing = self.bom.routing
        # Frozen with everything else. A plant that turns backflushing
        # on halfway through a run would have the first half issued by
        # hand and the second half drawn automatically, and the two
        # would meet in the middle as a variance nobody can explain.
        self.backflush = self.bom.backflush
        self.operations.all().delete()
        for operation in operations:
            WorkOrderOperation.objects.create(
                work_order=self, sequence=operation.sequence,
                name=operation.name, work_centre=operation.work_centre,
                setup_minutes=operation.setup_minutes,
                units_per_hour=operation.rate(self.bom.uom),
                planned_minutes=operation.minutes_for(batch_quantity, self.bom.uom),
            )

        on_date = to_date(on_date) or timezone.now().date()
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.work_order", on_date,
                name="Work Orders", prefix="WO-",
            )
        plan = planned_cost(
            self.bom, self.quantity_ordered, self.warehouse, self.uom
        )
        stock_quantity = self.item.to_stock_quantity(
            self.quantity_ordered, self.uom
        )
        self.planned_material_cost = plan.materials
        self.planned_conversion_cost = plan.conversion
        self.planned_unit_cost = (
            (plan.net / stock_quantity).quantize(Decimal("0.000001"))
            if stock_quantity else Decimal("0")
        )
        self.status = WorkOrderStatus.RELEASED
        self.released_at = timezone.now()
        super().save(update_fields=[
            "number", "planned_unit_cost", "planned_material_cost",
            "planned_conversion_cost", "routing", "backflush", "status",
            "released_at", "updated_at",
        ])
        return self

    def is_rework(self):
        return self.rework_of_id is not None

    def _check_rework(self):
        """
        A rework run names the batch it is putting right, and that
        batch is one an inspection actually failed.

        The second half is the one worth having. Rework consumes stock
        at full value and books it back as good, so a rework order
        pointed at a batch nothing is wrong with is a way of laundering
        a shortage: draw two tonnes of good fabric, book two tonnes of
        good fabric, and the difference disappears into the run.
        """
        from apps.quality.models import ReleaseStatus
        from apps.quality.release import release_status

        if self.bom.is_rework and self.rework_of_id is None:
            raise ValidationError(
                f"{self.bom} is a rework recipe and this run does not say "
                "which batch it is putting right."
            )
        if not self.bom.is_rework and self.rework_of_id is not None:
            raise ValidationError(
                f"This run names {self.rework_of} as the batch to put right, "
                f"but {self.bom} is not a rework recipe."
            )
        if self.rework_of_id is None:
            return
        if self.rework_of.item_id != self.item_id:
            raise ValidationError(
                f"{self.rework_of} is a batch of {self.rework_of.item} and "
                f"this run makes {self.item}."
            )
        if release_status(self.rework_of) != ReleaseStatus.HELD:
            raise ValidationError(
                f"{self.rework_of} is not held — nothing has failed it. "
                "Reworking a batch that passed draws good stock and books it "
                "back as salvage, which is a shortage with a document over it."
            )

    def backflush_for(self, quantity):
        """
        What booking `quantity` of output draws from the shelf.

        **Scrap consumes material too.** A run that made six hundred
        good and spoiled fifty ate polymer for six hundred and fifty,
        and a backflush counting only the good output under-issues by
        the difference every single time. It then turns up at close as
        a favourable material variance, which reads as the blend
        running light when in fact the issue was never made. That is
        the whole reason this takes a quantity rather than reading the
        entry's good output itself.

        Scaled off the frozen requirement rather than recomputed from
        the bill of materials, because the specification will have
        moved by the time the last shift books its output and the run
        must draw against what it was released on.
        """
        if self.quantity_ordered <= 0:
            return []
        share = Decimal(quantity) / self.quantity_ordered
        rows = []
        for component in self.components.select_related("item", "uom").all():
            # Quantized to what an issue line actually stores, so that
            # what is computed is what is written. Six decimal places
            # rounded into four on save is a difference that turns up
            # at close as a variance with no cause.
            wanted = (component.quantity_required * share).quantize(
                Decimal("0.0001")
            )
            if wanted > 0:
                rows.append((component, wanted))
        return rows

    @transaction.atomic
    def close(self, on_date=None, memo=""):
        """
        Stop the run and send what is left in work in progress to
        variance.

        A closed order holds nothing. Whatever went in and did not come
        out is over-consumption — or under, which is just as worth
        explaining — and it belongs in the profit and loss account
        rather than sitting in an asset nobody reconciles.
        """
        if self.status != WorkOrderStatus.RELEASED:
            raise ValidationError(
                f"{self} is {self.get_status_display().lower()}; only a released "
                "order can be closed."
            )
        on_date = to_date(on_date) or timezone.now().date()
        balance = round_money(self.unaccounted())
        if balance:
            label = memo or f"Closing variance on {self.number}"
            rows = [(
                ManufacturingSettings.account("wip", "a run is being closed"),
                -balance,
            )]
            time_overrun = round_money(self.conversion_variance())
            if time_overrun:
                rows.append((
                    ManufacturingSettings.account(
                        "conversion_variance",
                        "a run is being closed having taken more machine time "
                        "than it was planned at",
                    ),
                    time_overrun,
                ))
            # Material takes the remainder rather than being computed in
            # its own right, so the two always come to exactly what was
            # left in work in progress. Scrap, by-product recovery and
            # the rounding on five materials all land here, which is
            # where somebody looking for missing polymer would look.
            self.close_entry, _ = _post_entry(
                on_date, self.number, label, rows,
                balance_to=ManufacturingSettings.account(
                    "variance",
                    "a run is being closed with material unaccounted for",
                ),
            )
        self.status = WorkOrderStatus.CLOSED
        self.closed_at = timezone.now()
        super().save(update_fields=[
            "status", "closed_at", "close_entry", "updated_at",
        ])
        return self.close_entry

    # -- and back again --------------------------------------------------

    @transaction.atomic
    def cancel(self):
        """
        Abandon an order nothing has been drawn against.

        Once material has gone in there is nothing to cancel — the
        polymer is in a hopper. Such an order is closed, and what it ate
        becomes a variance, which is the honest record.
        """
        if self.status in (WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED):
            raise ValidationError(
                f"{self} is already {self.get_status_display().lower()}."
            )
        if self.posted_issues().exists() or self.posted_entries().exists():
            raise ValidationError(
                f"{self} has material against it. An order that has consumed "
                "something cannot be cancelled as though it never happened — "
                "close it, and what it consumed becomes a variance."
            )
        self.status = WorkOrderStatus.CANCELLED
        super().save(update_fields=["status", "updated_at"])
        return self

    @transaction.atomic
    def reopen(self, on_date=None, memo=""):
        """
        Undo a close, by reversing it rather than by editing it.

        Written in the same sitting as `close()`, because three of the
        last four defects found in this project were reverse paths that
        were never written at all.
        """
        if self.status != WorkOrderStatus.CLOSED:
            raise ValidationError(f"{self} is not closed.")
        if self.close_entry_id is not None:
            on_date = to_date(on_date) or timezone.now().date()
            self.reopened_entry = self.close_entry.create_reversal(
                entry_date=on_date,
                memo=memo or f"Reopening {self.number}",
            )
        self.close_entry = None
        self.status = WorkOrderStatus.RELEASED
        self.closed_at = None
        super().save(update_fields=[
            "status", "closed_at", "close_entry", "reopened_entry", "updated_at",
        ])
        return self.reopened_entry

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = WorkOrder.objects.filter(pk=self.pk).first()
            if previous is not None and previous.status in (
                WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED
            ):
                raise ValidationError(
                    f"{self} is {previous.get_status_display().lower()} and "
                    "cannot be changed. Reopen it, or raise another."
                )
        super().save(*args, **kwargs)


class WorkOrderComponent(AuditModel):
    """
    What this run was expected to take, as at the day it was released.

    A copy, deliberately. The BOM under it is computed from a
    specification and the specification will move; a run already on the
    floor must go on reading against what it was started on.
    """

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name="components"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity_required = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="Waste included — what should actually be drawn, not what "
                  "ends up in the product.",
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+"
    )
    waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0"),
        help_text="Carried over from the BOM so the expectation can be read "
                  "back apart from the allowance built into it.",
    )
    line_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["work_order", "line_number", "id"]

    def __str__(self):
        return f"{self.quantity_required} {self.uom} {self.item.sku}"

    def save(self, *args, **kwargs):
        # Written at release and frozen there: the whole point of the
        # copy is that a run goes on reading against what it started on.
        # Release itself writes these while the order is still a draft.
        if self.work_order.status != WorkOrderStatus.DRAFT:
            raise ValidationError(
                f"{self.work_order} is "
                f"{self.work_order.get_status_display().lower()}; its "
                "requirements were frozen when it was released and a run must "
                "go on reading against what it was started on."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.work_order.status != WorkOrderStatus.DRAFT:
            raise ValidationError(
                f"{self.work_order} is "
                f"{self.work_order.get_status_display().lower()}; its "
                "requirements cannot be removed after release."
            )
        return super().delete(*args, **kwargs)

    def quantity_issued(self):
        """Net of returns, in the item's stocking unit."""
        total = Decimal("0")
        for issue in self.work_order.posted_issues():
            for line in issue.lines.filter(item=self.item).select_related("item", "uom"):
                total += line.stock_quantity() * issue.sign()
        return total


class WorkOrderOperation(AuditModel):
    """
    One machine's share of one run, as at the day it was released.

    A copy, for the reason every copy here is a copy: the routing under
    it will change — a line is re-rated, an operation is inserted — and
    a run already on the floor must go on reading against what it was
    started on, including how long it was supposed to take.
    """

    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name="operations"
    )
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    work_centre = models.ForeignKey(
        WorkCentre, on_delete=models.PROTECT, related_name="work_order_operations"
    )
    setup_minutes = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0")
    )
    units_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="The rate this run was planned at, resolved at release — "
                  "the operation's own, or the machine's nominal one where the "
                  "operation did not say. Resolved rather than looked up "
                  "again, so a machine re-rated next month does not re-time a "
                  "run that has already happened.",
    )
    planned_minutes = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Setup plus run time for the whole order, frozen.",
    )

    class Meta:
        ordering = ["work_order", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["work_order", "sequence"],
                name="one_work_order_operation_per_sequence",
            ),
        ]

    def __str__(self):
        return f"{self.work_order} · {self.sequence}. {self.name}"

    def posted_bookings(self):
        return self.bookings.filter(posted=True, voided_at__isnull=True)

    def minutes_booked(self):
        total = self.posted_bookings().aggregate(
            total=models.Sum("minutes")
        )["total"]
        return total or Decimal("0")

    def quantity_completed(self):
        """
        What has come off this operation, where the shift counted it.

        A run's progress through its routing: how many sacks have been
        cut against how many have been stitched. Bookings that did not
        count contribute nothing rather than zero, which is the same
        number and a different claim — so a caller wanting to know
        whether anybody counted asks `counted_bookings()`.
        """
        total = self.posted_bookings().aggregate(
            total=models.Sum("quantity_completed")
        )["total"]
        return total or Decimal("0")

    def counted_bookings(self):
        return self.posted_bookings().filter(quantity_completed__isnull=False)

    def feeds_from(self):
        """
        The operation before this one that somebody actually counted.

        Walked back rather than taken as the immediately preceding
        sequence, because an operation nobody counted is not an
        operation that made nothing — and treating it as zero would
        refuse every booking after it.
        """
        for earlier in self.work_order.operations.filter(
            sequence__lt=self.sequence
        ).order_by("-sequence"):
            if earlier.counted_bookings().exists():
                return earlier
        return None

    def check_booking(self, minutes, completed):
        """
        Whether this booking is physically possible.

        Three questions, all of which a shift can get wrong by typing:
        whether the machine can have run that long, whether the run can
        have made that much, and whether the operation before this one
        has fed it that much. The third is the one no general-purpose
        system asks, and the one that catches a shift booking against
        the wrong line: a sack cannot be stitched before it is cut.
        """
        order = self.work_order
        if self.planned_minutes > 0:
            allowed = self.planned_minutes * (
                Decimal("1") + (order.time_allowance_percent or Decimal("0"))
                / Decimal("100")
            )
            if self.minutes_booked() + minutes > allowed:
                raise ValidationError(
                    f"{self} was planned at {self.planned_minutes} minutes and "
                    f"allows {order.time_allowance_percent}% over. Booking "
                    f"{minutes} on top of {self.minutes_booked()} would take it "
                    f"past {allowed:.2f}. Raise the allowance on the order if "
                    "the machine really ran that long."
                )
        if completed is None:
            return
        made = order.item.to_stock_quantity(completed, order.uom)
        already = order.item.to_stock_quantity(
            self.quantity_completed(), order.uom
        )
        ceiling = order.maximum_output()
        if already + made > ceiling:
            raise ValidationError(
                f"{self} would have made {already + made} against a run for "
                f"{order.quantity_ordered} {order.uom}, which allows up to "
                f"{ceiling}."
            )
        # What the booked minutes could have made at this operation's own
        # rate. A line does run above its nominal speed — good polymer,
        # an experienced crew — so the same allowance the order gives to
        # time is given to speed, both being two sides of one rate being
        # approximate. Five times the rate is a typed figure, and it had
        # a line reading 556% performance.
        #
        # Setup produces nothing, which this is naturally tolerant of:
        # the minutes the output should have needed can only be smaller
        # than the minutes booked, so setup only widens the margin.
        rate = self.units_per_hour
        if rate:
            in_bom_units = order.uom.convert_to(
                already + made, order.bom.uom
            ) if order.uom_id != order.bom.uom_id else (already + made)
            should_have_taken = in_bom_units / rate * Decimal("60")
            allowed_minutes = (self.minutes_booked() + minutes) * (
                Decimal("1") + (order.time_allowance_percent or Decimal("0"))
                / Decimal("100")
            )
            if should_have_taken > allowed_minutes:
                raise ValidationError(
                    f"{self} runs at {rate} an hour, so "
                    f"{self.minutes_booked() + minutes} minutes could not have "
                    f"made {already + made}: that would have needed "
                    f"{should_have_taken:.1f} minutes. Either the quantity or "
                    "the time is mistyped, or the rate on the routing is wrong."
                )
        upstream = self.feeds_from()
        if upstream is not None:
            fed = order.item.to_stock_quantity(
                upstream.quantity_completed(), order.uom
            )
            if already + made > fed:
                raise ValidationError(
                    f"{self} would have made {already + made} from the "
                    f"{fed} that {upstream.name} has fed it. Nothing can leave "
                    "an operation that never went into it — check which "
                    "operation this shift was booked against."
                )

    def save(self, *args, **kwargs):
        if self.work_order.status != WorkOrderStatus.DRAFT:
            raise ValidationError(
                f"{self.work_order} is "
                f"{self.work_order.get_status_display().lower()}; its routing "
                "was frozen when it was released."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.work_order.status != WorkOrderStatus.DRAFT:
            raise ValidationError(
                f"{self.work_order} is "
                f"{self.work_order.get_status_display().lower()}; its routing "
                "cannot be changed after release."
            )
        return super().delete(*args, **kwargs)


def _post_entry(on_date, reference, memo, rows, balance_to=None):
    """
    Write one journal entry from signed amounts: positive is a debit.

    `balance_to` is the account that takes whatever the other rows come
    to, and it is not optional in spirit. Rounding a total and rounding
    its parts are different numbers: a blend of five materials whose
    values each carry a fraction of a paisa sums to one figure and
    rounds to another, and an entry built from both is out by a paisa
    and refused by the ledger — which is how this was found, on the
    first run with real prices in it. Giving the balancing side the sum
    of the rounded rows makes the entry balance by construction rather
    than by luck.

    Returns the entry and what the balancing account actually took, so
    the caller freezes the figure that is in the journal rather than
    the one it started with.

    Written out rather than put through `post_inventory_entry`, whose
    counterpart is goods received not invoiced going in and cost of
    sales going out. Neither is work in progress, and no argument to it
    spells one — this project has already posted a document backwards
    by bending that helper to a shape it did not have.
    """
    totals = {}
    for account, amount in rows:
        amount = round_money(amount)
        if not amount:
            continue
        totals[account] = totals.get(account, Decimal("0")) + amount
    balancing = Decimal("0")
    if balance_to is not None:
        balancing = -sum(totals.values(), Decimal("0"))
        if balancing:
            totals[balance_to] = totals.get(balance_to, Decimal("0")) + balancing
    rows = [(account, amount) for account, amount in totals.items() if amount]
    if not rows:
        return None, balancing
    entry = JournalEntry.objects.create(
        date=on_date, reference=reference or "", memo=memo[:255]
    )
    for account, amount in rows:
        if amount > 0:
            JournalLine.objects.create(
                entry=entry, account=account, debit=amount, description=memo[:255]
            )
        else:
            JournalLine.objects.create(
                entry=entry, account=account, credit=-amount, description=memo[:255]
            )
    entry.post()
    return entry, balancing


def _check_order_is_open_for(order, what):
    """
    Refuse to move money on a run that has been closed.

    A close sends everything left in work in progress to variance and
    nobody looks at the order again. Voiding a document against it
    afterwards puts money back into an account that is supposed to be
    empty, where it stays — which is exactly the sort of balance this
    module exists to make impossible. Reopening reverses the close
    first, and reopening is a posting rather than an edit.
    """
    if not order.is_open():
        raise ValidationError(
            f"{order} is {order.get_status_display().lower()}, so you cannot "
            f"{what}: the money would land in work in progress and stay there. "
            "Reopen the order first."
        )


class IssueDirection(models.TextChoices):
    ISSUE = "issue", "Issued to the run"
    RETURN = "return", "Returned to the store"


class MaterialIssue(AuditModel):
    """
    Material drawn from the store against a run, or handed back.

    A return is this document the other way round rather than a
    negative issue, and each of its lines names the issue line it
    reverses. That is not tidiness: material goes out at what taking it
    off the shelf costs, and by the time any of it comes back the shelf
    has moved. Returning it at today's average would hand the run a
    credit it never received and leave the difference in work in
    progress with nothing to explain it.
    """

    number = models.CharField(max_length=32, blank=True)
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="issues"
    )
    direction = models.CharField(
        max_length=8, choices=IssueDirection.choices, default=IssueDirection.ISSUE
    )
    issue_date = models.DateField()
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="material_issues"
    )
    memo = models.CharField(max_length=255, blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What this document actually moved, frozen when it posted. "
                  "The work-in-progress balance is a sum of these rather than a "
                  "recomputation, so it cannot come to a different answer from "
                  "the journal it was posted to.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="material_issue_number_unique",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft material issue {self.pk}"

    def sign(self):
        """+1 when material went out to the run, -1 when it came back."""
        return Decimal("1") if self.direction == IssueDirection.ISSUE else Decimal("-1")

    def is_voided(self):
        return self.voided_at is not None

    @transaction.atomic
    def post(self, memo=""):
        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        lines = list(self.lines.select_related("item", "uom", "lot"))
        if not lines:
            raise ValidationError("Cannot post a material issue with no lines.")
        if not self.work_order.is_open():
            raise ValidationError(
                f"{self.work_order} is "
                f"{self.work_order.get_status_display().lower()}; material can "
                "only move against a released order."
            )
        # Before anything reads the shelf: two issues can each find
        # enough polymer for a run and between them take more than there
        # is.
        lock_positions((line.item, self.warehouse) for line in lines)

        self.issue_date = to_date(self.issue_date)
        occurred_at = timezone.now()
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.material_issue", self.issue_date,
                name="Material Issues", prefix="MI-",
            )
        label = memo or self.memo or f"{self.get_direction_display()} {self.number}"

        total = Decimal("0")
        by_item = {}
        for line in lines:
            value = line.post(self, occurred_at, label)
            total += value
            by_item[line.item] = by_item.get(line.item, Decimal("0")) + value

        wip = ManufacturingSettings.account(
            "wip", "material is being issued to a run"
        )
        rows = [
            (inventory_account_for(item), -value * self.sign())
            for item, value in by_item.items()
        ]
        self.journal_entry, charged = _post_entry(
            self.issue_date, self.number, label, rows, balance_to=wip
        )
        self.posted = True
        self.posted_at = occurred_at
        # What work in progress actually took, to the paisa, rather than
        # the unrounded sum it was worked out from.
        self.posted_value = charged
        super().save(update_fields=[
            "number", "issue_date", "posted", "posted_at", "posted_value",
            "journal_entry", "updated_at",
        ])
        return self.journal_entry

    @transaction.atomic
    def void(self, on_date=None, memo=""):
        """Undo a posting that should not have happened at all."""
        if not self.posted:
            raise ValidationError(f"{self} is not posted.")
        if self.is_voided():
            raise ValidationError(f"{self} is already voided.")
        _check_order_is_open_for(self.work_order, "void this issue")
        # The relation is line-to-line: a return line names the issue
        # line it hands back, so the question is asked of this
        # document's lines rather than of the document.
        if self.lines.filter(
            returned_by__issue__posted=True,
            returned_by__issue__voided_at__isnull=True,
        ).exists():
            raise ValidationError(
                f"{self} has material returned against it. Void the return "
                "first, or the store would be crediting stock back against an "
                "issue that no longer exists."
            )
        on_date = to_date(on_date) or timezone.now().date()
        occurred_at = timezone.now()
        label = memo or f"Void of {self.number}"
        lines = list(self.lines.select_related("item", "uom", "lot"))
        lock_positions((line.item, self.warehouse) for line in lines)
        for line in lines:
            line.reverse(self, occurred_at, label)
        if self.journal_entry_id:
            self.voided_entry = self.journal_entry.create_reversal(
                entry_date=on_date, memo=label
            )
        self.voided_at = occurred_at
        super().save(update_fields=["voided_entry", "voided_at", "updated_at"])
        return self.voided_entry

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = MaterialIssue.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    f"Cannot modify {self} once it is posted. Void it and raise "
                    "another."
                )
        super().save(*args, **kwargs)


class MaterialIssueLine(AuditModel):
    issue = models.ForeignKey(
        MaterialIssue, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="Always positive. Which way it moves is the document's "
                  "direction, not the line's sign.",
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+"
    )
    lot = models.ForeignKey(
        "inventory.Lot", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    bin = models.ForeignKey(
        "inventory.StorageBin", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    returns_line = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="returned_by",
        help_text="On a return, the issue line this hands back. Required, "
                  "because the value coming back is the value that went out and "
                  "the shelf has moved since.",
    )
    unit_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What a unit was worth when it moved, frozen here. A return "
                  "reads it off the line it returns.",
    )
    stock_movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="The movement this wrote, so the reversal knows what to undo "
                  "rather than reconstructing it.",
    )
    line_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["issue", "line_number", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="material_issue_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.uom} {self.item.sku}"

    def stock_quantity(self):
        return self.item.to_stock_quantity(self.quantity, self.uom)

    def _check_may_be_drawn(self, issue):
        """
        A held batch does not go into a run — except into the run that
        exists to put it right.

        The exemption is exactly one batch wide. Without it a rework
        order cannot consume the roll it was raised for, which makes
        the whole disposition useless; widened by one inch it becomes
        the way every held batch gets used, because a rework order is
        easy to raise and quality holds are inconvenient. So the line
        must name the failed batch itself: a rework run drawing good
        stock of the same item is salvage on paper and a shortage in
        fact.
        """
        order = issue.work_order
        if order.rework_of_id and self.item_id == order.item_id:
            if self.lot_id != order.rework_of_id:
                raise ValidationError(
                    f"{order} is reworking {order.rework_of}, and this line "
                    f"draws {self.item} from "
                    f"{self.lot or 'no batch in particular'}. A rework run "
                    "consumes the batch it was raised for and no other — "
                    "drawing good stock instead books salvage the plant never "
                    "made."
                )
            return
        check_released(self.item, self.lot, action="go into a run")

    def post(self, issue, occurred_at, label):
        """Write the movement and return what it was worth, unsigned."""
        quantity = self.stock_quantity()
        if issue.direction == IssueDirection.ISSUE:
            check_available(
                self.item, issue.warehouse, quantity, lot=self.lot,
                action="issue",
            )
            self._check_may_be_drawn(issue)
            value = cost_of_removing(
                self.item, issue.warehouse, quantity, lot=self.lot
            )
            movement_quantity = -self.quantity
            movement_type = MovementType.ISSUE
        else:
            if self.returns_line_id is None:
                raise ValidationError(
                    f"{self}: a return must name the issue line it hands back. "
                    "Material comes back at what it went out at, and the shelf "
                    "has moved since."
                )
            if self.returns_line.issue.work_order_id != issue.work_order_id:
                raise ValidationError(
                    f"{self}: that issue line belongs to "
                    f"{self.returns_line.issue.work_order}, not to "
                    f"{issue.work_order}."
                )
            value = (self.returns_line.unit_cost or Decimal("0")) * quantity
            movement_quantity = self.quantity
            movement_type = MovementType.RECEIPT
        self.unit_cost = (
            (value / quantity).quantize(Decimal("0.000001"))
            if quantity else Decimal("0")
        )
        self.stock_movement = StockMovement.objects.create(
            item=self.item, warehouse=issue.warehouse,
            movement_type=movement_type, uom=self.uom,
            quantity=movement_quantity, unit_cost=self.unit_cost,
            lot=self.lot, bin=self.bin, occurred_at=occurred_at,
            reference=issue.number, notes=label[:255],
        )
        super().save(update_fields=["unit_cost", "stock_movement", "updated_at"])
        return value

    def reverse(self, issue, occurred_at, label):
        """Put the movement back, at the value it went out at."""
        movement = self.stock_movement
        if movement is None:
            return None
        return StockMovement.objects.create(
            item=self.item, warehouse=issue.warehouse,
            movement_type=(
                MovementType.RECEIPT if movement.quantity < 0 else MovementType.ISSUE
            ),
            uom=self.item.uom, quantity=-movement.quantity,
            unit_cost=self.unit_cost, lot=self.lot, bin=self.bin,
            occurred_at=occurred_at, reference=issue.number, notes=label[:255],
        )

    def save(self, *args, **kwargs):
        # The document refuses to be edited once posted and its lines
        # have to refuse with it. Guarding only the header is the shape
        # this project keeps copying: the quantity moves, the ledger
        # does not, and the two disagree for ever. `post()` writes
        # through `super().save()` and so is not caught by this.
        if self.issue.posted:
            raise ValidationError(
                f"Cannot modify a line on {self.issue}, which is posted. Void "
                "it and raise another."
            )
        self.item.check_uom(self.uom)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.issue.posted:
            raise ValidationError(
                f"Cannot delete a line on {self.issue}, which is posted. Void "
                "it and raise another."
            )
        return super().delete(*args, **kwargs)


class ProductionEntry(AuditModel):
    """
    Output booked off a run: good sacks, by-products, and failures.

    Booked at the planned cost frozen on the order, not at what the run
    has cost so far. A run is not finished when the first pallet comes
    off it, and pricing that pallet at the material issued to date would
    make the first sack of a run cost several times the last.

    Scrap is separate from by-product on purpose. A by-product is worth
    something and goes on a shelf; scrap is a thousand mis-stitched
    sacks that go in a skip. Both consume the run, and only one of them
    leaves anything behind.
    """

    number = models.CharField(max_length=32, blank=True)
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="entries"
    )
    entry_date = models.DateField()
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="production_entries"
    )
    quantity_produced = models.DecimalField(max_digits=18, decimal_places=4)
    quantity_scrapped = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"),
        help_text="Output that failed. It consumed the run and left nothing, so "
                  "it is written off rather than valued.",
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+"
    )
    lot = models.ForeignKey(
        "inventory.Lot", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="The batch this output is. Required when the item is tracked.",
    )
    bin = models.ForeignKey(
        "inventory.StorageBin", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    work_centre = models.ForeignKey(
        WorkCentre, null=True, blank=True, on_delete=models.PROTECT,
        related_name="production_entries",
        help_text="Which machine this came off, when a run spans more than one.",
    )
    memo = models.CharField(max_length=255, blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What this took out of work in progress, frozen when it "
                  "posted — output, by-products and scrap together.",
    )
    unit_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The order's planned cost, copied here when this posted. A "
                  "reader of this entry should not have to go and ask the order "
                  "what it was at the time.",
    )
    stock_movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    backflush_issue = models.ForeignKey(
        "MaterialIssue", null=True, blank=True, on_delete=models.PROTECT,
        related_name="backflushed_by", editable=False,
        help_text="The issue this entry drew for itself, on a run that "
                  "backflushes. Recorded rather than recomputed, because "
                  "voiding this entry has to put back exactly what it took "
                  "and the requirement will have moved by then.",
    )

    class Meta:
        ordering = ["-entry_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="production_entry_number_unique",
            ),
            models.CheckConstraint(
                check=Q(quantity_produced__gte=0) & Q(quantity_scrapped__gte=0),
                name="production_quantities_not_negative",
            ),
            # An entry that booked nothing and scrapped nothing is a
            # document saying nothing happened.
            models.CheckConstraint(
                check=Q(quantity_produced__gt=0) | Q(quantity_scrapped__gt=0),
                name="production_entry_books_something",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft production entry {self.pk}"

    def is_voided(self):
        return self.voided_at is not None

    def stock_quantity(self):
        return self.work_order.item.to_stock_quantity(
            self.quantity_produced, self.uom
        )

    def scrapped_stock_quantity(self):
        return self.work_order.item.to_stock_quantity(
            self.quantity_scrapped, self.uom
        )

    def consumed_output(self):
        """
        Output this entry ate material for: the good and the spoiled
        alike.

        A run that made six hundred good and spoiled fifty ate polymer
        for six hundred and fifty. Backflushing the good output alone
        under-issues by the difference and then reports it at close as
        a favourable material variance — the blend reading light when
        in fact the issue was never made.
        """
        return self.quantity_produced + self.quantity_scrapped

    def _backflush(self, label):
        """
        Draw what this output consumed, as its own issue.

        Its own document rather than movements written here, so that
        everything downstream — the work-in-progress balance, the
        material variance, `quantity_issued`, the void path — reads a
        backflushed run and a hand-issued one through exactly the same
        rows. A second way of getting material out of a store is a
        second set of bugs.
        """
        quantity = self.work_order.item.to_stock_quantity(
            self.consumed_output(), self.uom
        )
        rows = self.work_order.backflush_for(quantity)
        if not rows:
            return None
        issue = MaterialIssue.objects.create(
            work_order=self.work_order, direction=IssueDirection.ISSUE,
            issue_date=self.entry_date, warehouse=self.warehouse,
            memo=f"Backflushed by {self.number}"[:255],
        )
        for index, (component, wanted) in enumerate(rows, start=1):
            MaterialIssueLine.objects.create(
                issue=issue, item=component.item, quantity=wanted,
                uom=component.uom, line_number=index,
            )
        issue.post(memo=label)
        return issue

    @transaction.atomic
    def post(self, memo=""):
        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        order = self.work_order
        if not order.is_open():
            raise ValidationError(
                f"{order} is {order.get_status_display().lower()}; output can "
                "only be booked against a released order."
            )
        if order.planned_unit_cost is None:
            raise ValidationError(
                f"{order} has no planned cost, so there is nothing to value this "
                "output at. Release it first."
            )
        byproducts = list(self.byproducts.select_related("item", "uom"))
        lock_positions(
            [(order.item, self.warehouse)]
            + [(row.item, self.warehouse) for row in byproducts]
        )
        order.check_output(
            self.stock_quantity() + self.scrapped_stock_quantity()
        )

        self.entry_date = to_date(self.entry_date)
        occurred_at = timezone.now()
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.production", self.entry_date,
                name="Production Entries", prefix="PR-",
            )
        label = memo or self.memo or f"Production {self.number} on {order.number}"
        self.unit_cost = order.planned_unit_cost

        rows = []
        taken = Decimal("0")
        made = self.stock_quantity()
        if made:
            value = self.unit_cost * made
            self.stock_movement = StockMovement.objects.create(
                item=order.item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, uom=self.uom,
                quantity=self.quantity_produced, unit_cost=self.unit_cost,
                lot=self.lot, bin=self.bin, occurred_at=occurred_at,
                reference=self.number, notes=label[:255],
            )
            rows.append((inventory_account_for(order.item), value))
            taken += value

        for row in byproducts:
            value = row.post(self, occurred_at, label)
            rows.append((inventory_account_for(row.item), value))
            taken += value

        scrapped = self.scrapped_stock_quantity()
        if scrapped:
            value = self.unit_cost * scrapped
            rows.append((
                ManufacturingSettings.account(
                    "scrap", "a run has booked output that failed"
                ),
                value,
            ))
            taken += value

        wip = ManufacturingSettings.account(
            "wip", "output is being booked off a run"
        )
        self.journal_entry, released = _post_entry(
            self.entry_date, self.number, label, rows, balance_to=wip
        )
        self.posted = True
        self.posted_at = occurred_at
        # Work in progress was credited, so what came out of it is the
        # other sign of what the balancing row took.
        self.posted_value = -released
        # After the output, deliberately. The issue it raises posts
        # against this same run and reads the shelf, and a backflush
        # that fails for want of polymer must take the output booking
        # down with it rather than leaving a run that made something
        # out of nothing.
        if order.backflush:
            self.backflush_issue = self._backflush(label)
        super().save(update_fields=[
            "number", "entry_date", "posted", "posted_at", "posted_value",
            "unit_cost", "stock_movement", "journal_entry", "backflush_issue",
            "updated_at",
        ])
        return self.journal_entry

    @transaction.atomic
    def void(self, on_date=None, memo=""):
        """
        Take the output back off the shelf and put the cost back into
        the run.

        Written in the same sitting as `post()`. The sacks may already
        have shipped, in which case this drives the shelf negative and
        the warehouse's own rule on that decides whether it is allowed —
        which is the right place for that argument, not here.
        """
        if not self.posted:
            raise ValidationError(f"{self} is not posted.")
        if self.is_voided():
            raise ValidationError(f"{self} is already voided.")
        _check_order_is_open_for(self.work_order, "void this entry")
        on_date = to_date(on_date) or timezone.now().date()
        occurred_at = timezone.now()
        label = memo or f"Void of {self.number}"
        byproducts = list(self.byproducts.select_related("item", "uom"))
        lock_positions(
            [(self.work_order.item, self.warehouse)]
            + [(row.item, self.warehouse) for row in byproducts]
        )
        if self.stock_movement_id is not None:
            StockMovement.objects.create(
                item=self.work_order.item, warehouse=self.warehouse,
                movement_type=MovementType.ISSUE, uom=self.uom,
                quantity=-self.quantity_produced, unit_cost=self.unit_cost,
                lot=self.lot, bin=self.bin, occurred_at=occurred_at,
                reference=self.number, notes=label[:255],
            )
        for row in byproducts:
            row.reverse(self, occurred_at, label)
        # The reverse of the backflush, written in the same sitting as
        # the backflush itself. Voiding output that drew its own
        # material and leaving the material drawn would put the run's
        # whole consumption into variance for output that no longer
        # exists.
        if self.backflush_issue_id and not self.backflush_issue.is_voided():
            self.backflush_issue.void(on_date=on_date, memo=label)
        if self.journal_entry_id:
            self.voided_entry = self.journal_entry.create_reversal(
                entry_date=on_date, memo=label
            )
        self.voided_at = occurred_at
        super().save(update_fields=["voided_entry", "voided_at", "updated_at"])
        return self.voided_entry

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = ProductionEntry.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    f"Cannot modify {self} once it is posted. Void it and raise "
                    "another."
                )
        super().save(*args, **kwargs)


class ProductionByproduct(AuditModel):
    """
    Regrind, trim and loom waste actually collected off a run.

    Not the BOM's expectation — what the shift supervisor weighed. The
    gap between the two is half of where the polymer went.
    """

    entry = models.ForeignKey(
        ProductionEntry, on_delete=models.CASCADE, related_name="byproducts"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+"
    )
    lot = models.ForeignKey(
        "inventory.Lot", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    bin = models.ForeignKey(
        "inventory.StorageBin", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    unit_value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What this was taken into stock at, frozen when it posted.",
    )
    stock_movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    line_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["entry", "line_number", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="production_byproduct_positive"
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.uom} {self.item.sku}"

    def _valuation(self):
        """
        What a kilo of this is worth, from the BOM that named it.

        Read off the order's BOM rather than typed on the line, so two
        entries against one run cannot value the same regrind
        differently.
        """
        bom = self.entry.work_order.bom
        row = bom.byproducts.filter(item=self.item).first()
        if row is None:
            raise ValidationError(
                f"{self.item} is not a by-product of {bom}. A run can only give "
                "back what its bill of materials says it gives back — anything "
                "else is stock appearing from nowhere with a value nobody chose."
            )
        quantity = self.item.to_stock_quantity(self.quantity, self.uom)
        if quantity <= 0:
            return Decimal("0"), Decimal("0")
        if row.valuation == ByproductValuation.SHARE:
            # A share of what the run was planned to cost in materials,
            # frozen at release — not of what has been issued so far.
            # The running figure grows through the run, and the same
            # regrind would come back at one price on Tuesday and twice
            # that on Thursday.
            order = self.entry.work_order
            expected = row.quantity * order.bom.scale_for(
                order.quantity_ordered, order.uom
            )
            expected = self.item.to_stock_quantity(expected, row.uom)
            share = (order.planned_material_cost or Decimal("0")) * (
                row.cost_share_percent or Decimal("0")
            ) / Decimal("100")
            rate = share / expected if expected else Decimal("0")
            value = rate * quantity
        else:
            value = byproduct_value(row, self.quantity, None)
        return value, quantity

    def post(self, entry, occurred_at, label):
        value, quantity = self._valuation()
        self.unit_value = (
            (value / quantity).quantize(Decimal("0.000001"))
            if quantity else Decimal("0")
        )
        self.stock_movement = StockMovement.objects.create(
            item=self.item, warehouse=entry.warehouse,
            movement_type=MovementType.RECEIPT, uom=self.uom,
            quantity=self.quantity, unit_cost=self.unit_value,
            lot=self.lot, bin=self.bin, occurred_at=occurred_at,
            reference=entry.number, notes=label[:255],
        )
        super().save(update_fields=["unit_value", "stock_movement", "updated_at"])
        return self.unit_value * quantity

    def reverse(self, entry, occurred_at, label):
        if self.stock_movement_id is None:
            return None
        return StockMovement.objects.create(
            item=self.item, warehouse=entry.warehouse,
            movement_type=MovementType.ISSUE, uom=self.uom,
            quantity=-self.quantity, unit_cost=self.unit_value,
            lot=self.lot, bin=self.bin, occurred_at=occurred_at,
            reference=entry.number, notes=label[:255],
        )

    def save(self, *args, **kwargs):
        if self.entry.posted:
            raise ValidationError(
                f"Cannot modify a by-product on {self.entry}, which is posted. "
                "Void it and raise another."
            )
        self.item.check_uom(self.uom)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.posted:
            raise ValidationError(
                f"Cannot delete a by-product on {self.entry}, which is posted. "
                "Void it and raise another."
            )
        return super().delete(*args, **kwargs)


class TimeBooking(AuditModel):
    """
    A machine ran for a while against a run, and what that cost.

    Charged at the work centre's own rate and credited to conversion
    absorbed, which is a contra account against the power, wages and
    depreciation posted elsewhere. Its balance is the plant's
    over- or under-absorption — whether the machines ran as many hours
    as the standard costed them at.

    A booking with a quantity on it is also the run's progress through
    its routing: how many sacks have been cut against how many have been
    stitched. Optional, because a shift that books four hours on a loom
    and cannot say how many metres came off it has still told the truth
    about the four hours.
    """

    number = models.CharField(max_length=32, blank=True)
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="time_bookings"
    )
    operation = models.ForeignKey(
        WorkOrderOperation, on_delete=models.PROTECT, related_name="bookings",
        help_text="Which of the run's operations ran. Its work centre is what "
                  "sets the rate, so a booking cannot be made against a "
                  "machine this run never went near.",
    )
    booking_date = models.DateField(
        help_text="The day the shift is NAMED for, not the day the clock said. "
                  "A night shift running to six in the morning books all its "
                  "hours to the day before, or half a crew's work lands on the "
                  "wrong day and two shifts both look wrong. Derived from "
                  "`started_at` when that is given."
    )
    started_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Wall clock when the machine started. Given, it decides both "
                  "which shift this was and which day that shift belongs to, so "
                  "nobody has to work out the night shift's date by hand.",
    )
    shift = models.ForeignKey(
        "Shift", null=True, blank=True, on_delete=models.PROTECT,
        related_name="time_bookings",
        help_text="Which crew's slot. A plant that does not run shifts leaves "
                  "it empty and loses nothing but the by-shift reports.",
    )
    operators = models.ManyToManyField(
        "hr.Employee", blank=True, related_name="time_bookings",
        help_text="Who was on the machine. For attribution, not for costing: "
                  "the labour in the cost comes from the work centre's rate, "
                  "and adding these people's wages on top would charge the run "
                  "twice for the same crew.",
    )
    minutes = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Time on the machine, setup included. Always positive: a "
                  "booking made in error is voided, not negated.",
    )
    quantity_completed = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="What came off this operation in that time, in the run's "
                  "own unit. Blank where the shift did not count it.",
    )
    memo = models.CharField(max_length=255, blank=True)
    hourly_rate = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, editable=False,
        help_text="What the machine cost an hour when this posted, frozen "
                  "here. A work centre re-rated in April must not re-price a "
                  "shift that ran in March.",
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What this put into work in progress, frozen when it posted.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-booking_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="time_booking_number_unique",
            ),
            models.CheckConstraint(
                check=Q(minutes__gt=0), name="time_booking_minutes_positive"
            ),
            models.CheckConstraint(
                check=Q(quantity_completed__isnull=True)
                | Q(quantity_completed__gte=0),
                name="time_booking_quantity_not_negative",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft time booking {self.pk}"

    def is_voided(self):
        return self.voided_at is not None

    def hours(self):
        return self.minutes / Decimal("60")

    def resolve_shift(self):
        """
        Work out which shift this was, and which day it belongs to.

        The docstring on `booking_date` says a night shift books to the
        day before; this is the code that makes it true rather than a
        comment that hopes so.
        """
        if self.started_at is None:
            return
        if self.shift_id is None:
            self.shift = Shift.covering(self.started_at)
            if self.shift is None:
                raise ValidationError(
                    f"No active shift covers {timezone.localtime(self.started_at)}. "
                    "Either the shifts do not cover the whole day or this "
                    "booking's clock time is wrong."
                )
        elif not self.shift.covers(self.started_at):
            raise ValidationError(
                f"{self.shift} does not run at "
                f"{timezone.localtime(self.started_at).time()}."
            )
        self.booking_date = self.shift.shift_date_for(self.started_at)

    def check_crew(self):
        """
        Everybody named was employed here on the day.

        A shift booked against somebody who left in March is a typed
        employee number, and the yield-by-crew report it feeds is worse
        than no report: it is a report somebody will act on.
        """
        on_date = to_date(self.booking_date)
        for operator in self.operators.all():
            # `is_employed_on` is HR's own answer to this and already
            # knows about hire dates, terminations and the order of the
            # two. A second copy here would be one to keep in step.
            if not operator.is_employed_on(on_date):
                raise ValidationError(
                    f"{operator} was not employed here on {on_date}, and a "
                    "shift booked against somebody who was not there is a "
                    "typed employee number."
                )

    @transaction.atomic
    def post(self, memo=""):
        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        if self.operation.work_order_id != self.work_order_id:
            raise ValidationError(
                f"{self.operation} belongs to {self.operation.work_order}, not "
                f"to {self.work_order}."
            )
        if not self.work_order.is_open():
            raise ValidationError(
                f"{self.work_order} is "
                f"{self.work_order.get_status_display().lower()}; time can only "
                "be booked against a released order."
            )
        self.operation.check_booking(self.minutes, self.quantity_completed)
        self.resolve_shift()
        self.check_crew()
        self.booking_date = to_date(self.booking_date)
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.time_booking", self.booking_date,
                name="Time Bookings", prefix="TB-",
            )
        label = memo or self.memo or (
            f"{self.operation.name} on {self.operation.work_centre.code}, "
            f"{self.number}"
        )
        self.hourly_rate = self.operation.work_centre.conversion_rate_per_hour()
        value = self.hours() * self.hourly_rate
        if value:
            self.journal_entry, charged = _post_entry(
                self.booking_date, self.number, label,
                [(
                    ManufacturingSettings.account(
                        "conversion_absorbed", "machine time is being charged to a run"
                    ),
                    -value,
                )],
                balance_to=ManufacturingSettings.account(
                    "wip", "machine time is being charged to a run"
                ),
            )
            self.posted_value = charged
        else:
            # A plant that has chosen not to absorb conversion books the
            # hours and posts nothing. The hours are still worth having:
            # they are what the time variance is measured from.
            self.posted_value = Decimal("0")
        self.posted = True
        self.posted_at = timezone.now()
        super().save(update_fields=[
            "number", "booking_date", "shift", "hourly_rate", "posted",
            "posted_at", "posted_value", "journal_entry", "updated_at",
        ])
        return self.journal_entry

    @transaction.atomic
    def void(self, on_date=None, memo=""):
        """Take the hours and their cost back off the run."""
        if not self.posted:
            raise ValidationError(f"{self} is not posted.")
        if self.is_voided():
            raise ValidationError(f"{self} is already voided.")
        _check_order_is_open_for(self.work_order, "void this booking")
        on_date = to_date(on_date) or timezone.now().date()
        if self.journal_entry_id:
            self.voided_entry = self.journal_entry.create_reversal(
                entry_date=on_date, memo=memo or f"Void of {self.number}"
            )
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_entry", "voided_at", "updated_at"])
        return self.voided_entry

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = TimeBooking.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    f"Cannot modify {self} once it is posted. Void it and book "
                    "again."
                )
        super().save(*args, **kwargs)
