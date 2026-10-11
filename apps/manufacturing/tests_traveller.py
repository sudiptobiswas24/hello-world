"""
The laminated sack run of 5,000 pieces: coated at 3,000 an hour and cut
at 600, so 100 and 500 planned minutes. Its coating is 1.26 m² x 18 GSM
a sack, 113.4 kg for the run, grossed up for 4% waste to 118.125 kg;
its thread 1.2 g a sack, 6 kg, grossed up for 2.5% to 6.154 kg. The
coater holds it to 18 GSM +/- 10%: 16.20 to 19.80.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Lot
from apps.sales.models import CustomerProfile, SalesOrder, SalesOrderLine

from .orders import WorkOrder, WorkOrderStatus
from .tests_orders import TODAY
from .tests_station_coat import CoatingTestCase
from .traveller import traveller


class TravellerTests(CoatingTestCase):
    def test_steps_materials_and_checks_as_released(self):
        card = traveller(self.lam_run)
        self.assertEqual((card["number"], card["status"], card["quantity_ordered"],
                          card["quantity_to_start"], card["uom"], card["specification"],
                          card["construction"]),
                         (self.lam_run.number, "Released", "5000", "5000", "pcs",
                          "B-LAM (60 x 100 cm, 87 GSM)", "laminated"))
        self.assertEqual([(s["sequence"], s["name"], s["where"], s["planned_minutes"])
                          for s in card["steps"]],
                         [(10, "Coat", "COAT", "100"), (20, "Cut and stitch", "CONV", "500")])
        materials = {m["sku"]: (m["quantity"], m["uom"], m["waste_percent"])
                     for m in card["materials"]}
        self.assertEqual((materials["LAM-PP"], materials["THREAD"]),
                         (("118.125", "kg", "4"), ("6.154", "kg", "2.5")))
        self.assertEqual(card["coating"], {"target": "18.00", "lower": "16.20",
                                           "upper": "19.80", "samples": 3})
        self.assertEqual([row["characteristic"] for row in card["limits"]],
                         ["Finished bag weight"])
        self.assertIn(self.lam_run.number, card["barcode"])

    def test_read_off_the_run_not_the_bill_it_came_from(self):
        component = self.lam_spec.bom.components.get(item__sku="THREAD")
        type(component).objects.filter(pk=component.pk).update(quantity=Decimal("99"))
        materials = {m["sku"]: m["quantity"] for m in traveller(self.lam_run)["materials"]}
        self.assertEqual(materials["THREAD"], "6.154")

    def test_a_limit_printed_to_its_own_places(self):
        line = self.lam_spec.inspection_plan.lines.get()
        type(line.characteristic).objects.filter(pk=line.characteristic_id).update(
            decimal_places=3)
        row = traveller(self.lam_run)["limits"][0]
        self.assertEqual((row["target"], row["lower"], row["upper"]),
                         tuple(str(value.quantize(Decimal("0.001"))) for value in
                               (line.target, line.lower_limit, line.upper_limit)))
        self.assertTrue(all(len(row[key].split(".")[1]) == 3
                            for key in ("target", "lower", "upper")))

    def test_the_machine_the_step_is_on_the_step_done_outside_and_the_tools(self):
        from .tooling import Tool, ToolKind

        steps = {step.name: step for step in self.lam_run.operations.all()}
        type(steps["Coat"]).objects.filter(pk=steps["Coat"].pk).update(machine=self.k1)
        type(steps["Cut and stitch"]).objects.filter(pk=steps["Cut and stitch"].pk).update(
            is_outside=True, work_centre=None, units_per_hour=None)
        # A reed sorts after a die by kind; the card lists them by code.
        self.lam_run.tools.add(Tool.objects.create(code="DIE-7", name="Die", kind=ToolKind.DIE),
                               Tool.objects.create(code="A-REED", name="Reed",
                                                   kind=ToolKind.REED))
        card = traveller(self.lam_run)
        self.assertEqual([(s["where"], s["machine"]) for s in card["steps"]],
                         [("COAT", "K-1"), ("Outside", "")])
        self.assertEqual(card["tools"], ["A-REED", "DIE-7"])

    def test_an_unlaminated_sack_has_no_coating_line(self):
        self.assertIsNone(traveller(self.bag_run)["coating"])

    def test_who_it_is_for_and_what_it_puts_right(self):
        customer = Party.objects.create(code="DCM", name="Deccan Cement")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        profile = CustomerProfile.objects.create(party=customer, sacks_per_bale=500,
                                                 marking="Batch and month on the back")
        order = SalesOrder.objects.create(customer=customer, order_date=TODAY)
        # Packed as the order was taken, whatever the customer asks of the next one.
        profile.sacks_per_bale, profile.marking = 250, "Nothing"
        profile.save()
        line = SalesOrderLine.objects.create(order=order, item=self.lam_bag, uom=self.pcs,
                                             warehouse=self.plant, quantity=Decimal("5000"),
                                             unit_price=Decimal("14"))
        lot = Lot.objects.create(item=self.lam_bag, code="OLD-1")
        WorkOrder.objects.filter(pk=self.lam_run.pk).update(sales_order_line=line,
                                                            rework_of=lot)
        self.lam_run.refresh_from_db()
        card = traveller(self.lam_run)
        order.refresh_from_db()
        self.assertEqual((card["customer_order"], card["rework_of"]),
                         ({"number": order.number, "customer": str(customer), "sacks_per_bale": 500,
                           "marking": "Batch and month on the back"}, "OLD-1"))

    def test_not_a_draft_nor_a_cancelled_run(self):
        draft = WorkOrder.objects.create(item=self.lam_bag, bom=self.lam_spec.bom,
                                         quantity_ordered=Decimal("100"), uom=self.pcs,
                                         warehouse=self.plant)
        with self.assertRaisesMessage(ValidationError, "is a draft"):
            traveller(draft)
        WorkOrder.objects.filter(pk=draft.pk).update(status=WorkOrderStatus.CANCELLED)
        draft.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is cancelled"):
            traveller(draft)
        WorkOrder.objects.filter(pk=self.lam_run.pk).update(status=WorkOrderStatus.CLOSED)
        self.lam_run.refresh_from_db()
        self.assertEqual(traveller(self.lam_run)["status"], "Closed")

    def test_a_run_not_from_a_specification(self):
        from apps.inventory.models import Item

        from .bom import BillOfMaterials, BomComponent

        bom = BillOfMaterials.objects.create(item=self.lam_bag, name="By hand",
                                             quantity_produced=Decimal("1000"), uom=self.pcs,
                                             is_default=False, version=9)
        BomComponent.objects.create(bom=bom, item=Item.objects.get(sku="THREAD"),
                                    quantity=Decimal("1.2"), uom=self.kg)
        run = WorkOrder.objects.create(item=self.lam_bag, bom=bom,
                                       quantity_ordered=Decimal("100"), uom=self.pcs,
                                       warehouse=self.plant)
        run.release(TODAY)
        card = traveller(run)
        self.assertEqual((card["specification"], card["limits"], card["coating"]),
                         (None, [], None))


class TravellerPageTests(CoatingTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("planner"))

    def test_printed_as_a_page(self):
        response = self.client.get(f"/api/manufacturing/work-orders/{self.lam_run.pk}/traveller/")
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        for text in (f"Job card {self.lam_run.number}", "<svg", "Cut and stitch", "118.125",
                     "Coating weight (at the coater)", "16.20", "3 pairs"):
            self.assertIn(text, page)

    def test_a_draft_is_refused(self):
        draft = WorkOrder.objects.create(item=self.lam_bag, bom=self.lam_spec.bom,
                                         quantity_ordered=Decimal("100"), uom=self.pcs,
                                         warehouse=self.plant)
        response = self.client.get(f"/api/manufacturing/work-orders/{draft.pk}/traveller/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("is a draft", response.json()[0])
