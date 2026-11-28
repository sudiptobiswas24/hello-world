"""
The specification, costing, job-work and customer-material screens'
server side, asked as the people who use them.

The process engineer writes the specifications and the planner reads
them. The controller keeps the rates a quotation is costed at and the
standard costs; a sales rep reads the cost sheet behind a quotation and
changes no rate. The stores manager sends material out to a job worker
and takes a customer's own material in and back. Each list names what a
row is about.
"""

from decimal import Decimal

from django.core.management import call_command

from .models import JobWorkLoss
from .tests_floor_reports import as_
from .tests_inward import InwardTestCase
from .tests_jobwork import JobWorkTestCase
from .tests_woven import WovenTestCase

TAPES = "/api/manufacturing/tape-specifications/"
RATES = "/api/manufacturing/material-rates/"


class SpecificationTests(WovenTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def sent(self):
        return {"code": "T1000", "tape_item": self.tape_item.pk, "denier": "1000", "tape_width_mm": "2.5",
                "virgin_granule": self.virgin.pk, "filler_item": self.filler.pk, "filler_percent": "8"}

    def test_the_planner_reads_one_and_does_not_write_one(self):
        planner = as_("Production Planner")
        self.assertEqual(planner.post(TAPES, self.sent(), format="json").status_code, 403)
        self.tape()
        self.assertEqual([row["code"] for row in planner.get(TAPES).json()], ["T1000"])

    def test_the_engineer_writes_one_and_finds_it(self):
        engineer = as_("Process Engineer")
        made = engineer.post(TAPES, self.sent(), format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual([row["code"] for row in engineer.get(TAPES, {"is_active": "true", "search": "t1000"}).json()],
                         ["T1000"])
        self.assertEqual(engineer.get(TAPES, {"is_active": "false"}).json(), [])


class RateTests(WovenTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def rate(self, client, rate="98.5"):
        return client.post(RATES, {"item": self.virgin.pk, "rate": rate, "valid_from": "2026-06-01"}, format="json")

    def test_the_controller_keeps_the_rate_sheet(self):
        controller = as_("Controller")
        made = self.rate(controller)
        self.assertEqual(made.status_code, 201, made.content)
        [row] = controller.get(RATES, {"item": self.virgin.pk, "from": "2026-06-01"}).json()
        self.assertEqual((row["item_label"], row["rate"]), ("PP-RAFFIA · PP homopolymer, raffia grade", "98.5000"))
        self.assertEqual(controller.get(RATES, {"to": "2026-05-31"}).json(), [])

    def test_whoever_quotes_reads_the_cost_sheet_and_sets_no_rate(self):
        rep = as_("Sales Rep")
        self.assertEqual(self.rate(rep).status_code, 403)
        self.assertEqual(rep.get("/api/manufacturing/cost-sheets/").status_code, 200)

    def test_the_engineer_does_not_set_what_material_costs(self):
        self.assertEqual(self.rate(as_("Process Engineer")).status_code, 403)


class JobWorkScreenTests(JobWorkTestCase):
    """
    The outside fixture's run sends 1,000 kg of fabric out to be coated;
    this challan sends 600 kg of it, and 4 kg is lost at the laminator.
    """

    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.stores = as_("Stores Manager")

    def test_the_stores_manager_sends_material_out_and_records_a_loss(self):
        steps = self.stores.get("/api/manufacturing/work-order-operations/",
                                {"work_order__status": "released", "is_outside": "true"}).json()
        self.assertEqual([step["id"] for step in steps], [self.coat.pk])
        challan = self.stores.post("/api/manufacturing/job-work-challans/", {
            "job_worker": self.laminator.pk, "challan_date": "2026-06-01"}, format="json")
        self.assertEqual(challan.status_code, 201, challan.content)
        challan = challan.json()["id"]
        line = self.stores.post("/api/manufacturing/job-work-lines/", {
            "challan": challan, "operation": self.coat.pk, "description": "Woven fabric for coating",
            "hsn_code": "63053300", "quantity": "600", "value": "57000", "tax_rate": "5"}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        posted = self.stores.post(f"/api/manufacturing/job-work-challans/{challan}/post/")
        self.assertEqual(posted.status_code, 200, posted.content)
        [row] = self.stores.get("/api/manufacturing/job-work-challans/", {"posted": "true"}).json()
        self.assertEqual(row["job_worker_name"], "Laminator")
        self.assertEqual(row["lines"][0]["operation_label"],
                         f"{self.lamination.number} · {self.coat.sequence} {self.coat.name}")

        loss = self.stores.post("/api/manufacturing/job-work-losses/", {
            "line": line.json()["id"], "loss_date": "2026-06-05", "quantity": "4"}, format="json")
        self.assertEqual(loss.status_code, 201, loss.content)
        [lost] = self.stores.get("/api/manufacturing/job-work-losses/", {"line__challan": challan}).json()
        self.assertEqual((lost["line_description"], lost["quantity"]), ("Woven fabric for coating", "4.0000"))
        voided = self.stores.post(f"/api/manufacturing/job-work-losses/{lost['id']}/void/")
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertIsNotNone(JobWorkLoss.objects.get(pk=lost["id"]).voided_at)

    def test_the_planner_sends_nothing_out(self):
        response = as_("Production Planner").post("/api/manufacturing/job-work-challans/", {
            "job_worker": self.laminator.pk, "challan_date": "2026-06-01"}, format="json")
        self.assertEqual(response.status_code, 403)


class CustomerMaterialScreenTests(InwardTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.stores = as_("Stores Manager")

    def test_in_and_back_named_for_the_customer(self):
        base = "/api/manufacturing/"
        receipt = self.stores.post(base + "customer-material-receipts/", {
            "customer": self.sunrise.pk, "warehouse": self.held.pk, "received_on": "2026-05-25",
            "their_challan": "SC/114", "their_challan_date": "2026-05-25"}, format="json")
        self.assertEqual(receipt.status_code, 201, receipt.content)
        receipt = receipt.json()["id"]
        line = self.stores.post(base + "customer-material-receipt-lines/", {
            "receipt": receipt, "item": self.virgin.pk, "lot": self.their_lot.pk,
            "quantity": "500", "declared_value": "55000"}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        self.assertEqual(self.stores.post(base + f"customer-material-receipts/{receipt}/post/").status_code, 200)
        [row] = self.stores.get(base + "customer-material-receipts/", {"customer": self.sunrise.pk}).json()
        self.assertEqual((row["customer_name"], row["warehouse_name"]), ("Sunrise Cement", "Held for Sunrise"))
        self.assertEqual((row["lines"][0]["lot_code"], Decimal(row["lines"][0]["quantity"])), ("SC-PP-1", Decimal("500")))

        # What can go back: only their own received, posted lines.
        lines = self.stores.get(base + "customer-material-receipt-lines/", {
            "receipt__customer": self.sunrise.pk, "receipt__posted": "true"}).json()
        self.assertEqual([found["id"] for found in lines], [line.json()["id"]])
        back = self.stores.post(base + "customer-material-returns/", {
            "customer": self.sunrise.pk, "warehouse": self.held.pk, "returned_on": "2026-06-01"}, format="json")
        self.assertEqual(back.status_code, 201, back.content)
        back = back.json()["id"]
        self.stores.post(base + "customer-material-return-lines/", {
            "material_return": back, "receipt_line": line.json()["id"], "item": self.virgin.pk,
            "lot": self.their_lot.pk, "quantity": "200"}, format="json")
        self.assertEqual(self.stores.post(base + f"customer-material-returns/{back}/post/").status_code, 200)
        [sent] = self.stores.get(base + "customer-material-returns/", {"posted": "true"}).json()
        self.assertEqual((sent["customer_name"], sent["lines"][0]["item_label"]),
                         ("Sunrise Cement", "PP-RAFFIA · PP homopolymer"))

    def test_a_sales_rep_takes_no_material_in(self):
        response = as_("Sales Rep").post("/api/manufacturing/customer-material-receipts/", {
            "customer": self.sunrise.pk, "warehouse": self.held.pk, "received_on": "2026-05-25"}, format="json")
        self.assertEqual(response.status_code, 403)
