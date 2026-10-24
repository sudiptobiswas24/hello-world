"""The station and report pages: who may open them, and what the report shows."""

from decimal import Decimal

from django.contrib.auth.models import Permission, User

from .tests_station_report import ReportTestCase


class PagesTestCase(ReportTestCase):
    def user(self, name, *codenames):
        user = User.objects.create_user(name, password="pw")
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        return user


class StationPageTests(PagesTestCase):
    def test_nobody_signed_in_is_sent_to_sign_in(self):
        response = self.client.get(f"/station/{self.station.code}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_a_device_without_the_permission_is_refused(self):
        self.client.force_login(self.user("clerk"))
        self.assertEqual(self.client.get(f"/station/{self.station.code}/").status_code, 403)

    def test_the_station_device_gets_the_screen_and_a_csrf_cookie(self):
        self.client.force_login(self.user("station-lx1", "weigh_at_station"))
        response = self.client.get(f"/station/{self.station.code}/")
        self.assertEqual(response.status_code, 200)
        # escapejs writes the hyphen as \u002D, which JavaScript reads as one.
        self.assertContains(response, 'const API = "/api/manufacturing/stations/LX\\u002D1/"')
        self.assertIn("csrftoken", response.cookies)

    def test_a_station_out_of_use_is_not_found(self):
        type(self.station).objects.filter(pk=self.station.pk).update(is_active=False)
        self.client.force_login(self.user("station-lx1", "weigh_at_station"))
        self.assertEqual(self.client.get(f"/station/{self.station.code}/").status_code, 404)


class ReportPageTests(PagesTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user("manager", "view_loomstation"))
        self.url = f"/station/{self.station.code}/report/?date=2026-06-01"

    def test_it_needs_the_permission(self):
        self.client.force_login(self.user("someone"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_figures_worked_by_hand(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for text in ("37.4 kg", "8.04% of 465.0 kg consumed", "L-17 over the limit",
                     "+6.30%", "FR-260601-N-L20-01", "Scale not connected", "EMP-0087",
                     "Measure two L-17 rolls by length"):
            with self.subTest(text=text):
                self.assertContains(response, text)

    def test_the_whole_run_declared_against_weight(self):
        # 4,131 m declared against 4,000 m from weight across both looms.
        response = self.client.get(self.url)
        self.assertEqual(response.context["overall_percent"], Decimal("3.28"))
        self.assertContains(response, "131 m declared beyond what weights support")

    def test_a_bad_date_is_not_found(self):
        self.assertEqual(self.client.get(self.url.replace("2026-06-01", "June")).status_code, 404)
