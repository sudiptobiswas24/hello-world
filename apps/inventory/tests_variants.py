"""
One product, many of it.

A shirt is not one item; it is a red medium, a red large and so on, each
separately counted, priced and shipped. Modelling them as unrelated SKUs
works right up to the point somebody asks how many shirts there are.

The variant *is* an Item, because everything in this codebase points at
Item and making the variant something new would mean repointing all of
it. These tests check that the template holds the product together
without changing what a plain item does.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Currency, UnitOfMeasure, UnitOfMeasureCategory

from .models import (
    Item,
    ItemAttribute,
    ItemAttributeValue,
    ItemTemplate,
    ItemVariantValue,
    MovementType,
    StockMovement,
    TrackingMode,
    Warehouse,
    variant_key,
)


class VariantTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        self.litre = UnitOfMeasure.objects.create(
            code="L", name="Litre", category=UnitOfMeasureCategory.VOLUME
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
        )
        self.north = Warehouse.objects.create(code="N", name="North")

        self.colour = ItemAttribute.objects.create(code="COL", name="Colour", sequence=1)
        self.red = ItemAttributeValue.objects.create(
            attribute=self.colour, code="RED", name="Red", sequence=1
        )
        self.blue = ItemAttributeValue.objects.create(
            attribute=self.colour, code="BLU", name="Blue", sequence=2
        )
        self.size = ItemAttribute.objects.create(code="SIZ", name="Size", sequence=2)
        self.medium = ItemAttributeValue.objects.create(
            attribute=self.size, code="M", name="Medium", sequence=1
        )
        self.large = ItemAttributeValue.objects.create(
            attribute=self.size, code="L", name="Large", sequence=2
        )

    def shirt(self, **kwargs):
        template = ItemTemplate.objects.create(
            code="SHIRT", name="Oxford shirt", uom=self.each,
            sale_price=Decimal("30"), **kwargs
        )
        template.attributes.set([self.colour, self.size])
        return template

    def stock(self, item, quantity, cost="5", warehouse=None):
        return StockMovement.objects.create(
            item=item, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=item.uom,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )


class GeneratingVariantsTests(VariantTestCase):
    def test_every_combination_becomes_an_item(self):
        template = self.shirt()
        created = template.generate_variants()
        self.assertEqual(len(created), 4)
        self.assertEqual(
            sorted(item.sku for item in created),
            ["SHIRT-BLU-L", "SHIRT-BLU-M", "SHIRT-RED-L", "SHIRT-RED-M"],
        )

    def test_a_variant_reads_as_the_product_and_its_values(self):
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        self.assertEqual(variant.name, "Oxford shirt (Red, Medium)")
        self.assertEqual(variant.variant_description(), "Red, Medium")

    def test_variants_inherit_what_the_product_decided(self):
        template = self.shirt(
            tracking=TrackingMode.LOT, costing_method="fifo",
            inventory_account=self.inventory,
        )
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        self.assertEqual(variant.uom, self.each)
        self.assertEqual(variant.tracking, "lot")
        self.assertEqual(variant.costing_method, "fifo")
        self.assertEqual(variant.inventory_account, self.inventory)
        self.assertEqual(variant.sale_price, Decimal("30.00"))

    def test_generating_twice_creates_nothing_new(self):
        # A variant that has been sold is a product with history, and
        # regenerating the set is not a reason to destroy it.
        template = self.shirt()
        template.generate_variants()
        self.assertEqual(template.generate_variants(), [])
        self.assertEqual(template.variants.count(), 4)

    def test_a_new_value_adds_only_its_own_combinations(self):
        template = self.shirt()
        template.generate_variants()
        ItemAttributeValue.objects.create(
            attribute=self.colour, code="GRN", name="Green", sequence=3
        )
        created = template.generate_variants()
        self.assertEqual(
            sorted(item.sku for item in created), ["SHIRT-GRN-L", "SHIRT-GRN-M"]
        )
        self.assertEqual(template.variants.count(), 6)

    def test_a_product_that_varies_by_nothing_is_not_a_template(self):
        plain = ItemTemplate.objects.create(code="P", name="Plain", uom=self.each)
        with self.assertRaises(ValidationError) as caught:
            plain.generate_variants()
        self.assertIn("varies by nothing", str(caught.exception))

    def test_an_attribute_with_no_values_says_so(self):
        finish = ItemAttribute.objects.create(code="FIN", name="Finish")
        template = self.shirt()
        template.attributes.add(finish)
        with self.assertRaises(ValidationError) as caught:
            template.generate_variants()
        self.assertIn("nothing to vary by", str(caught.exception))

    def test_only_some_combinations_can_be_asked_for(self):
        # Not every colour comes in every size.
        template = self.shirt()
        created = template.generate_variants(
            only=[(self.red, self.medium), (self.blue, self.large)]
        )
        self.assertEqual(
            sorted(item.sku for item in created), ["SHIRT-BLU-L", "SHIRT-RED-M"]
        )


class AVariantIsAnItemTests(VariantTestCase):
    def test_stock_moves_for_a_variant_like_any_other_item(self):
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        self.stock(variant, "40", "12")
        self.assertEqual(variant.on_hand_at(self.north), Decimal("40"))
        self.assertEqual(variant.stock_value_at(self.north), Decimal("480.00"))

    def test_each_variant_is_counted_on_its_own(self):
        template = self.shirt()
        template.generate_variants()
        red = Item.objects.get(sku="SHIRT-RED-M")
        blue = Item.objects.get(sku="SHIRT-BLU-M")
        self.stock(red, "40")
        self.stock(blue, "10")
        self.assertEqual(red.on_hand_at(self.north), Decimal("40"))
        self.assertEqual(blue.on_hand_at(self.north), Decimal("10"))

    def test_the_product_can_say_how_many_there_are_altogether(self):
        # The question unrelated SKUs cannot answer, and the reason this
        # model exists.
        template = self.shirt()
        template.generate_variants()
        for sku, quantity in (("SHIRT-RED-M", "40"), ("SHIRT-BLU-L", "10")):
            self.stock(Item.objects.get(sku=sku), quantity)
        self.assertEqual(template.on_hand_at(), Decimal("50"))
        self.assertEqual(template.on_hand_at(self.north), Decimal("50"))

    def test_the_product_can_say_what_it_is_all_worth(self):
        template = self.shirt()
        template.generate_variants()
        self.stock(Item.objects.get(sku="SHIRT-RED-M"), "40", "12")
        self.stock(Item.objects.get(sku="SHIRT-BLU-L"), "10", "20")
        self.assertEqual(template.stock_value_at(), Decimal("680.00"))

    def test_a_variant_knows_its_siblings(self):
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        self.assertEqual(variant.siblings().count(), 3)

    def test_a_plain_item_is_unchanged_and_has_no_product(self):
        # Every item that existed before this behaves exactly as it did.
        plain = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.stock(plain, "100")
        self.assertFalse(plain.is_variant())
        self.assertEqual(plain.variant_description(), "")
        self.assertEqual(plain.siblings().count(), 0)
        self.assertEqual(plain.on_hand_at(self.north), Decimal("100"))


class VariantsStayCoherentTests(VariantTestCase):
    def test_two_variants_cannot_be_the_same_combination(self):
        from django.db.utils import IntegrityError

        template = self.shirt()
        template.generate_variants()
        with self.assertRaises(IntegrityError):
            Item.objects.create(
                template=template, sku="SHIRT-DUP", name="Duplicate",
                uom=self.each, variant_key=variant_key([self.red, self.medium]),
            )

    def test_the_key_does_not_depend_on_the_order_values_were_given(self):
        # Otherwise "red, medium" and "medium, red" are two products.
        self.assertEqual(
            variant_key([self.red, self.medium]),
            variant_key([self.medium, self.red]),
        )

    def test_a_variant_must_share_the_products_unit(self):
        # A product-level total that sums litres and bottles is a sum of
        # incompatible numbers, and that total is the only reason the
        # template exists.
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        variant.uom = self.litre
        with self.assertRaises(ValidationError) as caught:
            variant.save()
        self.assertIn("must share its unit of measure", str(caught.exception))

    def test_a_variant_must_share_the_products_costing_method(self):
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        variant.costing_method = "fifo"
        with self.assertRaises(ValidationError) as caught:
            variant.save()
        self.assertIn("must share its costing method", str(caught.exception))

    def test_a_variant_must_share_the_products_tracking(self):
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        variant.tracking = TrackingMode.LOT
        with self.assertRaises(ValidationError):
            variant.save()

    def test_which_variant_something_is_cannot_be_changed(self):
        # Changing a shirt's colour does not change the shirt; it makes
        # it a different product, and rewrites that product's history.
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        variant.variant_key = variant_key([self.blue, self.large])
        with self.assertRaises(ValidationError) as caught:
            variant.save()
        self.assertIn("already a particular variant", str(caught.exception))

    def test_a_variant_cannot_be_moved_to_another_product(self):
        template = self.shirt()
        template.generate_variants()
        other = ItemTemplate.objects.create(code="TEE", name="T-shirt", uom=self.each)
        variant = Item.objects.get(sku="SHIRT-RED-M")
        variant.template = other
        with self.assertRaises(ValidationError) as caught:
            variant.save()
        self.assertIn("cannot be moved", str(caught.exception))

    def test_a_variant_cannot_carry_an_attribute_the_product_has_not_got(self):
        finish = ItemAttribute.objects.create(code="FIN", name="Finish")
        matte = ItemAttributeValue.objects.create(
            attribute=finish, code="MAT", name="Matte"
        )
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        with self.assertRaises(ValidationError) as caught:
            ItemVariantValue.objects.create(item=variant, value=matte)
        self.assertIn("does not vary by", str(caught.exception))

    def test_a_variant_cannot_be_two_colours(self):
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        with self.assertRaises(ValidationError) as caught:
            ItemVariantValue.objects.create(item=variant, value=self.blue)
        self.assertIn("cannot have two", str(caught.exception))

    def test_one_value_code_per_attribute(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            ItemAttributeValue.objects.create(
                attribute=self.colour, code="RED", name="Also red"
            )


class RetiringAProductTests(VariantTestCase):
    def test_retiring_the_product_retires_its_variants(self):
        # Leaving them active is how a discontinued line keeps being sold.
        template = self.shirt()
        template.generate_variants()
        template.deactivate()
        self.assertFalse(template.is_active)
        self.assertEqual(template.variants.filter(is_active=True).count(), 0)

    def test_a_retired_product_still_has_its_stock(self):
        # Retiring stops it being ordered; it does not empty the shelves.
        # The first version filtered the valuation to active variants and
        # the quantity not, so on_hand said forty and the value said
        # nothing — which is the doc-versus-ledger drift this codebase
        # derives everything to avoid.
        template = self.shirt()
        template.generate_variants()
        variant = Item.objects.get(sku="SHIRT-RED-M")
        self.stock(variant, "40", "12")
        template.deactivate()
        variant.refresh_from_db()
        self.assertEqual(variant.on_hand_at(self.north), Decimal("40"))
        self.assertEqual(template.on_hand_at(), Decimal("40"))
        self.assertEqual(template.stock_value_at(), Decimal("480.00"))

    def test_the_products_quantity_and_value_never_disagree(self):
        template = self.shirt()
        template.generate_variants()
        red = Item.objects.get(sku="SHIRT-RED-M")
        blue = Item.objects.get(sku="SHIRT-BLU-L")
        self.stock(red, "40", "10")
        self.stock(blue, "10", "10")
        blue.is_active = False
        blue.save()
        self.assertEqual(template.on_hand_at(), Decimal("50"))
        self.assertEqual(template.stock_value_at(), Decimal("500.00"))
