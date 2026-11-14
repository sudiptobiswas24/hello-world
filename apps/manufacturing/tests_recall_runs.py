"""
A recall down the runs (O207, the production lineage).

Tape doff T-1: 30 kg of it goes on L-17's warp creel at 09:00, and the
fabric roll F weighed off at 10:42 is woven from it. F is mounted on C-1
at 11:00 and bundle B of 500 sacks is counted from it at 12:00, passed by
its own weight; 200 sacks of B are shipped. Then T-1 is rejected. F (no
inspection of its own) and B (its pass came first) are held, each saying
it is made from T-1 and how T-1 stands; B's to-do names the delivery that
took 200; voiding T-1's rejection releases both.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.core.chatter import FollowUp
from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item, Lot
from apps.quality.models import Inspection, InspectionPlan, Reading, ReleaseStatus
from apps.quality.release import release_status, why_held
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .process_rolls import mount_roll
from .station import LineKind, LoomStation
from .tape_loads import CreelSide, load_tape
from .tests_bag_counts import IN, ConversionTestCase
from .tests_orders import TODAY
from .tests_station import at


class RunRecallTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.clients = {}
        for username, role, number in (("asha", "Quality Manager", "EMP-0300"),
                                       ("sunil", "Stores Manager", None)):
            user = User.objects.create_user(username)
            user.groups.add(Group.objects.get(name=role))
            if number is not None:
                employee = self.employee(number, username.title())
                employee.user = user
                employee.save()
            self.clients[username] = APIClient()
            self.clients[username].force_authenticate(user)
        # The fabric's plan advisory, as a plant that does not inspect each roll keeps it.
        InspectionPlan.objects.filter(item=self.fabric).update(is_mandatory=False)
        self.tape = self.spec.warp_tape.tape_item
        Item.objects.filter(pk=self.tape.pk).update(tracking="lot")
        self.tape.refresh_from_db()
        self.doff = Lot.objects.create(item=self.tape, code="T-1")
        self.stock(self.tape, "100", "120", lot=self.doff)
        load_tape(self.station, self.operator, self.l17, "T-1", "30", CreelSide.WARP, at=at(TODAY, 9))
        self.fabric_lot = self.weigh(when=at(TODAY, 10, 42)).lot
        LoomStation.objects.filter(pk=self.cv.pk).update(kind=LineKind.CONVERSION)  # as tests_process_rolls
        self.cv.refresh_from_db()
        mount_roll(self.cv, self.operator, self.c1, self.fabric_lot.code, at=at(TODAY, 11))
        self.bundle = self.count(machine=self.c1).inspection.lot
        cement = Party.objects.create(code="DCM", name="Deccan Cement")
        PartyRoleAssignment.objects.create(party=cement, role=PartyRole.CUSTOMER)
        order = SalesOrder.objects.create(customer=cement, order_date=TODAY)
        SalesOrderLine.objects.create(order=order, item=self.bag, uom=self.pcs, warehouse=self.plant,
                                      quantity=Decimal("200"), unit_price=Decimal("12"))
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(), warehouse=self.plant,
                                    lot=self.bundle, quantity_shipped=Decimal("200"))
        delivery.post()
        self.delivery_number = delivery.number

    def reject_the_doff(self):
        """T-1 measured after the fact, its denier at 1,085 against 950 to 1,050: rejected."""
        plan = self.spec.warp_tape.inspection_plan
        # Made mandatory now, after the doff went on the creel: the plan rejects what fails it.
        InspectionPlan.objects.filter(pk=plan.pk).update(is_mandatory=True)
        line = plan.lines.get(derived_from="denier")
        rejection = Inspection.objects.create(lot=self.doff, plan=plan, inspected_on=TODAY)
        for number in range(line.sample_size):
            Reading.objects.create(inspection=rejection, plan_line=line, value=Decimal("1085"),
                                   sample_reference=f"S{number}")
        response = self.clients["asha"].post(f"/api/quality/inspections/{rejection.pk}/post/")
        self.assertEqual(response.status_code, 200, response.content)
        rejection.refresh_from_db()
        return rejection


class HeldDownTheRunsTests(RunRecallTestCase):
    def test_fabric_and_bags_made_from_a_rejected_doff_are_held_with_the_words(self):
        self.assertEqual([release_status(lot) for lot in (self.fabric_lot, self.bundle)],
                         [ReleaseStatus.UNINSPECTED, ReleaseStatus.RELEASED])
        rejection = self.reject_the_doff()
        said = f"made from {self.doff}, rejected on 1 June 2026 ({rejection.number})"
        self.assertEqual([release_status(lot) for lot in (self.fabric_lot, self.bundle)],
                         [ReleaseStatus.HELD, ReleaseStatus.HELD])
        self.assertIn(said, why_held(self.fabric, self.fabric_lot, warehouse=self.plant))
        self.assertIn(said, why_held(self.bag, self.bundle, warehouse=self.plant))
        rows = {row["code"]: row["held_because"] for row in self.clients["sunil"].get(
            "/api/inventory/lots/", {"item": self.bag.pk}).json()}
        self.assertIn(said, rows[self.bundle.code])
        recall = Inspection.objects.get(lot=self.bundle, recalled_by=rejection)
        (to_do,) = FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(Inspection),
                                           object_id=recall.pk)
        self.assertIn(f"In stock: 300 {self.bag.uom.code} in {self.plant}.", to_do.note)
        self.assertIn(f"200 {self.bag.uom.code} on {self.delivery_number}", to_do.note)

    def test_the_rejection_voided_both_go_again(self):
        rejection = self.reject_the_doff()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.clients["asha"].post(f"/api/quality/inspections/{rejection.pk}/void/",
                                                 {"reason": "Gauge out of calibration"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([release_status(lot) for lot in (self.fabric_lot, self.bundle)],
                         [ReleaseStatus.UNINSPECTED, ReleaseStatus.RELEASED])
        self.assertFalse(Inspection.objects.filter(recalled_by=rejection, voided_at__isnull=True).exists())


class WhatTheGateCostsTests(RunRecallTestCase):
    def test_the_queries_one_lot_and_a_page_of_batches_ask(self):
        """Measured, not bounded tight: printed for the report, and held under a ceiling."""
        self.reject_the_doff()
        for number in range(25):  # a page of batches on the shelf, none made from anything
            self.stock(self.bag, "10", "12", lot=Lot.objects.create(item=self.bag, code=f"BG-X{number:02d}"))
        with CaptureQueriesContext(connection) as one:
            why_held(self.bag, self.bundle, warehouse=self.plant)
        with CaptureQueriesContext(connection) as page:
            response = self.clients["sunil"].get("/api/inventory/lots/", {"page_size": 25, "item": self.bag.pk})
        self.assertEqual(response.status_code, 200)
        rows = len(response.json()) if isinstance(response.json(), list) else len(response.json()["results"])
        print(f"\nGATE COST: why_held of the bundle, three levels: {len(one)} queries; "
              f"the Batches list, {rows} lots: {len(page)} queries")
        self.assertLess(len(one), 80)
