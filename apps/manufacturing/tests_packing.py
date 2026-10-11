"""
A bale of the sack takes one cover and four straps. Stores hold 10
covers at 12.00 and 100 straps at 1.50; pressing a bale draws 1 and 4,
and 18.00 lands in packing consumed. Without the packing reason set the
bale is refused and nothing moves; with fewer covers on the shelf than a
bale takes, the same. A sack with no recipe packs with nothing drawn.
Breaking the bale does not put the cover back: it was cut.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalLine
from apps.inventory.adjustments import AdjustmentDirection, AdjustmentReason, StockAdjustment
from apps.inventory.models import Item, MovementType, StockMovement

from .bales import Bale, break_bale
from .orders import ManufacturingSettings
from .packing import BALE_PACKING, BalePacking, PackingLine
from .tests_bales import BaleTestCase


class PackingTestCase(BaleTestCase):
    def setUp(self):
        super().setUp()
        self.cover = Item.objects.create(sku="PK-COVER", name="Bale cover", uom=self.pcs)
        self.strap = Item.objects.create(sku="PK-STRAP", name="PP strap", uom=self.pcs)
        for item, quantity, cost in ((self.cover, "10", "12"), (self.strap, "100", "1.5")):
            StockMovement.objects.create(item=item, warehouse=self.plant, movement_type=MovementType.RECEIPT,
                                         uom=self.pcs, quantity=Decimal(quantity), unit_cost=Decimal(cost),
                                         occurred_at=timezone.now())
        self.consumed = Account.objects.create(code="5400", name="Packing consumed", account_type=AccountType.EXPENSE)
        self.reason = AdjustmentReason.objects.create(code="PACKING", name="Packing to bales", account=self.consumed,
                                                      direction=AdjustmentDirection.DECREASE)
        ManufacturingSettings.objects.update(packing_reason=self.reason)
        PackingLine.objects.create(item=self.bag, packing_item=self.cover, quantity=Decimal("1"))
        PackingLine.objects.create(item=self.bag, packing_item=self.strap, quantity=Decimal("4"))

    def on_hand(self, item):
        return item.on_hand_at(self.plant)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class RefusedTests(PackingTestCase):
    def test_a_recipe_names_something_else_and_more_than_nothing(self):
        with self.assertRaisesMessage(ValidationError, "does not pack itself"):
            PackingLine.objects.create(item=self.bag, packing_item=self.bag, quantity=Decimal("1"))
        with self.assertRaisesMessage(ValidationError, "more than nothing"):
            PackingLine.objects.create(item=self.cover, packing_item=self.strap, quantity=Decimal("0"))

    def test_without_the_reason_no_bale_is_pressed_and_nothing_moves(self):
        ManufacturingSettings.objects.update(packing_reason=None)
        with self.assertRaisesMessage(ValidationError, "packing material is written off under"):
            self.bale()
        self.assertEqual((Bale.objects.count(), StockAdjustment.objects.filter(raised_by=BALE_PACKING).count()), (0, 0))
        self.assertEqual(self.on_hand(self.cover), Decimal("10"))

    def test_fewer_covers_than_a_bale_takes_refuses_the_bale(self):
        PackingLine.objects.filter(packing_item=self.cover).update(quantity=Decimal("11"))
        with self.assertRaises(ValidationError):
            self.bale()
        self.assertEqual((Bale.objects.count(), self.on_hand(self.cover), self.on_hand(self.strap)),
                         (0, Decimal("10"), Decimal("100")))


class PressedTests(PackingTestCase):
    def test_pressing_draws_the_packing_and_books_its_cost(self):
        bale = self.bale()
        self.assertEqual((self.on_hand(self.cover), self.on_hand(self.strap)), (Decimal("9"), Decimal("96")))
        packing = BalePacking.objects.get(bale=bale)
        self.assertEqual([(item.sku, quantity) for item, quantity in packing.lines()],
                         [("PK-COVER", Decimal("1")), ("PK-STRAP", Decimal("4"))])
        # 1 x 12.00 + 4 x 1.50
        self.assertEqual(packing.cost(), Decimal("18.00"))
        self.assertEqual(self.balance(self.consumed), Decimal("18.00"))
        self.assertEqual((packing.adjustment.posted, packing.adjustment.raised_by), (True, BALE_PACKING))

    def test_a_sack_with_no_recipe_packs_with_nothing_drawn(self):
        PackingLine.objects.all().delete()
        bale = self.bale()
        self.assertFalse(BalePacking.objects.filter(bale=bale).exists())
        self.assertEqual(self.on_hand(self.cover), Decimal("10"))

    def test_breaking_the_bale_does_not_put_the_cover_back(self):
        bale = self.bale()
        break_bale(bale, "Pressed against the wrong order")
        self.assertEqual((self.on_hand(self.cover), self.on_hand(self.strap)), (Decimal("9"), Decimal("96")))
        self.assertTrue(BalePacking.objects.get(bale=bale).adjustment.posted)


class PackingApiTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_recipe_is_kept_by_the_process_engineer_and_the_bale_says_what_it_took(self):
        engineer = self.as_("Process Engineer")
        rows = engineer.get("/api/manufacturing/packing-lines/", {"item": self.bag.pk}).json()
        self.assertEqual([(row["packing_item_sku"], row["quantity"], row["uom"]) for row in rows],
                         [("PK-COVER", "1.0000", "pcs"), ("PK-STRAP", "4.0000", "pcs")])
        label = Item.objects.create(sku="PK-LABEL", name="Bale label", uom=self.pcs)
        made = engineer.post("/api/manufacturing/packing-lines/", {
            "item": self.bag.pk, "packing_item": label.pk, "quantity": "1"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        twice = engineer.post("/api/manufacturing/packing-lines/", {
            "item": self.bag.pk, "packing_item": label.pk, "quantity": "2"}, format="json")
        self.assertEqual(twice.status_code, 400)
        self.assertEqual(engineer.delete(f"/api/manufacturing/packing-lines/{made.json()['id']}/").status_code, 204)
        self.assertEqual(self.as_("Warehouse Staff").post("/api/manufacturing/packing-lines/", {
            "item": self.bag.pk, "packing_item": label.pk, "quantity": "1"}, format="json").status_code, 403)

        bale = self.bale()
        row = self.as_("Production Supervisor").get(f"/api/manufacturing/bales/{bale.pk}/").json()
        self.assertEqual(([(line["item"], line["quantity"]) for line in row["packing"]], row["packing_cost"]),
                         ([("PK-COVER", "1"), ("PK-STRAP", "4")], "18.00"))
