"""
review_acct probes, g21 O219 (TCS on scrap), O220 (IRN cancellation) and the shared challan.
Every test is a claim about money or a document; a failure is a finding. Not part of the product's suite.

  DJANGO_TIME_ZONE=Asia/Kolkata python manage.py test apps.sales.tests_review_acct_tcs
"""

import datetime
from decimal import Decimal as D

from django.core import mail
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import (
    Account, AccountType, JournalLine, Payment, PaymentDirection, TcsDeclaration, TcsRate,
)
from apps.gst.einvoice import EInvoice
from apps.gst.returns import gstr1, gstr3b
from apps.gst.tests import SEPTEMBER, gstin
from apps.sales.documents import render_invoice_pdf, tax_invoice
from apps.sales.models import Invoice, InvoicePayment
from apps.sales.tcs import TcsChallan, TcsCollection, tcs_return
from apps.sales.tests_tcs import OCTOBER, TcsTestCase
from apps.gst.tests_tax_invoice import printed_text

SEP = lambda d: datetime.date(2026, 9, d)  # noqa: E731
OCT = lambda d: datetime.date(2026, 10, d)  # noqa: E731


class Base(TcsTestCase):
    def ledger_to(self, code, day):
        rows = JournalLine.objects.filter(account__code=code, entry__posted=True, entry__date__lte=day).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        return (rows["credit"] or D("0")) - (rows["debit"] or D("0"))

    def buyer(self, code, pan_letter="B", with_gstin=True):
        party = self.party(code, gstin=gstin(f"27AAC{pan_letter}M1111A1Z")) if with_gstin else self.party(code)
        self.address(party, "1 Test Road", "Nashik", "27" if with_gstin else "Maharashtra", "422001")
        return party

    def s2(self, day=None):
        return self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)], **({"day": day} if day else {}))

    def pay_into(self, invoice, amount):
        payment = Payment.objects.create(
            party=invoice.customer, direction=PaymentDirection.RECEIPT, payment_date=invoice.invoice_date,
            amount=D(amount), currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)
        payment.post()
        return InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=D(amount))


class WindowAndRefusals(Base):
    def test_24_hour_window_edges_in_the_plants_zone(self):
        record = self.registered(self.s2())
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        ack = datetime.datetime(2026, 10, 14, 23, 50, tzinfo=ist)
        EInvoice.objects.filter(pk=record.pk).update(ack_date=ack)
        record.refresh_from_db()
        record.cancellation("2", "x", now=ack + datetime.timedelta(hours=23, minutes=59))
        record.cancellation("2", "x", now=ack + datetime.timedelta(hours=24))
        with self.assertRaises(ValidationError):
            record.cancellation("2", "x", now=ack + datetime.timedelta(hours=24, minutes=1))
        with self.assertRaises(ValidationError):
            record.cancellation("2", "x", now=ack - datetime.timedelta(minutes=1))

    def test_the_portals_own_time_is_held_to_the_window_when_it_is_recorded_late(self):
        record = self.registered(self.s2())
        ack = timezone.now() - datetime.timedelta(days=3)
        EInvoice.objects.filter(pk=record.pk).update(ack_date=ack)
        record.refresh_from_db()
        record.cancellation("2", "x", now=ack + datetime.timedelta(hours=1))
        with self.assertRaises(ValidationError):
            record.record_cancellation(ack + datetime.timedelta(hours=24, minutes=1))

    def test_refused_with_a_payment_against_it(self):
        invoice = self.s2()
        record = self.registered(invoice)
        self.pay_into(invoice, "100")
        with self.assertRaises(ValidationError):
            record.cancellation("2", "x")

    def test_refused_with_a_tcs_challan_against_it(self):
        invoice = self.t1()
        record = self.registered(invoice)
        TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00123", "0510308")
        with self.assertRaises(ValidationError):
            record.cancellation("2", "x")

    def test_a_payment_that_lands_between_the_request_and_the_portals_answer_does_not_strand_the_books(self):
        """The portal has cancelled; the books refuse to follow. Unproven as a defect: what is the way out?"""
        invoice = self.s2()
        record = self.registered(invoice)
        record.cancellation("2", "x")
        self.pay_into(invoice, "100")
        with self.assertRaises(ValidationError):
            record.record_cancellation(timezone.now())


