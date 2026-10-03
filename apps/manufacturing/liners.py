"""
The liner line: LDPE film blown as a tube, then cut and sealed into
liners that go inside the sack on the one BCS that inserts them.

Two specifications, as tape and fabric are two: a blown film is a
stocked roll of a width and a thickness, and more than one liner can be
cut from it. Each builds its own bill, so a planner exploding a lined
sack sees liners, then film, then polymer, each on its own machine.

**Film** is weighed per metre from its size alone: a tube laid flat is
two layers of the width, so 58 cm at 50 micron is 2 x 0.58 x 50 x 0.92
= 53.36 g a metre. The blend is LDPE with the rest as its balance, as
virgin PP is the balance of a tape.

**A liner** is so many centimetres of that tube, cut and sealed across
the bottom. It is counted, not weighed: the sack's bill asks for one a
sack, and the weight a sack carries for it comes from here.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from apps.core.models import AuditModel, UnitOfMeasureCategory
from apps.core.windows import covers
from apps.inventory.models import Item
from apps.quality.models import Evaluation

from .bom import BillOfMaterials, ByproductValuation
from .woven import (
    CM_PER_M,
    LDPE_GRAMS_PER_MICRON,
    ONE_HUNDRED,
    TUBE_LAYERS,
    SpecificationMixin,
    SpecificationWindow,
    _check_weighed_in,
    _percent,
)

FILM_BATCH_KG = Decimal("100")
LINER_BATCH_PIECES = Decimal("1000")


class FilmSpecification(SpecificationMixin, SpecificationWindow, AuditModel):
    """A blown LDPE tube: its blend, its thickness and its lay-flat width."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    film_item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="film_specifications",
        help_text="The film roll this makes, stocked by weight.")
    micron = models.DecimalField(max_digits=6, decimal_places=2,
                                 help_text="Thickness of one layer of the tube.")
    lay_flat_width_cm = models.DecimalField(max_digits=8, decimal_places=2)
    base_polymer = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="+",
        help_text="LDPE. Its share is whatever the rest of the blend leaves.")
    lldpe_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+",
                                   help_text="LLDPE, for toughness and seal strength.")
    lldpe_percent = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))
    masterbatch_item = models.ForeignKey(Item, null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="+")
    masterbatch_percent = models.DecimalField(max_digits=6, decimal_places=3,
                                              default=Decimal("0"))
    extrusion_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("3"),
        help_text="Of what is fed in: start-up, gauge bands trimmed off, bubble breaks.")
    waste_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+",
                                   help_text="What the collected film waste is stocked as.")
    waste_recovered_percent = models.DecimalField(max_digits=6, decimal_places=3,
                                                  default=Decimal("80"))
    micron_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("10"),
        help_text="How far a gauge reading may be from the thickness.")
    routing = models.ForeignKey("Routing", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="film_specifications")
    inspection_plan = models.OneToOneField(
        "quality.InspectionPlan", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="film_specification", editable=False)
    bom = models.OneToOneField(BillOfMaterials, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="film_specification",
                               editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(micron__gt=0) & Q(lay_flat_width_cm__gt=0),
                                   name="film_has_a_size"),
            models.CheckConstraint(
                check=Q(lldpe_percent__gte=0) & Q(masterbatch_percent__gte=0),
                name="film_blend_shares_not_negative"),
            models.CheckConstraint(
                check=Q(extrusion_waste_percent__gte=0) & Q(extrusion_waste_percent__lt=100)
                & Q(waste_recovered_percent__gte=0) & Q(waste_recovered_percent__lte=100)
                & Q(micron_tolerance_percent__gte=0),
                name="film_percentages_sensible"),
        ]

    def __str__(self):
        return f"{self.code} ({self.micron} micron, {self.lay_flat_width_cm} cm)"

    def base_percent(self):
        return ONE_HUNDRED - self.lldpe_percent - self.masterbatch_percent

    def grams_per_metre(self):
        """Two layers of the lay-flat width, at the film's thickness."""
        return (TUBE_LAYERS * self.lay_flat_width_cm / CM_PER_M * self.micron
                * LDPE_GRAMS_PER_MICRON)

    def bom_item(self):
        return self.film_item

    def bom_batch(self):
        return FILM_BATCH_KG

    def bom_uom(self):
        return self.film_item.uom

    def bom_components(self):
        rows = [(self.base_polymer, FILM_BATCH_KG * _percent(self.base_percent()),
                 self.base_polymer.uom, self.extrusion_waste_percent,
                 "LDPE, the balance of the blend")]
        for item, percent, note in ((self.lldpe_item, self.lldpe_percent, "LLDPE"),
                                    (self.masterbatch_item, self.masterbatch_percent,
                                     "Masterbatch")):
            # _check() has refused an item without its share, and the reverse.
            if item is not None:
                rows.append((item, FILM_BATCH_KG * _percent(percent), item.uom,
                             self.extrusion_waste_percent, note))
        return rows

    def bom_byproducts(self):
        if self.waste_item is None:
            return []
        return [(self.waste_item,
                 self.recovered_waste(FILM_BATCH_KG, self.extrusion_waste_percent),
                 self.waste_item.uom, ByproductValuation.STANDARD)]

    def inspection_lines(self):
        margin = self.micron * _percent(self.micron_tolerance_percent)
        return [("MICRON", "Film thickness", ("micron", "Micron"), self.micron,
                 self.micron - margin, self.micron + margin, 5, Evaluation.MEAN, "micron")]

    def _check(self):
        if self.base_percent() <= 0:
            raise ValidationError(f"{self.code}: the blend leaves no LDPE.")
        for item, percent, label in ((self.lldpe_item, self.lldpe_percent, "LLDPE"),
                                     (self.masterbatch_item, self.masterbatch_percent,
                                      "masterbatch")):
            if (item is None) != (not percent):
                raise ValidationError(f"{self.code}: the {label} needs both an item and a share.")
        unit = _check_weighed_in(self.film_item, None, "the film", self.code)
        for item, label in ((self.base_polymer, "the LDPE"), (self.lldpe_item, "the LLDPE"),
                            (self.masterbatch_item, "the masterbatch"),
                            (self.waste_item, "the film waste")):
            if item is not None:
                _check_weighed_in(item, unit, label, self.code)

    @transaction.atomic
    def save(self, *args, **kwargs):
        self._check()
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()
        # Every liner cut from it weighs what a length of it weighs.
        for liner in self.liners.all():
            liner.save()


