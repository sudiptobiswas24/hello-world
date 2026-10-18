"""
g22 review of fix_stores: probes for O203 (working days), O241 (what the screen offers by role).
"""
import datetime

from apps.quality import tests_decisions as base
from apps.quality.models import Disposition, Inspection

MONDAY = base.MONDAY


def only_mine(cls):
    for name in dir(cls):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


@only_mine
class StoresReviewWorkingDayProbes(base.AwaitingTestCase):
    def due_after_posting_on(self, day):
        from django.utils import timezone

        waiting = self.awaiting()
        Inspection.objects.filter(pk=waiting.pk).update(
            posted_at=timezone.make_aware(datetime.datetime.combine(day, datetime.time(10))))
        rows = self.clients["asha"].get("/api/quality/inspections/awaiting/").json()
        return [row["due_on"] for row in rows if row["number"] == waiting.number][0]

    def test_w1_the_plants_own_week_counts_when_the_plant_works_a_saturday(self):
        from apps.planning.models import PlanningSettings

        settings = PlanningSettings.get()
        settings.working_days = "123456"
        settings.save()
        friday = MONDAY + datetime.timedelta(days=4)
        self.assertEqual(self.due_after_posting_on(friday), "2026-06-06",
                         "Saturday 6 June is a working day of the plant's own calendar; due on Monday 8 June")

    def test_w2_the_holiday_list_of_the_plants_own_region_counts(self):
        from apps.hr.calendars import PublicHoliday
        from apps.planning.models import PlanningSettings

        settings = PlanningSettings.get()
        settings.holiday_region = "WB-KOL"
        settings.save()
        PublicHoliday.objects.create(name="Kolkata only", date=MONDAY + datetime.timedelta(days=1), region="WB-KOL")
        self.assertEqual(self.due_after_posting_on(MONDAY), "2026-06-03",
                         "Tuesday 2 June shuts this plant's region; due Wednesday")

    def test_w3_a_sunday_and_a_company_wide_holiday_are_skipped(self):
        from apps.hr.calendars import PublicHoliday

        friday = MONDAY + datetime.timedelta(days=4)
        PublicHoliday.objects.create(name="Monday off", date=MONDAY + datetime.timedelta(days=7))
        self.assertEqual(self.due_after_posting_on(friday), "2026-06-09")


@only_mine
class StoresReviewScreenOffersProbes(base.AwaitingTestCase):
    def test_h1_an_inspector_is_not_told_a_release_is_theirs_and_the_server_agrees(self):
        held = self.awaiting()                                  # a failure waiting for the Quality Manager
        again = self.failed()
        Inspection.objects.filter(pk=again.pk).update(lot=held.lot)
        again.refresh_from_db()
        for who in ("meera", "asha"):
            row = self.clients[who].get(f"/api/quality/inspections/{again.pk}/").json()
            self.assertTrue(row["releasing_is_theirs"], who)
        # a re-post as accept by the Inspector is refused by the server, the same fact
        Inspection.objects.filter(pk=again.pk).update(disposition=Disposition.ACCEPT)
        refused = self.post(again, "meera")
        self.assertGreaterEqual(refused.status_code, 400, refused.content)
        self.assertFalse(Inspection.objects.get(pk=again.pk).posted)

    def test_h2_the_flag_is_false_where_no_verdict_holds_the_batch(self):
        draft = self.failed()
        row = self.clients["meera"].get(f"/api/quality/inspections/{draft.pk}/").json()
        self.assertFalse(row["releasing_is_theirs"])

    def test_h3_an_inspector_may_not_post_a_concession_by_the_api(self):
        draft = self.failed(disposition=Disposition.CONCESSION)
        refused = self.post(draft, "meera")
        self.assertGreaterEqual(refused.status_code, 400, refused.content)
