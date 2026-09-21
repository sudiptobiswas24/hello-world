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
from apps.inventory.availability import check_available
from apps.inventory.costing import cost_of_removing
from apps.inventory.locking import lock_positions
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.inventory.valuation import inventory_account_for

from .bom import BillOfMaterials, ByproductValuation, byproduct_value, planned_cost


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
        "variance": "production variance",
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
    days_per_week = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("7"),
        help_text="Days a week it runs. Deliberately a number rather than a "
                  "calendar: a public holiday is a company-wide fact and this "
                  "module has no business owning one.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                check=Q(available_hours_per_day__gt=0)
                & Q(available_hours_per_day__lte=24),
                name="work_centre_hours_in_a_day",
            ),
            models.CheckConstraint(
                check=Q(days_per_week__gt=0) & Q(days_per_week__lte=7),
                name="work_centre_days_in_a_week",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

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
    scheduled_start = models.DateField(null=True, blank=True)
    scheduled_end = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=WorkOrderStatus.choices,
        default=WorkOrderStatus.DRAFT,
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
        the specification said. This is the figure the close sends to
        variance, and it goes on reading the same after the close —
        which is what makes it the right number to explain a finished
        run by, and the wrong one to report as a balance.
        """
        return self.material_cost() - self.output_value()

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
        if not self.bom.is_active:
            raise ValidationError(f"{self.bom} is not active.")
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
        self.planned_unit_cost = (
            (plan.net / stock_quantity).quantize(Decimal("0.000001"))
            if stock_quantity else Decimal("0")
        )
        self.status = WorkOrderStatus.RELEASED
        self.released_at = timezone.now()
        super().save(update_fields=[
            "number", "planned_unit_cost", "planned_material_cost", "routing",
            "status", "released_at", "updated_at",
        ])
        return self

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
            self.close_entry, _ = _post_entry(
                on_date, self.number, label,
                [(
                    ManufacturingSettings.account("wip", "a run is being closed"),
                    -balance,
                )],
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

    def post(self, issue, occurred_at, label):
        """Write the movement and return what it was worth, unsigned."""
        quantity = self.stock_quantity()
        if issue.direction == IssueDirection.ISSUE:
            check_available(
                self.item, issue.warehouse, quantity, lot=self.lot,
                action="issue",
            )
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
        super().save(update_fields=[
            "number", "entry_date", "posted", "posted_at", "posted_value",
            "unit_cost", "stock_movement", "journal_entry", "updated_at",
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
