"""
Working-day arithmetic, which every date a plan promises rests on.

June 2026 is used throughout: the 1st is a Monday, so the 6th and 7th
are the first weekend and the 8th is the next Monday.
"""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from .calendars import PublicHoliday, WorkingCalendar

MONDAY = datetime.date(2026, 6, 1)
FRIDAY = datetime.date(2026, 6, 5)
SATURDAY = datetime.date(2026, 6, 6)
SUNDAY = datetime.date(2026, 6, 7)
NEXT_MONDAY = datetime.date(2026, 6, 8)


class WorkingCalendarTests(TestCase):
    def weekdays(self, region=""):
        return WorkingCalendar("12345", region)

    def continuous(self):
        return WorkingCalendar("1234567")

    def test_it_knows_which_days_it_works(self):
        calendar = self.weekdays()
        self.assertTrue(calendar.is_working(FRIDAY))
        self.assertFalse(calendar.is_working(SATURDAY))
        self.assertEqual(calendar.days_a_week(), 5)

    def test_a_holiday_is_not_a_working_day(self):
        PublicHoliday.objects.create(name="Bakrid", date=datetime.date(2026, 6, 3))
        self.assertFalse(self.weekdays().is_working(datetime.date(2026, 6, 3)))

    def test_a_holiday_somewhere_else_is_a_working_day_here(self):
        PublicHoliday.objects.create(
            name="Pongal", date=datetime.date(2026, 6, 3), region="TN"
        )
        self.assertTrue(self.weekdays(region="TS").is_working(datetime.date(2026, 6, 3)))
        self.assertFalse(self.weekdays(region="TN").is_working(datetime.date(2026, 6, 3)))

    def test_counting_a_week(self):
        self.assertEqual(self.weekdays().count(MONDAY, SUNDAY), 5)
        self.assertEqual(self.continuous().count(MONDAY, SUNDAY), 7)

    def test_counting_backwards_is_refused(self):
        with self.assertRaises(ValidationError):
            self.weekdays().count(SUNDAY, MONDAY)

    def test_zero_days_before_a_working_day_is_that_day(self):
        self.assertEqual(self.weekdays().offset_back(FRIDAY, 0), FRIDAY)

    def test_zero_days_before_a_day_off_is_the_working_day_before_it(self):
        """
        A run wanted on a Sunday has to be finished by Friday. Saying
        Sunday would have the plant promising a day it does not work.
        """
        self.assertEqual(self.weekdays().offset_back(SUNDAY, 0), FRIDAY)

    def test_one_working_day_before_monday_is_the_friday(self):
        self.assertEqual(self.weekdays().offset_back(NEXT_MONDAY, 1), FRIDAY)
        self.assertEqual(self.continuous().offset_back(NEXT_MONDAY, 1), SUNDAY)

    def test_five_working_days_spans_the_weekend(self):
        # Friday the 5th back five working days is the Friday before.
        self.assertEqual(
            self.weekdays().offset_back(FRIDAY, 5), datetime.date(2026, 5, 29)
        )

    def test_a_holiday_pushes_the_start_back_another_day(self):
        PublicHoliday.objects.create(name="Bakrid", date=datetime.date(2026, 6, 3))
        # Friday back two working days: Thursday, then Wednesday is the
        # holiday, so Tuesday.
        self.assertEqual(
            self.weekdays().offset_back(FRIDAY, 2), datetime.date(2026, 6, 2)
        )

    def test_forwards_is_the_mirror_of_backwards(self):
        calendar = self.weekdays()
        self.assertEqual(calendar.offset_forward(FRIDAY, 1), NEXT_MONDAY)
        self.assertEqual(calendar.offset_back(NEXT_MONDAY, 1), FRIDAY)

    def test_a_negative_offset_is_refused(self):
        with self.assertRaises(ValidationError):
            self.weekdays().offset_back(FRIDAY, -1)

    def test_a_pattern_that_never_works_is_refused_rather_than_looping(self):
        """
        Without the guard this does not terminate. A working pattern
        with every day taken out by holidays has no answer, and the
        honest answer is to say so rather than to search for ever.
        """
        for day in range(1, 90):
            PublicHoliday.objects.create(
                name=f"Shut {day}", date=MONDAY + datetime.timedelta(days=day),
            )
            PublicHoliday.objects.create(
                name=f"Shut back {day}", date=MONDAY - datetime.timedelta(days=day),
            )
        PublicHoliday.objects.create(name="Shut", date=MONDAY)
        with self.assertRaises(ValidationError):
            self.weekdays().offset_back(MONDAY, 1)

    def test_holidays_are_read_once_not_once_a_question(self):
        """
        Built as an object for this: a plan offsets a lead time for
        every item it touches, and a function would query per item.
        """
        PublicHoliday.objects.create(name="Bakrid", date=datetime.date(2026, 6, 3))
        calendar = self.weekdays()
        with self.assertNumQueries(0):
            calendar.count(MONDAY, SUNDAY)
            calendar.offset_back(FRIDAY, 3)
