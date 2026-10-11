"""
Ten 6205 bearings in the store at 250.00 each. Two go to the repair on
E-1: the store has eight, and 500.00 is maintenance expense — Dr
maintenance 500.00, Cr inventory 500.00. Returned, the store has ten
again and the expense is reversed. Twenty cannot be issued from ten.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.adjustments import AdjustmentDirection, AdjustmentReason
from apps.inventory.models import Item, MovementType, StockMovement

from .maintenance import SpareIssue, issue_spares, reliability, return_spares
from .orders import ManufacturingSettings
from .tests_breakdowns import DAY, BreakdownTestCase
from .tests_orders import TODAY


class SparesTestCase(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        self.pcs = UnitOfMeasure.objects.create(code="pcs", name="Pieces",
                                                category=UnitOfMeasureCategory.COUNT)
        self.bearing = Item.objects.create(sku="BRG-6205", name="Bearing 6205", uom=self.pcs)
        StockMovement.objects.create(item=self.bearing, warehouse=self.plant,
                                     movement_type=MovementType.RECEIPT, uom=self.pcs,
                                     quantity=Decimal("10"), unit_cost=Decimal("250"),
                                     occurred_at=timezone.now())
        self.upkeep = Account.objects.create(code="5300", name="Maintenance",
                                             account_type=AccountType.EXPENSE)
        self.reason = AdjustmentReason.objects.create(
            code="SPARES", name="Spares to maintenance", account=self.upkeep,
            direction=AdjustmentDirection.DECREASE)
        ManufacturingSettings.objects.update(spares_reason=self.reason)
        self.job = self.broken("90")

    def expense(self):
        rows = JournalLine.objects.filter(account=self.upkeep, entry__posted=True)
        debit = rows.aggregate(total=Sum("debit"))["total"] or Decimal("0")
        credit = rows.aggregate(total=Sum("credit"))["total"] or Decimal("0")
        return debit - credit


class IssuedAndReturnedTests(SparesTestCase):
    def test_out_of_the_store_into_maintenance(self):
        issue = issue_spares(self.job, self.plant, [(self.bearing, "2")], on_date=TODAY,
                             issued_to=self.fitter)
        self.assertEqual((self.bearing.on_hand_at(self.plant), issue.value(),
                          self.job.spares_value(), self.expense()),
                         (Decimal("8"), Decimal("500.00"), Decimal("500.00"),
                          Decimal("500.00")))
        return_spares(issue, on_date=TODAY)
        self.assertEqual((self.bearing.on_hand_at(self.plant), self.job.spares_value(),
                          self.expense()),
                         (Decimal("10"), Decimal("0"), Decimal("0")))
        with self.assertRaisesMessage(ValidationError, "was already returned"):
            return_spares(issue)

    def test_returned_through_the_job_not_behind_its_back(self):
        issue = issue_spares(self.job, self.plant, [(self.bearing, "2")], on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "was raised by a maintenance spare issue"):
            issue.adjustment.void()
        self.assertEqual(self.job.spares_value(), Decimal("500.00"))

    def test_not_more_than_the_store_has(self):
        with self.assertRaisesMessage(ValidationError, "Only 10"):
            issue_spares(self.job, self.plant, [(self.bearing, "20")], on_date=TODAY)
        self.assertFalse(SpareIssue.objects.exists())

    def test_what_an_issue_must_be(self):
        with self.assertRaisesMessage(ValidationError, "Say which spares"):
            issue_spares(self.job, self.plant, [], on_date=TODAY)
        for quantity in ("0", "-1", "NaN"):
            with self.assertRaisesMessage(ValidationError, "Issue more than nothing"):
                issue_spares(self.job, self.plant, [(self.bearing, quantity)], on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "is a number"):
            issue_spares(self.job, self.plant, [(self.bearing, "two")], on_date=TODAY)
        ManufacturingSettings.objects.update(spares_reason=None)
        with self.assertRaisesMessage(ValidationError, "manufacturing settings"):
            issue_spares(self.job, self.plant, [(self.bearing, "1")], on_date=TODAY)
        other = AdjustmentReason.objects.create(code="SP2", name="Other", account=self.upkeep)
        issue = issue_spares(self.job, self.plant, [(self.bearing, "1")], on_date=TODAY,
                             reason=other)
        self.assertEqual(issue.adjustment.reason, other)


class TheJobTheyWentToTests(SparesTestCase):
    def test_only_to_an_open_job(self):
        self.job.complete(on_date=TODAY, action="Bearing changed")
        with self.assertRaisesMessage(ValidationError, "is not open"):
            issue_spares(self.job, self.plant, [(self.bearing, "1")], on_date=TODAY)

    def test_returned_while_it_is_open_used_once_it_is_done(self):
        issue = issue_spares(self.job, self.plant, [(self.bearing, "2")], on_date=TODAY)
        self.job.complete(on_date=TODAY, action="Bearing changed")
        issue.job.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is closed; its spares are as"):
            return_spares(issue)

    def test_not_cancelled_with_spares_against_it(self):
        issue = issue_spares(self.job, self.plant, [(self.bearing, "2")], on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "return them first"):
            self.job.cancel("Raised in error")
        return_spares(issue)
        self.job.cancel("Raised in error")

    def test_a_service_with_spares_is_not_deleted(self):
        service = self.schedule(days=90).raise_job(as_of=TODAY)
        issue = issue_spares(service, self.plant, [(self.bearing, "1")], on_date=TODAY)
        return_spares(issue)
        with self.assertRaisesMessage(ValidationError, "is a record of a failure"):
            service.delete()

    def test_counted_in_the_machines_reliability(self):
        issue_spares(self.job, self.plant, [(self.bearing, "2")], on_date=TODAY)
        returned = issue_spares(self.job, self.plant, [(self.bearing, "1")], on_date=TODAY)
        return_spares(returned)
        found = reliability(TODAY, TODAY + DAY, machine=self.e1)
        self.assertEqual(found["spares_value"], Decimal("500.00"))


class SparesApiTests(SparesTestCase):
    def test_issued_and_returned_through_the_job(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("stores"))
        base = f"/api/manufacturing/maintenance-jobs/{self.job.pk}/"
        response = client.post(base + "spares/", {
            "warehouse": self.plant.pk, "issued_to": self.fitter.pk, "on_date": str(TODAY),
            "lines": [{"item": self.bearing.pk, "quantity": "2"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["value"], body["spares_value"]), ("500.00", "500.00"))
        response = client.post(base + "spares/", {
            "warehouse": self.plant.pk, "lines": [{"item": self.bearing.pk,
                                                   "quantity": "99"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        response = client.post(base + f"spares/{body['id']}/return/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["spares_value"], "0")
        self.assertEqual(self.bearing.on_hand_at(self.plant), Decimal("10"))
