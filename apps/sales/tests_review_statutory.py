"""Review probes for g24: the TCS challan's section and form against the collections it pays over."""

import datetime
from decimal import Decimal as D

from apps.accounting.models import TcsRate

from .tcs import TcsChallan, tcs_return
from .tests_tcs import TcsTestCase


class TcsChallanProbes(TcsTestCase):
    def test_p17_march_collections_paid_in_april_are_named_as_the_return_names_them(self):
        TcsRate.objects.filter(pk=self.rate.pk).update(section_code="394(1)")
        early = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2026, 1, 1),
                                       section_code="206C(1)", rate_percent=D("1"), no_pan_rate_percent=D("5"))
        early.confirm(self.accountant)
        self.invoice(self.mh, [(self.scrap, "2000", "45", "0", self.pair)], day=datetime.date(2026, 3, 16))
        [row] = tcs_return(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
        challan = TcsChallan.pay(self.section, "2026-03-01", datetime.date(2026, 4, 7), self.bank, "00200",
                                 "0510308")
        # the return calls the collection 206C(1) and a 1961-Act payment; so should its challan
        self.assertEqual((row["section"], challan.section_code, challan.form),
                         ("206C(1)", "206C(1)", "Challan ITNS 281"))
