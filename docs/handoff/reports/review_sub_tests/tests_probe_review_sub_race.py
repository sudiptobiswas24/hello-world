"""Races for gate g19 (PostgreSQL only): complaint batches moved, a concession against a return."""

import datetime
import threading
import unittest
from decimal import Decimal

from django.db import connection
from django.db.models.signals import pre_save
from django.test import tag

from apps.manufacturing import tests_orders as run_fixture

from .tests_races import RaceCase, fixture, race


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class G19RaceProbes(RaceCase):
    def test_a_batch_is_not_moved_off_a_complaint_as_it_closes(self):
        """X closes (counting L under X's lock) while L is moved to Y: X must not close naming nothing."""
        from apps.manufacturing.complaints import Complaint, ComplaintLot, ComplaintStatus
        from apps.manufacturing.tests_complaints import ComplaintTestCase

        made = fixture(self, ComplaintTestCase)
        x = made.ready()
        y = made.complaint()
        row = x.complained_lots.get()
        closing = threading.Event()
        pre_save.connect(lambda **kwargs: closing.set(), sender=Complaint, weak=False, dispatch_uid="probe-closing")
        self.addCleanup(pre_save.disconnect, sender=Complaint, dispatch_uid="probe-closing")

        def move():
            closing.wait(5)
            moving = ComplaintLot.objects.get(pk=row.pk)
            moving.complaint_id = y.pk
            moving.save()

        outcomes = race(
            (Complaint, ComplaintLot),
            lambda: Complaint.objects.get(pk=x.pk).close(
                "Metal in regrind", by=made.qa, on_date=run_fixture.TODAY + datetime.timedelta(days=1)),
            move,
        )
        x.refresh_from_db()
        self.assertFalse(x.status == ComplaintStatus.CLOSED and not x.complained_lots.exists(), outcomes)
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)

    def test_a_concession_and_a_return_of_the_same_assemblies_are_not_both_made(self):
        """10 held assemblies in the bay: the Quality Manager accepts them while the stores send them back."""
        from apps.purchasing.models import GoodsReceipt, ReceiptInspection
        from apps.purchasing.tests_probe_review_sub import ConcessionProbes

        made = fixture(self, ConcessionProbes)
        made.person("qm9", "Quality Manager")
        manager = made.manager
        receipt = made.received()
        outcomes = race(
            ReceiptInspection,
            lambda: GoodsReceipt.objects.get(pk=receipt.pk).accept(
                made.warehouse, by=manager, reason="Re-checked at the vendor"),
            lambda: GoodsReceipt.objects.get(pk=receipt.pk).reject(note="From F-1", debit_bills=False),
        )
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)
        self.assertEqual(made.assembly.on_hand_at(made.bay), Decimal("0"))
        self.assertGreaterEqual(made.assembly.on_hand_at(made.warehouse), Decimal("0"))
