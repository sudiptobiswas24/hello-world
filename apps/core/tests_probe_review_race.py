"""
review_race (gate g21): O17 on the integrated tip. The viewsets that now take
RowHeldViewSetMixin alone, on every method, mapped or not.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.audit import AuditableViewSetMixin, RowHeldViewSetMixin
from apps.core.tests_second_factor import staff_session


class RowHeldViewSetProbe(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("boss", "b@x.in", "pw")
        self.client = APIClient(raise_request_exception=False)
        staff_session(self.client, self.user)
        self.seen = {}

    def go(self, label, method, url, data=None):
        response = getattr(self.client, method)(url, data or {}, format="json")
        self.seen[(label, method)] = response.status_code
        return response

    def test_country_party_tag_and_saved_filter_on_every_method(self):
        made = self.go("country", "post", "/api/core/countries/", {"code": "ZZ", "name": "Zedland"})
        self.assertEqual(made.status_code, 201, made.content)
        url = f"/api/core/countries/{made.json()['id']}/"
        self.assertEqual(self.go("country", "patch", url, {"name": "Zed"}).status_code, 200)
        self.assertEqual(self.go("country", "put", url, {"code": "ZZ", "name": "Zed 2"}).status_code, 200)
        self.assertEqual(self.go("country", "post", url, {}).status_code, 405)
        self.assertEqual(self.go("country", "delete", url).status_code, 204)
        self.assertEqual(self.go("country", "delete", url).status_code, 404)

        made = self.go("tag", "post", "/api/core/party-tags/", {"name": "VIP"})
        self.assertEqual(made.status_code, 201, made.content)
        url = f"/api/core/party-tags/{made.json()['id']}/"
        self.assertEqual(self.go("tag", "patch", url, {"description": "x"}).status_code, 200)
        self.assertEqual(self.go("tag", "post", url, {}).status_code, 405)
        self.assertEqual(self.go("tag", "delete", url).status_code, 204)

        made = self.go("filter", "post", "/api/core/saved-filters/",
                       {"screen": "/sales/invoices", "name": "Mine", "query": "a=1"})
        self.assertIn(made.status_code, (201, 400), made.content)
        if made.status_code == 201:
            url = f"/api/core/saved-filters/{made.json()['id']}/"
            self.assertEqual(self.go("filter", "patch", url, {"name": "Mine 2"}).status_code, 200)
            self.assertEqual(self.go("filter", "delete", url).status_code, 204)

    def test_the_gst_viewsets_answer_405_to_what_their_route_never_mapped(self):
        from apps.gst.gstr2b import Gstr2bStatement

        row = Gstr2bStatement.objects.create(period="2026-09")
        for label, base, pk in (("einvoice", "einvoices", 1), ("eway", "eway-bills", 1), ("gstr2b", "gstr2b", row.pk)):
            url = f"/api/gst/{base}/{pk}/"
            for method in ("put", "patch", "post"):
                self.go(label, method, url, {})
            self.seen[(label, "allow")] = self.client.options(url).headers.get("Allow", "")
        for method in ("put", "patch", "post"):
            self.assertEqual(self.seen[("gstr2b", method)], 405, method)
        self.assertEqual(self.go("gstr2b", "delete", f"/api/gst/gstr2b/{row.pk}/").status_code, 204)

    def test_the_mixin_split_keeps_what_the_two_halves_were_for(self):
        # Neither half may hide the other's methods.
        self.assertTrue(issubclass(AuditableViewSetMixin, RowHeldViewSetMixin))
        for name in ("dispatch", "reads_only_some", "finalize_response", "perform_create"):
            self.assertTrue(hasattr(AuditableViewSetMixin, name), name)
            self.assertFalse(hasattr(RowHeldViewSetMixin, name), name)

    def tearDown(self):
        print("O17-STATUSES", sorted(self.seen.items(), key=str))
