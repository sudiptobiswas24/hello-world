"""
What the plan proposes, and why each line of it exists.

A planned order is a suggestion and nothing else. It holds no stock,
posts no entry and commits no money, and that is the point: material
requirements planning is a proposal a person reads, argues with, and
either firms or throws away. A system that turned its own arithmetic
straight into purchase orders would be buying polymer on the strength
of a sales forecast nobody had looked at.

**Every planned order says what asked for it.** The `PlannedDemand`
rows underneath it are not an audit nicety, they are the whole
usability of the thing. A planner handed "buy 4,000 kg of PP on the
14th" has no way to judge it; handed "3,000 kg because SO-2026-00017
line 1 is due on the 21st, 1,000 kg to hold safety stock, rounded up
to a 4,000 kg bag multiple" they can, in a few seconds, decide whether
the sales order is real.

**A run is a snapshot, not a living list.** Planning is re-run when
anything changes, and each run records what it proposed on the day it
proposed it. Suggestions from an old run are history — they are not
quietly rewritten, and they are not counted as supply, because nothing
in the world happened when they were written. Only firming one makes
a fact, and what it makes is a work order or a requisition, which are
the things that already know how to be facts.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel
from apps.inventory.models import Item, Warehouse


class PlanningSettings(AuditModel):
    """
    The assumptions a plan is allowed to make when nothing else says.

    Every one of these is a fallback for a missing fact, and each is
    stated rather than defaulted in code so that a plan built on a
    guess can be read back as one.
    """

    horizon_days = models.PositiveIntegerField(
        default=90,
        help_text="How far ahead to plan. Demand beyond this is left alone: an "
                  "order for a date further out than anything can be bought or "
                  "made for is not a shortage yet, and reporting it as one "
                  "fills the list with noise.",
    )
    default_buy_lead_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Used when no agreed vendor price names a lead time. Left "
                  "empty, an item with no agreed lead time refuses to be "
                  "planned rather than being planned as if it arrived the day "
                  "it was ordered.",
    )
    default_make_lead_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Used when a bill of materials has no routing, so nothing "
                  "can compute how long a run takes.",
    )
    queue_days = models.PositiveIntegerField(
        default=0,
        help_text="Added to every computed run time to allow for waiting for a "
                  "machine. A stated allowance, because nothing here schedules "
                  "against a machine's real backlog.",
    )
    working_days = models.CharField(
        max_length=7, default="1234567",
        help_text="Which days this plant works, as ISO weekday numbers — 1 for "
                  "Monday through 7 for Sunday. Run times are counted against "
                  "this; a vendor's quoted lead time is not, because their "
                  "weekends are already inside the number they quoted.",
    )
    holiday_region = models.CharField(
        max_length=32, blank=True,
        help_text="Which public holiday list shuts this plant. Blank takes the "
                  "company-wide one only.",
    )
    requisition_requester = models.ForeignKey(
        "core.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Whose name a firmed buy is raised in. A requisition with no "
                  "requester is a request from nobody, which no approver can "
                  "act on.",
    )

    class Meta:
        verbose_name_plural = "planning settings"

    def __str__(self):
        return "Planning settings"

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()


class PlannedOrderKind(models.TextChoices):
    MAKE = "make", "Make"
    BUY = "buy", "Buy"


class PlannedOrderStatus(models.TextChoices):
    SUGGESTED = "suggested", "Suggested"
    FIRMED = "firmed", "Firmed"
    CANCELLED = "cancelled", "Cancelled"


class DemandSource(models.TextChoices):
    SALES = "sales", "Sales order"
    WORK_ORDER = "work_order", "Open work order"
    PLANNED = "planned", "Another planned order"
    SAFETY = "safety", "Safety stock"


class PlanningRun(AuditModel):
    """
    One pass of the planner over one warehouse, kept as it stood.

    Per warehouse because netting across shelves is a lie a planner
    pays for: polymer in the Hyderabad godown does not cover a run in
    Nagpur, and a plan that adds them reports no shortage until the
    lorry does not arrive.
    """

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="planning_runs"
    )
    planned_on = models.DateField(
        help_text="The date the plan treats as today. Everything due before it "
                  "is already late.",
    )
    horizon_end = models.DateField()
    ran_at = models.DateTimeField(default=timezone.now, editable=False)
    cut_links = models.TextField(
        blank=True, editable=False,
        help_text="Bill-of-materials links this run had to break to give every "
                  "item a level, one per line. A cut link means that item's "
                  "demand was netted against stock and firm supply only.",
    )
    deferred_demand = models.TextField(
        blank=True, editable=False,
        help_text="Dependent demand this run could not net, because the item it "
                  "was for had already been planned. A consequence of a cut "
                  "link, and the one thing a planner must read before trusting "
                  "the rest.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-ran_at", "-id"]

    def __str__(self):
        # The time, not just the date. Planning is re-run whenever
        # anything changes, so several runs a day against one warehouse
        # is the normal case and a name that cannot tell them apart is
        # a list of rows nobody can attribute.
        return (
            f"Plan {self.pk} for {self.warehouse} on {self.planned_on}"
            f" ({timezone.localtime(self.ran_at):%H:%M})"
        )

    def suggestions(self):
        return self.orders.filter(status=PlannedOrderStatus.SUGGESTED)

    def late(self):
        """Suggestions that needed starting before the day they were planned."""
        return [order for order in self.orders.all() if order.is_late()]

    def lapsed(self):
        """
        Suggestions firmed into a document that has since been
        cancelled.

        The one thing a planner cannot see from either end: the plan
        calls them handled and the shop floor has no run for them.
        """
        return [order for order in self.orders.all() if order.has_lapsed()]

    def is_complete(self):
        """
        Whether everything this run found a need for was also netted.

        False means a cut link sent dependent demand to an item that
        had already been planned, and `deferred_demand` says which. A
        run that is not complete is still useful — it is what the plant
        has — but it understates, and nothing should read it as the
        whole answer.
        """
        return not self.deferred_demand


class PlannedOrder(AuditModel):
    """
    Make this much of this, starting then, or the date it is for will
    not hold.
    """

    run = models.ForeignKey(PlanningRun, on_delete=models.CASCADE, related_name="orders")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="planned_orders")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=8, choices=PlannedOrderKind.choices)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="In the item's stocking unit, which is the unit the netting "
                  "was done in. A plan that mixes a sales line's bales with a "
                  "bill of materials' kilos nets neither.",
    )
    needed_by = models.DateField(help_text="The date the shortage bites.")
    release_on = models.DateField(
        help_text="`needed_by` less the lead time. A date in the past is not "
                  "moved forward to today: how late the plan already is, is "
                  "the number a planner needs.",
    )
    lead_days = models.PositiveIntegerField()
    level = models.PositiveIntegerField(
        default=0, help_text="Low-level code; the order this was planned in.",
    )
    bom = models.ForeignKey(
        "manufacturing.BillOfMaterials", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
        help_text="The bill of materials a make was exploded against, frozen "
                  "because it is computed from a specification and the "
                  "specification will change.",
    )
    vendor = models.ForeignKey(
        "core.Party", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Who a buy was priced and lead-timed against.",
    )
    rounded_up_by = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"),
        help_text="How much of this quantity nothing asked for: what rounding "
                  "the order up added, to a whole bag or pallet or to the "
                  "precision a quantity is stored at. Shown rather than buried, "
                  "because it is stock the plan chose to carry.",
    )
    status = models.CharField(
        max_length=12, choices=PlannedOrderStatus.choices,
        default=PlannedOrderStatus.SUGGESTED,
    )
    work_order = models.ForeignKey(
        "manufacturing.WorkOrder", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="planned_from", editable=False,
    )
    requisition_line = models.ForeignKey(
        "purchasing.PurchaseRequisitionLine", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="planned_from", editable=False,
    )
    firmed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["run", "level", "needed_by", "item__sku", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0), name="planned_order_quantity_positive"
            ),
        ]

    def __str__(self):
        return (
            f"{self.get_kind_display()} {self.quantity} {self.item.uom} "
            f"{self.item.sku} by {self.needed_by}"
        )

    def is_late(self):
        """Whether this needed starting before the plan was even run."""
        return self.release_on < self.run.planned_on

    def days_late(self):
        if not self.is_late():
            return 0
        return (self.run.planned_on - self.release_on).days

    # Rounding smaller than this is the last decimal place of the
    # stored quantity and nothing else. It is recorded, because the
    # reasons have to add up to the order exactly, and it is not said
    # out loud: a plan that appends "0.0001 added by rounding" to
    # twenty-two lines out of twenty-six has taught its reader to skip
    # the line by the third one.
    WORTH_SAYING = Decimal("0.0001")

    def explanation(self):
        """The sentence a planner reads instead of the number."""
        parts = [f"{demand.describe()}" for demand in self.demands.all()]
        if self.rounded_up_by > self.WORTH_SAYING:
            parts.append(f"{self.rounded_up_by} added by rounding the order up")
        return "; ".join(parts) or "nothing recorded"

    # -- firming --------------------------------------------------------

    def firmed_into(self):
        """
        The document this became, if it still stands.

        Derived rather than trusted from the status, because the
        document can be cancelled and nothing tells the suggestion. A
        planned order reading "firmed" with a cancelled run behind it
        is the worst of both: the plan will not suggest it again, and
        nothing is making it.
        """
        from apps.manufacturing.orders import WorkOrderStatus

        if self.work_order_id and self.work_order.status != WorkOrderStatus.CANCELLED:
            return self.work_order
        if self.requisition_line_id:
            from apps.purchasing.models import RequisitionStatus

            dead = (RequisitionStatus.REJECTED, RequisitionStatus.CANCELLED)
            if self.requisition_line.requisition.status not in dead:
                return self.requisition_line
        return None

    def has_lapsed(self):
        """Firmed, and the thing it was firmed into is gone."""
        return (
            self.status == PlannedOrderStatus.FIRMED
            and self.firmed_into() is None
        )

    def _check_firmable(self):
        standing = self.firmed_into()
        if standing is not None:
            raise ValidationError(
                f"{self} has already been firmed into {standing}."
            )
        if self.status == PlannedOrderStatus.CANCELLED:
            raise ValidationError(f"{self} was cancelled. Re-run the plan.")
        newer = PlanningRun.objects.filter(
            warehouse_id=self.warehouse_id, ran_at__gt=self.run.ran_at
        ).exists()
        if newer:
            raise ValidationError(
                f"{self} comes from the plan of {self.run.planned_on}, and a "
                "later one has been run since. Firming from a stale plan is "
                "how the same shortage gets ordered twice: re-run the plan "
                "and firm from that."
            )

    @transaction.atomic
    def firm(self, requested_by=None):
        """
        Turn the suggestion into the document that can actually be
        acted on, and stop suggesting it.

        A make becomes a draft work order, not a released one. Release
        freezes the requirements and the planned cost against the shelf
        as it stands, and doing that on a planner's behalf at a date
        they have not yet agreed to is exactly the kind of fact this
        system is careful not to invent.

        A suggestion whose run was later cancelled may be firmed
        again. It is the mirror of cancelling the run: the fact went
        away, so the suggestion is a suggestion once more, and
        refusing on the strength of a status that no longer describes
        anything would leave the shortage with nothing making it and
        nothing proposing it.
        """
        self._check_firmable()
        if self.kind == PlannedOrderKind.MAKE:
            made = self._firm_make()
        else:
            made = self._firm_buy(requested_by)
        self.status = PlannedOrderStatus.FIRMED
        self.firmed_at = timezone.now()
        self.save(update_fields=[
            "status", "firmed_at", "work_order", "requisition_line", "updated_at",
        ])
        return made

    def _firm_make(self):
        from apps.manufacturing.orders import WorkOrder

        if self.bom is None:
            raise ValidationError(
                f"{self} has no bill of materials, so there is nothing to raise "
                "a run against."
            )
        pegged = [d for d in self.demands.all() if d.sales_order_line_id]
        self.work_order = WorkOrder.objects.create(
            item=self.item,
            bom=self.bom,
            quantity_ordered=self.quantity,
            uom=self.item.uom,
            warehouse=self.warehouse,
            routing=self.bom.routing,
            scheduled_start=self.release_on,
            scheduled_end=self.needed_by,
            # Only when one customer line asked for it. A run covering
            # three lines belongs to none of them, and picking the first
            # would make the coverage report say something false about
            # the other two.
            sales_order_line=(
                pegged[0].sales_order_line if len(pegged) == 1 else None
            ),
            notes=f"Planned {self.run.planned_on}: {self.explanation()}",
        )
        return self.work_order

    def _firm_buy(self, requested_by=None):
        from apps.purchasing.models import (
            PurchaseRequisition,
            PurchaseRequisitionLine,
            RequisitionStatus,
        )

        requester = requested_by or PlanningSettings.get().requisition_requester
        if requester is None:
            raise ValidationError(
                "Nobody is named to raise a purchase requisition. Set a "
                "requester in the planning settings, or pass one."
            )
        requisition = PurchaseRequisition.objects.filter(
            status=RequisitionStatus.DRAFT,
            requested_by=requester,
            needed_by=self.release_on,
        ).first() or PurchaseRequisition.objects.create(
            requested_by=requester,
            request_date=self.run.planned_on,
            needed_by=self.release_on,
            justification=f"Planned {self.run.planned_on}.",
        )
        self.requisition_line = PurchaseRequisitionLine.objects.create(
            requisition=requisition,
            item=self.item,
            uom=self.item.uom,
            quantity=self.quantity,
            suggested_vendor=self.vendor,
        )
        return self.requisition_line

    def cancel(self):
        """
        Say no to a suggestion without waiting for the next run to
        forget it.

        A firmed order is not cancelled from here: the work order or
        the requisition is the fact now, and cancelling the suggestion
        that led to it would leave the fact standing with nothing
        pointing at it. Once that document is itself cancelled there
        is nothing left to point at, and the suggestion can be
        answered like any other.
        """
        standing = self.firmed_into()
        if standing is not None:
            raise ValidationError(
                f"{self} has been firmed into {standing}. Cancel that instead."
            )
        self.status = PlannedOrderStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])


class PlannedDemand(AuditModel):
    """
    One reason a planned order exists, and how much of it that reason
    accounts for.

    Several per order is the normal case and the interesting one: a
    single run covering two customers and a safety top-up is the
    ordinary shape of a plant's week, and the only way to argue with
    the run is to see all three.
    """

    planned_order = models.ForeignKey(
        PlannedOrder, on_delete=models.CASCADE, related_name="demands"
    )
    source = models.CharField(max_length=16, choices=DemandSource.choices)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="How much of the planned order this reason accounts for, in "
                  "the item's stocking unit.",
    )
    needed_by = models.DateField()
    sales_order_line = models.ForeignKey(
        "sales.SalesOrderLine", null=True, blank=True, on_delete=models.CASCADE,
        related_name="planned_demands",
    )
    work_order = models.ForeignKey(
        "manufacturing.WorkOrder", null=True, blank=True, on_delete=models.CASCADE,
        related_name="planned_demands",
    )
    parent = models.ForeignKey(
        PlannedOrder, null=True, blank=True, on_delete=models.CASCADE,
        related_name="children",
        help_text="The planned order whose components this demand is part of.",
    )
    line_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["planned_order", "line_number", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name="planned_demand_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} for {self.describe()}"

    def describe(self):
        if self.source == DemandSource.SALES and self.sales_order_line_id:
            line = self.sales_order_line
            return (
                f"{self.quantity} for {line.order.number or 'a draft order'} "
                f"due {self.needed_by}"
            )
        if self.source == DemandSource.WORK_ORDER and self.work_order_id:
            return f"{self.quantity} for {self.work_order} due {self.needed_by}"
        if self.source == DemandSource.PLANNED and self.parent_id:
            return f"{self.quantity} for planned {self.parent} due {self.needed_by}"
        if self.source == DemandSource.SAFETY:
            return f"{self.quantity} to hold safety stock"
        return f"{self.quantity} due {self.needed_by}"
