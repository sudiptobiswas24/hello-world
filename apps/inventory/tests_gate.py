"""
The gate register, weighings and gate passes (L17, L18), driven through the API as the people who do
it: the Security Guard at the gate, the Stores Manager who approves a pass and may type a weight.
Numbers worked in /home/user/work/calc_fix_gate/scenarios.py (A, D, E).
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone
from rest_framework.test import APIClient

from apps.assets.tests import AssetTestCase
from apps.core.models import Company, UnitOfMeasure
from apps.manufacturing.station_scale import ScaleReading, post_reading

from .gate import GatePass, GateVisit, PassStatus, VisitStatus, Weighing, overdue_passes
from .models import Item, MovementType, StockMovement, Warehouse

D = Decimal


class GateTestCase(AssetTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category="weight")
        self.polymer = Item.objects.create(sku="PP-RAFFIA", name="Polypropylene raffia", uom=self.kg)
        self.guard = self.client_as("Security Guard")
        self.stores = self.client_as("Stores Manager")

    def client_as(self, role):
        user = User.objects.create_user("api_" + role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        client.user = user
        return client

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, getattr(response, "data", response))
        return response.data

    def visit(self, purpose="inward_order", vehicle="mh 12 ab 1234"):
        return self.ok(self.guard.post("/api/inventory/gate-visits/", {
            "vehicle_number": vehicle, "driver_name": "Ramesh", "driver_mobile": "9800000000",
            "transporter_name": "Speedy Roadways", "purpose": purpose, "reference": "PO-2026-00001",
            "carried": "400 bags polymer", "in_at": "2020-01-01T00:00:00Z"}, format="json"), 201)

    def weigh(self, weighing, side, source, kg=None, client=None, **extra):
        if source == "scale":
            # Where the readings are: the bridge holds the reading and marks it used (manufacturing).
            return (client or self.guard).post(f"/api/manufacturing/weighbridge/{weighing}/from-scale/",
                                               {"side": side}, format="json")
        return (client or self.guard).post(f"/api/inventory/weighings/{weighing}/record/",
                                           {"side": side, "source": source, "kg": kg, **extra}, format="json")


class VisitTests(GateTestCase):
    def test_a_visit_is_numbered_timed_by_the_gate_and_never_deleted(self):
        before = timezone.now()
        visit = self.visit()
        self.assertTrue(visit["number"].startswith("GATE-"))
        self.assertEqual(visit["vehicle_number"], "MH12AB1234")
        made = GateVisit.objects.get(pk=visit["id"])
        self.assertGreaterEqual(made.in_at, before, "the gate's clock, not the time typed")
        self.assertIn(self.guard.delete(f"/api/inventory/gate-visits/{visit['id']}/").status_code, (403, 405))
        with self.assertRaisesMessage(ValidationError, "cancelled, never deleted"):
            made.delete()

    def test_a_vehicle_leaves_through_its_out_entry_and_what_it_recorded_then_stands(self):
        visit = self.visit()
        left = self.ok(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/"))
        self.assertEqual(left["status"], "out")
        self.assertIsNotNone(left["out_at"])
        refused = self.guard.patch(f"/api/inventory/gate-visits/{visit['id']}/", {"driver_name": "Suresh"},
                                   format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/").status_code, 400)

    def test_a_visit_left_open_past_the_set_hours_shows_on_the_health_check(self):
        from apps.web.health import integrity

        visit = self.visit()
        finding = lambda: next(row for row in integrity(fresh=True)["findings"] if row["key"] == "vehicles_in")
        self.assertEqual((finding()["ok"], finding()["count"]), (True, 0))
        GateVisit.objects.filter(pk=visit["id"]).update(in_at=timezone.now() - datetime.timedelta(hours=13))
        self.assertEqual((finding()["ok"], finding()["count"]), (False, 1))
        Company.objects.update(gate_visit_open_hours=14)
        self.assertEqual((finding()["ok"], finding()["count"]), (True, 0))


class WeighingTests(GateTestCase):
    def start(self, visit):
        return self.ok(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/weigh/", {"scale_code": "WB-1"},
                                       format="json"), 201)

    def test_gross_from_the_scale_tare_typed_by_who_may_net_derived_and_typed_marked(self):
        visit = self.visit()
        weighing = self.start(visit)
        self.assertEqual(self.weigh(weighing["id"], "gross", "scale").status_code, 400, "nothing reported yet")
        post_reading("WB-1", "25860.000", True)
        gross = self.ok(self.weigh(weighing["id"], "gross", "scale"))
        self.assertEqual((gross["gross_kg"], gross["gross_source"]), ("25860.000", "scale"))
        reading = ScaleReading.objects.get()
        self.assertEqual((reading.weighing_id, reading.weighed_side), (weighing["id"], "gross"),
                         "the reading holds the pointer; the weighing keeps its figures")
        self.assertEqual(Weighing.objects.get(pk=weighing["id"]).gross_at, reading.read_at)
        refused = self.guard.post(f"/api/inventory/weighings/{weighing['id']}/record/",
                                  {"side": "tare", "source": "scale", "kg": "1"}, format="json")
        self.assertIn("taken from the weighbridge's reading", str(refused.data), "no figure sent as the scale's")
        # The guard may not type a weight; the Stores Manager may, with a reason.
        refused = self.weigh(weighing["id"], "tare", "typed", "15920", reason="scale_offline")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("may type", str(refused.data))
        self.assertEqual(self.weigh(weighing["id"], "tare", "typed", "15920", client=self.stores).status_code, 400,
                         "a typed weight gives its reason")
        done = self.ok(self.weigh(weighing["id"], "tare", "typed", "15920", client=self.stores,
                                  reason="scale_offline"))
        self.assertEqual(done["net_kg"], D("9940.000"))
        self.assertEqual(done["typed"], {"gross": False, "tare": True, "any": True})
        self.assertEqual(done["state"], "weighed")
        self.assertEqual(self.weigh(weighing["id"], "tare", "typed", "15900", client=self.stores,
                                    reason="other", note="again").status_code, 400, "weighed once; replaced if wrong")

    def test_one_reading_weighs_one_side_once(self):
        first, second = self.start(self.visit()), self.start(self.visit(vehicle="MH12AB9999"))
        post_reading("WB-1", "25860.000", True)
        self.ok(self.weigh(first["id"], "gross", "scale"))
        refused = self.weigh(second["id"], "gross", "scale")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("already weighed", str(refused.data))

    def test_a_vehicle_is_weighed_once_at_a_time_and_cannot_leave_half_weighed(self):
        visit = self.visit()
        weighing = self.start(visit)
        self.assertEqual(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/weigh/").status_code, 400)
        post_reading("WB-1", "25860.000", True)
        self.ok(self.weigh(weighing["id"], "gross", "scale"))
        refused = self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("no tare", str(refused.data))
        self.assertEqual(self.guard.post(f"/api/inventory/gate-visits/{self.visit(purpose='visitor')['id']}/weigh/"
                                         ).status_code, 400, "a visitor is not weighed")

    def test_a_weighing_replaced_keeps_its_predecessor_as_it_was(self):
        visit = self.visit()
        weighing = self.start(visit)
        post_reading("WB-1", "25860.000", True)
        self.ok(self.weigh(weighing["id"], "gross", "scale"))
        self.ok(self.weigh(weighing["id"], "tare", "typed", "15920", client=self.stores, reason="calibration"))
        self.assertEqual(self.stores.post(f"/api/inventory/weighings/{weighing['id']}/replace/").status_code, 400,
                         "replaced with a reason")
        following = self.ok(self.stores.post(f"/api/inventory/weighings/{weighing['id']}/replace/",
                                             {"reason": "tare typed while the bridge was down"}, format="json"), 201)
        old = Weighing.objects.get(pk=weighing["id"])
        self.assertIsNotNone(old.replaced_at)
        self.assertEqual((old.gross_kg, old.tare_kg, old.net_kg()), (D("25860.000"), D("15920.000"), D("9940.000")))
        self.assertEqual(self.ok(self.guard.get(f"/api/inventory/weighings/{old.pk}/"))["replaced_by"],
                         following["id"])
        self.assertEqual(self.weigh(old.pk, "gross", "typed", "1", client=self.stores, reason="power").status_code,
                         400, "a replaced weighing is weighed no more")
        post_reading("WB-1", "25860.000", False)  # off the scale and back: a new settled reading
        post_reading("WB-1", "25860.000", True)
        self.ok(self.weigh(following["id"], "gross", "scale"))
        post_reading("WB-1", "15900.000", True)
        again = self.ok(self.weigh(following["id"], "tare", "scale"))
        self.assertEqual((again["net_kg"], again["typed"]["any"]), (D("9960.000"), False))
        with self.assertRaisesMessage(ValidationError, "never delete"):
            old.delete()

    def test_a_visit_cancelled_after_its_weighing_keeps_the_weighing_read_as_cancelled(self):
        visit = self.visit()
        weighing = self.start(visit)
        self.ok(self.weigh(weighing["id"], "gross", "typed", "25860", client=self.stores, reason="power"))
        self.assertEqual(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/cancel/").status_code, 400)
        cancelled = self.ok(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/cancel/",
                                            {"reason": "entered twice"}, format="json"))
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["weighings"][0]["state"], "cancelled")
        self.assertEqual(cancelled["weighings"][0]["gross_kg"], "25860.000")


class GatePassTests(GateTestCase):
    def setUp(self):
        super().setUp()
        self.motor = self.asset()
        self.controller = self.client_as("Controller")
        self.rollers = Item.objects.create(sku="ROLLER", name="Guide roller", uom=self.kg)

    def returnable(self, visit, expected=None):
        made = self.ok(self.guard.post("/api/inventory/gate-passes/", {
            "kind": "returnable", "party_name": "Shree Rewinding Works", "purpose": "Rewind",
            "expected_return_date": str(expected or timezone.localdate() + datetime.timedelta(days=7)),
            "visit": visit["id"]}, format="json"), 201)
        for body in ({"asset": self.motor.pk, "quantity": "1"}, {"item": self.rollers.pk, "quantity": "3"}):
            self.ok(self.guard.post("/api/inventory/gate-pass-lines/", {"gate_pass": made["id"], **body},
                                    format="json"), 201)
        return self.ok(self.guard.get(f"/api/inventory/gate-passes/{made['id']}/"))

    def out_on(self):
        return self.ok(self.controller.get(f"/api/assets/assets/{self.motor.pk}/"))["out_on_pass"]

    def test_a_returnable_pass_approved_out_with_its_vehicle_and_back_in_part_then_damaged(self):
        visit = self.visit(purpose="gate_pass")
        made = self.returnable(visit)
        self.assertTrue(made["number"].startswith("RGP-"))
        self.assertEqual(self.guard.post(f"/api/inventory/gate-passes/{made['id']}/approve/").status_code, 403)
        refused = self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/")
        self.assertEqual(refused.status_code, 400, "not approved: the vehicle does not take it")
        self.ok(self.stores.post(f"/api/inventory/gate-passes/{made['id']}/approve/"))
        self.assertIsNone(self.out_on(), "approved, not yet gone")
        self.ok(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/"))
        self.assertEqual(self.out_on()["number"], made["number"], "the asset register reads it as out")
        lines = {row["label"]: row["id"] for row in made["lines"]}
        roller, motor = lines["ROLLER Guide roller"], lines[f"{self.motor.number} Lathe"]
        url = f"/api/inventory/gate-passes/{made['id']}/returned/"
        self.assertEqual(self.guard.post(url, {"line": roller, "quantity": "4"}, format="json").status_code, 400)
        self.assertEqual(self.guard.post(url, {"line": roller, "quantity": "2",
                                               "returned_on": str(timezone.localdate() - datetime.timedelta(days=1))},
                                         format="json").status_code, 400, "back before it went")
        part = self.ok(self.guard.post(url, {"line": roller, "quantity": "2"}, format="json"))
        self.assertEqual([row["outstanding"] for row in part["lines"] if row["id"] == roller], [D("1")])
        self.assertEqual(part["status"], "out")
        self.assertEqual(self.guard.post(url, {"line": roller, "quantity": "1", "condition": "damaged"},
                                         format="json").status_code, 400, "damage is said")
        self.ok(self.guard.post(url, {"line": roller, "quantity": "1", "condition": "damaged",
                                      "note": "bearing seized"}, format="json"))
        back = self.ok(self.guard.post(url, {"line": motor, "quantity": "1"}, format="json"))
        self.assertEqual(back["status"], "returned")
        self.assertIsNone(self.out_on())
        damaged = [entry for row in back["lines"] for entry in row["returns"] if entry["condition"] == "damaged"]
        self.assertEqual([(entry["quantity"], entry["note"]) for entry in damaged], [("1.0000", "bearing seized")])
        # A return recorded wrongly is cancelled, kept, and the motor reads as out again.
        entry = next(e for row in back["lines"] if row["id"] == motor for e in row["returns"])
        again = self.ok(self.guard.post(f"/api/inventory/gate-passes/{made['id']}/cancel-return/",
                                        {"entry": entry["id"], "reason": "the motor was another"}, format="json"))
        self.assertEqual(again["status"], "out")
        self.assertEqual(self.out_on()["number"], made["number"])

    def test_an_overdue_returnable_pass_is_listed_until_it_is_back(self):
        visit = self.visit(purpose="gate_pass")
        made = self.returnable(visit)
        self.ok(self.stores.post(f"/api/inventory/gate-passes/{made['id']}/approve/"))
        self.ok(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/"))
        listed = lambda: [row["number"] for row in self.ok(self.guard.get("/api/inventory/gate-passes/?overdue=1"))]
        self.assertEqual(listed(), [])
        GatePass.objects.filter(pk=made["id"]).update(expected_return_date=timezone.localdate()
                                                      - datetime.timedelta(days=1))
        self.assertEqual(listed(), [made["number"]])
        for row in made["lines"]:
            self.ok(self.guard.post(f"/api/inventory/gate-passes/{made['id']}/returned/",
                                    {"line": row["id"], "quantity": str(row["quantity"])}, format="json"))
        self.assertEqual(listed(), [])
        self.assertEqual(list(overdue_passes()), [])

    def test_a_pass_is_approved_before_its_vehicle_leaves_never_after(self):
        visit = self.visit(purpose="gate_pass")
        made = self.returnable(visit)
        gate_pass = GatePass.objects.get(pk=made["id"])
        GateVisit.objects.filter(pk=visit["id"]).update(status=VisitStatus.OUT, out_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "has left"):
            gate_pass.approve(self.stores.user)
        self.assertEqual(GatePass.objects.get(pk=made["id"]).status, PassStatus.DRAFT)

    def test_an_asset_is_out_on_one_pass_at_a_time(self):
        first = self.returnable(self.visit(purpose="gate_pass"))
        self.ok(self.stores.post(f"/api/inventory/gate-passes/{first['id']}/approve/"))
        second = self.returnable(self.visit(purpose="gate_pass", vehicle="MH12AB9999"))
        refused = self.stores.post(f"/api/inventory/gate-passes/{second['id']}/approve/")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("out on another pass", str(refused.data))

    def test_stock_leaving_for_good_names_the_movement_that_took_it_off_the_shelf(self):
        warehouse = Warehouse.objects.create(code="SCRAP", name="Scrap yard")
        made = self.ok(self.guard.post("/api/inventory/gate-passes/", {
            "kind": "non_returnable", "party_name": "Kabadi Traders", "expected_return_date": "2030-01-01"},
            format="json"), 201)
        self.assertTrue(made["number"].startswith("NRGP-"))
        self.assertIsNone(made["expected_return_date"])
        line = self.ok(self.guard.post("/api/inventory/gate-pass-lines/", {
            "gate_pass": made["id"], "item": self.polymer.pk, "quantity": "6"}, format="json"), 201)
        refused = self.stores.post(f"/api/inventory/gate-passes/{made['id']}/approve/")
        self.assertIn("name the delivery, write-off or challan", str(refused.data))
        StockMovement.objects.create(item=self.polymer, warehouse=warehouse, uom=self.kg,
                                     movement_type=MovementType.ADJUSTMENT, quantity=D("5"), document_quantity=D("5"),
                                     unit_cost=D("95"), value=D("475"), occurred_at=timezone.now(), reference="ADJ-0")
        out = StockMovement.objects.create(item=self.polymer, warehouse=warehouse, uom=self.kg,
                                           movement_type=MovementType.ADJUSTMENT, quantity=D("-5"),
                                           document_quantity=D("-5"), unit_cost=D("95"), value=D("-475"),
                                           occurred_at=timezone.now(), reference="ADJ-1")
        self.ok(self.guard.patch(f"/api/inventory/gate-pass-lines/{line['id']}/", {"movement": out.pk},
                                 format="json"))
        refused = self.stores.post(f"/api/inventory/gate-passes/{made['id']}/approve/")
        self.assertIn("took 5 off the shelf", str(refused.data))
        self.ok(self.guard.patch(f"/api/inventory/gate-pass-lines/{line['id']}/", {"quantity": "5"}, format="json"))
        self.assertEqual(self.ok(self.stores.post(f"/api/inventory/gate-passes/{made['id']}/approve/"))["status"],
                         "approved")
        self.assertEqual(self.guard.patch(f"/api/inventory/gate-pass-lines/{line['id']}/", {"quantity": "4"},
                                          format="json").status_code, 400, "approved: it stands")

    def test_a_cancelled_pass_stays_numbered_and_one_out_cannot_be_cancelled(self):
        visit = self.visit(purpose="gate_pass")
        made = self.returnable(visit)
        self.assertIn(self.guard.delete(f"/api/inventory/gate-passes/{made['id']}/").status_code, (403, 405))
        self.ok(self.stores.post(f"/api/inventory/gate-passes/{made['id']}/approve/"))
        self.ok(self.guard.post(f"/api/inventory/gate-visits/{visit['id']}/leave/"))
        refused = self.guard.post(f"/api/inventory/gate-passes/{made['id']}/cancel/", {"reason": "x"}, format="json")
        self.assertEqual(refused.status_code, 400)


class TheGuardSeesNoPricesTests(GateTestCase):
    def test_the_guard_reads_the_gate_and_names_items_and_assets_by_code_alone(self):
        self.asset()
        for url in ("/api/inventory/items/", "/api/assets/assets/", "/api/core/parties/",
                    "/api/purchasing/purchase-orders/", "/api/purchasing/weight-claims/"):
            self.assertEqual(self.guard.get(url).status_code, 403, url)
        items = self.ok(self.guard.get("/api/inventory/gate-passes/items/?search=raffia"))
        self.assertEqual(items, [{"id": self.polymer.pk, "sku": "PP-RAFFIA", "name": "Polypropylene raffia",
                                  "uom": "kg"}])
        assets = self.ok(self.guard.get("/api/inventory/gate-passes/assets/"))
        self.assertEqual(set(assets[0]), {"id", "number", "name"})
        for url in ("/api/inventory/gate-visits/", "/api/inventory/weighings/", "/api/inventory/gate-passes/"):
            self.assertEqual(self.guard.get(url).status_code, 200, url)


class InventoryImportsNothingFromManufacturingTests(TestCase):
    """manufacturing depends on inventory; inventory naming manufacturing back is a cycle (g25 review)."""

    def test_no_inventory_module_imports_manufacturing_anywhere_in_it(self):
        from apps.core.management.commands.audit_invariants import Command, source_of

        self.assertEqual(Command().imports_against_the_module_graph({"inventory": source_of("inventory")}), [])

    def test_the_check_sees_one_inside_a_function(self):
        from pathlib import Path

        from apps.core.management.commands.audit_invariants import Command

        planted = {Path("apps/inventory/planted.py"): "def weigh():\n    from apps.manufacturing.station_scale "
                                                      "import take_reading\n    import apps.manufacturing.daily\n"}
        found = Command().imports_against_the_module_graph({"inventory": planted})
        self.assertEqual([detail for _, detail in found], [
            "apps/inventory/planted.py:2 imports apps.manufacturing.station_scale: inventory may not depend on it.",
            "apps/inventory/planted.py:3 imports apps.manufacturing.daily: inventory may not depend on it."])


@tag("migration")
class ThePointerCarriedToTheReadingMigrationTests(TransactionTestCase):
    """manufacturing 0088 over a database that ran the first draft of inventory 0042 (the weighing pointing at its readings)."""

    BEFORE = [("manufacturing", "0087_fabric_roll_lot_protected"),
              ("inventory", "0042_item_weight_tolerance_percent_gatepass_gatepassline_and_more")]
    AFTER = [("manufacturing", "0088_scale_reading_names_its_weighing")]

    def test_each_reading_is_given_the_weighing_that_named_it_and_the_stray_columns_go(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        old = executor.loader.project_state(self.BEFORE).apps
        with connection.cursor() as cursor:
            for side in ("gross", "tare"):
                cursor.execute(f"ALTER TABLE inventory_weighing ADD COLUMN {side}_reading_id integer NULL")
        Reading = old.get_model("manufacturing", "ScaleReading")
        moment = timezone.now()
        gross, tare, untouched = (Reading.objects.create(scale_code="WB-1", gross_kg=kg, stable=True, read_at=moment)
                                  for kg in (D("25860"), D("15920"), D("100")))
        Visit = old.get_model("inventory", "GateVisit")
        visit = Visit.objects.create(number="GATE-1", vehicle_number="MH12AB1234", driver_name="R",
                                     purpose="inward_order", in_at=moment)
        weighing = old.get_model("inventory", "Weighing").objects.create(number="WB-1", visit=visit)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE inventory_weighing SET gross_reading_id = %s, tare_reading_id = %s WHERE id = %s",
                           [gross.pk, tare.pk, weighing.pk])
        executor = MigrationExecutor(connection)
        executor.migrate(self.AFTER)
        new = executor.loader.project_state(self.AFTER).apps.get_model("manufacturing", "ScaleReading")
        self.assertEqual(sorted(new.objects.values_list("pk", "weighing_id", "weighed_side")),
                         sorted([(gross.pk, weighing.pk, "gross"), (tare.pk, weighing.pk, "tare"),
                                 (untouched.pk, None, "")]))
        with connection.cursor() as cursor:
            columns = {c.name for c in connection.introspection.get_table_description(cursor, "inventory_weighing")}
        self.assertFalse({"gross_reading_id", "tare_reading_id"} & columns)
        # And back: the pointer goes with its field; the readings stay.
        executor = MigrationExecutor(connection)
        executor.migrate([("manufacturing", "0087_fabric_roll_lot_protected")])
        self.assertEqual(old.get_model("manufacturing", "ScaleReading").objects.count(), 3)
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
