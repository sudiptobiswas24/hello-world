"""review_sec3 probes: O124 on the manufacturing lines, as the people who edit them, through the API."""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.tests_probe_review_sec3 import endpoint_for
from apps.manufacturing import tests_inward, tests_orders as run_fixture
from apps.manufacturing.orders import MaterialIssueLine, ProductionByproduct
from apps.manufacturing.views import (
    CustomerMaterialReceiptLineViewSet,
    MaterialIssueLineViewSet,
    ProductionByproductViewSet,
)


def a_person(*roles):
    call_command("setup_roles", verbosity=0)
    user = User.objects.create_user("-".join(roles).replace(" ", "_").lower())
    user.groups.add(*Group.objects.filter(name__in=roles))
    api = APIClient()
    api.force_authenticate(user)
    return api


class O124Issues(run_fixture.RunTestCase):
    def setUp(self):
        super().setUp()
        self.api = a_person("Production Supervisor")
        self.order_ = self.order("10")
        self.order_.release(run_fixture.TODAY)
        self.posted = self.full_issue(self.order_)
        self.posted.post()
        self.draft = self.issue(self.order_, [(self.order_.components.first().item, "1")])
        self.url = endpoint_for(MaterialIssueLineViewSet)

    def test_a_posted_issues_line_is_not_moved_off_it(self):
        line = self.posted.lines.first()
        got = self.api.patch(f"{self.url}{line.pk}/", {"issue": self.draft.pk}, format="json")
        self.assertEqual(got.status_code, 400, got.content)
        self.assertEqual(MaterialIssueLine.objects.get(pk=line.pk).issue_id, self.posted.pk)

    def test_a_drafts_line_is_not_moved_onto_a_posted_issue(self):
        line = self.draft.lines.first()
        got = self.api.patch(f"{self.url}{line.pk}/", {"issue": self.posted.pk}, format="json")
        self.assertEqual(got.status_code, 400, got.content)

    def test_a_line_is_not_added_edited_or_deleted_on_a_posted_issue(self):
        line = self.posted.lines.first()
        add = self.api.post(self.url, {"issue": self.posted.pk, "item": line.item_id, "quantity": "1", "uom": self.kg.pk,
                                       "line_number": 99}, format="json")
        edit = self.api.patch(f"{self.url}{line.pk}/", {"quantity": "5"}, format="json")
        gone = self.api.delete(f"{self.url}{line.pk}/")
        self.assertEqual([r.status_code for r in (add, edit, gone)], [400, 400, 400],
                         [r.content[:200] for r in (add, edit, gone)])


class O124Byproducts(run_fixture.RunTestCase):
    def setUp(self):
        super().setUp()
        self.api = a_person("Production Supervisor")
        order = self.order("10")
        order.release(run_fixture.TODAY)
        self.full_issue(order).post()
        item = self.regrind
        self.posted = self.produce(order, "10", byproducts=[(item, "1")])
        self.posted.post()
        self.draft = self.produce(order, "0", byproducts=[(item, "1")])
        self.url = endpoint_for(ProductionByproductViewSet)
        self.item = item

    def test_a_posted_entrys_byproduct_is_not_moved_off_edited_or_deleted(self):
        line = self.posted.byproducts.first()
        move = self.api.patch(f"{self.url}{line.pk}/", {"entry": self.draft.pk}, format="json")
        edit = self.api.patch(f"{self.url}{line.pk}/", {"quantity": "7"}, format="json")
        gone = self.api.delete(f"{self.url}{line.pk}/")
        self.assertEqual([r.status_code for r in (move, edit, gone)], [400, 400, 400], [r.content[:200] for r in (move, edit, gone)])
        self.assertEqual(ProductionByproduct.objects.get(pk=line.pk).entry_id, self.posted.pk)

    def test_a_drafts_byproduct_is_not_moved_onto_a_posted_entry_nor_added_to_it(self):
        line = self.draft.byproducts.first()
        move = self.api.patch(f"{self.url}{line.pk}/", {"entry": self.posted.pk}, format="json")
        add = self.api.post(self.url, {"entry": self.posted.pk, "item": self.item.pk, "quantity": "1", "uom": self.kg.pk,
                                       "line_number": 9}, format="json")
        self.assertEqual([move.status_code, add.status_code], [400, 400], [move.content[:200], add.content[:200]])


