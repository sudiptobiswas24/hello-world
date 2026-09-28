"""
Bundles off the conversion line: B1 and B2 of 500 bags, B3 of 400.

  300 of B1 split into RB-A (200) and RB-B (100): B1 keeps 200.
  B2 (released) joined with B3 (held for rework) is held; with B3 not
  inspected at all, not inspected.
  B1 expiring on the 30th and B2 on the 20th join into a batch expiring
  on the 20th.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.core.models import Party
from apps.inventory.models import Item, Lot, Warehouse
from apps.quality.models import Disposition, Inspection, Reading, ReleaseStatus
from apps.quality.release import check_released, release_status
from apps.sales.models import CustomerProfile, DeliveryLine, ThirdPartyRelease, ThirdPartyReleaseLine

from .certificates import issue
from .demand import runs_that_made
from .rebatch import Rebatch, RebatchLine, rebatch, remade_into, sources
from .tests_bales import BaleTestCase
from .tests_orders import TODAY
from .trace import recall


class RebatchTestCase(BaleTestCase):
    def new(self, code):
        return Lot.objects.create(item=self.bag, code=code)

    def split(self, taken="300", rows=(("RB-A", "200"), ("RB-B", "100")), source=None):
        made = [(self.new(code), quantity) for code, quantity in rows]
        return rebatch(self.bag, self.plant, [(source or self.b1, taken)], made,
                       "Half failed the customer's check", on_date=TODAY), [lot for lot, _ in made]

    def value(self):
        return self.bag._replay_valuation(self.plant)

    def held_for_rework(self, lot):
        inspection = Inspection.objects.create(lot=lot, plan=self.bag_spec.inspection_plan,
                                               inspected_on=TODAY,
                                               disposition=Disposition.REWORK)
        line = self.bag_spec.inspection_plan.lines.get()
        for number in range(line.sample_size):
            Reading.objects.create(inspection=inspection, plan_line=line,
                                   value=Decimal("110"), sample_reference=f"S{number}")
        inspection.post()


class SplitTests(RebatchTestCase):
    def test_the_sacks_move_and_the_shelf_is_worth_the_same(self):
        before = self.value()
        document, (a, b) = self.split()
        self.assertTrue(document.number.startswith("RB-"))
        self.assertEqual([lot.on_hand_at(self.plant) for lot in (self.b1, a, b)],
                         [Decimal("200"), Decimal("200"), Decimal("100")])
        after = self.value()
        self.assertEqual((after[0], after[1].quantize(Decimal("0.01"))),
                         (before[0], before[1].quantize(Decimal("0.01"))))

    def test_the_new_batches_remember_where_they_came_from(self):
        _, (a, b) = self.split()
        self.assertEqual(sources(a), [self.b1])
        self.assertEqual(runs_that_made(a), runs_that_made(self.b1))
        ((document, quantity, made),) = remade_into(self.b1)
        self.assertEqual((quantity, made), (Decimal("300"), [a, b]))

    def test_a_recall_follows_the_sacks_under_their_new_number(self):
        _, (a, b) = self.split()
        delivery = self.delivery("150")
        DeliveryLine.objects.create(delivery=delivery, order_line=delivery.sales_order.lines.get(),
                                    warehouse=self.plant, lot=a, quantity_shipped=Decimal("150"))
        delivery.post()
        found = recall(self.b1)
        self.assertIn((self.b1, [a, b]), [(row["lot"], row["made"]) for row in found["descendants"]])
        self.assertEqual([(row["lot"], row["quantity"]) for row in found["customers"]],
                         [(a, Decimal("150"))])


class StandingTests(RebatchTestCase):
    def test_a_split_of_a_released_batch_is_released_until_inspected_itself(self):
        _, (a, _) = self.split()
        self.assertEqual(release_status(a), ReleaseStatus.RELEASED)
        check_released(self.bag, a, action="be shipped")

    def test_joining_never_passes_what_was_not_passed(self):
        self.b3.inspections.get().void("Scale out")
        joined = self.new("RB-J")
        rebatch(self.bag, self.plant, [(self.b2, "100"), (self.b3, "100")], [(joined, "150"),
                                                                            (self.new("RB-K"), "50")],
                "Short ends", on_date=TODAY)
        self.assertEqual(release_status(joined), ReleaseStatus.UNINSPECTED)
        with self.assertRaisesMessage(ValidationError, "has not been inspected"):
            check_released(self.bag, joined, action="be shipped")
        self.held_for_rework(self.b3)
        self.assertEqual(release_status(joined), ReleaseStatus.HELD)
        with self.assertRaisesMessage(ValidationError, "re-made from a batch that is held"):
            check_released(self.bag, joined, action="be shipped")
        # Held beats not yet inspected.
        self.b2.inspections.get().void("Scale out")
        self.assertEqual(release_status(joined), ReleaseStatus.HELD)

    def test_it_expires_when_its_soonest_source_does(self):
        Lot.objects.filter(pk=self.b1.pk).update(expires_on=datetime.date(2027, 6, 30))
        Lot.objects.filter(pk=self.b2.pk).update(expires_on=datetime.date(2027, 6, 20))
        self.b1.refresh_from_db()
        self.b2.refresh_from_db()
        joined, other = self.new("RB-J"), self.new("RB-K")
        rebatch(self.bag, self.plant, [(self.b1, "10"), (self.b2, "10")],
                [(joined, "15"), (other, "5")], "Short ends", on_date=TODAY)
        joined.refresh_from_db()
        self.assertEqual(joined.expires_on, datetime.date(2027, 6, 20))

    def test_the_certificate_says_what_its_sources_measured(self):
        _, (a, _) = self.split()
        delivery = self.delivery("50")
        DeliveryLine.objects.create(delivery=delivery, order_line=delivery.sales_order.lines.get(),
                                    warehouse=self.plant, lot=a, quantity_shipped=Decimal("50"))
        delivery.post()
        (batch,) = issue(delivery, TODAY).content["lines"][0]["batches"]
        self.assertEqual((batch["lot"], batch["inspection"]), ("RB-A", None))
        self.assertEqual([row["lot"] for row in batch["remade_from"]], [self.b1.code])
        self.assertEqual(batch["remade_from"][0]["inspection"],
                         self.b1.inspections.get().number)

    def test_the_customers_inspector_must_see_the_new_batch(self):
        CustomerProfile.objects.create(party=self.cement, third_party_inspection=True)
        agency = Party.objects.create(code="SGS", name="SGS India")
        release = ThirdPartyRelease.objects.create(customer=self.cement, agency=agency,
                                                   their_reference="IC-1", inspected_on=TODAY)
        ThirdPartyReleaseLine.objects.create(release=release, lot=self.b1,
                                             quantity_offered=Decimal("500"),
                                             quantity_released=Decimal("500"))
        release.post()
        _, (a, _) = self.split()
        delivery = self.delivery("10")
        DeliveryLine.objects.create(delivery=delivery, order_line=delivery.sales_order.lines.get(),
                                    warehouse=self.plant, lot=a, quantity_shipped=Decimal("10"))
        with self.assertRaisesMessage(ValidationError, "is 10 short"):
            delivery.post()


class RefusedTests(RebatchTestCase):
    def test_what_a_rebatch_may_not_do(self):
        with self.assertRaisesMessage(ValidationError, "same batch"):
            self.split("100", (("RB-1", "100"),))
        with self.assertRaisesMessage(ValidationError, "300 taken and 299 made"):
            self.split("300", (("RB-2", "200"), ("RB-3", "99")))
        with self.assertRaisesMessage(ValidationError, "has held stock before"):
            rebatch(self.bag, self.plant, [(self.b1, "20")], [(self.b2, "10"),
                                                              (self.new("RB-4"), "10")],
                    "x", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "has 500 on the shelf"):
            self.split("501", (("RB-5", "500"), ("RB-6", "1")))
        with self.assertRaisesMessage(ValidationError, "Say why"):
            rebatch(self.bag, self.plant, [(self.b1, "2")],
                    [(self.new("RB-7"), "1"), (self.new("RB-8"), "1")], " ", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "is counted whole"):
            self.split("1", (("RB-11", "0.5"), ("RB-12", "0.5")))
        with self.assertRaisesMessage(ValidationError, "appears once"):
            rebatch(self.bag, self.plant, [(self.b1, "2"), (self.b1, "2")],
                    [(self.new("RB-9"), "2"), (self.new("RB-10"), "2")], "x", on_date=TODAY)

    def test_a_bundle_in_a_sealed_bale_stays_in_it(self):
        self.bale()
        with self.assertRaisesMessage(ValidationError, "has 0 on the shelf and not in a sealed"):
            self.split("2", (("RB-1", "1"), ("RB-2", "1")))

    def test_only_batches_of_this_sack_kept_in_batches(self):
        other = Item.objects.create(sku="BAG-2", name="Other", uom=self.pcs, tracking="lot")
        stranger = Lot.objects.create(item=other, code="O-1")
        with self.assertRaisesMessage(ValidationError, "is not BAG-60X100"):
            rebatch(self.bag, self.plant, [(self.b1, "2")],
                    [(stranger, "1"), (self.new("RB-1"), "1")], "x", on_date=TODAY)
        plain = Item.objects.create(sku="PLAIN", name="Plain", uom=self.pcs)
        with self.assertRaisesMessage(ValidationError, "not kept in batches"):
            rebatch(plain, self.plant, [(self.b1, "2")],
                    [(self.new("RB-2"), "1"), (self.new("RB-3"), "1")], "x", on_date=TODAY)
        consignor = Party.objects.create(code="VEN", name="Vendor")
        theirs = Warehouse.objects.create(code="CONS", name="Consigned",
                                          consignment_vendor=consignor)
        with self.assertRaisesMessage(ValidationError, "holds VEN - Vendor's stock"):
            rebatch(self.bag, theirs, [(self.b1, "2")],
                    [(self.new("RB-4"), "1"), (self.new("RB-5"), "1")], "x", on_date=TODAY)


class UndoneTests(RebatchTestCase):
    def test_voided_while_untouched_puts_the_sacks_back(self):
        before = self.value()
        document, (a, b) = self.split()
        document.void("Wrong bundle")
        after = self.value()
        self.assertEqual((after[0], after[1].quantize(Decimal("0.01"))),
                         (before[0], before[1].quantize(Decimal("0.01"))))
        self.assertEqual([lot.on_hand_at(self.plant) for lot in (self.b1, a, b)],
                         [Decimal("500"), Decimal("0"), Decimal("0")])
        self.assertEqual((sources(a), remade_into(self.b1)), ([], []))
        with self.assertRaisesMessage(ValidationError, "not a standing re-batch"):
            document.void("Again")

    def test_not_once_a_new_batch_has_been_used(self):
        document, (a, _) = self.split()
        self.bale((a, 10))
        with self.assertRaisesMessage(ValidationError, "RB-A has been used since"):
            document.void("Wrong bundle")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            document.void(" ")

    def test_nor_once_one_has_shipped(self):
        document, (a, _) = self.split()
        delivery = self.delivery("5")
        DeliveryLine.objects.create(delivery=delivery, order_line=delivery.sales_order.lines.get(),
                                    warehouse=self.plant, lot=a, quantity_shipped=Decimal("5"))
        delivery.post()
        with self.assertRaisesMessage(ValidationError, "RB-A has been used since"):
            document.void("Wrong bundle")

    def test_posted_is_posted(self):
        document, _ = self.split()
        document.reason = "Other"
        with self.assertRaisesMessage(ValidationError, "is posted. Void it"):
            document.save()
        with self.assertRaisesMessage(ValidationError, "is posted; void it"):
            document.delete()
        line = document.lines.first()
        line.quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "its lines do not change"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "its lines do not change"):
            line.delete()
        self.assertEqual(RebatchLine.objects.filter(rebatch=document).count(), 3)
        self.assertEqual(Rebatch.objects.get().reason, "Half failed the customer's check")


class RebatchApiTests(RebatchTestCase):
    def test_recorded_and_voided(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("stores"))
        response = client.post("/api/manufacturing/rebatches/record/", {
            "item": self.bag.pk, "warehouse": self.plant.pk, "reason": "Re-sort",
            "rebatched_on": "2026-06-01", "taken": [{"lot": self.b1.pk, "quantity": "300"}],
            "made": [{"code": "RB-A", "quantity": "200"}, {"code": "RB-B", "quantity": "100"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(sorted((row["side"], row["lot_code"]) for row in body["lines"]),
                         [("in", self.b1.code), ("out", "RB-A"), ("out", "RB-B")])
        response = client.post(f"/api/manufacturing/rebatches/{body['id']}/void/",
                               {"reason": "Wrong"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post("/api/manufacturing/rebatches/record/", {
            "item": self.bag.pk, "warehouse": self.plant.pk, "reason": "Re-sort",
            "taken": [{"lot": self.b1.pk, "quantity": "300"}],
            "made": [{"code": "RB-A", "quantity": "300"}, {"code": "", "quantity": "0"}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        response = client.post("/api/manufacturing/rebatches/record/", {
            "item": self.bag.pk, "warehouse": self.plant.pk, "reason": "Re-sort",
            "taken": [{"lot": self.b1.pk, "quantity": "300"}],
            "made": [{"code": "RB-A", "quantity": "150"}, {"code": "RB-C", "quantity": "150"}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("RB-A has held stock before", response.content.decode())
