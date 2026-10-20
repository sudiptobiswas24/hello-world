"""audit2_core probe: a follow-up's link is shown on the assignee's Home as a link."""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Party


class FollowUpLinkTests(TestCase):
    def test_a_follow_up_for_someone_else_cannot_carry_a_link_off_the_site(self):
        """
        Home.tsx renders `<Link to={row.link}>`; router 8 treats an absolute address as an external anchor.
        A rep who may plan follow-ups puts 'https://pay-vendor.example/login' on the Controller's Home.
        """
        rep = User.objects.create_superuser("rep", "r@example.com", "x")
        boss = User.objects.create_superuser("controller", "c@example.com", "x")
        party = Party.objects.create(code="C-1", name="Acme")
        client = APIClient()
        client.force_authenticate(rep)
        for link in ("https://pay-vendor.example/login", "//pay-vendor.example/login", "javascript:alert(1)",
                     "/\\pay-vendor.example/login", "/\t/pay-vendor.example/login"):
            response = client.post("/api/core/follow-ups/", {
                "model": "core.party", "id": party.pk, "summary": "Call back", "due_on": str(datetime.date.today()),
                "assigned_to": boss.pk, "link": link}, format="json")
            self.assertEqual(response.status_code, 400, (link, response.status_code))  # a browser reads / backslash and / tab / as //