class CancellingUndoesEverything(Base):
    def cancel(self, invoice):
        record = self.registered(invoice)
        record.cancellation("2", "Wrong quantity")
        return record, record.record_cancellation(timezone.now())

    def test_scrap_invoice_gst_and_tcs_both_undone_and_the_returns_follow(self):
        invoice = self.t1()
        record, note = self.cancel(invoice)
        for code in ("1100", "2290", "2201", "2202", "4000"):
            with self.subTest(account=code):
                self.assertEqual(self.ledger(code), D("0"))
        self.assertEqual((note.invoice_date, note.tcs_amount, note.total()), (invoice.invoice_date, D("1062.00"), D("107262.00")))
        rows = tcs_return(SEP(1), SEP(30))
        self.assertEqual((sum(r["amount"] for r in rows), sum(r["base"] for r in rows)), (D("0"), D("0")))
        one = gstr1(*SEPTEMBER)
        lists = {k: v for k, v in one.items() if isinstance(v, list) and v and k not in ("documents", "warnings", "not_built")}
        self.assertEqual(lists, {}, "GSTR-1 still lists a cancelled invoice or its reversal")
        three = gstr3b(*SEPTEMBER)["3.1a"]
        self.assertEqual(set(three.values()) - {D("0"), D("0.00")}, set(), f"3B outward not zero: {three}")
        self.assertEqual([(r["kind"], r["total"], r["cancelled"]) for r in one["documents"] if r["kind"] in ("invoices", "credit_notes")],
                         [("invoices", 1, 1), ("credit_notes", 1, 1)])

    def test_the_month_of_the_cancellation_shows_nothing_either(self):
        self.cancel(self.t1())
        from apps.gst.tests import month
        october = month("2026-10")
        one = gstr1(*october)
        lists = {k: v for k, v in one.items() if isinstance(v, list) and v and k not in ("documents", "warnings", "not_built")}
        self.assertEqual(lists, {})
        self.assertEqual([r["total"] for r in one["documents"]], [0] * len(one["documents"]))

    def test_the_same_month_cancellation(self):
        today = timezone.localdate()
        invoice = self.invoice(self.mh, [(self.scrap, "100", "45", "0", self.pair)], day=today)
        record, note = self.cancel(invoice)
        from apps.gst.tests import month
        one = gstr1(*month(today.strftime("%Y-%m")))
        lists = {k: v for k, v in one.items() if isinstance(v, list) and v and k not in ("documents", "warnings", "not_built")}
        self.assertEqual(lists, {})
        self.assertEqual(self.ledger("2290"), D("0"))

    def test_cancelling_twice(self):
        invoice = self.s2()
        record, note = self.cancel(invoice)
        with self.assertRaises(ValidationError):
            record.cancellation("2", "again")
        with self.assertRaises(ValidationError):
            record.record_cancellation(timezone.now())
        self.assertEqual(Invoice.objects.filter(credits=invoice).count(), 1)

    def test_a_cancelled_invoice_takes_no_payment(self):
        invoice = self.s2()
        self.cancel(invoice)
        invoice.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.pay_into(invoice, "100")

    def test_a_cancelled_invoice_takes_no_credit_note(self):
        invoice = self.s2()
        self.cancel(invoice)
        invoice.refresh_from_db()
        with self.assertRaises(ValidationError):
            invoice.create_credit_note()

    def test_a_cancelled_invoice_does_not_print_as_a_tax_invoice(self):
        invoice = self.s2()
        self.cancel(invoice)
        invoice.refresh_from_db()
        self.assertNotEqual(tax_invoice(invoice)["heading"], "Tax Invoice")

    def test_the_books_reversal_note_is_not_sendable_as_a_credit_note(self):
        invoice = self.s2()
        record, note = self.cancel(invoice)
        with self.assertRaises(ValidationError):
            note.email_to_customer(to="buyer@example.com")

    def test_the_books_reversal_note_is_not_printed_as_a_credit_note_under_section_34(self):
        invoice = self.s2()
        record, note = self.cancel(invoice)
        self.assertNotEqual(tax_invoice(note)["heading"], "Credit Note")


