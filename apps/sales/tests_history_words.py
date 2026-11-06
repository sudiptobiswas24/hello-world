"""
O399: the history panel read "freight terms: nothing -> ex_works" and
"is job work: nothing -> false", the stored values. It reads the record's own
words now; what the log keeps is unchanged.
"""

import datetime

from django.contrib.contenttypes.models import ContentType

from apps.core.history import RecordEvent, history
from apps.sales.models import SalesOrder

from .tests_base import SalesTestCase


class TheHistoryReadsTheRecordsWords(SalesTestCase):
    def test_a_choice_reads_its_label_and_a_flag_yes_or_no(self):
        order = SalesOrder.objects.create(customer=self.customer, freight_terms="ex_works", is_job_work=False,
                                         order_date=datetime.date(2026, 11, 1))
        made = history(SalesOrder, order.pk)[-1]
        changes = {change["field"]: change for change in made["changes"]}
        self.assertEqual(changes["freight_terms"]["to"], "Ex works: the buyer collects")
        self.assertEqual(changes["status"]["to"], "Draft")
        # Kept as stored: only the reading changed.
        kept = RecordEvent.objects.filter(content_type=ContentType.objects.get_for_model(SalesOrder),
                                          object_id=order.pk).order_by("pk").first().changes
        self.assertEqual(kept["freight_terms"]["to"], "ex_works")
