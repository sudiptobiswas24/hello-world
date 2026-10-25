"""review_sechr probes: each test states a fact that SHOULD hold; a failure is a finding."""
import datetime

from django.contrib.auth.models import Group, User
from django.test import TestCase

from .models import Party
from .tests_roles import RolesTestCase


class F1_SiteLinksProbe(RolesTestCase):
    def test_dot_segments_that_collapse_to_a_protocol_relative_path_are_refused(self):
        party = Party.objects.create(code="C-1", name="Acme")
        boss = User.objects.create_user("controller")
        boss.groups.add(Group.objects.get(name="Controller"))
        rep = self.as_("Sales Rep")
        got = {}
        for link in ("/.//pay-vendor.example/login", "/x/..//pay-vendor.example", "/%2e//pay-vendor.example",
                     "/./\t/x", "/a/%2e%2e//pay-vendor.example"):
            r = rep.post("/api/core/follow-ups/", {"model": "core.party", "id": party.pk, "summary": "Call",
                         "due_on": str(datetime.date.today()), "assigned_to": boss.pk, "link": link}, format="json")
            got[link] = r.status_code
        self.assertEqual({k: v for k, v in got.items() if v != 400}, {})


class F2_MarginProbe(RolesTestCase):
    def test_a_NaN_margin_is_a_400_not_a_crash(self):
        client = self.as_("Controller")
        out = {}
        for bad in ("NaN", "sNaN", "Infinity", "-1", "x"):
            try:
                out[bad] = client.get("/api/web/bank-stock-statement/", {"stock_margin": bad}).status_code
            except Exception as error:  # an unhandled exception in the test client is a 500
                out[bad] = type(error).__name__
        self.assertEqual({k: v for k, v in out.items() if v != 400}, {})
