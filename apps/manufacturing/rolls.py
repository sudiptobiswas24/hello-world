"""
A roll of fabric, which the ledger counts in kilos and the plant talks
about in metres.

This is the unit trap this codebase has walked into three times, and
it is the one the global unit-of-measure graph cannot get out of. A
kilogramme converts to a tonne by a constant; a kilogramme of woven
fabric converts to a metre by a factor that depends on how heavy the
fabric is and how wide the tube is, and both of those are properties
of the roll. Two rolls of the same item, off the same loom, on the
same day, hold different numbers of metres, and no factor written
against the item can say so.

**Weight and length are both measured; the GSM is what they say.** The
loom counts metres off a roller and the scale weighs the roll, and the
fabric's real weight per square metre falls out of the two. That is
the direction the causation runs and it is also free: every roll is
weighed and metered anyway, so the plant gets a grammage measurement
on every single roll without a laboratory and without an inspector.
Deriving the weight from a nominal GSM instead would put a
specification number into the stock ledger — a figure describing what
the loom was set to rather than what came off it.

**The conversion is the roll's own, never the specification's.** A
roll that came off heavy holds fewer metres than the nominal figure
says, and a cutting table handed the nominal figure runs short in the
middle of a customer's order. `metres_per_kg()` on the specification
answers "what should this fabric do"; `metres_per_kg()` on the roll
answers "how much is on this particular roll", and only the second one
may be used to promise anybody anything.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel

GRAMMES_PER_KG = Decimal("1000")
MM_PER_M = Decimal("1000")
ONE_HUNDRED = Decimal("100")


class FabricRoll(AuditModel):
    """
    One roll off a loom: how wide, how long, and how much it weighs.

    A roll is a batch, so it is a `Lot` — not a parallel stock record
    of its own. Everything the inventory module already does for a
    batch, from valuation to expiry to the genealogy of what went into
    it, keeps working, and this adds only the three measurements a
    woven fabric roll has and a bag of granule does not.
    """

    lot = models.OneToOneField(
        "inventory.Lot", on_delete=models.CASCADE, related_name="fabric_roll",
        help_text="The batch this roll is. One roll, one batch — a roll is "
                  "the unit the plant moves, cuts and traces.",
    )
    specification = models.ForeignKey(
        "FabricSpecification", null=True, blank=True, on_delete=models.PROTECT,
        related_name="rolls",
        help_text="What it was meant to be, for reading the measurements "
                  "against. A roll with no specification is still a roll; it "
                  "simply has nothing to be heavy or light against.",
    )
    entry = models.ForeignKey(
        "ProductionEntry", null=True, blank=True, on_delete=models.PROTECT,
        related_name="rolls",
        help_text="The booking that wound it, where there is one.",
    )
    width_mm = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Lay-flat width as wound, in millimetres. The tube's own "
                  "width, not twice it: the doubling is the weave's business.",
    )
    length_m = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Running metres off the loom's counter.",
    )
    net_weight_kg = models.DecimalField(
        max_digits=12, decimal_places=3,
        help_text="What the scale said, core excluded.",
    )
    core_weight_kg = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0"),
        help_text="The tube it is wound on. Recorded so that a gross weight "
                  "off the floor scale can be reconciled to the net one the "
                  "ledger holds.",
    )
    is_tubular = models.BooleanField(
        default=True,
        help_text="A tube laid flat is two thicknesses of fabric and a square "
                  "metre of it weighs twice what the GSM says. Getting this "
                  "wrong halves or doubles every grammage the plant reads.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["lot__item", "lot__code"]
        constraints = [
            models.CheckConstraint(
                check=Q(width_mm__gt=0) & Q(length_m__gt=0)
                & Q(net_weight_kg__gt=0),
                name="fabric_roll_is_a_real_roll",
            ),
            models.CheckConstraint(
                check=Q(core_weight_kg__gte=0),
                name="fabric_roll_core_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.lot} — {self.length_m} m x {self.width_mm} mm"

    # -- what it is ------------------------------------------------------

    def layers(self):
        """A tube laid flat is two thicknesses; flat cloth is one."""
        return Decimal("2") if self.is_tubular else Decimal("1")

    def width_m(self):
        return self.width_mm / MM_PER_M

    def area_sqm(self):
        """Fabric area, both layers of a tube counted."""
        return self.width_m() * self.length_m * self.layers()

    def gross_weight_kg(self):
        return self.net_weight_kg + self.core_weight_kg

    def implied_gsm(self):
        """
        What this roll actually weighs per square metre.

        Measured, not specified. The scale and the loom's counter both
        already exist, so this is a grammage reading on every roll the
        plant makes at no cost at all — where a laboratory gives one
        reading per batch if somebody remembers to ask for it.
        """
        area = self.area_sqm()
        if area <= 0:
            return Decimal("0")
        return (self.net_weight_kg * GRAMMES_PER_KG / area).quantize(
            Decimal("0.0001")
        )

    def metres_per_kg(self):
        """
        This roll's own conversion, which is the only one that may be
        used to promise anybody anything.

        The specification's figure says what the fabric should do. A
        roll that came off four per cent heavy holds four per cent
        fewer metres, and a cutting table handed the nominal number
        runs short in the middle of a customer's order.
        """
        if self.net_weight_kg <= 0:
            return Decimal("0")
        return (self.length_m / self.net_weight_kg).quantize(Decimal("0.000001"))

    def metres_for(self, kilos):
        """How many metres `kilos` off this roll comes to."""
        return (Decimal(kilos) * self.metres_per_kg()).quantize(Decimal("0.01"))

    def kilos_for(self, metres):
        """What to take off the shelf to get `metres` of this roll."""
        rate = self.metres_per_kg()
        if rate <= 0:
            return Decimal("0")
        return (Decimal(metres) / rate).quantize(Decimal("0.001"))

    # -- how it reads against what it was meant to be --------------------

    def target_gsm(self):
        if self.specification_id is None:
            return None
        return self.specification.target_gsm or self.specification.gsm()

    def gsm_deviation_percent(self):
        """How far off the roll came, as a percentage of the target."""
        target = self.target_gsm()
        if not target:
            return None
        return (self.implied_gsm() - target) / target * ONE_HUNDRED

    def is_within_tolerance(self):
        """
        Whether the scale and the counter agree with the specification.

        None when there is nothing to judge it against, which is not
        the same answer as yes — an uninspected roll is not a passed
        roll, and this follows the same rule the quality module does.
        """
        deviation = self.gsm_deviation_percent()
        if deviation is None:
            return None
        return abs(deviation) <= self.specification.gsm_tolerance_percent

    # -- guards ----------------------------------------------------------

    def clean(self):
        self._check_against_entry()

    def save(self, *args, **kwargs):
        # On save rather than only in clean(), because rolls are
        # written by shop-floor code and Django never calls full_clean
        # for you — the lesson `GoodsReceiptLine` taught this codebase
        # by checking a quantity that nothing validated.
        self._check_against_entry()
        super().save(*args, **kwargs)

    def _check_against_entry(self):
        """
        A roll booked against a production entry is that entry's
        output, weighed.

        Both halves are checked because both mistakes put a roll into
        stock that the ledger disagrees with. A roll pointing at an
        entry that booked a different batch traces to the wrong run
        for ever; a roll whose weight does not match what the entry
        booked means the shelf and the scale have already parted
        company on day one.
        """
        if self.specification_id and self.specification.fabric_item_id != self.lot.item_id:
            raise ValidationError(
                f"{self.specification} is for "
                f"{self.specification.fabric_item} and this roll is "
                f"{self.lot.item}. A roll read against another fabric's "
                "specification is heavy or light against nothing."
            )
        if self.entry_id is None:
            return
        entry = self.entry
        if entry.lot_id != self.lot_id:
            raise ValidationError(
                f"{entry} booked {entry.lot or 'no batch'} and this roll is "
                f"{self.lot}. A roll is the batch its own booking made, or the "
                "genealogy points at the wrong run for ever."
            )
        booked = entry.stock_quantity()
        if abs(booked - self.net_weight_kg) > Decimal("0.001"):
            raise ValidationError(
                f"{entry} booked {booked} and this roll weighs "
                f"{self.net_weight_kg}. The shelf and the scale have to agree "
                "at the moment the roll is made; they will not get closer "
                "later."
            )


# -- what is on the shelf, in the unit the floor asks in -----------------


def rolls_at(item, warehouse):
    """
    Every roll of this fabric on this shelf, with what is left of it.

    Read off the stock ledger rather than off the roll, because a roll
    is cut. Its `length_m` is what came off the loom and does not
    change; what is left is a quantity of stock, and the metres left
    are that quantity through this roll's own conversion.
    """
    from apps.inventory.tracking import lots_at

    found = []
    for lot, quantity in lots_at(item, warehouse):
        roll = getattr(lot, "fabric_roll", None)
        if roll is None:
            continue
        found.append((roll, quantity, roll.metres_for(quantity)))
    return found


def metres_on_hand(item, warehouse):
    """
    How many metres of this fabric are on this shelf.

    The number the cutting table asks for and the one the unit-of-
    measure graph cannot answer, because every roll converts at its
    own rate. Summed roll by roll at each roll's own weight per metre,
    never at a nominal figure applied to the total.
    """
    return sum(
        (metres for _roll, _quantity, metres in rolls_at(item, warehouse)),
        Decimal("0"),
    )


def unrolled_stock(item, warehouse):
    """
    Fabric on the shelf that no roll accounts for.

    Returned rather than ignored. Stock in a roll-tracked item with no
    roll behind it converts to no metres at all, so a cutting table
    reading `metres_on_hand` is being told about less fabric than the
    plant owns — and silently, which is the worst way to be short.
    """
    from apps.inventory.tracking import lots_at

    total = Decimal(item.on_hand_at(warehouse))
    rolled = sum(
        (quantity for _roll, quantity, _metres in rolls_at(item, warehouse)),
        Decimal("0"),
    )
    return total - rolled


def weighed_gsm(work_order):
    """
    What a run's fabric actually came off at, averaged over its rolls.

    A second source for the measurement that `explain.py` joins to the
    material variance, and the one that does not depend on anybody
    inspecting anything: every roll is weighed and metered, so a run
    that ate more polymer than the specification said can be read
    against the grammage of its own output whether or not a sample
    ever reached a laboratory.
    """
    rolls = [
        roll for roll in FabricRoll.objects.filter(
            entry__work_order=work_order, entry__posted=True,
            entry__voided_at__isnull=True,
        ).select_related("specification", "entry")
    ]
    if not rolls:
        return None
    area = sum((roll.area_sqm() for roll in rolls), Decimal("0"))
    weight = sum((roll.net_weight_kg for roll in rolls), Decimal("0"))
    if area <= 0:
        return None
    targets = [roll.target_gsm() for roll in rolls if roll.target_gsm()]
    # Weighted by area, not averaged across rolls: a four-metre remnant
    # and a two-thousand-metre roll are not one reading each.
    average = (weight * GRAMMES_PER_KG / area).quantize(Decimal("0.0001"))
    target = (
        sum(targets, Decimal("0")) / len(targets) if targets else None
    )
    return {
        "source": "weighed",
        "rolls": len(rolls),
        "measured": average,
        "target": target,
        "deviation": average - target if target else None,
        "deviation_percent": (
            (average - target) / target * ONE_HUNDRED if target else None
        ),
    }
