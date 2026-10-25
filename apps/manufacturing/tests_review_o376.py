"""Review probe O376: the Controller and the Bookkeeper read stock movements and runs, and can do nothing else."""
import re

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.urls import get_resolver
from rest_framework.test import APIClient

from apps.inventory.models import StockMovement
from apps.manufacturing.orders import WorkOrder
from .tests_orders import RunTestCase

PREFIXES = ("api/manufacturing/work-orders", "api/inventory/stock-movements", "api/inventory/stock-reports",
            "api/manufacturing/dispatch", "api/manufacturing/run-flow")


def routes(patterns=None, prefix=""):
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        text = prefix + str(p.pattern)
        if hasattr(p, "url_patterns"):
            yield from routes(p.url_patterns, text)
        else:
            yield text, p


class ReviewO376(RunTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.run_ = self.order()
        self.run_.release()

    def as_(self, role):
        user = User.objects.filter(username=role.replace(" ", "_")).first() or User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_no_route_on_runs_or_the_stock_ledger_takes_a_write_from_the_two_roles(self):
        movement = StockMovement.objects.first()
        found, leaks, seen = 0, [], []
        gating = {"manufacturing.view_workorder", "inventory.view_stockmovement"}
        for text, p in routes():
            if "format" in text:
                continue
            cls = getattr(p.callback, "cls", None) or getattr(p.callback, "view_class", None)
            model = getattr(getattr(cls, "queryset", None), "model", None)
            gated = getattr(cls, "required_permission", None) in gating or model in (WorkOrder, StockMovement)
            if not gated:
                continue
            url = text.replace("(?P<pk>[^/.]+)", str(self.run_.pk if "work-order" in text or "run-flow" in text else movement.pk))
            url = "/" + url.replace("^", "").replace("$", "")
            for role in ("Controller", "Bookkeeper"):
                client = self.as_(role)
                before = (WorkOrder.objects.count(), StockMovement.objects.count())
                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    r = getattr(client, method.lower())(url, {}, format="json")
                    seen.append((role, method, url, r.status_code))
                    if r.status_code not in (403, 405):
                        leaks.append((role, method, url, r.status_code))
                self.assertEqual(before, (WorkOrder.objects.count(), StockMovement.objects.count()), (role, url))
            found += 1
        print("\nO376 routes swept:", found, "calls:", len(seen))
        self.assertGreater(found, 20)
        self.maxDiff = None; self.assertEqual(leaks, [])

    def test_the_roles_grant_is_read_only(self):
        from apps.core.management.commands.setup_roles import ROLES
        for role in ("Controller", "Bookkeeper"):
            held = [p for p in ROLES[role] if re.match(r"(inventory|manufacturing)\.(add|change|delete)_(stockmovement|workorder\w*|materialissue|productionentry)$", p)]
            self.assertEqual(held, [], role)
