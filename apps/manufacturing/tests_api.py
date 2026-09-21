"""
The door to manufacturing.

Everything here was built, tested and reachable only from a Python
shell: the specifications, the explosion, the work order and every
posting on it. These tests are about reachability, and about a refusal
arriving as an answer rather than as a crash — the domain behaviour has
its own tests.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .tests_orders import TODAY, RunTestCase
from .tests_woven import WovenTestCase


class ManufacturingApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_every_collection_answers(self):
        for path in (
            "tape-specifications", "fabric-specifications", "bag-specifications",
            "boms", "bom-components", "bom-byproducts", "work-centres",
            "work-orders", "material-issues", "material-issue-lines",
            "production-entries", "production-byproducts",
        ):
            response = self.client.get(f"/api/manufacturing/{path}/")
            self.assertEqual(response.status_code, 200, path)

    def test_a_bom_carries_its_components_and_their_gross(self):
        response = self.client.get(f"/api/manufacturing/boms/{self.bom.pk}/")
        self.assertEqual(response.status_code, 200)
        virgin = [
            row for row in response.data["components"]
            if row["item"] == self.virgin.pk
        ][0]
        # 75 net, 3% lost on the input: 77.319588 gross.
        self.assertAlmostEqual(
            Decimal(str(virgin["gross_quantity"])), Decimal("77.319588"), places=5
        )

    def test_the_explosion_is_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/explosion/?quantity=1000"
        )
        self.assertEqual(response.status_code, 200)
        skus = {row["item"] for row in response.data}
        self.assertIn("PP-RAFFIA", skus)
        self.assertTrue(any(row["is_byproduct"] for row in response.data))

    def test_the_requirements_say_what_the_yard_is_short_of(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/requirements/"
            "?quantity=10000&warehouse=P"
        )
        self.assertEqual(response.status_code, 200)
        virgin = [r for r in response.data if r["item"] == "PP-RAFFIA"][0]
        # 7,731.96 kg wanted against 2,000 on the shelf.
        self.assertGreater(Decimal(str(virgin["short_by"])), Decimal("5000"))

    def test_the_material_balance_is_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/material-balance/?quantity=1000"
        )
        self.assertEqual(response.status_code, 200)
        regrind = [r for r in response.data if r["item"] == "REGRIND"][0]
        self.assertGreater(
            Decimal(str(regrind["net"])), Decimal("100"),
        )

    def test_an_unparseable_quantity_is_a_sentence_and_not_a_crash(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/explosion/?quantity=lots"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be a number", str(response.data))

    def test_a_run_can_be_driven_from_end_to_end(self):
        order = self.order()
        base = f"/api/manufacturing/work-orders/{order.pk}/"
        self.assertEqual(self.client.post(f"{base}release/").status_code, 200)
        order.refresh_from_db()
        self.assertIsNotNone(order.planned_unit_cost)

        issue = self.full_issue(order)
        self.assertEqual(
            self.client.post(
                f"/api/manufacturing/material-issues/{issue.pk}/post/"
            ).status_code, 200,
        )
        entry = self.produce(order, "1000", byproducts=[(self.regrind, "24.7423")])
        self.assertEqual(
            self.client.post(
                f"/api/manufacturing/production-entries/{entry.pk}/post/"
            ).status_code, 200,
        )
        self.assertEqual(self.client.post(f"{base}close/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_the_variance_is_reachable_in_kilogrammes(self):
        order = self.order()
        order.release(TODAY)
        rows = [
            (component.item, component.quantity_required)
            for component in order.components.all()
        ]
        rows[0] = (self.virgin, rows[0][1] + Decimal("50"))
        self.issue(order, rows).post()
        response = self.client.get(
            f"/api/manufacturing/work-orders/{order.pk}/material-variance/"
        )
        self.assertEqual(response.status_code, 200)
        virgin = [r for r in response.data if r["item"] == "PP-RAFFIA"][0]
        self.assertAlmostEqual(
            Decimal(str(virgin["difference"])), Decimal("50"), places=2
        )

    def test_a_refusal_arrives_as_a_sentence(self):
        order = self.order()
        order.release(TODAY)
        order.close(TODAY)
        response = self.client.post(
            f"/api/manufacturing/work-orders/{order.pk}/release/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("only a draft order can be released", str(response.data))


class SpecificationApiTests(WovenTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_a_fabric_specification_carries_its_derived_figures(self):
        fabric = self.fabric()
        response = self.client.get(
            f"/api/manufacturing/fabric-specifications/{fabric.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(
            Decimal(str(response.data["gsm"])), Decimal("87.489"), places=3
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["metres_per_kg"])), Decimal("9.525"), places=3
        )

    def test_a_bag_specification_carries_what_a_sack_weighs(self):
        bag = self.bag()
        response = self.client.get(
            f"/api/manufacturing/bag-specifications/{bag.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["cut_length_cm"])), Decimal("105"))
        self.assertAlmostEqual(
            Decimal(str(response.data["bag_grams"])), Decimal("111.4363"), places=3
        )

    def test_a_mesh_that_cannot_reach_the_quoted_gsm_is_refused_as_a_sentence(self):
        response = self.client.post("/api/manufacturing/fabric-specifications/", {
            "code": "F-BAD", "fabric_item": self.fabric_item.pk,
            "warp_tape": self.tape().pk, "ends_per_inch": "8",
            "picks_per_inch": "8", "lay_flat_width_cm": "60",
            "weave": "tubular", "target_gsm": "87.5",
            "gsm_tolerance_percent": "5",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("70.0 GSM", str(response.data))

    def test_a_computed_bom_reports_that_it_is_computed(self):
        tape = self.tape()
        response = self.client.get(f"/api/manufacturing/boms/{tape.bom.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_computed"])


class TheAdminDoesNotOfferWhatTheModelWillRefuseTests(RunTestCase):
    """
    The models refuse to edit a closed run or a posted document. An
    admin page that lets somebody retype a closed run's quantity and
    then refuses the save is a worse answer than one that does not
    offer the box.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.admin.sites import site

        self.site = site
        self.request = None

    def readonly(self, obj):
        admin = self.site._registry[type(obj)]
        return set(admin.get_readonly_fields(self.request, obj))

    def test_a_closed_run_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        self.assertNotIn("quantity_ordered", self.readonly(order))
        order.close(TODAY)
        self.assertIn("quantity_ordered", self.readonly(order))
        self.assertIn("bom", self.readonly(order))

    def test_a_cancelled_run_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        order.cancel()
        self.assertIn("quantity_ordered", self.readonly(order))

    def test_a_posted_issue_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "10")])
        self.assertNotIn("warehouse", self.readonly(document))
        document.post()
        self.assertIn("warehouse", self.readonly(document))

    def test_a_posted_production_entry_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        self.full_issue(order).post()
        entry = self.produce(order, "1000")
        self.assertNotIn("quantity_produced", self.readonly(entry))
        entry.post()
        self.assertIn("quantity_produced", self.readonly(entry))

    def test_a_typed_bom_stays_editable(self):
        # The guard must let the ordinary case through, or it is only a
        # refusal. This BOM was typed, not computed.
        self.assertFalse(self.bom.is_computed)
        self.assertNotIn("quantity_produced", self.readonly(self.bom))
