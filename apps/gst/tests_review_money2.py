"""Review of fix_money2 (gst): probes."""

import datetime
from unittest import mock

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.sales.models import Invoice

from .tests import DAY, D, gstin
from .tests_einvoice import EInvoiceTestCase, IRN, signed_qr


class Portal(EInvoiceTestCase):
    def registered(self, ahead, price="1000"):
        from .einvoice import prepare

        invoice = self.sell(self.ka, price)
        record = prepare(invoice)
        now = timezone.now()
        with mock.patch("django.utils.timezone.now", return_value=now):
            record.record(IRN, "112610000000001", now + ahead, signed_qr(
                Irn=IRN, DocNo=invoice.number, SellerGstin=self.settings.gstin,
                BuyerGstin=record.payload["BuyerDtls"]["Gstin"], TotInvVal=record.payload["ValDtls"]["TotInvVal"]))
        return record, now

    def test_ack_4_59_then_cancel_and_before_ack(self):
        from .einvoice import CANCEL_WITHIN  # noqa

        record, now = self.registered(datetime.timedelta(minutes=4, seconds=59))
        print("ack accepted", record.ack_date - now)
        # cancel with the plant's own now (portal's CancelDate earlier than the ack it gave)
        record.build_cancellation("1", "mistake") if hasattr(record, "build_cancellation") else None

    def test_ack_5_01(self):
        with self.assertRaises(ValidationError):
            self.registered(datetime.timedelta(minutes=5, seconds=1))

    def test_ack_before_invoice_backwards(self):
        with self.assertRaises(ValidationError):
            self.registered(-datetime.timedelta(days=400))

    def test_ack_naive_datetime(self):
        from .einvoice import prepare

        invoice = self.sell(self.ka, "1000")
        record = prepare(invoice)
        naive = datetime.datetime.now() + datetime.timedelta(days=9)
        try:
            record.record(IRN, "1", naive, "x")
        except ValidationError:
            pass
        except TypeError as exc:
            self.fail(f"500 on naive datetime: {exc}")

    def test_eway_cancel_after_skew(self):
        from .ewaybill import prepare

        bill = prepare(self.sell(self.ka, "100000"), vehicle_number="MH12AB1234")
        now = timezone.now()
        bill.record("123456789012", now + datetime.timedelta(minutes=4, seconds=59), None)
        bill.cancel("duplicate")
        print("eway cancelled_at before generated_at:", bill.cancelled_at < bill.generated_at)


from apps.core.tests_roles import RolesTestCase  # noqa: E402


class BadPeriods(RolesTestCase):
    def test_garbage_periods_never_500(self):
        c = self.as_("GST Officer")
        bad = ["99999-01", "0000-01", "0001-01", "9999-12", "2026-1e1", " 2026-01", "2026-01 ", "+2026-01",
               "2026-01-01", "-2026-01", "２０２６-０１", "2026-0", "%00", "2026-13", "10000-01", "٢٠٢٦-٠١",
               "9999999999999999999999-01", "2026-99999999999999999999"]
        codes = {}
        for p in bad:
            for url in ("/api/gst/gstr1/?period=", "/api/gst/gstr1/json/?period=", "/api/gst/gstr3b/?period=",
                        "/api/gst/gstr2b/match/?period="):
                try:
                    r = c.get(url + p)
                    codes[(url, p)] = r.status_code
                except Exception as exc:  # a 500 raised through the test client
                    codes[(url, p)] = repr(exc)[:80]
        worse = {k: v for k, v in codes.items() if v not in (200, 400)}
        print("BAD PERIOD results", worse)
        self.assertEqual(worse, {})
        for url in ("/api/gst/itc04/?start=0000-01-01&end=9999-12-31", "/api/gst/itc04/?start=99999-01-01&end=2026-01-01",
                    "/api/gst/gstr2b/?period=99999-01"):
            try:
                print(url, c.get(url).status_code)
            except Exception as exc:
                print(url, "RAISED", repr(exc)[:100])
