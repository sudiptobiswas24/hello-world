import datetime
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from apps.gst.tests_itc04 import Itc04TestCase, H1
from apps.gst.itc04 import itc04
from apps.accounting.models import AccountingPeriod
from apps.manufacturing.outside import OutsideMovement


def show(*a):
    print("\nRV", *a)


class O61(Itc04TestCase):
    def close_june(self):
        p = AccountingPeriod.objects.create(name="Jun", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 6, 30))
        p.close()

    def test_zero_value_receipt_into_closed_month(self):
        before = len(itc04(*H1)["received"])
        self.close_june()
        for value in ("1400", "0"):
            try:
                m = self.back(self.lamination, "100", value)
                show("value", value, "accepted; entry:", m.journal_entry_id, "ITC-04 received rows", before, "->", len(itc04(*H1)["received"]))
            except ValidationError as e:
                show("value", value, "refused", e)

    def test_void_of_zero_value_receipt_in_closed_month(self):
        m = self.back(self.lamination, "100", "0")
        self.close_june()
        n = len(itc04(*H1)["received"])
        try:
            m.void()
            show("void of zero-value receipt accepted:", n, "->", len(itc04(*H1)["received"]))
        except ValidationError as e:
            show("void refused", e)
        except TypeError as e:
            show("void sig", e)
