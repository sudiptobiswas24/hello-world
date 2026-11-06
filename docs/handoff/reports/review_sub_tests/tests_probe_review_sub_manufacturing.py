"""O150 through the API as a Quality Manager (gate g19 review)."""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.manufacturing.complaints import Complaint, ComplaintLot, ComplaintStatus
from apps.manufacturing.tests_complaints import ComplaintTestCase


class ComplaintLotApiProbes(ComplaintTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        manager = User.objects.create(username="qm")
        manager.groups.add(Group.objects.get(name="Quality Manager"))
        self.client = APIClient()
        self.client.force_authenticate(manager)

    def test_a_batch_added_to_a_rejected_complaint_is_a_400_and_names_nothing(self):
        complaint = self.complaint()
        complaint.reject("Not our sacks", by=self.qa)
        response = self.client.post(f"/api/manufacturing/complaints/{complaint.pk}/lots/",
                                    {"lot": self.tape_lot.code, "quantity": "600"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(ComplaintLot.objects.filter(complaint=complaint).exists())

    def test_the_only_batch_of_a_closed_complaint_cannot_be_deleted(self):
        complaint = self.ready()
        complaint.close("Metal in regrind", by=self.qa, on_date=complaint.received_on)
        row = complaint.complained_lots.get()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            row.delete()
        self.assertEqual(complaint.complained_lots.count(), 1)

    def test_a_batch_cannot_be_moved_into_a_complaint_that_is_closed_or_out_of_one(self):
        from django.core.exceptions import ValidationError
        closed = self.ready()
        closed.close("Metal in regrind", by=self.qa, on_date=closed.received_on)
        other = self.complaint()
        row = closed.complained_lots.get()
        row.complaint = other
        with self.assertRaises(ValidationError):
            row.save()
        mover = ComplaintLot.objects.create(complaint=other, lot=self.tape_lot, quantity=Decimal("100"))
        mover.complaint = closed
        with self.assertRaises(ValidationError):
            mover.save()
