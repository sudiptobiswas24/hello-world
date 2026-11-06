"""
The morning after 1 September on the weaving bank: 2,000 kg made and
100 kg scrapped, so waste 100 / 2,100 = 4.76%; the loom's meter went
from 10,000 to 10,120 on the day shift (120 kWh, on runs) and to 10,250
on the night shift (130 kWh, nobody booked), 250 kWh in all and 0.125
kWh a kilogramme. The extrusion line made nothing and has no meter. On
the 3rd nobody read the meter, and the row says so.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import Lot

from .daily import daily_production
from .orders import ProductionEntry
from .tests_energy import SEP, EnergyTestCase


class DailyProductionTestCase(EnergyTestCase):
    def entry_on(self, day, made, scrapped="0"):
        entry = ProductionEntry.objects.create(
            work_order=self.a, entry_date=day, warehouse=self.plant, quantity_produced=Decimal(made),
            quantity_scrapped=Decimal(scrapped), uom=self.kg, work_centre=self.centre, machine=self.l17,
            lot=Lot.objects.create(item=self.fabric, code=f"FAB-{day.day}"))
        entry.post()
        return entry

    def row(self, rows, code):
        [row] = [row for row in rows if row["code"] == code]
        return row


class DailyProductionTests(DailyProductionTestCase):
    def test_made_scrap_waste_and_kwh_a_kilogramme(self):
        self.the_two_days()
        self.entry_on(SEP(1), "2000", "100")
        weave = self.row(daily_production(SEP(1)), "WEAVE")
        self.assertEqual((weave["unit"], weave["made"], weave["scrapped"], weave["waste_percent"], weave["kg"]),
                         ("kg", Decimal("2000"), Decimal("100"), Decimal("4.76"), Decimal("2000")))
        self.assertEqual((weave["kwh"], weave["idle_kwh"], weave["kwh_per_kg"], weave["unread"]),
                         (Decimal("250"), Decimal("130"), Decimal("0.125"), False))

    def test_a_section_with_nothing_shows_zero_and_no_figures_it_cannot_give(self):
        self.the_two_days()
        extrusion = self.row(daily_production(SEP(1)), "EXT-1")
        self.assertEqual((extrusion["made"], extrusion["scrapped"], extrusion["waste_percent"], extrusion["kg"],
                          extrusion["kwh"], extrusion["kwh_per_kg"]), (0, 0, None, None, None, None))

    def test_a_voided_entry_is_not_output(self):
        self.the_two_days()
        entry = self.entry_on(SEP(1), "2000")
        entry.void(memo="Counted twice")
        weave = self.row(daily_production(SEP(1)), "WEAVE")
        self.assertEqual((weave["made"], weave["kg"], weave["kwh_per_kg"]), (0, None, None))

    def test_a_day_nobody_read_the_meter_says_so(self):
        self.the_two_days()
        weave = self.row(daily_production(SEP(3)), "WEAVE")
        self.assertEqual((weave["kwh"], weave["unread"]), (0, True))


class DailyProductionApiTests(DailyProductionTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_supervisor_reads_it_and_the_stores_do_not(self):
        self.the_two_days()
        self.entry_on(SEP(1), "2000", "100")
        supervisor = self.as_("Production Supervisor")
        got = supervisor.get("/api/manufacturing/work-centres/daily/", {"day": "2026-09-01"})
        self.assertEqual(got.status_code, 200, got.content)
        weave = self.row(got.json(), "WEAVE")
        self.assertEqual((weave["made"], weave["waste_percent"], weave["kwh"], weave["kwh_per_kg"]),
                         ("2000.0000", "4.76", "250.000", "0.125"))
        # Yesterday when no day is given: a day with nothing on it still lists every section.
        self.assertEqual(supervisor.get("/api/manufacturing/work-centres/daily/").status_code, 200)
        self.assertEqual(supervisor.get("/api/manufacturing/work-centres/daily/", {"day": "x"}).status_code, 400)
        self.assertEqual(self.as_("Warehouse Staff").get("/api/manufacturing/work-centres/daily/").status_code, 403)