class LinerSpecification(SpecificationMixin, SpecificationWindow, AuditModel):
    """So many centimetres of a blown tube, cut and sealed: one liner."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    liner_item = models.ForeignKey(Item, on_delete=models.PROTECT,
                                   related_name="liner_specifications",
                                   help_text="The liner, counted.")
    film = models.ForeignKey(FilmSpecification, on_delete=models.PROTECT,
                             related_name="liners")
    cut_length_cm = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="Tube cut for one liner, the seal included.")
    seal_waste_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("2"),
        help_text="Of the film fed in: bad seals, the roll's ends.")
    waste_recovered_percent = models.DecimalField(max_digits=6, decimal_places=3,
                                                  default=Decimal("80"))
    weight_tolerance_percent = models.DecimalField(max_digits=5, decimal_places=2,
                                                   default=Decimal("5"))
    routing = models.ForeignKey("Routing", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="liner_specifications")
    inspection_plan = models.OneToOneField(
        "quality.InspectionPlan", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="liner_specification", editable=False)
    bom = models.OneToOneField(BillOfMaterials, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="liner_specification",
                               editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(cut_length_cm__gt=0), name="liner_has_a_length"),
            models.CheckConstraint(
                check=Q(seal_waste_percent__gte=0) & Q(seal_waste_percent__lt=100)
                & Q(waste_recovered_percent__gte=0) & Q(waste_recovered_percent__lte=100)
                & Q(weight_tolerance_percent__gte=0),
                name="liner_percentages_sensible"),
        ]

    def __str__(self):
        return f"{self.code} ({self.film.lay_flat_width_cm} x {self.cut_length_cm} cm)"

    def liner_grams(self):
        return self.film.grams_per_metre() * self.cut_length_cm / CM_PER_M

    def bom_item(self):
        return self.liner_item

    def bom_batch(self):
        return LINER_BATCH_PIECES

    def bom_uom(self):
        return self.liner_item.uom

    def bom_components(self):
        # Grammes a liner are kilos a thousand of them.
        film = self.film.film_item
        return [(film, self.liner_grams(), film.uom, self.seal_waste_percent,
                 f"{self.film.code}, {self.cut_length_cm} cm a liner")]

    def bom_byproducts(self):
        waste = self.film.waste_item
        if waste is None:
            return []
        return [(waste, self.recovered_waste(self.liner_grams(), self.seal_waste_percent),
                 waste.uom, ByproductValuation.STANDARD)]

    def inspection_lines(self):
        weight = self.liner_grams()
        margin = weight * _percent(self.weight_tolerance_percent)
        return [("LINERWT", "Liner weight", ("g-liner", "Grammes a liner"), weight,
                 weight - margin, weight + margin, 10, Evaluation.MEAN, "liner_weight")]

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.liner_item.uom.category != UnitOfMeasureCategory.COUNT:
            raise ValidationError(
                f"{self.code}: liners are counted off the cut-and-seal machine, and "
                f"{self.liner_item.sku} is measured in {self.liner_item.uom}.")
        super().save(*args, **kwargs)
        self.rebuild_bom()
        self.rebuild_inspection_plan()
        self.resave_dependents()

    @transaction.atomic
    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        # A sack whose liner has no specification left cannot say what it
        # weighs, and refuses: the delete goes back with it.
        self.resave_dependents()
        return result

    def resave_dependents(self):
        from .woven import BagSpecification

        for bag in BagSpecification.objects.filter(liner_item=self.liner_item):
            bag.save()


def liner_specification_for(item, on_date):
    """The one liner specification in force for a counted liner on a day."""
    specs = [spec for spec in LinerSpecification.objects.filter(liner_item=item,
                                                                 is_active=True)
             if covers(spec.valid_from, spec.valid_to, on_date)]
    if len(specs) != 1:
        raise ValidationError(
            f"{item} has {len(specs) or 'no'} liner specification"
            f"{'s' if len(specs) > 1 else ''} in force on {on_date}; a counted liner's "
            "weight comes from exactly one.")
    return specs[0]
