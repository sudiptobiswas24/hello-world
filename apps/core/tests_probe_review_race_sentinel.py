"""review_race: the sentinel sorts what it cannot see in order."""
from django.db import transaction
from django.test import TestCase

from apps.core import lock_order
from apps.core.models import Party


class SentinelValuesListProbe(TestCase):
    def test_a_values_list_lock_in_descending_order_is_reported_like_one_with_the_pk(self):
        for n in range(3):
            Party.objects.create(code=f"P{n}", name=f"P{n}")
        with transaction.atomic():
            with self.assertRaises(lock_order.LockOrderViolation):
                list(Party.objects.select_for_update().order_by("-pk"))
        with transaction.atomic():
            # The same locks in the same descending order, read as codes: reported as well now (F8 fixed).
            with self.assertRaises(lock_order.LockOrderViolation):
                list(Party.objects.select_for_update().order_by("-pk").values_list("code", flat=True))
