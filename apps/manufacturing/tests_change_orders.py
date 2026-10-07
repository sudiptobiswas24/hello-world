"""
A recipe in use changes by a change order. Version 1 of the product
(10 kg of virgin and 2 of colour a batch, trim off it as a by-product,
regrind allowed instead of virgin) is to change from the first of
November: the order copies it to version 2, line for line, to be
edited; applied, version 1 ends on the 31st of October, version 2 is
the default from the 1st, and the draft run due on the 5th moves to it
while the run due on the 20th of October and the run already released
stay as they were.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from .bom import BomByproduct, BomSubstitute, ByproductValuation, default_bom_for
from .changes import BomChangeOrder, ChangeStatus, raise_change
from .orders import WorkOrder, WorkOrderStatus
from .tests_effectivity import NOV_1, OCT_31, EffectivityTestCase

NOV_5 = datetime.date(2026, 11, 5)
OCT_20 = datetime.date(2026, 10, 20)


class ChangeTestCase(EffectivityTestCase):
    def setUp(self):
        super().setUp()
        self.one = self.recipe(1)
        self.virgin_line = self.line(self.one, self.virgin, "10", number=1)
        self.line(self.one, self.colour, "2", number=2)
        BomByproduct.objects.create(bom=self.one, item=self.regrind, quantity=Decimal("0.5"), uom=self.kg,
                                    valuation=ByproductValuation.STANDARD)
        BomSubstitute.objects.create(component=self.virgin_line, item=self.regrind, quantity_per=Decimal("1.1"))

    def make_run(self, bom, starts, status=WorkOrderStatus.DRAFT):
        order = WorkOrder.objects.create(item=self.product, bom=bom, quantity_ordered=Decimal("100"), uom=self.kg,
                                         warehouse=self.plant, work_centre=self.loom, scheduled_start=starts)
        if status == WorkOrderStatus.RELEASED:
            order.release(on_date=starts)
        return order


class RaisingTests(ChangeTestCase):
    def test_the_new_version_is_a_copy_to_edit_and_not_yet_the_default(self):
        change = raise_change(self.one, NOV_1, "Thinner tape: 9 of virgin")
        two = change.draft
        self.assertEqual((two.item, two.version, two.is_default, two.is_active, two.valid_from, two.valid_to),
                         (self.product, 2, False, True, NOV_1, None))
        self.assertEqual([(row.item, row.quantity, row.line_number) for row in two.components.order_by("line_number")],
                         [(self.virgin, Decimal("10"), 1), (self.colour, Decimal("2"), 2)])
        self.assertEqual([(row.item, row.quantity) for row in two.byproducts.all()], [(self.regrind, Decimal("0.5"))])
        self.assertEqual([(row.component.bom, row.item, row.quantity_per)
                          for row in BomSubstitute.objects.filter(component__bom=two)],
                         [(two, self.regrind, Decimal("1.1"))])
        self.assertEqual((change.status, change.number, str(change)), (ChangeStatus.DRAFT, "", f"Change to {self.one}"))
        # Version 1 still answers for every day until the change is applied.
        self.assertEqual(default_bom_for(self.product, NOV_5), self.one)

    def test_what_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "Say the first day"):
            raise_change(self.one, None, "why")
        with self.assertRaisesMessage(ValidationError, "Say what changes"):
            raise_change(self.one, NOV_1, "  ")
        raise_change(self.one, NOV_1, "first")
        with self.assertRaisesMessage(ValidationError, "already has a change order open"):
            raise_change(self.one, NOV_5, "second")
        bounded = self.recipe(5, valid_from=NOV_1, valid_to=datetime.date(2026, 11, 30), is_default=False)
        with self.assertRaisesMessage(ValidationError, "takes effect after that"):
            raise_change(bounded, NOV_1, "same day")
        with self.assertRaisesMessage(ValidationError, "the days between have no recipe"):
            raise_change(bounded, datetime.date(2026, 12, 5), "a gap")
        computed = self.recipe(6, is_default=False)
        computed.is_computed = True
        computed._rebuilding = True
        computed.save()
        with self.assertRaisesMessage(ValidationError, "is computed from"):
            raise_change(computed, NOV_1, "edit")


class ApplyingTests(ChangeTestCase):
    def test_applied_the_new_version_takes_over_from_its_day_and_draft_runs_move(self):
        soon, later = self.make_run(self.one, OCT_20), self.make_run(self.one, NOV_5)
        released = self.make_run(self.one, NOV_5, status=WorkOrderStatus.RELEASED)
        undated = self.make_run(self.one, None)
        change = raise_change(self.one, NOV_1, "Thinner tape")
        self.virgin_line_two = change.draft.components.get(item=self.virgin)
        self.virgin_line_two.quantity = Decimal("9")
        self.virgin_line_two.save()
        change.apply(note="Agreed with the floor")
        self.one.refresh_from_db()
        two = change.draft
        two.refresh_from_db()
        self.assertEqual((self.one.valid_to, self.one.is_default, self.one.is_active), (OCT_31, True, True))
        self.assertEqual((two.valid_from, two.is_default, two.is_active), (NOV_1, True, True))
        self.assertEqual((default_bom_for(self.product, OCT_31), default_bom_for(self.product, NOV_1)), (self.one, two))
        for order in (soon, later, released, undated):
            order.refresh_from_db()
        self.assertEqual([order.bom for order in (soon, later, released, undated)], [self.one, two, self.one, two])
        self.assertEqual((change.status, change.number[:4], change.decision_note),
                         (ChangeStatus.APPLIED, "ECO-", "Agreed with the floor 2 draft runs moved to version 2."))
        with self.assertRaisesMessage(ValidationError, "already applied"):
            change.apply()
        change.reason = "edited after"
        with self.assertRaisesMessage(ValidationError, "decided once"):
            change.save()

    def test_an_old_version_that_never_had_a_day_goes_out_of_use(self):
        fresh = self.recipe(3, valid_from=NOV_1, is_default=False)
        self.line(fresh, self.virgin, "10")
        change = raise_change(fresh, datetime.date(2026, 11, 2), "found before use")
        # The window was edited after the order was raised: the version now starts on the change's day.
        fresh.valid_from = datetime.date(2026, 11, 2)
        fresh.save()
        change.apply()
        fresh.refresh_from_db()
        self.assertEqual((fresh.is_active, fresh.valid_to), (False, None))

    def test_a_version_with_nothing_in_it_is_not_applied(self):
        change = raise_change(self.one, NOV_1, "strip it")
        change.draft.components.all().delete()
        with self.assertRaisesMessage(ValidationError, "has no inputs"):
            change.apply()

    def test_rejected_the_draft_version_goes_out_of_use_and_the_old_one_stands(self):
        change = raise_change(self.one, NOV_1, "not this")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            change.reject(note=" ")
        change.reject(note="Costed too high")
        change.draft.refresh_from_db()
        self.one.refresh_from_db()
        self.assertEqual((change.status, change.draft.is_active, self.one.valid_to, self.one.is_default),
                         (ChangeStatus.REJECTED, False, None, True))
        self.assertEqual(default_bom_for(self.product, NOV_5), self.one)
        # Rejected, the version can be changed again.
        self.assertEqual(raise_change(self.one, NOV_1, "again").draft.version, 3)


class OfficeTests(ChangeTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_engineer_raises_it_from_the_bill_and_applies_it(self):
        engineer = self.as_("Process Engineer")
        raised = engineer.post(f"/api/manufacturing/boms/{self.one.pk}/change-order/",
                               {"effective_from": "2026-11-01", "reason": "Thinner tape"}, format="json")
        self.assertEqual(raised.status_code, 201, raised.content)
        change = engineer.get(f"/api/manufacturing/bom-change-orders/{raised.json()['id']}/").json()
        self.assertEqual((change["status"], change["supersedes"], change["draft"], change["effective_from"],
                          change["supersedes_label"], change["draft_label"], change["created_by_name"]),
                         ("draft", self.one.pk, raised.json()["draft"], "2026-11-01",
                          "PROD · Product, version 1", "PROD · Product, version 2", "process_engineer"))
        again = engineer.post(f"/api/manufacturing/boms/{self.one.pk}/change-order/",
                              {"effective_from": "2026-11-01", "reason": "Twice"}, format="json")
        self.assertEqual((again.status_code, "already has a change order open" in again.content.decode()), (400, True))
        planner = self.as_("Production Planner")
        self.assertEqual(planner.get(f"/api/manufacturing/bom-change-orders/{change['id']}/").status_code, 200)
        self.assertEqual(planner.post(f"/api/manufacturing/bom-change-orders/{change['id']}/apply/", {}, format="json").status_code, 403)
        applied = engineer.post(f"/api/manufacturing/bom-change-orders/{change['id']}/apply/", {"note": "Go"}, format="json")
        self.assertEqual((applied.status_code, applied.json()["status"], applied.json()["number"][:4]), (200, "applied", "ECO-"), applied.content)
        self.assertEqual(BomChangeOrder.objects.get(pk=change["id"]).decided_by.username, "process_engineer")
        self.assertEqual(engineer.delete(f"/api/manufacturing/bom-change-orders/{change['id']}/").status_code, 400)
        self.assertEqual(self.as_("AR Manager").get("/api/manufacturing/bom-change-orders/").status_code, 403)
