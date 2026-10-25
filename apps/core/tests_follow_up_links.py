"""
O317: a follow-up's link is shown on its assignee's Home as a link, so it
may only be a path on this site. The auditor's probe (audit2_core C5): a
browser reads `/\\host` and `/<tab>/host` as `//host`, which passed the old
check of a leading `//` alone.
"""
import datetime

from django.contrib.auth.models import Group, User

from .chatter import same_site_path
from .models import Party
from .tests_roles import RolesTestCase

OFF_THE_SITE = ("https://pay-vendor.example/login", "//pay-vendor.example/login", "javascript:alert(1)",
                "/\\pay-vendor.example/login", "/\t/pay-vendor.example/login", "/%5cpay-vendor.example/login",
                "/%2f/pay-vendor.example/login", "/%252f%252fpay-vendor.example/login", "/\n/pay-vendor.example",
                # Dot segments a browser resolves to //host (review_sechr F1).
                "/.//pay-vendor.example/login", "/x/..//pay-vendor.example", "/%2e//pay-vendor.example",
                "/a/%2e%2e//pay-vendor.example", "/../..//pay-vendor.example")


class AFollowUpLinkStaysOnTheSiteTests(RolesTestCase):
    def setUp(self):
        super().setUp()
        self.party = Party.objects.create(code="C-1", name="Acme")
        self.boss = User.objects.create_user("controller")
        self.boss.groups.add(Group.objects.get(name="Controller"))
        self.rep = self.as_("Sales Rep")

    def plan(self, link):
        return self.rep.post("/api/core/follow-ups/", {
            "model": "core.party", "id": self.party.pk, "summary": "Call back",
            "due_on": str(datetime.date.today()), "assigned_to": self.boss.pk, "link": link}, format="json")

    def test_a_rep_cannot_put_an_outside_address_on_the_controllers_home(self):
        answers = {link: (response.status_code, list(response.json())) for link in OFF_THE_SITE
                   if (response := self.plan(link))}
        self.assertEqual({link: answer for link, answer in answers.items() if answer != (400, ["link"])}, {})

    def test_a_place_in_the_application_is_kept(self):
        answer = self.plan("/app/sales/customers/1?tab=notes")
        self.assertEqual(answer.status_code, 201, answer.content)
        self.assertEqual(answer.json()["link"], "/app/sales/customers/1?tab=notes")

    def test_a_saved_view_asks_the_same(self):
        for screen in ("/\\pay-vendor.example", "/.//pay-vendor.example"):
            refused = self.rep.post("/api/core/saved-filters/", {"screen": screen, "name": "Mine", "query": {}},
                                    format="json")
            self.assertEqual((refused.status_code, list(refused.json())), (400, ["screen"]), screen)
        kept = self.rep.post("/api/core/saved-filters/", {"screen": "/app/sales/orders", "name": "Mine",
                                                          "query": {}}, format="json")
        self.assertEqual(kept.status_code, 201, kept.content)

    def test_the_rule_parses_rather_than_reads_the_prefix(self):
        self.assertEqual([link for link in OFF_THE_SITE if same_site_path(link)], [])
        self.assertTrue(same_site_path("/app/?q=a%20b"))
        self.assertTrue(same_site_path("/app/./sales/../sales/orders"))  # dots that stay on the site are a path
