"""Reviewer probes for the gate and the weighbridge (review_gate). Each asserts what SHOULD be true."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType
from apps.core.models import UnitOfMeasure
from apps.inventory.gate import GatePass, GatePassLine, GateVisit, Weighing
from apps.inventory.models import Item
from apps.manufacturing.station_scale import post_reading, weigh_from_bridge

from .models import GoodsReceipt
from .tests_weight_claims import DAY, D, WeighedReceiptTestCase
from .weight_claims import WeightClaim


class ClaimProbes(WeighedReceiptTestCase, TestCase):
    def settle(self, claim, **body):
        return self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/settle/", body, format="json")

    def test_c1_supplier_claim_when_the_bill_price_differs_from_the_order_price(self):
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        bill_line = self.bill(line, "10000", "96")
        claim = self.raise_claim(received, against="supplier")
        r = self.settle(claim, bill_line=bill_line.pk, on_date=str(DAY))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.balance(self.grni), D("0"), "GRNI must clear")

    def test_c2_transporter_settle_withdraw_raise_again_settle(self):
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        self.bill(line)
        c1 = self.raise_claim(received, against="transporter", transporter=self.transporter.pk)
        self.assertEqual(self.settle(c1).status_code, 200)
        w = self.manager.post(f"/api/purchasing/weight-claims/{c1['id']}/withdraw/", {"reason": "x"}, format="json")
        self.assertEqual(w.status_code, 200, w.data)
        c2 = self.raise_claim(received, against="transporter", transporter=self.transporter.pk)
        self.assertEqual(self.settle(c2).status_code, 200)
        self.assertEqual((self.balance(self.grni), self.balance(self.claims)), (D("0"), D("5700.00")))

    def test_c3_claim_on_a_receipt_returned_to_the_vendor(self):
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        self.bill(line)
        received.create_return()
        r = self.manager.post("/api/purchasing/weight-claims/", {"receipt": received.pk, "against": "transporter",
                                                                 "transporter": self.transporter.pk}, format="json")
        if r.status_code == 201:
            s = self.settle(r.data)
            claims, grni = self.balance(self.claims), self.balance(self.grni)
            self.fail(f"claim raised on a receipt already returned; settle={s.status_code}; "
                      f"claims={claims} grni={grni}; the goods are back with the vendor")

    def test_c4_edges_of_the_tolerance(self):
        # exactly at: net 9950.000, short 50 == allowance 50
        at = self.receipt(self.order(), "9950", self.weighing(gross="25870", vehicle="MH12AB0001"))
        self.raise_claim(at, status=400, against="supplier")
        # one gram past
        past = self.receipt(self.order(), "9949.999", self.weighing(gross="25869.999", vehicle="MH12AB0002"))
        c = self.raise_claim(past, against="supplier")
        self.assertEqual(c["quantity"], "50.0010")
        self.assertEqual(c["value"], "4750.10")  # 50.001 * 95 = 4750.095 -> 4750.10

    def test_c5_item_stocked_in_tonnes(self):
        t = UnitOfMeasure.objects.create(code="t", name="Tonne", category="weight")
        from apps.inventory.models import ItemUnit
        item = Item.objects.create(sku="PP-T", name="Polymer by the tonne", uom=t)
        ItemUnit.objects.create(item=item, uom=self.kg, factor=D("0.001"))
        line = self.order("10", "95000", item, t)
        received = self.receipt(line, "9.94", self.weighing())
        c = self.raise_claim(received, against="supplier")
        self.assertEqual((c["quantity"], c["value"]), ("0.0600", "5700.00"))

    def test_c6_half_and_half_each_settled(self):
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        bl = self.bill(line)
        a = self.raise_claim(received, against="transporter", transporter=self.transporter.pk, quantity="30")
        b = self.raise_claim(received, against="supplier", quantity="30")
        self.assertEqual(self.settle(a).status_code, 200)
        r = self.settle(b, bill_line=bl.pk, on_date=str(DAY))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.balance(self.grni), D("0"))

    def test_c7_a_posted_receipt_cannot_have_its_documented_weight_rewritten(self):
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        su = User.objects.create_superuser("root", "r@x.com", "p")
        c = APIClient(); c.force_authenticate(su)
        r = c.patch(f"/api/purchasing/goods-receipts/{received.pk}/", {"documented_weight_kg": "9940"}, format="json")
        received.refresh_from_db()
        self.assertEqual(received.documented_weight_kg, D("10000"), f"posted receipt edited: {r.status_code}")

    def test_c8_posted_receipt_cannot_be_pointed_at_another_weighing(self):
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        other = self.weighing(gross="25880", vehicle="MH12AB7777")
        su = User.objects.create_superuser("root", "r@x.com", "p")
        c = APIClient(); c.force_authenticate(su)
        c.patch(f"/api/purchasing/goods-receipts/{received.pk}/", {"weighing": other.pk}, format="json")
        received.refresh_from_db()
        self.assertEqual(received.weighing_id, Weighing.objects.filter(visit__vehicle_number="MH12AB1234").first().pk)


class WeightProbes(WeighedReceiptTestCase, TestCase):
    def guard(self):
        u = User.objects.create_user("guard"); u.groups.add(Group.objects.get(name="Security Guard"))
        c = APIClient(); c.force_authenticate(u); return c, u

    def test_w1_guard_cannot_type_a_weight(self):
        c, _ = self.guard()
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        w = v.start_weighing("WB-1")
        r = c.post(f"/api/inventory/weighings/{w.pk}/record/", {"side": "gross", "source": "typed", "kg": "100",
                                                                "reason": "power"}, format="json")
        self.assertGreaterEqual(r.status_code, 400)
        w.refresh_from_db(); self.assertIsNone(w.gross_kg)

    def test_w2_a_reading_used_twice(self):
        post_reading("WB-1", "25860", True)
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        w = v.start_weighing("WB-1")
        weigh_from_bridge(w, "gross")
        with self.assertRaises(ValidationError):
            weigh_from_bridge(Weighing.objects.get(pk=w.pk), "tare")
        v2 = GateVisit.objects.create(vehicle_number="MH12AB2", driver_name="R", purpose="inward_order")
        w2 = v2.start_weighing("WB-1")
        with self.assertRaises(ValidationError):
            weigh_from_bridge(w2, "gross")

    def test_w3_tare_above_or_equal_gross(self):
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        w = v.start_weighing("WB-1")
        w.record("gross", "typed", "1000", reason="power", by=self.typist)
        for kg in ("1000", "1000.001"):
            with self.assertRaises(ValidationError):
                Weighing.objects.get(pk=w.pk).record("tare", "typed", kg, reason="power", by=self.typist)

    def test_w4_a_typed_weight_by_a_guard_user_object_directly(self):
        _, u = self.guard()
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        w = v.start_weighing("WB-1")
        with self.assertRaises(ValidationError):
            w.record("gross", "typed", "1000", reason="power", by=u)

    def test_w5_a_guard_cannot_burn_a_production_scale_reading(self):
        post_reading("TAPE-LINE-3", "42", True)  # a factory scale, nothing to do with the gate
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        c, _ = self.guard()
        w = c.post(f"/api/inventory/gate-visits/{v.pk}/weigh/", {"scale_code": "TAPE-LINE-3"}, format="json")
        r = c.post(f"/api/manufacturing/weighbridge/{w.data['id']}/from-scale/", {"side": "gross"}, format="json")
        self.assertGreaterEqual(r.status_code, 400, "a vehicle weighed on a roll scale's reading")

    def test_w6_receipt_in_tonnes_against_kg_net(self):
        t = UnitOfMeasure.objects.create(code="t", name="Tonne", category="weight")
        from apps.inventory.models import ItemUnit
        ItemUnit.objects.create(item=self.polymer, uom=t, factor=D("1000"))
        line = self.order("10", "95000", None, t)
        r = self.receipt(line, "9.94", self.weighing())
        refused = self.receipt(self.order("10", "95000", None, t), "10", self.weighing(vehicle="MH12AB9"), post=False)
        with self.assertRaises(ValidationError):
            refused.post()
        self.assertTrue(r.posted)


class PassProbes(WeighedReceiptTestCase, TestCase):
    def test_p1_a_pass_is_not_approved_by_its_maker(self):
        maker = User.objects.create_user("sm"); maker.groups.add(Group.objects.get(name="Stores Manager"))
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="gate_pass")
        item = Item.objects.create(sku="R1", name="Roller", uom=self.kg)
        gp = GatePass.objects.create(kind="returnable", party_name="Rewinders", visit=v,
                                     expected_return_date=datetime.date(2099, 1, 1), created_by=maker)
        GatePassLine.objects.create(gate_pass=gp, item=item, quantity=D("3"))
        with self.assertRaises(ValidationError):
            gp.approve(maker)

    def test_p2_an_approved_pass_without_a_vehicle_cannot_be_approved(self):
        item = Item.objects.create(sku="R1", name="Roller", uom=self.kg)
        gp = GatePass.objects.create(kind="returnable", party_name="Rewinders",
                                     expected_return_date=datetime.date(2099, 1, 1))
        GatePassLine.objects.create(gate_pass=gp, item=item, quantity=D("3"))
        su = User.objects.create_superuser("s", "s@x.com", "p")
        try:
            gp.approve(su)
        except ValidationError:
            return
        # approved with no vehicle: no code path takes it OUT, so it never reads as out or overdue
        self.assertFalse(hasattr(GatePass, "go_out_without_a_vehicle"))
        self.fail("approved with no vehicle: status=%s and nothing can ever send it out" % GatePass.objects.get(pk=gp.pk).status)

    def _asset(self):
        from apps.accounting.models import BooksOpening
        from apps.assets.models import AssetCategory, FixedAsset
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        plant, accum = acc("1500", "Plant", AccountType.ASSET), acc("1590", "Accum", AccountType.ASSET)
        dep, disp, op = acc("6100", "Dep", AccountType.EXPENSE), acc("6200", "Disp", AccountType.EXPENSE), acc("3900", "Open", AccountType.EQUITY)
        BooksOpening.objects.create(opened_on=datetime.date(2026, 1, 1), opening_account=op)
        cat = AssetCategory.objects.create(code="PLANT", name="Plant", asset_account=plant, accumulated_account=accum,
                                           expense_account=dep, disposal_account=disp, default_life_months=12)
        a = FixedAsset.objects.create(name="Lathe", category=cat, acquisition_date=datetime.date(2026, 1, 1),
                                      cost=D("12000"), life_months=12)
        a.place_in_service(on_date=datetime.date(2026, 1, 1))
        return a

    def test_p3_an_asset_out_on_a_pass_is_not_disposed_of(self):
        asset = self._asset()
        su = User.objects.create_superuser("s", "s@x.com", "p")
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="gate_pass")
        gp = GatePass.objects.create(kind="returnable", party_name="Repairer", visit=v,
                                     expected_return_date=datetime.date(2099, 1, 1))
        GatePassLine.objects.create(gate_pass=gp, asset=asset, quantity=D("1"))
        gp.approve(su)
        GateVisit.objects.get(pk=v.pk).check_out()
        self.assertEqual(GatePass.objects.get(pk=gp.pk).status, "out")
        with self.assertRaises(ValidationError):
            asset.dispose(on_date=datetime.date(2026, 7, 1))

    def test_p3b_an_asset_approved_on_a_pass_is_not_disposed_of_before_it_leaves(self):
        asset = self._asset()
        su = User.objects.create_superuser("s", "s@x.com", "p")
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="gate_pass")
        gp = GatePass.objects.create(kind="returnable", party_name="Repairer", visit=v,
                                     expected_return_date=datetime.date(2099, 1, 1))
        GatePassLine.objects.create(gate_pass=gp, asset=asset, quantity=D("1"))
        gp.approve(su)
        with self.assertRaises(ValidationError):
            asset.dispose(on_date=datetime.date(2026, 7, 1))

    def test_p4_a_nonreturnable_stock_line_must_name_a_movement_via_the_api(self):
        c = APIClient(); c.force_authenticate(User.objects.create_superuser("s", "s@x.com", "p"))
        item = Item.objects.create(sku="R1", name="Roller", uom=self.kg)
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="gate_pass")
        gp = GatePass.objects.create(kind="non_returnable", party_name="Scrap buyer", visit=v)
        GatePassLine.objects.create(gate_pass=gp, item=item, quantity=D("3"))
        r = c.post(f"/api/inventory/gate-passes/{gp.pk}/approve/", {}, format="json")
        self.assertGreaterEqual(r.status_code, 400)


class RoleProbes(WeighedReceiptTestCase, TestCase):
    def as_role(self, name):
        u = User.objects.create_user("u_" + name.replace(" ", "_")); u.groups.add(Group.objects.get(name=name))
        c = APIClient(); c.force_authenticate(u); return c

    def test_r1_the_guard_reads_no_money_anywhere(self):
        from django.urls import get_resolver
        import re
        # build data: a posted weighed receipt, a claim, a pass with an item line
        line = self.order(); received = self.receipt(line, "9940", self.weighing())
        self.raise_claim(received, against="supplier")
        item = Item.objects.create(sku="R1", name="Roller", uom=self.kg, standard_cost=D("123.45")) if hasattr(Item, "standard_cost") else self.polymer
        g = self.as_role("Security Guard")
        from django.urls import URLPattern, URLResolver, get_resolver

        def walk(patterns, prefix=""):
            for each in patterns:
                text = prefix + str(each.pattern)
                if isinstance(each, URLResolver):
                    yield from walk(each.url_patterns, text)
                elif "<" not in text and "(?P" not in text and "\\." not in text:
                    yield text.replace("^", "").replace("$", "")
        root = {u: "/" + u for u in set(walk(get_resolver().url_patterns)) if u.startswith("api/") and "format" not in u}
        seen, leaks, readable = 0, [], []
        money = re.compile(r"(price|cost|value|amount|rate|total|balance|credit_limit|salary)", re.I)
        for name, url in root.items():
            r = g.get(url)
            if r.status_code != 200 or not hasattr(r, "data"):
                continue
            readable.append(name)
            rows = r.data.get("results", r.data) if hasattr(r.data, "get") else r.data
            blob = str(rows)[:20000]
            hits = set(m.group(1).lower() for m in re.finditer(r"'([a-z_]*(?:price|cost|value|amount|rate|total|balance|salary)[a-z_]*)'", blob))
            if hits:
                leaks.append((name, sorted(hits)))
        self.assertEqual(leaks, [], f"readable by the guard: {readable}")
        for q in ("PP", "Polymer", "RIL", "WCL", "GRN", "PO"):
            r = g.get("/api/web/search/", {"q": q, "search": q})
            blob = str(getattr(r, "data", ""))
            self.assertNotRegex(blob, r"(5700|944300|price)", f"search {q}: {blob[:300]}")
        r = g.get("/api/inventory/gate-passes/items/", {"search": "PP"})
        self.assertEqual(set(r.data[0].keys()), {"id", "sku", "name", "uom"})


class MoreProbes(WeighedReceiptTestCase, TestCase):
    def test_v1_open_too_long_and_cancelled_with_a_weighing(self):
        from django.utils import timezone
        from apps.inventory.gate import open_too_long
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        w = v.start_weighing("WB-1")
        GateVisit.objects.filter(pk=v.pk).update(in_at=timezone.now() - datetime.timedelta(hours=13))
        self.assertEqual([x.pk for x in open_too_long()], [v.pk])
        GateVisit.objects.get(pk=v.pk).cancel("entered twice")
        self.assertEqual(list(open_too_long()), [], "a cancelled visit is not still in")
        from apps.inventory.gate_api import WeighingSerializer
        self.assertEqual(WeighingSerializer(Weighing.objects.get(pk=w.pk)).data["state"], "cancelled")
        with self.assertRaises(ValidationError):
            Weighing.objects.get(pk=w.pk).record("gross", "typed", "100", reason="power", by=self.typist)

    def test_v2_a_half_weighed_vehicle_does_not_leave(self):
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="inward_order")
        w = v.start_weighing("WB-1")
        w.record("gross", "typed", "25860", reason="power", by=self.typist)
        with self.assertRaises(ValidationError):
            GateVisit.objects.get(pk=v.pk).check_out()

    def test_v3_in_and_out_cannot_be_typed(self):
        c = APIClient(); c.force_authenticate(User.objects.create_superuser("s", "s@x.com", "p"))
        r = c.post("/api/inventory/gate-visits/", {"vehicle_number": "MH12AB1", "driver_name": "R",
                                                   "purpose": "visitor", "in_at": "2020-01-01T00:00:00Z",
                                                   "out_at": "2019-01-01T00:00:00Z", "status": "out"}, format="json")
        v = GateVisit.objects.get(pk=r.data["id"])
        self.assertEqual(v.status, "in"); self.assertIsNone(v.out_at); self.assertGreater(v.in_at.year, 2024)

    def test_p5_a_user_holding_add_and_approve_cannot_approve_the_pass_they_made_over_the_api(self):
        from django.contrib.auth.models import Permission
        u = User.objects.create_user("sm2"); u.groups.add(Group.objects.get(name="Stores Manager"))
        u.groups.add(Group.objects.get(name="Security Guard"))
        c = APIClient(); c.force_authenticate(u)
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="gate_pass")
        item = Item.objects.create(sku="R1", name="Roller", uom=self.kg)
        r = c.post("/api/inventory/gate-passes/", {"kind": "returnable", "party_name": "X", "visit": v.pk,
                                                    "expected_return_date": "2099-01-01"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        c.post("/api/inventory/gate-pass-lines/", {"gate_pass": r.data["id"], "item": item.pk, "quantity": "2"}, format="json")
        a = c.post(f"/api/inventory/gate-passes/{r.data['id']}/approve/", {}, format="json")
        self.assertGreaterEqual(a.status_code, 400, "maker approved own pass over the API")

    def test_p6_returnable_pass_in_part_damaged_and_never(self):
        su = User.objects.create_superuser("s", "s@x.com", "p")
        item = Item.objects.create(sku="R1", name="Roller", uom=self.kg)
        v = GateVisit.objects.create(vehicle_number="MH12AB1", driver_name="R", purpose="gate_pass")
        gp = GatePass.objects.create(kind="returnable", party_name="X", visit=v, pass_date=DAY, expected_return_date=DAY)
        ln = GatePassLine.objects.create(gate_pass=gp, item=item, quantity=D("10"))
        gp.approve(su); GateVisit.objects.get(pk=v.pk).check_out()
        gp = GatePass.objects.get(pk=gp.pk)
        gp.record_return(ln, "4"); gp = GatePass.objects.get(pk=gp.pk)
        self.assertEqual(gp.status, "out")
        with self.assertRaises(ValidationError):
            gp.record_return(ln, "6.0001")
        gp.record_return(ln, "6", condition="damaged", note="bent shaft")
        self.assertEqual(GatePass.objects.get(pk=gp.pk).status, "returned")