class CollectedOnlyWhereTheLawSaysAndAsItStood(Base):
    def test_rate_changes_on_the_first_and_mid_month(self):
        for start, percent in ((SEP(16), "2"), (OCT(1), "3")):
            rate = TcsRate.objects.create(section=self.section, applies_from=start, rate_percent=D(percent), no_pan_rate_percent=D("5"))
            rate.confirm(self.accountant)
        got = {}
        for day in (SEP(15), SEP(16), SEP(30), OCT(1)):
            got[day] = self.t1(day=day).tcs_amount if False else self.invoice(
                self.mh, [(self.scrap, "2000", "45", "0", self.pair)], day=day).tcs_amount
        self.assertEqual(got, {SEP(15): D("1062.00"), SEP(16): D("2124.00"), SEP(30): D("2124.00"), OCT(1): D("3186.00")})

    def test_an_unconfirmed_future_rate_does_not_touch_earlier_days(self):
        TcsRate.objects.create(section=self.section, applies_from=SEP(20), rate_percent=D("2"), no_pan_rate_percent=D("5"))
        self.assertEqual(self.invoice(self.mh, [(self.scrap, "2000", "45", "0", self.pair)], day=SEP(19)).tcs_amount, D("1062.00"))
        with self.assertRaises(ValidationError):
            self.invoice(self.mh, [(self.scrap, "2000", "45", "0", self.pair)], day=SEP(20))

    def test_a_credit_note_gives_back_at_the_rate_collected_after_the_rate_moved(self):
        invoice = self.t1()
        rate = TcsRate.objects.create(section=self.section, applies_from=SEP(20), rate_percent=D("2"), no_pan_rate_percent=D("5"))
        rate.confirm(self.accountant)
        note = invoice.create_credit_note(on_date=SEP(25))
        self.assertEqual(note.tcs_amount, D("1062.00"))
        self.assertEqual(self.ledger("2290"), D("0"))

    def test_form_127_in_force_expired_given_after_and_boundary(self):
        cases = {
            "in force": (SEP(1), SEP(1) - datetime.timedelta(days=30), datetime.date(2027, 3, 31), D("0")),
            "expired the day before": (datetime.date(2026, 4, 1), datetime.date(2026, 4, 2), SEP(9), D("1062.00")),
            "valid to the day itself": (datetime.date(2026, 4, 1), datetime.date(2026, 4, 2), SEP(10), D("0")),
            "given after the invoice date": (datetime.date(2026, 4, 1), SEP(11), datetime.date(2027, 3, 31), D("1062.00")),
            "given on the day": (datetime.date(2026, 4, 1), SEP(10), datetime.date(2027, 3, 31), D("0")),
        }
        for index, (name, (valid_from, received, valid_to, expected)) in enumerate(cases.items()):
            buyer = self.buyer(f"B{index}", pan_letter="DEFGH"[index])
            TcsDeclaration.objects.create(party=buyer, section=self.section, reference=f"F{index}",
                                          received_on=received, valid_from=valid_from, valid_to=valid_to)
            with self.subTest(case=name):
                self.assertEqual(self.t1(buyer).tcs_amount, expected)

    def test_another_buyers_declaration_exempts_nobody_else(self):
        other = self.buyer("OTHER", "J")
        TcsDeclaration.objects.create(party=other, section=self.section, received_on=SEP(1),
                                      valid_from=datetime.date(2026, 4, 1), valid_to=datetime.date(2027, 3, 31))
        self.assertEqual(self.t1().tcs_amount, D("1062.00"))

    def test_a_draft_that_is_deleted_collects_none_and_a_refused_post_leaves_no_number_or_row(self):
        draft = self.t1(post=False)
        draft.delete()
        self.assertEqual((TcsCollection.objects.count(), self.ledger("2290")), (0, D("0")))
        TcsRate.objects.create(section=self.section, applies_from=SEP(1), rate_percent=D("2"), no_pan_rate_percent=D("5"))
        refused = self.t1(post=False)
        with self.assertRaises(ValidationError):
            refused.post()
        refused.refresh_from_db()
        self.assertEqual((refused.posted, refused.number, refused.tcs_amount, TcsCollection.objects.count()), (False, "", D("0"), 0))
        self.assertEqual(self.ledger("2290"), D("0"))
        self.assertEqual(self.ledger("1100"), D("0"))

    def test_the_credit_notes_share_the_whole_and_no_more(self):
        invoice = self.t1()
        got = [self.credit(invoice, kg, SEP(20)).tcs_amount for kg in ("700", "700", "600")]
        self.assertEqual(got, [D("372.00"), D("372.00"), D("318.00")])
        self.assertEqual(sum(got), D("1062.00"))
        self.assertEqual((self.ledger("2290"), self.ledger("1100")), (D("0"), D("0")))

    def test_the_credit_notes_in_a_different_order(self):
        invoice = self.t1()
        got = [self.credit(invoice, kg, SEP(20)).tcs_amount for kg in ("600", "700", "700")]
        self.assertEqual(sum(got), D("1062.00"))
        self.assertEqual(self.ledger("2290"), D("0"))

    def test_a_no_pan_buyer_credit_by_thirds(self):
        nopan = self.party("NOPAN2")
        self.address(nopan, "4 Station Road", "Nashik", "Maharashtra", "422001")
        invoice = self.t1(nopan)
        self.assertEqual(invoice.tcs_amount, D("5310.00"))
        got = [self.credit(invoice, kg, SEP(20)).tcs_amount for kg in ("667", "667", "666")]
        self.assertEqual(sum(got), D("5310.00"))
        self.assertEqual(self.ledger("2290"), D("0"))


