"""
The day of acceptance kept on the bill, and the interest the MSMED Act adds
to a micro or small supplier paid late (s.16): compound, monthly rests, three
times the bank rate the Reserve Bank notifies. Every figure is worked day by
day in calc_g38_purch/msme_interest.py, which shares no code with msme.py.

The bank rate here is 6.50% from 1 January 2026 (19.5% a year), a figure
made up for the tests: the plant's own is a dated row its accountant confirms.

  refusals: no rate kept, a rate not confirmed, a later unconfirmed rate over
            an older confirmed one; a confirmed rate is not deleted; a role
            that may not keep rates is refused, and one that may not read bills.
  S1  10,000 due 1 Jul, paid 15 Jul: 14 days                      74.79
  S2  50,000 due 3 Mar 2027, unpaid at 31 Mar: 28 days           747.95
  S3  10,000 due 1 Jul, unpaid at 30 Sep: rests 2 Aug, 2 Sep     494.08  (simple: 486.16)
  S4  the same, the rate 6.00% from 1 Aug / 15 Aug / 31 Aug      468.21 / 474.14 / 480.92
  S5  4,000 paid 15 Jul and 6,000 paid 20 Aug                     29.92 + 161.28 = 191.20
      at 31 Jul, the 6,000 still unpaid                           29.92 paid late, 96.16 unpaid
  S6  paid 15 Jul, the payment voided: unpaid at 31 Aug          328.54
  S7  600 due 16 Jul and 400 due 4 Aug, paid 10 Aug                8.01 and 1.28
  S8  due 30 Jan 2027, unpaid at 31 Mar: rests 28 Feb, 31 Mar    323.20
  S9  ten lakh due 1 Jul, unpaid at 31 Mar 2027              155,668.59
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.accounting.models import BankRate, Payment

from .models import Bill, GoodsReceipt
from .msme import RateNotConfirmed, interest_on, keep_unkept_due_dates, msme_bills, msme_note, msme_statement
from . import tests_msme
from .tests_msme import JUNE, MsmeTestCase

D = Decimal
day = datetime.date
JULY1, SEP30 = day(2026, 7, 1), day(2026, 9, 30)
RATES = [(day(2026, 1, 1), D("6.50"), True)]


def paise(amount):
    return amount.quantize(D("0.01"))


class InterestArithmeticTests(SimpleTestCase):
    def test_nothing_by_the_due_day(self):
        self.assertEqual(interest_on(D("10000"), JULY1, JULY1, RATES), 0)
        self.assertEqual(interest_on(D("10000"), JULY1, day(2026, 6, 20), RATES), 0)
        self.assertEqual(interest_on(D("10000"), JULY1, JULY1, []), 0)  # no rate is asked for no days

    def test_days_inside_the_first_month_are_simple(self):
        self.assertEqual(paise(interest_on(D("10000"), JULY1, day(2026, 7, 15), RATES)), D("74.79"))
        self.assertEqual(paise(interest_on(D("50000"), day(2027, 3, 3), day(2027, 3, 31), RATES)), D("747.95"))

    def test_each_months_interest_is_added_at_its_end(self):
        self.assertEqual(paise(interest_on(D("10000"), JULY1, SEP30, RATES)), D("494.08"))
        self.assertEqual(paise(interest_on(D("1000000"), JULY1, day(2027, 3, 31), RATES)), D("155668.59"))

    def test_a_month_counted_from_the_31st_ends_on_the_months_last_day(self):
        self.assertEqual(paise(interest_on(D("10000"), day(2027, 1, 30), day(2027, 3, 31), RATES)), D("323.20"))

    def test_a_rate_changed_inside_the_period_is_charged_each_rate_for_its_own_days(self):
        for changed, expected in ((day(2026, 8, 1), "468.21"), (day(2026, 8, 15), "474.14"),
                                  (day(2026, 8, 31), "480.92")):
            with self.subTest(changed=changed):
                rates = RATES + [(changed, D("6.00"), True)]
                self.assertEqual(paise(interest_on(D("10000"), JULY1, SEP30, rates)), D(expected))

    def test_no_rate_and_a_rate_not_confirmed_are_refused_in_words(self):
        with self.assertRaisesMessage(RateNotConfirmed, "no bank rate is kept from 02 Jul 2026 or before"):
            interest_on(D("10000"), JULY1, SEP30, [(day(2026, 8, 1), D("6.50"), True)])
        with self.assertRaisesMessage(RateNotConfirmed, "6.5% from 01 Jan 2026 is not confirmed"):
            interest_on(D("10000"), JULY1, SEP30, [(day(2026, 1, 1), D("6.50"), False)])
        # A later rate nobody confirmed is not read past to the older confirmed one...
        later = RATES + [(day(2026, 9, 1), D("9.00"), False)]
        with self.assertRaisesMessage(RateNotConfirmed, "9% from 01 Sep 2026 is not confirmed"):
            interest_on(D("10000"), JULY1, SEP30, later)
        # ...and does not touch days before it applies.
        self.assertEqual(paise(interest_on(D("10000"), JULY1, day(2026, 7, 15), later)), D("74.79"))


class WithABankRate(MsmeTestCase):
    def setUp(self):
        super().setUp()
        self.accountant = User.objects.create_user("accountant")
        self.rate = BankRate.objects.create(applies_from=day(2026, 1, 1), rate_percent=D("6.50"),
                                            authority="made up for the tests")
        self.rate.confirm(self.accountant)

    def listed(self, bill, as_of):
        return [row for row in msme_bills(day(2026, 1, 1), day(2027, 12, 31), as_of=as_of) if row["bill"] == bill.pk]

    def interest(self, bill, as_of):
        [row] = self.listed(bill, as_of)
        return row["paid_late"], row["interest_paid_late"], row["interest_unpaid"], row["interest"]


class InterestOnTheListTests(WithABankRate):
    def test_until_the_rate_is_confirmed_the_list_says_so_and_shows_no_figure(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", day(2026, 7, 15))
        self.rate.rate_percent = D("6.75")  # the edit path: a corrected figure waits for the accountant again
        self.rate.save()
        self.assertIsNone(BankRate.objects.get().confirmed_on)
        [row] = self.listed(bill, SEP30)
        self.assertEqual((row["interest"], row["interest_paid_late"], row["interest_unpaid"]), (None, None, D("0.00")))
        self.assertIn("the bank rate of 6.75% from 01 Jan 2026 is not confirmed", row["interest_state"])
        found = msme_statement(JUNE, day(2026, 6, 30), as_of=SEP30)
        self.assertIsNone(found["totals"]["interest"])
        self.assertEqual(found["totals"]["paid_late"], D("10000.00"))
        self.assertIn("not confirmed", found["interest_state"])
        # What the days say does not wait for the rate.
        self.assertEqual((row["days_late"], row["paid_on"]), (14, day(2026, 7, 15)))

    def test_no_rate_at_all(self):
        self.rate.rate_percent = D("6.75")
        self.rate.save()
        self.rate.delete()  # unconfirmed, so it may go
        bill = self.bill(terms=self.net30)
        [row] = self.listed(bill, SEP30)
        self.assertIsNone(row["interest"])
        self.assertIn("no bank rate is kept", row["interest_state"])

    def test_a_confirmed_rate_is_not_deleted(self):
        with self.assertRaisesMessage(ValidationError, "is confirmed"):
            self.rate.delete()
        self.assertTrue(BankRate.objects.exists())

    def test_a_negative_rate_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "not negative"):
            BankRate.objects.create(applies_from=day(2026, 2, 1), rate_percent=D("-1"))

    def test_paid_on_time_owes_nothing(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", JULY1)
        self.assertEqual(self.interest(bill, SEP30), (D("0"), D("0.00"), D("0.00"), D("0.00")))

    def test_paid_late(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", day(2026, 7, 15))
        self.assertEqual(self.interest(bill, SEP30), (D("10000.00"), D("74.79"), D("0.00"), D("74.79")))

    def test_unpaid_it_runs_to_the_day_asked_with_its_rests(self):
        bill = self.bill(terms=self.net30)
        self.assertEqual(self.interest(bill, SEP30), (D("0"), D("0.00"), D("494.08"), D("494.08")))
        self.assertEqual(self.interest(bill, JULY1), (D("0"), D("0.00"), D("0.00"), D("0.00")))

    def test_a_rate_changed_in_the_period(self):
        bill = self.bill(terms=self.net30)
        new = BankRate.objects.create(applies_from=day(2026, 8, 15), rate_percent=D("6.00"))
        self.assertIsNone(self.interest(bill, SEP30)[3])  # entered, not yet confirmed
        self.assertIsNotNone(self.interest(bill, day(2026, 8, 14))[3])  # days before it applies do not wait for it
        new.confirm(self.accountant)
        self.assertEqual(self.interest(bill, SEP30)[3], D("474.14"))

    def test_a_part_payment_stops_the_interest_on_its_part_only(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "4000", day(2026, 7, 15))
        self.assertEqual(self.interest(bill, day(2026, 7, 31)), (D("4000.00"), D("29.92"), D("96.16"), D("126.08")))
        self.pay(bill, "6000", day(2026, 8, 20))
        self.assertEqual(self.interest(bill, SEP30), (D("10000.00"), D("191.20"), D("0.00"), D("191.20")))
        # Asked as of a day before the second payment, it had not been made.
        self.assertEqual(self.interest(bill, day(2026, 7, 31)), (D("4000.00"), D("29.92"), D("96.16"), D("126.08")))

    def test_a_voided_payment_never_paid_it(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", day(2026, 7, 15))
        self.assertEqual(self.interest(bill, day(2026, 8, 31)), (D("10000.00"), D("74.79"), D("0.00"), D("74.79")))
        Payment.objects.get().void(memo="Cheque returned", on_date=day(2026, 7, 20))
        self.assertEqual(self.interest(bill, day(2026, 8, 31)), (D("0"), D("0.00"), D("328.54"), D("328.54")))
        [row] = self.listed(bill, day(2026, 8, 31))
        self.assertEqual((row["paid_on"], row["unpaid"], row["at_risk"]), (None, D("10000.00"), D("10000.00")))

    def test_each_deliverys_part_bears_interest_from_its_own_day(self):
        order, line = tests_msme.AcceptedOnDeliveryTests.order(self)
        tests_msme.AcceptedOnDeliveryTests.receive(self, order, line, JUNE, "6")
        tests_msme.AcceptedOnDeliveryTests.receive(self, order, line, day(2026, 6, 20), "4")
        bill = tests_msme.AcceptedOnDeliveryTests.bill_for(self, order, line, day(2026, 6, 25), "10")
        self.pay(bill, "1000", day(2026, 8, 10))
        rows = self.listed(bill, SEP30)
        self.assertEqual([(row["amount"], row["due"], row["accepted_on"], row["interest"]) for row in rows], [
            (D("600.00"), day(2026, 7, 16), day(2026, 6, 20), D("8.01")),
            (D("400.00"), day(2026, 8, 4), day(2026, 6, 20), D("1.28"))])

    def test_the_list_foots_and_the_years_note_reads_the_same_bills(self):
        late = self.bill(terms=self.net30)
        self.pay(late, "10000", day(2026, 7, 15))
        self.bill("50000", day=day(2027, 2, 1), terms=self.net30)
        self.bill("10000", day=day(2027, 3, 1), terms=self.net30)
        end = day(2027, 3, 31)
        found = msme_statement(day(2026, 4, 1), end, as_of=end)
        self.assertEqual(found["totals"], {
            "amount": D("70000.00"), "unpaid": D("60000.00"), "at_risk": D("50000.00"), "paid_late": D("10000.00"),
            "interest_paid_late": D("74.79"), "interest_unpaid": D("747.95"), "interest": D("822.74")})
        for name, total in found["totals"].items():
            self.assertEqual(sum(row[name] for row in found["rows"]), total, name)
        self.assertEqual(found["bank_rates"], [{"applies_from": day(2026, 1, 1), "rate_percent": D("6.5000"),
                                                "confirmed": True, "interest_percent": D("19.5000")}])
        self.assertEqual(msme_note(day(2026, 4, 1), end), {
            "year_start": day(2026, 4, 1), "year_end": end,
            "principal_unpaid": D("60000.00"), "principal_unpaid_past_its_day": D("50000.00"),
            "interest_on_unpaid": D("747.95"), "paid_after_the_day": D("10000.00"), "interest_paid": None,
            "interest_on_paid_late": D("74.79"), "interest_accrued_unpaid": D("822.74"),
            "interest_of_the_year": D("822.74"), "interest_state": ""})
        # The year after, nothing more paid: the late payment of July is an earlier year's, its interest
        # still unpaid; the year's own interest is what accrued inside it.
        self.assertEqual(msme_note(day(2027, 4, 1), day(2028, 3, 31)), {
            "year_start": day(2027, 4, 1), "year_end": day(2028, 3, 31),
            "principal_unpaid": D("60000.00"), "principal_unpaid_past_its_day": D("60000.00"),
            "interest_on_unpaid": D("13750.73"), "paid_after_the_day": D("0"), "interest_paid": None,
            "interest_on_paid_late": D("0.00"), "interest_accrued_unpaid": D("13825.52"),
            "interest_of_the_year": D("13002.78"), "interest_state": ""})


class DayOfAcceptanceKeptTests(MsmeTestCase):
    """
    Bill.accepted_on, the day the Act counts from, written as the bill posts
    and again whenever it moves. Net 60, 10 x 100, micro vendor.
    """

    order = tests_msme.AcceptedOnDeliveryTests.order
    receive = tests_msme.AcceptedOnDeliveryTests.receive
    bill_for = tests_msme.AcceptedOnDeliveryTests.bill_for

    def kept(self, bill):
        bill = Bill.objects.get(pk=bill.pk)
        return bill.accepted_on, bill.due_date

    def test_a_bill_for_no_delivery_counts_from_its_own_date(self):
        self.assertEqual(self.kept(self.bill(terms=self.net60)), (JUNE, day(2026, 7, 16)))

    def test_a_bill_for_a_delivery_counts_from_the_delivery_not_its_own_date(self):
        order, line = self.order()
        self.receive(order, line, day(2026, 6, 10), "10")
        self.assertEqual(self.kept(self.bill_for(order, line, day(2026, 6, 25), "10")),
                         (day(2026, 6, 10), day(2026, 7, 25)))

    def test_a_bill_for_several_counts_from_the_later(self):
        order, line = self.order()
        self.receive(order, line, JUNE, "6")
        self.receive(order, line, day(2026, 6, 20), "4")
        self.assertEqual(self.kept(self.bill_for(order, line, day(2026, 6, 25), "10")),
                         (day(2026, 6, 20), day(2026, 8, 4)))

    def test_a_vendor_the_act_does_not_cover_keeps_none(self):
        from apps.accounting.models import PartyTaxProfile

        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        self.assertEqual(self.kept(self.bill(terms=self.net60)), (None, day(2026, 7, 31)))

    def test_an_objection_empties_it_and_its_removal_or_withdrawal_writes_it_again(self):
        order, line = self.order()
        receipt, _ = self.receive(order, line, day(2026, 6, 10), "10")
        bill = self.bill_for(order, line, day(2026, 6, 25), "10")
        receipt.record_objection(day(2026, 6, 18), "Wrong denier")
        self.assertEqual(self.kept(bill), (None, day(2026, 8, 24)))
        GoodsReceipt.objects.get(pk=receipt.pk).remove_objection(day(2026, 6, 30))
        self.assertEqual(self.kept(bill), (day(2026, 6, 30), day(2026, 8, 14)))
        GoodsReceipt.objects.get(pk=receipt.pk).withdraw_objection()  # the reverse: keyed in error
        self.assertEqual(self.kept(bill), (day(2026, 6, 10), day(2026, 7, 25)))

    def test_a_later_bill_taking_the_delivery_moves_the_day_of_the_one_posted(self):
        # O499's path: 6 received 1 June, 4 on 20 June; the bill of 25 June for 4 posts first.
        order, line = self.order()
        self.receive(order, line, JUNE, "6")
        self.receive(order, line, day(2026, 6, 20), "4")
        late = self.bill_for(order, line, day(2026, 6, 25), "4")
        self.assertEqual(self.kept(late), (JUNE, day(2026, 7, 16)))
        early = self.bill_for(order, line, day(2026, 6, 5), "6")
        self.assertEqual(self.kept(early), (JUNE, day(2026, 7, 16)))
        self.assertEqual(self.kept(late), (day(2026, 6, 20), day(2026, 8, 4)))

    def test_a_delivery_after_the_bill_moves_it(self):
        from .models import BillPolicy, PurchaseOrder

        order, line = self.order()
        PurchaseOrder.objects.filter(pk=order.pk).update(bill_policy=BillPolicy.ORDERED)
        order.refresh_from_db()
        bill = self.bill_for(order, line, day(2026, 6, 5), "10")  # billed ahead: from its own date
        self.assertEqual(self.kept(bill), (day(2026, 6, 5), day(2026, 7, 20)))
        self.receive(order, line, day(2026, 6, 12), "10")
        self.assertEqual(self.kept(bill), (day(2026, 6, 12), day(2026, 7, 27)))

    def test_bills_posted_before_the_day_was_kept_are_given_it_after_migrate(self):
        order, line = self.order()
        self.receive(order, line, day(2026, 6, 10), "10")
        bill = self.bill_for(order, line, day(2026, 6, 25), "10")
        Bill.objects.filter(pk=bill.pk).update(accepted_on=None)
        keep_unkept_due_dates()
        self.assertEqual(self.kept(bill), (day(2026, 6, 10), day(2026, 7, 25)))


class AsThePeopleWhoDoItTests(MsmeTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.clients = {}
        for role in ("Controller", "AP Manager", "Warehouse Staff"):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            self.clients[role] = APIClient()
            self.clients[role].force_authenticate(user)

    def test_the_controller_keeps_and_confirms_the_rate_and_the_list_then_shows_interest(self):
        rates, controller, ap = "/api/accounting/bank-rates/", self.clients["Controller"], self.clients["AP Manager"]
        bill = self.bill(terms=self.net30)
        self.pay(bill, "4000", day(2026, 7, 15))
        query = {"start": "2026-06-01", "end": "2026-06-30", "as_of": "2026-07-31",
                 "year_start": "2026-04-01", "year_end": "2027-03-31"}
        statement = "/api/purchasing/purchasing-reports/msme-statement/"

        # Refusals first: who may not, and what is not a rate.
        self.assertEqual(self.clients["Warehouse Staff"].get(statement, query).status_code, 403)
        self.assertEqual(self.clients["Warehouse Staff"].get(rates).status_code, 403)
        body = {"applies_from": "2026-01-01", "rate_percent": "6.50", "authority": "made up for the tests"}
        self.assertEqual(ap.post(rates, body, format="json").status_code, 403)
        refused = controller.post(rates, {**body, "rate_percent": "-1"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(ap.get(statement, {"start": "2026-06-01"}).status_code, 400)

        made = controller.post(rates, body, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        rate = made.json()
        self.assertIsNone(rate["confirmed_on"])
        found = ap.get(statement, query).json()
        self.assertIsNone(found["rows"][0]["interest"])
        self.assertIn("not confirmed", found["interest_state"])
        self.assertIsNone(found["note"]["interest_accrued_unpaid"])

        self.assertEqual(ap.post(f"{rates}{rate['id']}/confirm/", rate, format="json").status_code, 403)
        # Confirming figures other than the row's is refused: the accountant vouches for what was read.
        wrong = controller.post(f"{rates}{rate['id']}/confirm/", {**rate, "rate_percent": "6.75"}, format="json")
        self.assertEqual(wrong.status_code, 400, wrong.content)
        confirmed = controller.post(f"{rates}{rate['id']}/confirm/", rate, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

        found = ap.get(statement, query).json()
        [row] = found["rows"]
        self.assertEqual((row["accepted_on"], row["due"], row["paid_late"], row["interest_paid_late"],
                          row["interest_unpaid"], row["interest"]),
                         ("2026-06-01", "2026-07-01", "4000.00", "29.92", "96.16", "126.08"))
        self.assertEqual(found["totals"]["interest"], "126.08")
        self.assertEqual(found["bank_rates"][0]["interest_percent"], "19.5000")
        # The year's note runs to the year's end, not the day asked: the 6,000 unpaid through 31 March 2027.
        self.assertEqual(found["note"]["principal_unpaid"], "6000.00")
        self.assertEqual(found["note"]["paid_after_the_day"], "4000.00")
        # The list as it was is still there, with the interest beside each part.
        [plain] = ap.get("/api/purchasing/purchasing-reports/msme/", query).json()
        self.assertEqual((plain["days_late"], plain["interest"]), (30, "126.08"))
        # The bill says the day it counts from.
        self.assertEqual(ap.get(f"/api/purchasing/bills/{bill.pk}/").json()["accepted_on"], "2026-06-01")

        # Edit: the corrected figure waits again, and the list stops showing a figure. Delete: only then.
        self.assertEqual(controller.delete(f"{rates}{rate['id']}/").status_code, 400)
        edited = controller.patch(f"{rates}{rate['id']}/", {"rate_percent": "6.75"}, format="json")
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertIsNone(edited.json()["confirmed_on"])
        self.assertIsNone(ap.get(statement, query).json()["totals"]["interest"])
        self.assertEqual(controller.delete(f"{rates}{rate['id']}/").status_code, 204)
