"""
Tape batch TAPE-A, made from polymer PP-2609, shipped 600 kg to the
customer and 300 kg to Other Sacks. The customer complains of TAPE-A
breaking: its genealogy leads to PP-2609, and Other Sacks still holds
300 kg of the same batch, which is the containment call to make.

Closed only with a root cause, a batch, a corrective or preventive
action, every action done, and those checked afterwards by somebody
other than their owner.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .complaints import (
    ActionKind,
    Complaint,
    ComplaintStatus,
    CorrectiveAction,
    complaints_by,
    overdue_actions,
)
from .tests_orders import TODAY
from .tests_trace import TraceTestCase

DAY = datetime.timedelta(days=1)


def person(number, name):
    party = Party.objects.create(code=number, name=name)
    PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
    return Employee.objects.create(party=party, employee_number=number,
                                   hire_date=datetime.date(2020, 1, 1))


class ComplaintTestCase(TraceTestCase):
    def setUp(self):
        super().setUp()
        self.a_run()
        self.ship("600")
        self.other = Party.objects.create(code="OTH", name="Other Sacks")
        PartyRoleAssignment.objects.create(party=self.other, role=PartyRole.CUSTOMER)
        self.ship_to(self.other, "300")
        self.qa = person("EMP-0501", "Quality head")
        self.plant_head = person("EMP-0502", "Plant head")

    def ship_to(self, customer, quantity):
        order = SalesOrder.objects.create(customer=customer, order_date=TODAY,
                                          currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.tape, uom=self.kg,
                                             warehouse=self.plant, quantity=Decimal(quantity),
                                             unit_price=Decimal("120"))
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=line, warehouse=self.plant,
                                    quantity_shipped=Decimal(quantity), lot=self.tape_lot)
        delivery.post()
        return delivery

    def complaint(self, **extra):
        values = dict(customer=self.customer, received_on=TODAY, category="strength",
                      description="Tape breaking on the loom")
        values.update(extra)
        return Complaint.objects.create(**values)

    def action(self, complaint, kind=ActionKind.CORRECTIVE, owner=None, due=TODAY + DAY):
        return CorrectiveAction.objects.create(
            complaint=complaint, kind=kind, description="Screen the regrind",
            owner=owner or self.plant_head, due_on=due)

    def ready(self):
        """A complaint with its batch and a corrective action done and verified."""
        complaint = self.complaint()
        complaint.add_lot(self.tape_lot, "600")
        action = self.action(complaint)
        action.done("Magnet and 120 mesh on the regrind line", on_date=TODAY)
        action.verify(self.qa, on_date=TODAY + DAY)
        return complaint


class TheBatchesTests(ComplaintTestCase):
    def test_numbered_and_linked_to_what_was_shipped(self):
        complaint = self.complaint()
        self.assertTrue(complaint.number.startswith("CMP-"))
        complaint.add_lot(self.tape_lot, "600")
        self.assertEqual(complaint.lots(), [self.tape_lot])

    def test_only_a_batch_shipped_to_that_customer(self):
        complaint = self.complaint()
        with self.assertRaisesMessage(ValidationError, "PP-2609 was never shipped to"):
            complaint.add_lot(self.polymer_lot)
        stranger = Party.objects.create(code="STR", name="Stranger")
        with self.assertRaisesMessage(ValidationError, "was never shipped to"):
            self.complaint(customer=stranger).add_lot(self.tape_lot)
        with self.assertRaisesMessage(ValidationError, "is more than nothing"):
            complaint.add_lot(self.tape_lot, "0")

    def test_a_batch_sent_back_was_still_shipped(self):
        self.ship("100", day=16).create_return(credit_invoices=False)
        self.assertEqual(self.complaint().add_lot(self.tape_lot).lot, self.tape_lot)

    def test_investigated_back_to_the_polymer_and_out_to_other_customers(self):
        complaint = self.complaint()
        complaint.add_lot(self.tape_lot)
        found = complaint.investigation()
        self.assertIn("PP-2609", {row["from_lot"].code for row in found["made_from"]["TAPE-A"]
                                  if row["from_lot"] is not None})
        (row,) = found["also_held_by"]
        self.assertEqual((row["customer"], row["quantity"]), (self.other, Decimal("300")))

    def test_what_a_complaint_must_say(self):
        with self.assertRaisesMessage(ValidationError, "Say what the customer complains of"):
            self.complaint(description="  ")
        with self.assertRaisesMessage(ValidationError, "Say what the action is"):
            CorrectiveAction.objects.create(complaint=self.complaint(), kind=ActionKind.CORRECTIVE,
                                            description=" ", owner=self.qa, due_on=TODAY)


class ClosingTests(ComplaintTestCase):
    def test_closed_when_the_actions_are_done_and_have_worked(self):
        complaint = self.ready()
        complaint.close("Metal in regrind  cut the tape", by=self.qa, on_date=TODAY + DAY)
        complaint.refresh_from_db()
        self.assertEqual((complaint.status, complaint.root_cause, complaint.decided_by,
                          complaint.decided_on),
                         (ComplaintStatus.CLOSED, "Metal in regrind cut the tape", self.qa,
                          TODAY + DAY))

    def test_not_without_a_root_cause_or_a_batch(self):
        complaint = self.ready()
        with self.assertRaisesMessage(ValidationError, "Write down the root cause"):
            complaint.close(" ", by=self.qa)
        bare = self.complaint()
        with self.assertRaisesMessage(ValidationError, "names no batch"):
            bare.close("Cause", by=self.qa)

    def test_not_on_containment_alone(self):
        complaint = self.complaint()
        complaint.add_lot(self.tape_lot)
        held = self.action(complaint, kind=ActionKind.CONTAINMENT)
        held.done("Stock quarantined")
        with self.assertRaisesMessage(ValidationError, "has no corrective or preventive"):
            complaint.close("Cause", by=self.qa)
        with self.assertRaisesMessage(ValidationError, "nothing to verify"):
            held.verify(self.qa)

    def test_not_with_an_action_undone_or_unchecked(self):
        complaint = self.complaint()
        complaint.add_lot(self.tape_lot)
        action = self.action(complaint)
        with self.assertRaisesMessage(ValidationError, "is not done"):
            complaint.close("Cause", by=self.qa)
        with self.assertRaisesMessage(ValidationError, "is not done yet"):
            action.verify(self.qa)
        action.done("Done", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "was done on"):
            action.done("Again")
        with self.assertRaisesMessage(ValidationError, "has not been checked"):
            complaint.close("Cause", by=self.qa)
        containment = self.action(complaint, kind=ActionKind.CONTAINMENT)
        action.verify(self.qa, on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "is not done"):
            complaint.close("Cause", by=self.qa)
        containment.done("Quarantined")
        complaint.close("Cause", by=self.qa)

    def test_checked_by_somebody_else_after_it_was_done(self):
        complaint = self.complaint()
        action = self.action(complaint, owner=self.plant_head)
        action.done("Done", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "somebody other than its owner"):
            action.verify(self.plant_head, on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "checked after it was done"):
            action.verify(self.qa, on_date=TODAY - DAY)
        action.verify(self.qa, on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "was verified on"):
            action.verify(self.qa, on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "Say what was done"):
            self.action(complaint).done(" ")

    def test_rejected_with_a_reason(self):
        complaint = self.complaint()
        with self.assertRaisesMessage(ValidationError, "Say why the complaint is not ours"):
            complaint.reject(" ", by=self.qa)
        complaint.reject("Customer's own stitching", by=self.qa, on_date=TODAY)
        self.assertEqual((complaint.status, complaint.rejection_reason),
                         (ComplaintStatus.REJECTED, "Customer's own stitching"))
        with self.assertRaisesMessage(ValidationError, "is rejected"):
            complaint.close("Cause", by=self.qa)
        with self.assertRaisesMessage(ValidationError, "is rejected"):
            complaint.reject("Again", by=self.qa)


class DecidedIsKeptTests(ComplaintTestCase):
    def test_a_decided_complaint_is_as_it_was_decided(self):
        complaint = self.ready()
        complaint.close("Cause", by=self.qa)
        complaint.description = "Changed"
        with self.assertRaisesMessage(ValidationError, "is closed; reopen it"):
            complaint.save()
        with self.assertRaisesMessage(ValidationError, "is closed; reopen it"):
            complaint.add_lot(self.tape_lot)
        with self.assertRaisesMessage(ValidationError, "is closed; reopen it"):
            self.action(complaint)
        with self.assertRaisesMessage(ValidationError, "is closed; reopen it"):
            complaint.complained_lots.get().delete()
        with self.assertRaisesMessage(ValidationError, "reject it"):
            complaint.delete()

    def test_reopened_with_why(self):
        complaint = self.ready()
        with self.assertRaisesMessage(ValidationError, "is open"):
            complaint.reopen("x")
        complaint.close("Cause", by=self.qa)
        with self.assertRaisesMessage(ValidationError, "Say why it is reopened"):
            complaint.reopen(" ")
        complaint.reopen("Breaking again on the next lot")
        complaint.refresh_from_db()
        self.assertEqual((complaint.status, complaint.decided_by, complaint.reopened_reason,
                          complaint.root_cause),
                         (ComplaintStatus.OPEN, None, "Breaking again on the next lot",
                          "Cause"))
        self.action(complaint)

    def test_an_action_done_stays_on_the_record(self):
        complaint = self.complaint()
        action = self.action(complaint)
        action.done("Done")
        with self.assertRaisesMessage(ValidationError, "is done; it is part of the record"):
            action.delete()
        spare = self.action(complaint)
        spare.delete()
        self.assertFalse(CorrectiveAction.objects.filter(pk=spare.pk).exists())


class ReportsTests(ComplaintTestCase):
    def test_actions_overdue_on_open_complaints(self):
        complaint = self.complaint()
        late = self.action(complaint, due=TODAY)
        self.action(complaint, due=TODAY + DAY)
        on_time = self.action(complaint, due=TODAY - DAY)
        on_time.done("Done", on_date=TODAY - DAY)
        rejected = self.complaint()
        self.action(rejected, due=TODAY - DAY)
        rejected.reject("Not ours", by=self.qa)
        self.assertEqual(overdue_actions(TODAY + DAY), [late])

    def test_counted_by_category_and_by_customer(self):
        self.complaint()
        self.complaint(category="print")
        self.complaint(customer=self.other)
        self.complaint(received_on=TODAY + 40 * DAY)
        self.assertEqual(complaints_by(TODAY, TODAY + DAY),
                         [("strength", 2), ("print", 1)])
        self.assertEqual(complaints_by(TODAY, TODAY + DAY, field="customer"),
                         [(self.customer.code, 2), ("OTH", 1)])
        with self.assertRaisesMessage(ValidationError, "by category or by customer"):
            complaints_by(TODAY, TODAY, field="colour")


class ComplaintApiTests(ComplaintTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("quality"))

    def test_from_complaint_to_close(self):
        response = self.client.post("/api/manufacturing/complaints/", {
            "customer": self.customer.pk, "received_on": str(TODAY), "category": "strength",
            "description": "Tape breaking"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        base = f"/api/manufacturing/complaints/{response.json()['id']}/"
        response = self.client.post(base + "lots/", {"lot": "TAPE-A", "quantity": "600"},
                                    format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["lots"],
                         [{"lot": "TAPE-A", "item": self.tape.sku, "quantity": "600.0000"}])
        self.assertEqual(self.client.post(base + "lots/", {"lot": "PP-2609"},
                                          format="json").status_code, 400)
        found = self.client.get(base + "investigation/").json()
        self.assertEqual(found["also_held_by"][0]["customer"], "OTH")
        self.assertIn("PP-2609", [row["from_lot"] for row in found["made_from"]["TAPE-A"]])
        response = self.client.post("/api/manufacturing/corrective-actions/", {
            "complaint": response.json()["id"], "kind": "corrective",
            "description": "Screen the regrind", "owner": self.plant_head.pk,
            "due_on": str(TODAY)}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        action = f"/api/manufacturing/corrective-actions/{response.json()['id']}/"
        self.assertEqual(self.client.post(base + "close/", {"root_cause": "Metal",
                                                            "by": self.qa.pk},
                                          format="json").status_code, 400)
        self.assertEqual(self.client.post(action + "done/", {"note": "Magnet fitted",
                                                             "on_date": str(TODAY)},
                                          format="json").status_code, 200)
        self.assertEqual(self.client.post(action + "verify/", {"by": self.plant_head.pk},
                                          format="json").status_code, 400)
        self.assertEqual(self.client.post(action + "verify/", {"by": self.qa.pk},
                                          format="json").status_code, 200)
        self.assertEqual(self.client.post(base + "close/", {"root_cause": "Metal"},
                                          format="json").status_code, 400)
        response = self.client.post(base + "close/", {"root_cause": "Metal", "by": self.qa.pk},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "closed")
        self.assertEqual(self.client.patch(base, {"description": "x"},
                                           format="json").status_code, 400)
        self.assertEqual(self.client.delete(base).status_code, 405)
        response = self.client.post(base + "reopen/", {"reason": "Again"}, format="json")
        self.assertEqual(response.json()["status"], "open")

    def test_reports(self):
        complaint = self.complaint()
        self.action(complaint, due=TODAY - DAY)
        response = self.client.get("/api/manufacturing/corrective-actions/overdue/",
                                   {"on_date": str(TODAY)})
        self.assertEqual(len(response.json()), 1)
        response = self.client.get("/api/manufacturing/complaints/counts/",
                                   {"start": str(TODAY), "end": str(TODAY), "by": "customer"})
        self.assertEqual(response.json(), [{"key": self.customer.code, "complaints": 1}])
        self.assertEqual(self.client.get("/api/manufacturing/complaints/counts/").status_code,
                         400)