class TheMonthFootsToThePayableAccount(Base):
    def scenario(self):
        a = self.t1()
        b = self.invoice(self.mh, [(self.scrap, "1234", "47.31", "0", self.pair)], day=SEP(18))
        self.credit(a, "500", SEP(20))
        c = self.invoice(self.mh, [(self.scrap, "800", "46", "0", self.pair)], day=OCT(2))
        self.credit(b, "234", OCT(3))
        return a, b, c

    def test_each_challan_is_the_payable_balance_at_the_months_end_and_clears_it(self):
        self.scenario()
        owed_sep = self.ledger_to("2290", SEP(30))
        sep = TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.bank, "00001", "0510308")
        self.assertEqual(sep.amount, owed_sep)
        self.assertEqual(self.ledger_to("2290", SEP(30)) - sep.amount, D("0"))
        owed_oct = self.ledger_to("2290", OCT(31))
        octo = TcsChallan.pay(self.section, "2026-10-01", OCT(6), self.bank, "00002", "0510308")
        self.assertEqual(octo.amount, owed_oct)
        self.assertEqual(self.ledger("2290"), D("0"))

    def test_the_quarters_rows_foot_to_what_was_collected_less_what_was_given_back(self):
        self.scenario()
        rows2 = tcs_return(datetime.date(2026, 7, 1), SEP(30))
        rows3 = tcs_return(OCT(1), datetime.date(2026, 12, 31))
        moved = lambda lo, hi: sum(  # noqa: E731
            (JournalLine.objects.filter(account__code="2290", entry__posted=True, entry__date__gte=lo, entry__date__lte=hi,
                                        entry__memo__startswith="TCS 206C(1) ").aggregate(c=Sum("credit"), d=Sum("debit"))[k] or D("0")) * s
            for k, s in (("c", 1), ("d", -1)))
        self.assertEqual(sum(r["amount"] for r in rows2), moved(datetime.date(2026, 7, 1), SEP(30)))
        self.assertEqual(sum(r["amount"] for r in rows3), moved(OCT(1), datetime.date(2026, 12, 31)))

    def test_a_challan_paid_twice_voided_and_paid_again(self):
        self.scenario()
        first = TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.bank, "00001", "0510308")
        with self.assertRaises(ValidationError):
            TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.bank, "00002", "0510308")
        first.void()
        with self.assertRaises(ValidationError):
            first.void()
        again = TcsChallan.pay(self.section, "2026-09-01", OCT(6), self.bank, "00003", "0510308")
        self.assertEqual(again.amount, first.amount)
        self.assertEqual(self.ledger_to("2290", SEP(30)) - again.amount, D("0"))

    def test_a_voided_challan_is_not_voided_before_it_was_paid_or_after_today(self):
        self.scenario()
        challan = TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.bank, "00001", "0510308")
        with self.assertRaises(ValidationError):
            challan.void(on_date=OCT(4))
        with self.assertRaises(ValidationError):
            challan.void(on_date=timezone.localdate() + datetime.timedelta(days=3))

    def test_a_challan_cannot_be_dated_before_the_tax_was_collected(self):
        self.t1()  # collected 10 September
        with self.assertRaises(ValidationError):
            TcsChallan.pay(self.section, "2026-09-01", SEP(1), self.bank, "00001", "0510308")

    def test_a_challan_cannot_be_paid_out_of_an_account_that_is_not_money(self):
        self.t1()
        with self.assertRaises(ValidationError):
            TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.ar, "00001", "0510308")

    def test_a_challan_is_not_edited_or_deleted(self):
        self.t1()
        challan = TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.bank, "00001", "0510308")
        challan.amount = D("1")
        with self.assertRaises(ValidationError):
            challan.save()
        with self.assertRaises(ValidationError):
            TcsChallan.objects.get(pk=challan.pk).delete()
        collection = TcsCollection.objects.get()
        collection.amount = D("1")
        with self.assertRaises(ValidationError):
            collection.save()
        with self.assertRaises(ValidationError):
            collection.delete()

    def test_a_collection_cannot_be_moved_to_another_challan_by_hand(self):
        self.t1()
        challan = TcsChallan.pay(self.section, "2026-09-01", OCT(5), self.bank, "00001", "0510308")
        collection = TcsCollection.objects.get()
        collection.challan = None
        collection.save()  # editable=False: the viewset cannot, but save() takes it
        collection.refresh_from_db()
        self.assertIsNotNone(collection.challan_id, "a paid collection was released from its challan by a plain save()")


