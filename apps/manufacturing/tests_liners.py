"""
The liner for the laminated 60 x 100 sack, blown and cut here.

Film LF-50 is 70 LDPE and 30 LLDPE, a 58 cm tube at 50 micron: two
layers of 0.58 m at 50 x 0.92 g is 53.36 g a metre. A hundred kilos
of it takes 70 and 30 kilos of polymer at 3% waste, and gives back
100 x 0.03 / 0.97 x 0.8 = 2.474227 kg of film waste. It is gauged
against 45 to 55 micron on five readings.

Liner LN-58 is 105 cm of it: 53.36 x 1.05 = 56.028 g, so a thousand
take 56.028 kg of film at 2% seal waste and give back 56.028 x 0.02 /
0.98 x 0.8 = 0.914743 kg. A bundle is weighed against 5% of 56.028:
53.2266 to 58.8294 g. The lined sack carries one a sack.

A roll off BF-1 weighs 152.4 kg on the 2.4 kg core: 150 kg over the
2,800 m on the winder is 150,000 / (2,800 x 2 x 0.58 x 0.92) = 50.20
micron by weight. Gauged at 49, 51, 50, 52 and 48 it is 50.00, in; at
56, 57, 58, 56 and 57 it is 56.80, out.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item, Lot

from .conversion import BagCount, bags_converted, record_bags, void_bags
from .liners import FilmSpecification, LinerSpecification
from .machines import Machine
from .orders import WorkCentre, WorkOrder
from .process_rolls import mount_roll, roll_chain
from .routing import Routing, RoutingOperation
from .station import LineKind, LoomStation
from .station_film import FilmRoll, record_film, void_film
from .tests_orders import TODAY
from .tests_quoting import settled_before
from .tests_station import at
from .tests_station_coat import CoatingTestCase
from .woven import BagSpecification

ON = ["49", "51", "50", "52", "48"]
OFF = ["56", "57", "58", "56", "57"]
LINERS_IN = ["56"] * 10


class LinerTestCase(CoatingTestCase):
    def setUp(self):
        super().setUp()
        self.ldpe = Item.objects.create(sku="LDPE", name="LDPE", uom=self.kg,
                                        standard_cost=Decimal("110"))
        self.lldpe = Item.objects.create(sku="LLDPE", name="LLDPE", uom=self.kg,
                                         standard_cost=Decimal("115"))
        self.film_waste = Item.objects.create(sku="LD-WASTE", name="Film waste", uom=self.kg,
                                              standard_cost=Decimal("40"))
        self.film_item = Item.objects.create(sku="FILM-58", name="Liner film 58 cm",
                                             uom=self.kg, tracking="lot",
                                             standard_cost=Decimal("130"))
        self.liner = Item.objects.create(sku="LINER-58", name="Liner 58 x 105", uom=self.pcs,
                                         tracking="lot", standard_cost=Decimal("8"))
        self.blown = WorkCentre.objects.create(code="BLOWN", name="Blown film")
        self.sealer = WorkCentre.objects.create(code="SEAL", name="Cut and seal")
        self.bf1 = Machine.objects.create(work_centre=self.blown, code="BF-1")
        self.cs1 = Machine.objects.create(work_centre=self.sealer, code="CS-1")
        film_routing = Routing.objects.create(code="R-FILM", name="Blow")
        RoutingOperation.objects.create(routing=film_routing, sequence=10, name="Blow",
                                        work_centre=self.blown, setup_minutes=Decimal("0"),
                                        units_per_hour=Decimal("80"), rate_uom=self.kg)
        liner_routing = Routing.objects.create(code="R-LINER", name="Cut and seal")
        RoutingOperation.objects.create(routing=liner_routing, sequence=10, name="Seal",
                                        work_centre=self.sealer, setup_minutes=Decimal("0"),
                                        units_per_hour=Decimal("2400"), rate_uom=self.pcs)
        self.film = FilmSpecification.objects.create(
            code="LF-50", film_item=self.film_item, micron=Decimal("50"),
            lay_flat_width_cm=Decimal("58"), base_polymer=self.ldpe, lldpe_item=self.lldpe,
            lldpe_percent=Decimal("30"), waste_item=self.film_waste, routing=film_routing)
        self.liner_spec = LinerSpecification.objects.create(
            code="LN-58", liner_item=self.liner, film=self.film, cut_length_cm=Decimal("105"),
            routing=liner_routing)
        self.film_run = self.released(self.film_item, self.film.bom, "600", self.kg)
        self.liner_run = self.released(self.liner, self.liner_spec.bom, "10000", self.pcs)
        self.bf = LoomStation.objects.create(code="BF", name="Blown film",
                                             warehouse=self.plant, kind=LineKind.BLOWN_FILM)
        self.bf.machines.set([self.bf1])
        self.bf.supervisors.add(self.supervisor)
        self.cs = LoomStation.objects.create(code="CS", name="Cut and seal",
                                             warehouse=self.plant, kind=LineKind.CONVERSION)
        self.cs.machines.set([self.cs1])
        self.cs.supervisors.add(self.supervisor)

    def released(self, item, bom, quantity, uom):
        run = WorkOrder.objects.create(item=item, bom=bom, quantity_ordered=Decimal(quantity),
                                       uom=uom, warehouse=self.plant)
        run.release(TODAY)
        return run

    def wind(self, gross="152.4", readings=ON, **extra):
        return record_film(self.bf, self.operator, self.bf1, gross, self.core, "2800",
                           readings, at=at(TODAY, 10), source="manual",
                           supervisor=extra.pop("supervisor", self.supervisor),
                           typed_reason="scale_offline", **extra)

    def seal(self, bags="1000", sample=LINERS_IN, **extra):
        return record_bags(self.cs, self.operator, self.cs1, bags, sample, at=at(TODAY, 11),
                           **extra)


class FilmTests(LinerTestCase):
    def test_what_a_hundred_kilos_of_film_takes(self):
        rows = {row.item.sku: (row.quantity, row.waste_percent)
                for row in self.film.bom.components.all()}
        self.assertEqual(rows, {"LDPE": (Decimal("70.000000"), Decimal("3.000")),
                                "LLDPE": (Decimal("30.000000"), Decimal("3.000"))})
        (waste,) = self.film.bom.byproducts.all()
        self.assertEqual((waste.item, waste.quantity), (self.film_waste, Decimal("2.474227")))
        self.assertEqual(self.film.grams_per_metre(), Decimal("53.3600"))
        (line,) = self.film.inspection_plan.lines.all()
        self.assertEqual((line.target, line.lower_limit, line.upper_limit, line.sample_size),
                         (Decimal("50.000000"), Decimal("45.000000"), Decimal("55.000000"), 5))

    def test_what_a_film_cannot_be(self):
        with self.assertRaisesMessage(ValidationError, "needs both an item and a share"):
            FilmSpecification.objects.create(
                code="LF-X", film_item=self.film_item, micron=Decimal("50"),
                lay_flat_width_cm=Decimal("58"), base_polymer=self.ldpe,
                lldpe_percent=Decimal("30"))
        with self.assertRaisesMessage(ValidationError, "leaves no LDPE"):
            FilmSpecification.objects.create(
                code="LF-Y", film_item=self.film_item, micron=Decimal("50"),
                lay_flat_width_cm=Decimal("58"), base_polymer=self.ldpe,
                lldpe_item=self.lldpe, lldpe_percent=Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "cannot be written"):
            FilmSpecification.objects.create(
                code="LF-Z", film_item=self.liner, micron=Decimal("50"),
                lay_flat_width_cm=Decimal("58"), base_polymer=self.ldpe)


class LinerTests(LinerTestCase):
    def test_what_a_thousand_liners_take(self):
        (film,) = self.liner_spec.bom.components.all()
        self.assertEqual((film.item, film.quantity, film.waste_percent),
                         (self.film_item, Decimal("56.028000"), Decimal("2.000")))
        (waste,) = self.liner_spec.bom.byproducts.all()
        self.assertEqual(waste.quantity, Decimal("0.914743"))
        (line,) = self.liner_spec.inspection_plan.lines.all()
        self.assertEqual((line.characteristic.code, line.lower_limit, line.upper_limit),
                         ("LINERWT", Decimal("53.226600"), Decimal("58.829400")))

    def test_a_liner_is_counted(self):
        with self.assertRaisesMessage(ValidationError, "liners are counted"):
            LinerSpecification.objects.create(code="LN-X", liner_item=self.film_item,
                                              film=self.film, cut_length_cm=Decimal("105"))

    def test_a_thicker_film_makes_a_heavier_liner(self):
        # 60 micron: 2 x 0.58 x 60 x 0.92 x 1.05 = 67.2336 g.
        self.film.micron = Decimal("60")
        self.film.save()
        self.liner_spec.refresh_from_db()
        self.assertEqual(self.liner_spec.bom.components.get().quantity, Decimal("67.233600"))


class LinedSackTests(LinerTestCase):
    def line_the_sack(self, **extra):
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = self.liner
        for name, value in extra.items():
            setattr(spec, name, value)
        spec.save()
        return spec

    def test_one_liner_a_sack_and_its_weight_in_the_sack(self):
        before = self.lam_spec.bag_grams()
        spec = self.line_the_sack()
        row = spec.bom.components.get(item=self.liner)
        self.assertEqual((row.quantity, row.uom, row.waste_percent),
                         (Decimal("1000.000000"), self.pcs, Decimal("2.500")))
        self.assertEqual(spec.bag_grams() - before, Decimal("56.028"))

    def test_its_weight_is_the_liners_own(self):
        with self.assertRaisesMessage(ValidationError, "Take the typed liner weight"):
            self.line_the_sack(liner_grams_per_bag=Decimal("50"))

    def test_a_liner_nobody_specified(self):
        other = Item.objects.create(sku="LINER-X", name="Liner", uom=self.pcs)
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = other
        with self.assertRaisesMessage(ValidationError, "has no liner specification"):
            spec.save()

    def test_a_liner_the_sack_uses_is_not_deleted_from_under_it(self):
        self.line_the_sack()
        # Django clears the instance's key on delete, rolled back or not.
        pk = self.liner_spec.pk
        with self.assertRaisesMessage(ValidationError, "has no liner specification"):
            self.liner_spec.delete()
        self.assertTrue(LinerSpecification.objects.filter(pk=pk).exists())

    def test_and_a_thicker_film_weighs_in_the_sack(self):
        spec = self.line_the_sack()
        before = spec.bag_grams()
        self.film.micron = Decimal("60")
        self.film.save()
        spec = BagSpecification.objects.get(pk=spec.pk)
        self.assertEqual(spec.bag_grams() - before, Decimal("11.2056"))


class WoundOffTests(LinerTestCase):
    def test_a_roll_is_a_batch_gauged_and_weighed(self):
        roll = self.wind()
        self.assertEqual((roll.net_kg, roll.mean_micron, roll.weighed_micron, roll.lot.code),
                         (Decimal("150.000"), Decimal("50.00"), Decimal("50.20"),
                          "LF-260601-D-BF1-01"))
        self.assertTrue(roll.inspection.posted)
        self.assertEqual(roll.lot.on_hand_at(self.plant), Decimal("150.0000"))
        self.assertEqual(roll.entry.work_order, self.film_run)

    def test_off_its_thickness_only_with_a_supervisor_and_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "Say why film off its micron"):
            self.wind(readings=OFF)
        roll = self.wind(readings=OFF, reason="Die gap reset")
        self.assertEqual((roll.mean_micron, roll.conceded_by), (Decimal("56.80"),
                                                                 self.supervisor))

    def test_what_a_roll_must_be(self):
        with self.assertRaisesMessage(ValidationError, "is not more than the"):
            self.wind("2.4")
        with self.assertRaisesMessage(ValidationError, "Check the micron 5 times; 4"):
            self.wind(readings=ON[:4])
        with self.assertRaisesMessage(ValidationError, "is not a blown-film line"):
            record_film(self.cv, self.operator, self.c1, "152.4", self.core, "2800", ON,
                        at=at(TODAY, 10))
        with self.assertRaisesMessage(ValidationError, "A typed weight needs"):
            record_film(self.bf, self.operator, self.bf1, "152.4", self.core, "2800", ON,
                        at=at(TODAY, 10), source="manual")

    def test_withdrawn_while_not_mounted(self):
        roll = self.wind()
        mount = mount_roll(self.cs, self.operator, self.cs1, roll.lot.code,
                           at=at(TODAY, 10, 30))
        with self.assertRaisesMessage(ValidationError, "has been mounted since"):
            void_film(roll, self.bf, self.supervisor, self.operator, "Wrong run")
        from .process_rolls import void_mount

        void_mount(mount, self.cs, self.supervisor, self.operator, "Wrong roll")
        void_film(roll, self.bf, self.supervisor, self.operator, "Wrong run")
        roll.refresh_from_db()
        self.assertIsNotNone(roll.voided_at)
        self.assertEqual(roll.lot.on_hand_at(self.plant), Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            void_film(roll, self.bf, self.supervisor, self.operator, "Again")


class SealedTests(LinerTestCase):
    def test_a_bundle_of_liners_off_the_roll_it_was_cut_from(self):
        roll = self.wind()
        mount_roll(self.cs, self.operator, self.cs1, roll.lot.code, at=at(TODAY, 10, 30))
        count = self.seal()
        self.assertEqual((count.bags, count.passed, count.work_order, count.inspection.lot.code),
                         (1000, True, self.liner_run, "LN-260601-D-CS1-01"))
        self.assertEqual([step["code"] for step in roll_chain(count.mount)], [roll.lot.code])
        self.assertEqual(count.inspection.lot.on_hand_at(self.plant), Decimal("1000"))

    def test_off_weight_liners_are_a_supervisors(self):
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.seal(sample=["60"] * 10)

    def test_sealing_is_not_stitching(self):
        from .conversion import liners_sealed

        self.seal()
        self.assertEqual(bags_converted(self.operator, TODAY), [])
        self.assertEqual(liners_sealed(self.operator, TODAY), [(TODAY, Decimal("1000"))])
        from .conversion import summary

        (row,) = summary(TODAY, TODAY)["by_operator"]
        self.assertEqual((row["bags"], row["liners"]), (0, 1000))
        count = BagCount.objects.get()
        void_bags(count, self.supervisor, "Miscounted")
        self.assertEqual(liners_sealed(self.operator, TODAY), [])


class OnTheScaleTests(LinerTestCase):
    """A roll off the blown-film line is weighed as a doff is."""

    def bridge(self, station):
        from .station_scale import post_reading

        LoomStation.objects.filter(pk=station.pk).update(scale_code="SC-9", scale_bridged=True)
        station.refresh_from_db()
        return post_reading("SC-9", "152.4", True, at=at(TODAY, 10))

    def test_a_film_roll_takes_the_scales_weight_once(self):
        reading = self.bridge(self.bf)
        roll = record_film(self.bf, self.operator, self.bf1, None, self.core, "2800", ON,
                           at=at(TODAY, 10))
        self.assertEqual((roll.gross_kg, roll.weight_source, roll.scale_reading),
                         (Decimal("152.400"), "scale", reading))
        reading.refresh_from_db()
        self.assertEqual(reading.used_by(), roll)
        with self.assertRaises(ValidationError):
            record_film(self.bf, self.operator, self.bf1, None, self.core, "2800", ON,
                        at=at(TODAY, 10))


class LinerApiTests(LinerTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Permission, User
        from rest_framework.test import APIClient

        self.office = APIClient()
        self.office.force_authenticate(User.objects.create_superuser("planner"))
        device = User.objects.create_user("film-line")
        device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        self.device = APIClient()
        self.device.force_authenticate(device)

    def test_specifications_through_the_office(self):
        response = self.office.post("/api/manufacturing/film-specifications/", {
            "code": "LF-60", "film_item": Item.objects.create(
                sku="FILM-60", name="Film", uom=self.kg, standard_cost=Decimal("130")).pk, "micron": "60",
            "lay_flat_width_cm": "58", "base_polymer": self.ldpe.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["grams_per_metre"], "64.032")
        body = self.office.get(f"/api/manufacturing/liner-specifications/"
                               f"{self.liner_spec.pk}/").json()
        self.assertEqual(body["liner_grams"], "56.028")
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = self.liner
        spec.save()
        response = self.office.delete(f"/api/manufacturing/liner-specifications/"
                                      f"{self.liner_spec.pk}/")
        self.assertEqual(response.status_code, 400)

    def test_a_roll_wound_off_and_withdrawn_at_the_station(self):
        pin, supervisor_pin = self.operator.issue_pin(), self.supervisor.issue_pin()
        base = f"/api/manufacturing/stations/{self.bf.code}/"
        self.device.post(base + "sign-in/", {"pin": pin}, format="json")
        response = self.device.post(base + "film/", {
            "machine": "BF-1", "gross_kg": "152.4", "core": "C-76", "metres": "2800",
            "micron": ON, "source": "manual", "supervisor_pin": supervisor_pin,
            "typed_reason": "scale_offline"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["net_kg"], body["mean_micron"], body["weighed_micron"]),
                         ("150.000", "50.00", "50.20"))
        response = self.device.post(base + f"film/{body['id']}/void/",
                                    {"supervisor_pin": supervisor_pin, "reason": "Test"},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(FilmRoll.objects.get().lot.on_hand_at(self.plant), Decimal("0"))


class CostedDownToThePolymerTests(LinerTestCase):
    """
    One lined sack takes 1 / 0.975 = 1.025641 liners; they take 1.025641
    x 0.056028 / 0.98 = 0.058637 kg of film, which takes 0.042316 kg of
    LDPE and 0.018135 of LLDPE; 0.002389 kg of film waste comes back.
    """

    def setUp(self):
        super().setUp()
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = self.liner
        spec.save()
        settled_before(TODAY)
        self.lined = BagSpecification.objects.get(pk=spec.pk)

    def test_the_liner_is_walked_not_priced(self):
        from .quoting import _Walk

        walk = _Walk(self.lined, TODAY)
        walk.walk(self.lined.bom, Decimal("1"))
        six = Decimal("0.000001")
        material = {item.sku: quantity.quantize(six) for item, quantity in walk.material.items()}
        self.assertNotIn("LINER-58", material)
        self.assertNotIn("FILM-58", material)
        self.assertEqual((material["LDPE"], material["LLDPE"]),
                         (Decimal("0.042316"), Decimal("0.018135")))
        self.assertEqual((walk.stage_kg["sealing"].quantize(six),
                          walk.stage_kg["blown_film"].quantize(six),
                          walk.credit[self.film_waste].quantize(six)),
                         (Decimal("1.025641"), Decimal("0.058637"), Decimal("0.002389")))

    def test_a_quote_asks_for_the_liner_lines_rates_not_the_liners(self):
        from .quoting import compute

        with self.assertRaises(ValidationError) as caught:
            compute(self.lined, TODAY)
        text = str(caught.exception)
        self.assertIn("no rate for LDPE", text)
        self.assertIn("no rate for liner film blowing", text)
        self.assertIn("no rate for liner cutting and sealing", text)
        self.assertNotIn("LINER-58", text)


class PlannedDownToThePolymerTests(LinerTestCase):
    def test_a_lined_sack_run_needs_liners_film_and_polymer(self):
        from apps.planning.models import PlanningSettings
        from apps.planning.mrp import plan

        PlanningSettings.objects.create(horizon_days=60, default_buy_lead_days=7,
                                        default_make_lead_days=2)
        self.film_run.cancel()
        self.liner_run.cancel()
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = self.liner
        spec.save()
        self.released(self.lam_bag, spec.bom, "1000", self.pcs)
        orders = {order.item.sku: order for order in plan(self.plant, planned_on=TODAY).orders.all()}
        self.assertEqual((orders["LINER-58"].kind, orders["LINER-58"].bom),
                         ("make", self.liner_spec.bom))
        self.assertEqual(orders["FILM-58"].bom, self.film.bom)
        # 1,000 / 0.975 liners up to 1,025.6411; x 0.056028 / 0.98 is
        # 58.6374 kg of film; 70 and 30 of it over 0.97 is the polymer.
        self.assertEqual({sku: orders[sku].quantity for sku in ("LINER-58", "FILM-58", "LDPE",
                                                                "LLDPE")},
                         {"LINER-58": Decimal("1025.6411"), "FILM-58": Decimal("58.6374"),
                          "LDPE": Decimal("42.3157"), "LLDPE": Decimal("18.1353")})


class WhichSpecificationTests(LinerTestCase):
    """Found by mutation: each of these once survived its guard being removed."""

    def lined(self):
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = self.liner
        spec.save()
        return spec

    def second(self, **extra):
        return LinerSpecification.objects.create(
            code="LN-58B", liner_item=self.liner, film=self.film, cut_length_cm=Decimal("110"),
            **extra)

    def test_the_one_in_force_on_the_day(self):
        # 110 cm: 53.36 x 1.10 = 58.696 g. The 105 cm one ended the day before.
        import datetime

        self.liner_spec.valid_to = TODAY - datetime.timedelta(days=1)
        self.liner_spec.save()
        self.second(valid_from=TODAY)
        self.assertEqual(self.lined().liner_grams(), Decimal("58.696"))

    def test_not_one_that_is_withdrawn(self):
        LinerSpecification.objects.filter(pk=self.liner_spec.pk).update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "has no liner specification"):
            self.lined()

    def test_two_in_force_is_one_too_many(self):
        # Saved, the second's bill would refuse to overlap the first's; this
        # is the count holding for rows that arrived some other way.
        LinerSpecification.objects.bulk_create([LinerSpecification(
            code="LN-58B", liner_item=self.liner, film=self.film,
            cut_length_cm=Decimal("110"))])
        with self.assertRaisesMessage(ValidationError, "has 2 liner specifications"):
            self.lined()

    def test_film_waste_is_weighed_like_the_film(self):
        with self.assertRaisesMessage(ValidationError, "the film waste"):
            FilmSpecification.objects.create(
                code="LF-W", film_item=self.film_item, micron=Decimal("50"),
                lay_flat_width_cm=Decimal("58"), base_polymer=self.ldpe, waste_item=self.liner,
                valid_from=TODAY)

    def test_a_film_run_is_made_to_a_film_specification(self):
        from .bom import BillOfMaterials

        # Straight in: saved, it would refuse to overlap the specification's.
        (typed,) = BillOfMaterials.objects.bulk_create([BillOfMaterials(
            item=self.film_item, name="Typed", version=9, quantity_produced=Decimal("100"),
            uom=self.kg, is_default=False)])
        WorkOrder.objects.filter(pk=self.film_run.pk).update(bom=typed)
        with self.assertRaisesMessage(ValidationError, "not made to a film specification"):
            self.wind()

    def test_a_bundle_is_weighed_only_against_a_weight(self):
        from apps.quality.models import Characteristic, PlanLine

        other = Characteristic.objects.get(code="MICRON")
        PlanLine.objects.filter(plan=self.liner_spec.inspection_plan).update(
            characteristic=other)
        with self.assertRaisesMessage(ValidationError, "has no bag weight to weigh against"):
            self.seal()

    def test_a_film_changed_after_the_day_cannot_be_costed_on_it(self):
        import datetime

        from django.utils import timezone

        from .quoting import compute

        self.lined()
        settled_before(TODAY)
        # The film alone edited after the day: the rest stood as it was.
        FilmSpecification.objects.filter(pk=self.film.pk).update(
            updated_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "LF-50"):
            compute(BagSpecification.objects.get(pk=self.lam_spec.pk), TODAY)
