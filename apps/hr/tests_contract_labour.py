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


class AStoppedContractorTests(TestCase):
    """O309: an inactive contractor takes no new worker, and its workers already working can still leave."""

    def test_no_new_worker_and_the_one_working_is_given_a_leaving_day(self):
        import datetime
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from apps.core.models import Party

        from .contract_labour import ContractWorker, LabourContractor

        contractor = LabourContractor.objects.create(
            party=Party.objects.create(code="CON1", name="Sai Labour"), licence_number="L1",
            licence_valid_to=datetime.date(2027, 3, 31), work_nature="Loading", max_workers=5)
        ravi = ContractWorker.objects.create(contractor=contractor, name="Ravi", gender="male",
                                             daily_wage=Decimal("600"), joined_on=datetime.date(2026, 10, 1))
        contractor.is_active = False
        contractor.save()
        with self.assertRaisesMessage(ValidationError, "no longer active"):
            ContractWorker.objects.create(contractor=contractor, name="Mohan", gender="male",
                                          daily_wage=Decimal("600"), joined_on=datetime.date(2026, 10, 2))
        ravi.left_on = datetime.date(2026, 10, 20)
        ravi.save()
        self.assertEqual(ContractWorker.objects.get(pk=ravi.pk).left_on, datetime.date(2026, 10, 20))


class NoWorkerJoinsAStoppedContractorTests(TestCase):
    """Review 4 (O309): moved to a stopped contractor, or brought back under one, is refused as a new worker is."""

    def test_moved_to_it_or_brought_back_under_it_is_refused(self):
        import datetime
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from apps.core.models import Party

        from .contract_labour import ContractWorker, LabourContractor

        def contractor(code, active=True):
            return LabourContractor.objects.create(
                party=Party.objects.create(code=code, name=code), licence_number=code,
                licence_valid_to=datetime.date(2027, 3, 31), work_nature="Loading", max_workers=5, is_active=active)

        working, stopped = contractor("CON1"), contractor("CON2")
        ravi = ContractWorker.objects.create(contractor=working, name="Ravi", gender="male", daily_wage=Decimal("600"),
                                             joined_on=datetime.date(2026, 10, 1))
        mohan = ContractWorker.objects.create(contractor=stopped, name="Mohan", gender="male",
                                              daily_wage=Decimal("600"), joined_on=datetime.date(2026, 10, 1),
                                              left_on=datetime.date(2026, 10, 5))
        stopped.is_active = False
        stopped.save()
        ravi.contractor = stopped
        with self.assertRaisesMessage(ValidationError, "no longer active"):
            ravi.save()
        mohan.left_on = None
        with self.assertRaisesMessage(ValidationError, "no longer active"):
            mohan.save()
        self.assertEqual(ContractWorker.objects.get(pk=ravi.pk).contractor, working)