class O124CustomerMaterial(tests_inward.InwardTestCase):
    def setUp(self):
        super().setUp()
        self.api = a_person("Production Supervisor", "Stores Manager")
        self.url = endpoint_for(CustomerMaterialReceiptLineViewSet)

    def test_a_posted_receipts_line_is_not_moved_off_edited_deleted_or_added_to(self):
        posted = self.receive("100", "A1")
        draft = self.receive("50", "A2", post=False)
        line, own = posted.lines.get(), draft.lines.get()
        move = self.api.patch(f"{self.url}{line.pk}/", {"receipt": draft.pk}, format="json")
        onto = self.api.patch(f"{self.url}{own.pk}/", {"receipt": posted.pk}, format="json")
        edit = self.api.patch(f"{self.url}{line.pk}/", {"quantity": "7"}, format="json")
        add = self.api.post(self.url, {"receipt": posted.pk, "item": self.virgin.pk, "quantity": "1"}, format="json")
        gone = self.api.delete(f"{self.url}{line.pk}/")
        got = [r.status_code for r in (move, onto, edit, add, gone)]
        self.assertEqual(got, [400] * 5, [r.content[:200] for r in (move, onto, edit, add, gone)])
        self.assertEqual((posted.lines.count(), draft.lines.count()), (1, 1))


class O124Return(tests_inward.InwardTestCase):
    def test_a_posted_returns_line_is_not_moved_off_edited_deleted_or_added_to_and_a_drafts_not_moved_onto_it(self):
        from apps.manufacturing.inward import CustomerMaterialReturn, CustomerMaterialReturnLine
        from apps.manufacturing.views import CustomerMaterialReturnLineViewSet

        api = a_person("Production Supervisor", "Stores Manager")
        url = endpoint_for(CustomerMaterialReturnLineViewSet)
        receipt = self.receive("500")
        sent = self.send_back(receipt, "300")
        draft = CustomerMaterialReturn.objects.create(customer=self.sunrise, warehouse=self.held,
                                                      returned_on=tests_inward.TODAY)
        CustomerMaterialReturnLine.objects.create(material_return=draft, receipt_line=receipt.lines.get(),
                                                  item=self.virgin, lot=self.their_lot, quantity=Decimal("10"))
        line, own = sent.lines.get(), draft.lines.get()
        calls = [api.patch(f"{url}{line.pk}/", {"material_return": draft.pk}, format="json"),
                 api.patch(f"{url}{own.pk}/", {"material_return": sent.pk}, format="json"),
                 api.patch(f"{url}{line.pk}/", {"quantity": "7"}, format="json"),
                 api.post(url, {"material_return": sent.pk, "receipt_line": receipt.lines.get().pk, "item": self.virgin.pk,
                                "lot": self.their_lot.pk, "quantity": "1"}, format="json"),
                 api.delete(f"{url}{line.pk}/")]
        self.assertEqual([r.status_code for r in calls], [400] * 5, [r.content[:160] for r in calls])
        self.assertEqual((sent.lines.count(), draft.lines.count()), (1, 1))


class O124Scrap(__import__("apps.manufacturing.tests_scrap", fromlist=["StepsTestCase"]).StepsTestCase):
    def test_booked_scrap_is_not_edited_deleted_added_nor_a_drafts_moved_onto_a_posted_entry(self):
        from apps.manufacturing.scrap import ProductionScrap

        api = a_person("Production Supervisor")
        posted = self.book("10", "5", [(self.seam, "5", None)])
        draft = self.produce(self.run_, "10", "0")
        mine = ProductionScrap.objects.create(entry=draft, reason=self.seam, quantity=Decimal("1"))
        line = posted.scrap_lines.get()
        url = "/api/manufacturing/production-scrap/"
        calls = [api.patch(f"{url}{line.pk}/", {"quantity": "1"}, format="json"),
                 api.delete(f"{url}{line.pk}/"),
                 api.post(url, {"entry": posted.pk, "reason": self.seam.pk, "quantity": "1"}, format="json"),
                 api.patch(f"{url}{mine.pk}/", {"entry": posted.pk}, format="json")]
        self.assertEqual([r.status_code for r in calls], [400] * 4, [r.content[:160] for r in calls])
        self.assertEqual((posted.scrap_lines.count(), draft.scrap_lines.count()), (1, 1))
