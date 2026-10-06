"""
The contract labour registers: a licence for two at once, a third refused
on any day two are working, a worker's stay extended past someone who
joined later refused too, and nothing deleted.
"""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .contract_labour import ContractWorker, LabourContractor, licences_due

JAN, FEB, MAR = (datetime.date(2026, month, 1) for month in (1, 2, 3))


class RegisterTests(TestCase):
    def setUp(self):
        party = Party.objects.create(code="V-LAB", name="Shakti Labour Services")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        self.contractor = LabourContractor.objects.create(
            party=party, licence_number="CLRA/2026/77", licence_valid_to=datetime.date(2026, 12, 31),
            work_nature="Bag stitching", max_workers=2)

    def worker(self, name, joined, left=None):
        return ContractWorker.objects.create(contractor=self.contractor, name=name, gender="female",
                                             daily_wage="520", joined_on=joined, left_on=left)

    def test_no_more_at_once_than_the_licence_allows(self):
        self.worker("Asha", JAN)
        early_leaver = self.worker("Bina", JAN, left=datetime.date(2026, 2, 15))
        with self.assertRaisesMessage(ValidationError, "covers 2 at once"):
            self.worker("Chitra", FEB)
        self.worker("Chitra", MAR)
        early_leaver.left_on = None
        with self.assertRaisesMessage(ValidationError, "01 Mar 2026"):
            early_leaver.save()

    def test_not_after_the_licence_ran_out_and_nothing_deleted(self):
        with self.assertRaisesMessage(ValidationError, "licence ran out"):
            self.worker("Dev", datetime.date(2027, 1, 2))
        asha = self.worker("Asha", JAN)
        with self.assertRaisesMessage(ValidationError, "give the day they left"):
            asha.delete()
        with self.assertRaisesMessage(ValidationError, "stays in the register"):
            self.contractor.delete()

    def test_licences_running_out(self):
        self.assertEqual(list(licences_due(30, on_date=datetime.date(2026, 12, 5))), [self.contractor])
        self.assertEqual(list(licences_due(30, on_date=datetime.date(2026, 11, 1))), [])
