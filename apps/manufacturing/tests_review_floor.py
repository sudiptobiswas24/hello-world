"""review_floor probes (read-only review of fix_floor). Prints what was seen; asserts what the fixes claim."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import Shift
from .rolls import FabricRoll
from .station import StationAttempt
from .tests_orders import TODAY
from .tests_station import StationTestCase, at
from .tests_station_api import StationApiTestCase


def role_client(role, name=None):
    call_command("setup_roles", verbosity=0)
    user = User.objects.create_user(name or role.replace(" ", "_"))
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client, user


class P1Bridge(StationApiTestCase):
    def test_roles(self):
        call_command("setup_roles", verbosity=0)
        holders = [g.name for g in Group.objects.all() if g.permissions.filter(codename="add_scalereading").exists()]
        print("P1 ADD_SCALEREADING HELD BY", holders)
        body = {"scale": "SC-1", "gross_kg": "106.8", "stable": True}
        for role in ("Station", "Production Supervisor", "Quality Inspector", "Quality Manager", "Scale bridge"):
            client, _ = role_client(role, "u" + role[:3] + str(len(role)))
            r = client.post("/api/manufacturing/scale-readings/", body, format="json")
            print("P1", role, r.status_code)
        # a person holding Station + Scale bridge
        client, user = role_client("Scale bridge", "both")
        user.groups.add(Group.objects.get(name="Station"))
        print("P1 both", client.post("/api/manufacturing/scale-readings/", body, format="json").status_code)
        su = User.objects.create_superuser("root", "r@x.y", "p")
        c = APIClient(); c.force_authenticate(su)
        print("P1 superuser", c.post("/api/manufacturing/scale-readings/", body, format="json").status_code)
        # a Scale bridge login: what else may it read/write
        client, _ = role_client("Scale bridge", "bridge2")
        from apps.manufacturing.urls import router
        reach = [p for p, _v, _n in router.registry if client.get(f"/api/manufacturing/{p}/").status_code == 200]
        print("P1 bridge reads", reach)
        print("P1 bridge station", client.get(self.base).status_code)


class P2Roll(StationApiTestCase):
    def test_roll(self):
        roll = self.weigh()
        client, _ = role_client("Production Supervisor", "sup2")
        url = f"/api/manufacturing/fabric-rolls/{roll.pk}/"
        body = client.get(url).json()
        r = client.put(url, body, format="json")
        print("P2 PUT same", r.status_code, r.content[:200])
        for f, v in (("lot", None), ("specification", None), ("notes", "x")):
            pass
        body2 = dict(body); body2["length_m"] = "1.00"
        r = client.put(url, body2, format="json")
        print("P2 PUT length", r.status_code, r.content[:200])
        # create a roll via API with no entry
        new = {k: v for k, v in body.items() if k in ("specification", "width_mm", "length_m", "net_weight_kg", "core_weight_kg", "is_tubular", "machine", "notes")}
        from apps.inventory.models import Lot
        lot = Lot.objects.create(item=self.fabric, code="FR-HAND-1")
        new["lot"] = lot.pk
        new["length_m"] = "500"
        r = client.post("/api/manufacturing/fabric-rolls/", new, format="json")
        print("P2 POST hand roll", r.status_code, r.content[:300])
        if r.status_code == 201:
            pk = r.json()["id"]
            r2 = client.patch(f"/api/manufacturing/fabric-rolls/{pk}/", {"length_m": "9999"}, format="json")
            print("P2 PATCH hand roll length", r2.status_code)
            r3 = client.patch(f"/api/manufacturing/fabric-rolls/{pk}/", {"entry": roll.entry_id}, format="json")
            print("P2 PATCH hand roll entry", r3.status_code, r3.content[:300])
            r4 = client.delete(f"/api/manufacturing/fabric-rolls/{pk}/")
            print("P2 DELETE hand roll", r4.status_code)
        # code: queryset update / save through model
        roll.refresh_from_db()
        roll.length_m = Decimal("5")
        try:
            roll.save()
            print("P2 model save length: ALLOWED")
        except ValidationError as e:
            print("P2 model save length refused")
        # void booking then delete
        entry = roll.entry
        print("P2 entry voidable?", hasattr(entry, "void"))
        try:
            entry.void(reason="probe") if "reason" in entry.void.__code__.co_varnames else entry.void()
            roll.refresh_from_db()
            print("P2 voided; stock", roll.lot.on_hand_at())
            r5 = client.delete(url)
            print("P2 DELETE after void", r5.status_code, r5.content[:200])
        except Exception as e:
            print("P2 void err", type(e).__name__, str(e)[:200])

    def test_admin(self):
        roll = self.weigh()
        su = User.objects.create_superuser("root", "r@x.y", "p")
        from django.test import Client
        from apps.core.tests_second_factor import staff_session
        c = Client(); staff_session(c, su)
        url = f"/admin/manufacturing/fabricroll/{roll.pk}/change/"
        r = c.get(url)
        print("P2 admin get", r.status_code)
        data = {k: (v.value() if hasattr(v, "value") else v) for k, v in {}.items()}
        form = r.context["adminform"].form
        data = {}
        for name, field in form.fields.items():
            val = form.initial.get(name, "")
            if val is None: val = ""
            if hasattr(val, "pk"): val = val.pk
            data[name] = val
        data["length_m"] = "7"
        for key in [k for k in (r.context["inline_admin_formsets"] or [])]:
            pass
        try:
            r = c.post(url, data)
            print("P2 admin post", r.status_code)
        except Exception as e:
            print("P2 admin post raised", type(e).__name__, str(e)[:150])
        roll.refresh_from_db(); print("P2 admin length now", roll.length_m)
        try:
            r = c.post(f"/admin/manufacturing/fabricroll/{roll.pk}/delete/", {"post": "yes"})
            print("P2 admin delete", r.status_code, FabricRoll.objects.filter(pk=roll.pk).exists())
        except Exception as e:
            print("P2 admin delete raised", type(e).__name__, str(e)[:150])


class P3Shifts(StationTestCase):
    def test_codes(self):
        from .shifts import batch_code
        for code in ("S1", "S2", "A1", "A2"):
            Shift.objects.bulk_create([Shift(code=code, name=code, starts_at=datetime.time(1), hours=Decimal("1"))])
        s1, s2 = Shift.objects.get(code="S1"), Shift.objects.get(code="S2")
        print("P3", batch_code("FR", self.l17, s1, TODAY), batch_code("FR", self.l17, s2, TODAY))
        Shift.objects.bulk_create([Shift(code="NIGHTSHIFT-LONGX", name="n", starts_at=datetime.time(2), hours=Decimal("1"))]); long_shift = Shift.objects.get(code="NIGHTSHIFT-LONGX")
        m = self.l17
        print("P3 length", len(batch_code("FR", m, long_shift, TODAY)))
        from apps.inventory.models import Lot
        print("P3 Lot.code max", Lot._meta.get_field("code").max_length, "Shift.code max", Shift._meta.get_field("code").max_length)

    def test_delete_then_weigh(self):
        r1 = self.weigh(when=at(TODAY, 10, 40)); r2 = self.weigh(when=at(TODAY, 10, 50))
        print("P3 codes", r1.lot.code, r2.lot.code)
        entry = r1.entry
        entry.void(reason="probe") if "reason" in entry.void.__code__.co_varnames else entry.void()
        try:
            r1.delete()
            print("P3 deleted first roll")
        except ValidationError as e:
            print("P3 delete refused", e.messages[:1])
            return
        try:
            r3 = self.weigh(when=at(TODAY, 11, 0))
            print("P3 next", r3.lot.code)
        except Exception as e:
            print("P3 NEXT WEIGH FAILED", type(e).__name__, str(e)[:200])

    def test_midnight(self):
        night = self.night
        r = self.weigh(when=at(TODAY + datetime.timedelta(days=1), 1, 5))
        print("P3 after midnight", r.lot.code, r.shift.code, r.shift_date)
        r = self.weigh(when=at(TODAY + datetime.timedelta(days=1), 1, 15))
        print("P3 after midnight 2", r.lot.code)
        r = self.weigh(when=at(TODAY + datetime.timedelta(days=1), 9, 0))
        print("P3 morning", r.lot.code, r.shift_date)


class P4Lock(StationApiTestCase):
    def test_two_people(self):
        other = self.employee("EMP-0200", "Other"); other_pin = other.issue_pin()
        wrong = [p for p in ("111111", "222222", "333333", "444444", "555555", "666666") if p not in (self.pin, other_pin, self.supervisor_pin)]
        t = at(TODAY, 10, 42)
        print("P4 op wrong x3", [self.sign_in(wrong[i]).status_code for i in range(3)])
        print("P4 right between", self.sign_in(self.pin).status_code)
        print("P4 other wrong x2", [self.sign_in(wrong[3 + i]).status_code for i in range(2)])
        print("P4 right now", self.sign_in(self.pin).status_code, self.sign_in(other_pin).status_code)
        with patch("apps.manufacturing.station_views.timezone.now", return_value=at(TODAY, 10, 53)), \
             patch("apps.manufacturing.station.timezone.now", return_value=at(TODAY, 10, 53)):
            print("P4 after 11 min", self.sign_in(self.pin).status_code)
        print("P4 attempts", StationAttempt.objects.count())
        # supervisor-pin doors
        print("P4 locked count", self.station.is_locked(at(TODAY, 10, 42)))


from django.test import override_settings
from .tests_backflush import ReworkTestCase
from .bom import BillOfMaterials, BomComponent
from .orders import WorkOrder
from .tests_costing import CostingTestCase
from .costing import CostVersion, StandardCost
from .tests_tape_loads import TapeLoadTestCase
from .tape_loads import CreelSide


class P5Rework(ReworkTestCase):
    def test_edits(self):
        client, _ = role_client("Process Engineer", "pe")
        bom = self.rework_bom
        line = bom.components.get(item=self.tape)
        other = BillOfMaterials.objects.create(item=self.tape, name="Other", version=9, quantity_produced=Decimal("100"),
                                               uom=self.kg, is_default=False, is_rework=True)
        BomComponent.objects.create(bom=other, item=self.tape, quantity=Decimal("98"), uom=self.kg, line_number=1)
        r = client.patch(f"/api/manufacturing/bom-components/{line.pk}/", {"bom": other.pk}, format="json")
        print("P5 move eating line to other bom", r.status_code, r.content[:200])
        print("P5 bom now eats", bom.eats_itself())
        r = client.patch(f"/api/manufacturing/bom-components/{line.pk}/", {"quantity": "0.0001"}, format="json")
        print("P5 tiny eating qty", r.status_code)
        r = client.patch(f"/api/manufacturing/boms/{other.pk}/", {"item": self.virgin.pk}, format="json")
        print("P5 change rework bom item", r.status_code, r.content[:200])
        r = client.patch(f"/api/manufacturing/boms/{bom.pk}/", {"is_rework": False}, format="json")
        print("P5 un-rework", r.status_code, r.content[:200])
        r = client.delete(f"/api/manufacturing/bom-components/{bom.components.get(item=self.virgin).pk}/")
        print("P5 delete other line", r.status_code)

    def test_released_then_edit(self):
        lot = self.failed_lot()
        order = self.rework_order(lot)
        order.release(TODAY)
        line = self.rework_bom.components.get(item=self.tape)
        try:
            line.delete(); print("P5 deleted eating line of released run")
        except ValidationError as e:
            print("P5 delete refused")
        # a draft bill saved is_rework with later line then deleted via queryset
        BomComponent.objects.filter(pk=line.pk).delete()
        print("P5 queryset delete leaves", self.rework_bom.eats_itself())


class P6Promise(TapeLoadTestCase):
    def setUp(self):
        super().setUp()
        WorkOrder.objects.filter(pk=self.run.pk).update(backflush=True)
        self.load("D-1", "100", CreelSide.WARP, 9)
        self.d1 = self.doffs["D-1"]

    def test_doors(self):
        from apps.inventory.models import Warehouse
        from apps.inventory.transfers import move_stock
        from django.utils import timezone
        out = {}
        # D-1 has 100 on hand, 100 promised: free = 0
        from apps.inventory.tracking import promised_to, refuse_drawing_what_is_promised
        print("P6 promised", promised_to(self.d1, self.plant))
        # a SMALL draw when on hand exceeds promise: D-2 has 100, nothing promised
        try:
            refuse_drawing_what_is_promised(self.d1, self.plant, Decimal("0.001"), action="x"); print("P6 0.001 allowed")
        except ValidationError:
            print("P6 0.001 refused")
        # the reverse: withdraw the load, then move
        from .tape_loads import TapeLoad, void_load
        load = TapeLoad.objects.get()
        print("P6 load voidable", [n for n in dir(load) if "void" in n])
        # other warehouse: promise is per-warehouse; does the draw in other warehouse count?
        store = Warehouse.objects.create(code="ST", name="Store")
        self.stock(self.tape, "50", "120", lot=self.d1) if False else None
        # sales delivery door / pick: plan_issue
        from apps.inventory.picking import plan_issue
        try:
            print("P6 plan_issue named", plan_issue(self.tape, self.plant, Decimal("10"), lot=self.d1))
        except Exception as e:
            print("P6 plan_issue", type(e).__name__, str(e)[:120])
        # voiding the load frees it
        try:
            void_load(load, self.station, self.supervisor, self.operator, 'probe')
            print("P6 voided load")
            move_stock(self.tape, self.plant, store, Decimal("40"), "T-1", timezone.now(), "", lot=self.d1)
            print("P6 move after withdraw OK")
        except Exception as e:
            print("P6 void/move", type(e).__name__, str(e)[:200])

    def test_free_part_moves(self):
        """Promise 100 of D-1 but shelf holds 150: 50 may go, 51 may not."""
        from apps.inventory.models import Warehouse
        from apps.inventory.transfers import move_stock
        from django.utils import timezone
        self.stock(self.tape, "50", "120", lot=self.d1)
        store = Warehouse.objects.create(code="ST", name="Store")
        for q in ("51", "50"):
            try:
                move_stock(self.tape, self.plant, store, Decimal(q), "T-1", timezone.now(), "", lot=self.d1)
                print("P6 moved", q)
            except ValidationError as e:
                print("P6 refused", q, e.messages[0][:100])

    def test_count_door(self):
        from apps.inventory.adjustments import StockCount, StockCountLine
        from apps.accounting.models import Account
        from apps.inventory.adjustments import AdjustmentReason, AdjustmentDirection
        reason = AdjustmentReason.objects.create(code="CNT", name="Count", account=Account.objects.order_by("pk").first(), direction=AdjustmentDirection.DECREASE)
        count = StockCount.objects.create(count_date=TODAY, warehouse=self.plant, reason=reason)
        StockCountLine.objects.create(count=count, item=self.tape, uom=self.tape.uom, lot=self.d1, counted_quantity=Decimal("60"))
        try:
            count.post(); print("P6 count posted")
        except ValidationError as e:
            print("P6 count refused", e.messages[0][:140])
        except Exception as e:
            print("P6 count ERR", type(e).__name__, str(e)[:200])


class P7Version(CostingTestCase):
    def test_doors(self):
        from apps.core.tests_second_factor import staff_session
        from django.test import Client
        v = self.version(); v.publish(on_date=TODAY)
        row = v.costs.first()
        client, _ = role_client("Controller", "ctl")
        base = "/api/manufacturing/"
        for label, req in (
            ("PUT version", client.put(f"{base}cost-versions/{v.pk}/", {"code": v.code, "name": "zz", "effective_from": str(TODAY)}, format="json")),
            ("PATCH published_on", client.patch(f"{base}cost-versions/{v.pk}/", {"published_on": "2020-01-01", "published_at": "2020-01-01T00:00:00Z"}, format="json")),
            ("PATCH notes", client.patch(f"{base}cost-versions/{v.pk}/", {"notes": "ok"}, format="json")),
            ("PUT row", client.put(f"{base}standard-costs/{row.pk}/", {"version": v.pk, "item": row.item_id, "material": "1", "conversion": "0", "byproduct_credit": "0"}, format="json")),
            ("POST row", client.post(f"{base}standard-costs/", {"version": v.pk, "item": self.tape.pk, "material": "1", "conversion": "0", "byproduct_credit": "0"}, format="json")),
            ("roll_up", client.post(f"{base}cost-versions/{v.pk}/roll_up/", {}, format="json")),
            ("roll-up", client.post(f"{base}cost-versions/{v.pk}/roll-up/", {}, format="json")),
            ("publish again", client.post(f"{base}cost-versions/{v.pk}/publish/", {}, format="json")),
        ):
            print("P7", label, req.status_code, req.content[:120])
        # move a draft row into the published version
        d = self.version("V2")
        r = client.patch(f"{base}standard-costs/{d.costs.first().pk}/", {"version": v.pk}, format="json")
        print("P7 move draft row into published", r.status_code, r.content[:150])
        # admin
        su = User.objects.create_superuser("root", "r@x.y", "p")
        c = Client(); staff_session(c, su)
        for url, data in ((f"/admin/manufacturing/costversion/{v.pk}/delete/", {"post": "yes"}),):
            try:
                r = c.post(url, data); print("P7 admin delete", r.status_code, CostVersion.objects.filter(pk=v.pk).exists())
            except Exception as e:
                print("P7 admin delete raised", type(e).__name__, str(e)[:120])
        from django.contrib import admin
        try:
            r = c.get(f"/admin/manufacturing/costversion/{v.pk}/change/"); print("P7 admin change get", r.status_code)
        except Exception as e:
            print("P7 admin get raised", type(e).__name__)
        # queryset-level delete on version leaves rows?
        v.refresh_from_db(); print('P7 published_on now', v.published_on, v.published_at)


class P8Others(StationApiTestCase):
    def test_other_weighed(self):
        from apps.manufacturing.urls import router
        client, _ = role_client("Production Supervisor", "sup8")
        for prefix, view, name in router.registry:
            model = getattr(getattr(view, "queryset", None), "model", None) or getattr(getattr(view, "serializer_class", None) and getattr(view.serializer_class, "Meta", None), "model", None)
            if model is None: continue
            if "weighed_at" in {f.name for f in model._meta.fields}:
                print("P8", prefix, model.__name__, "methods", sorted(m for m in view.http_method_names if hasattr(view, m) or m in ("put","patch","delete") and hasattr(view, {"put":"update","patch":"partial_update","delete":"destroy"}[m])))


from pathlib import Path
from django.test import SimpleTestCase
from apps.core.management.commands.audit_invariants import Command


class P9Audit(SimpleTestCase):
    def promise(self, body):
        text = "class Rebatch:\n    def _take_out(self, lines):\n" + body
        return [s for s, _ in Command().named_draws_past_the_promise(["manufacturing"], {"manufacturing": {Path("apps/manufacturing/example.py"): text}})]

    def weighed(self, body):
        from apps.manufacturing.rolls import FabricRoll
        text = "class FabricRoll(AuditModel):\n" + body
        return [s for s, d in Command().weighed_records_written_over_the_api(["manufacturing"], {"manufacturing": {Path("apps/manufacturing/example.py"): text}}, written={FabricRoll}) if "FabricRoll " in d]

    def test_all(self):
        print("P9 positional lot", self.promise("        return self.item.cost_of_removing(w, q, lot)\n"))
        print("P9 comment only", self.promise("        # refuse_drawing_what_is_promised later\n        return self.item.cost_of_removing(w, q, lot=lot)\n"))
        print("P9 two removals one asked", self.promise("        refuse_drawing_what_is_promised(a, w, q)\n        x = item.cost_of_removing(w, q, lot=a)\n        return item.cost_of_removing(w, q, lot=b)\n"))
        print("P9 direct movement no cost_of_removing", self.promise("        StockMovement.objects.create(item=i, lot=lot, quantity=-q, movement_type='issue')\n"))
        print("P9 weighed word in comment+unrelated raise", self.weighed("    def save(self):\n        # weighed\n        raise ValueError('x')\n    def delete(self):\n        x = 'weighed'\n        raise KeyError\n"))
        print("P9 weighed guard not conditioned", self.weighed("    def save(self):\n        if self.pk:\n            raise ValueError('weighed')\n    def delete(self):\n        raise KeyError('weighed')\n"))


class P10Bridged(StationApiTestCase):
    def test_bridged(self):
        from .station import LoomStation
        LoomStation.objects.filter(pk=self.station.pk).update(scale_bridged=True, scale_code="SC-1")
        self.sign_in()
        r = self.roll()
        print("P10 tablet roll at bridged station no reading", r.status_code, r.content[:160])
        # tablet tries to post a reading
        r = self.client.post("/api/manufacturing/scale-readings/", {"scale": "SC-1", "gross_kg": "106.8", "stable": True}, format="json")
        print("P10 tablet reading", r.status_code)
        # source=manual without supervisor
        r = self.roll(source="manual", reason="other")
        print("P10 manual no supervisor", r.status_code, r.content[:160])
        # bridge posts, tablet weighs
        bridge, _ = role_client("Scale bridge", "br10")
        print("P10 bridge post", bridge.post("/api/manufacturing/scale-readings/", {"scale": "SC-1", "gross_kg": "106.8", "stable": True}, format="json").status_code)
        r = self.roll()
        print("P10 roll with bridge reading", r.status_code, r.content[:120])
