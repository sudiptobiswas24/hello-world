"""review_sec3 probes: O129 (held_for named to a rep) and O124 (inventory lines), through the API as the role."""

import datetime

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.tests_probe_review_sec3 import endpoint_for, routes
from apps.inventory import tests_api, tests_adjustments
from apps.inventory.models import StockCount, StockCountLine
from apps.inventory.views import StockCountLineViewSet


class O129(TestCase):
    setUp = tests_api.AStoreNamesItsCustomerOnlyToTheirRepTests.setUp
    W = "/api/inventory/warehouses/"

    def codes(self, **params):
        got = self.api.get(self.W, params)
        return got.status_code, ([row["code"] for row in got.json()] if got.status_code == 200 else got.content[:120])

    def test_no_filter_narrows_to_a_hidden_customers_store(self):
        leaks = []
        for params in ({"held_for__in": str(self.theirs.pk)}, {"held_for__name": "Customer C2"},
                       {"held_for__name__icontains": "C2"}, {"held_for__code": "C2"},
                       {"held_for__sales_rep": "1"}, {"held_for__gt": str(self.mine.pk)},
                       {"search": "Customer C2"}, {"held_for__exact": str(self.theirs.pk)},
                       {"held_for__isnull": "false"}):
            status, codes = self.codes(**params)
            if status == 200 and "CM2" in codes and "CM1" not in codes:
                leaks.append((params, codes))
        self.assertEqual(leaks, [])

    def test_ordering_by_the_hidden_customers_name_does_not_sort_the_stores(self):
        up, down = self.codes(ordering="held_for__name")[1], self.codes(ordering="-held_for__name")[1]
        up_id, down_id = self.codes(ordering="held_for")[1], self.codes(ordering="-held_for")[1]
        self.assertEqual((up, up_id), (down, down_id), "the store list sorts by the hidden customer")

    def test_history_of_a_store_does_not_name_a_customer_the_rep_cannot_read(self):
        manager = User.objects.create_user("stores")
        manager.groups.add(Group.objects.get(name="Stores Manager"))
        stores = APIClient()
        stores.force_authenticate(manager)
        made = stores.post(self.W, {"code": "CM3", "name": "Third", "held_for": self.theirs.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        stores.patch(f"{self.W}{made.json()['id']}/", {"name": "Third, renamed"}, format="json")
        got = self.api.get("/api/core/history/", {"model": "inventory.warehouse", "id": made.json()["id"]})
        self.assertNotIn("Customer C2", got.content.decode(), got.content[:400])
        self.assertNotIn("C2", got.content.decode(), got.content[:400])

    def test_the_grouped_list_does_not_name_the_hidden_customer(self):
        summaries = [path for path, cls in routes() if cls.__module__ == "apps.web.api"]
        self.assertTrue(summaries, "no summary route found")
        leaks = []
        for path in summaries:
            got = self.api.get(path, {"endpoint": self.W, "by": "held_for"})
            if "Customer C2" in got.content.decode() or "Customer C2" in str(got.content):
                leaks.append((path, got.content[:300]))
        self.assertEqual(leaks, [])

    def test_no_get_route_names_the_hidden_customer_to_the_rep(self):
        from apps.core.tests_probe_review_sec3 import routes
        leaks = []
        for path, cls in routes():
            if "get" not in cls.http_method_names:
                continue
            got = self.api.get(path)
            text = got.content.decode("utf8", "replace")
            if got.status_code == 200 and ("Customer C2" in text or f'"held_for":{self.theirs.pk}' in text.replace(" ", "")):
                leaks.append(path)
        self.assertEqual(leaks, [])

    def test_the_rep_cannot_reach_a_refusal_naming_the_customer(self):
        """sales/models.py:4377 names held_for to whoever posts a delivery; is a rep that person?"""
        mine = sorted(p for p in self.anita.__class__.objects.get(pk=self.anita.pk).get_all_permissions() if "delivery" in p)
        self.assertEqual([p for p in mine if "post" in p or "add" in p or "change" in p], [],
                         "a Sales Rep can write deliveries: the 'holds X's material' refusal names a customer they may not read")


class O124Inventory(tests_adjustments.AdjustmentTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        head = User.objects.create_user("stores_head")
        head.groups.add(Group.objects.get(name="Stores Manager"))
        self.api = APIClient()
        self.api.force_authenticate(head)
        self.stock("100", "5")

    L = "/api/inventory/stock-adjustment-lines/"

    def test_a_draft_adjustments_line_is_not_moved_onto_a_posted_one(self):
        posted = self.adjust("-4", reason=self.shrink)
        draft = self.adjust("-1", reason=self.shrink, post=False)
        got = self.api.patch(f"{self.L}{draft.lines.get().pk}/", {"adjustment": posted.pk}, format="json")
        self.assertEqual(got.status_code, 400, got.content)
        self.assertEqual((posted.lines.count(), draft.lines.count()), (1, 1))

    def test_a_line_is_not_added_to_a_posted_adjustment(self):
        posted = self.adjust("-4", reason=self.shrink)
        got = self.api.post(self.L, {"adjustment": posted.pk, "item": self.item.pk, "uom": self.item.uom_id,
                                     "quantity": "-1"}, format="json")
        self.assertEqual(got.status_code, 400, got.content)
        self.assertEqual(posted.lines.count(), 1)

    def test_a_posted_adjustments_line_is_not_edited_or_deleted(self):
        posted = self.adjust("-4", reason=self.shrink)
        line = posted.lines.get()
        edit = self.api.patch(f"{self.L}{line.pk}/", {"quantity": "-9"}, format="json")
        gone = self.api.delete(f"{self.L}{line.pk}/")
        self.assertEqual((edit.status_code, gone.status_code), (400, 400), (edit.content, gone.content))
        self.assertEqual(posted.lines.count(), 1)

    def count_pair(self):
        posted = StockCount.objects.create(count_date=datetime.date(2026, 3, 1), warehouse=self.warehouse, reason=self.recount)
        posted.add(self.item, "100")
        posted.post()
        draft = StockCount.objects.create(count_date=datetime.date(2026, 3, 2), warehouse=self.warehouse, reason=self.recount)
        draft.add(self.item, "99")
        return posted, draft

    def test_a_count_line_is_not_moved_off_or_onto_a_posted_count(self):
        posted, draft = self.count_pair()
        url = endpoint_for(StockCountLineViewSet)
        off = self.api.patch(f"{url}{posted.lines.get().pk}/", {"count": draft.pk}, format="json")
        onto = self.api.patch(f"{url}{draft.lines.get().pk}/", {"count": posted.pk}, format="json")
        self.assertEqual((off.status_code, onto.status_code), (400, 400), (off.content, onto.content))
        self.assertEqual((posted.lines.count(), draft.lines.count()), (1, 1))

    def test_a_count_line_is_not_added_edited_or_deleted_on_a_posted_count(self):
        posted, draft = self.count_pair()
        url = endpoint_for(StockCountLineViewSet)
        add = self.api.post(url, {"count": posted.pk, "item": self.item.pk, "uom": self.item.uom_id,
                                  "counted_quantity": "1"}, format="json")
        edit = self.api.patch(f"{url}{posted.lines.get().pk}/", {"counted_quantity": "7"}, format="json")
        gone = self.api.delete(f"{url}{posted.lines.get().pk}/")
        self.assertEqual([r.status_code for r in (add, edit, gone)], [400, 400, 400], [r.content for r in (add, edit, gone)])
        self.assertEqual(posted.lines.count(), 1)
