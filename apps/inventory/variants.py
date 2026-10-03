"""
One product, many of it.

A shirt is not one item; it is a red medium, a red large, a blue medium
and so on, and each of those is separately counted, separately priced
and separately shipped. Modelling them as unrelated SKUs works right up
to the point somebody asks how many shirts there are.

The shape is a template and its variants, and the variant *is* an Item.
Everything in this codebase points at Item — movements, lots,
reservations, order lines, bins, price lists — so making the variant
something new would mean repointing all of it. A template sits above
Item instead and holds the defaults; an Item with no template is a
standalone product, which is every item that existed before this and
which behaves exactly as it did.

Pricing is deliberately not here. Sales already has price lists, and a
second pricing mechanism on the variant would be two answers to one
question, differing in whichever case nobody tested.
"""

from decimal import Decimal
from itertools import product

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.core.models import AuditModel, UnitOfMeasure


class ItemAttribute(AuditModel):
    """Something a product varies by: colour, size, finish."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    sequence = models.PositiveIntegerField(
        default=100, help_text="Order attributes appear in on a variant's name."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "code"]

    def __str__(self):
        return self.name


class ItemAttributeValue(AuditModel):
    attribute = models.ForeignKey(
        ItemAttribute, on_delete=models.CASCADE, related_name="values"
    )
    code = models.CharField(max_length=32, help_text="Used in the generated SKU.")
    name = models.CharField(max_length=255)
    sequence = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["attribute", "sequence", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "code"], name="one_value_code_per_attribute"
            ),
        ]

    def __str__(self):
        return f"{self.attribute.name}: {self.name}"


class ItemTemplate(AuditModel):
    """
    The product a set of variants are variants of.

    It holds what they must agree on and cannot itself be stocked — it
    is not an Item, so there is nowhere to put any. That is the point:
    a template with stock of its own would be a phantom SKU that no
    movement could ever explain.
    """

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    item_type = models.CharField(max_length=16, default="goods")
    track_inventory = models.BooleanField(default=True)
    tracking = models.CharField(max_length=8, default="none")
    costing_method = models.CharField(max_length=16, default="average")
    inventory_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    cogs_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    sale_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Starting point for a variant's own price. A price list still "
                  "decides what anybody is actually charged.",
    )
    attributes = models.ManyToManyField(
        ItemAttribute, related_name="templates", blank=True,
        help_text="What this product varies by. Variants are every combination.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def on_hand_at(self, warehouse=None):
        """
        How many of this product there are, across every variant.

        The question unrelated SKUs cannot answer, and the reason this
        model exists.
        """
        movements = StockMovementModel().objects.filter(item__template=self)
        if warehouse is not None:
            movements = movements.filter(warehouse=warehouse)
        return movements.aggregate(total=Sum("quantity"))["total"] or Decimal("0")

    def stock_value_at(self, warehouse=None, as_of=None):
        total = Decimal("0")
        from .models import Warehouse

        shelves = [warehouse] if warehouse is not None else list(Warehouse.objects.all())
        # Every variant, not only the active ones. Retiring a product
        # stops it being ordered; it does not empty the shelves, and a
        # value that disagrees with on_hand_at() is the drift this
        # codebase derives everything to avoid.
        for variant in self.variants.all():
            for shelf in shelves:
                total += variant.valuation_at(shelf, as_of=as_of)[1]
        return total.quantize(Decimal("0.01"))

    def combinations(self):
        """Every combination of values this template's attributes allow."""
        groups = []
        for attribute in self.attributes.order_by("sequence", "code"):
            values = list(attribute.values.filter(is_active=True).order_by("sequence", "code"))
            if not values:
                raise ValidationError(
                    f"{attribute} has no values, so there is nothing to vary by."
                )
            groups.append(values)
        return list(product(*groups)) if groups else []

    @transaction.atomic
    def generate_variants(self, only=None, sku_prefix=None):
        """
        Create the variants this template implies, skipping any that
        already exist.

        Skipping rather than replacing: a variant that has been sold,
        received or counted is a product with history, and regenerating
        the set is not a reason to destroy it.
        """
        from .models import Item

        if not self.attributes.exists():
            raise ValidationError(
                f"{self} varies by nothing. Give it an attribute, or make it a plain "
                "item instead of a template."
            )
        wanted = only if only is not None else self.combinations()
        prefix = sku_prefix or self.code
        created = []
        for combination in wanted:
            key = variant_key(combination)
            if Item.objects.filter(template=self, variant_key=key).exists():
                continue
            sku = "-".join([prefix] + [value.code for value in combination])
            item = Item.objects.create(
                template=self,
                sku=sku,
                name=f"{self.name} ({', '.join(value.name for value in combination)})",
                description=self.description,
                item_type=self.item_type,
                uom=self.uom,
                track_inventory=self.track_inventory,
                tracking=self.tracking,
                costing_method=self.costing_method,
                inventory_account=self.inventory_account,
                cogs_account=self.cogs_account,
                sale_price=self.sale_price,
                variant_key=key,
            )
            for value in combination:
                ItemVariantValue.objects.create(item=item, value=value)
            created.append(item)
        return created

    @transaction.atomic
    def deactivate(self):
        """
        Retire the product and everything it comes in.

        Leaving the variants active would hide the decision: the product
        is gone and its SKUs would still be orderable, which is how a
        discontinued line keeps being sold.
        """
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])
        self.variants.update(is_active=False)
        return self


def StockMovementModel():
    from .models import StockMovement

    return StockMovement


def variant_key(values):
    """
    A canonical name for one combination of attribute values.

    Sorted by value id so the same combination always produces the same
    key whatever order it was given in — otherwise "red, medium" and
    "medium, red" are two different products.
    """
    return ",".join(str(pk) for pk in sorted(value.pk for value in values))


class ItemVariantValue(AuditModel):
    """One attribute value a variant carries."""

    item = models.ForeignKey(
        "Item", on_delete=models.CASCADE, related_name="variant_values"
    )
    value = models.ForeignKey(
        ItemAttributeValue, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["value__attribute__sequence", "value__sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "value"], name="one_variant_value_per_item"
            ),
        ]

    def __str__(self):
        return str(self.value)

    def clean(self):
        if self.item_id and self.item.template_id:
            allowed = self.item.template.attributes.values_list("pk", flat=True)
            if self.value.attribute_id not in allowed:
                raise ValidationError(
                    f"{self.item.template} does not vary by "
                    f"{self.value.attribute}."
                )
        existing = ItemVariantValue.objects.filter(
            item=self.item, value__attribute=self.value.attribute
        )
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        if existing.exists():
            # A shirt is one colour. Two would make the variant's identity
            # ambiguous and its key wrong.
            raise ValidationError(
                f"{self.item} already has a {self.value.attribute} and cannot have two."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