class ThePrintOfAScrapInvoice(Base):
    def test_credit_note_names_its_invoice_and_says_what_it_gives_back(self):
        invoice = self.t1()
        note = self.credit(invoice, "500", SEP(20))
        text = " ".join(" ".join(printed_text(render_invoice_pdf, note)).split())
        self.assertIn(f"Against {invoice.number} dated 10 Sep 2026", text)
        self.assertIn("TCS given back u/s 206C(1)", text)
        printed = tax_invoice(note)
        self.assertEqual(printed["heading"], "Credit Note")
        self.assertEqual(printed["credits"]["number"], invoice.number)

    def test_the_credit_note_recipient_is_the_invoices_recipient_after_a_rename(self):
        """Rule 53: a credit note carries the particulars of the invoice it corrects."""
        from apps.core.models import Party
        invoice = self.s2()
        Party.objects.filter(pk=self.mh.pk).update(name="Renamed Buyer", legal_name="Renamed Buyer Ltd")
        note = invoice.create_credit_note(quantities={invoice.lines.get(): D("10")}, on_date=SEP(20))
        self.assertEqual(tax_invoice(note)["recipient"], tax_invoice(invoice)["recipient"])

    def test_every_copy_of_a_tcs_invoice_prints_the_tcs(self):
        invoice = self.t1()
        for copy in ("original", "duplicate", "triplicate"):
            text = " ".join(" ".join(printed_text(render_invoice_pdf, invoice, copy)).split())
            with self.subTest(copy=copy):
                self.assertIn("TCS collected u/s 206C(1) @ 1% on 106,200.00 1,062.00", text)
