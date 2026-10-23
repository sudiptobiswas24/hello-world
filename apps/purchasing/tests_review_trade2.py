"""review_trade2 probes (disposable)."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.tests_roles import RolesTestCase

from .models import Bill, BillLine
from .tests_lifecycle import PurchasingLifecycleTestCase


class Probe(RolesTestCase, PurchasingLifecycleTestCase):
    def setUp(self):
        PurchasingLifecycleTestCase.setUp(self)
        RolesTestCase.setUp(self)

    def client_as(self, role):
        user = get_user_model().objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        c = APIClient(); c.force_authenticate(user)
        return c

    def one_bill(self):
        order = self.make_order("10", "5")
        receipt = self.receive(order, 10)
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        return receipt, bill

    def test_p1_api_roles(self):
        receipt, bill = self.one_bill()
        line = bill.lines.get()
        wh = self.client_as("Warehouse Staff")
        r = wh.post(f"/api/purchasing/goods-receipts/{receipt.pk}/return_receipt/",
                    {"quantities": {str(receipt.lines.get().pk): "4"}}, format="json")
        print("P1 return 4 as warehouse", r.status_code, r.content[:150])
        print("P1 drafts", [(n.total(), n.posted) for n in Bill.objects.filter(debits=bill)])
        ap = self.client_as("AP Manager")
        for q in ("7", "6"):
            r = ap.post(f"/api/purchasing/bills/{bill.pk}/debit_note/", {"quantities": {str(line.pk): q}}, format="json")
            print("P1 AP debit", q, r.status_code, r.content[:160])
        r = ap.post(f"/api/purchasing/bills/{bill.pk}/debit_note/", {}, format="json")
        print("P1 whole", r.status_code, r.content[:120])
        draft = Bill.objects.get(debits=bill, posted=False)
        r = ap.post(f"/api/purchasing/bills/{draft.pk}/post_bill/", {}, format="json")
        print("P1 post draft", r.status_code, r.content[:120])
        print("P1 due", bill.amount_due())

    def test_p2_goodwill_then_return(self):
        receipt, bill = self.one_bill()
        note = Bill.objects.create(vendor=bill.vendor, bill_date=bill.bill_date, payable_account=self.payable, debits=bill)
        BillLine.objects.create(bill=note, description="goodwill", quantity=Decimal("1"), unit_price=Decimal("50"), expense_account=self.expense)
        note.post()
        print("P2 after goodwill due", bill.amount_due())
        try:
            receipt.create_return()
        except ValidationError as e:
            print("P2 return refused", e.messages)
        print("P2 notes", [(n.total(), n.posted) for n in Bill.objects.filter(debits=bill)], bill.amount_due())

    def test_p3_future_bill_by_hand(self):
        order = self.make_order("10", "5")
        self.receive(order, 10)
        future = timezone.localdate() + datetime.timedelta(days=20)
        bill = order.create_bill(self.payable, bill_date=future)
        try:
            bill.post()
        except ValidationError as e:
            print("P3 post future bill refused", e.messages); return
        try:
            n = bill.create_debit_note()
            print("P3 note dated", n.bill_date, "bill", bill.bill_date, n.posted)
        except ValidationError as e:
            print("P3 debit refused", e.messages)
        ap = self.client_as("AP Manager")
        r = ap.post(f"/api/purchasing/bills/{bill.pk}/debit_note/", {}, format="json")
        print("P3 api", r.status_code, r.content[:200])

    def test_p4_capitalise_with_draft(self):
        from apps.assets.models import AssetCategory
        receipt, bill = self.one_bill()
        line = bill.lines.get()
        draft = bill.create_debit_note(quantities={line: Decimal("4")}, post=False)
        print("P4 debitable", line.quantity_debitable(), "debited", line.quantity_debited())
        cat = AssetCategory.objects.first()
        print("P4 category", cat)
