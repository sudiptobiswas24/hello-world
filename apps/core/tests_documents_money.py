"""
O409: every PDF printed money with a black box for the rupee sign (the font
has no glyph), the western way (1,764,000.00 under words saying "seventeen
lakh"), and a tax invoice's amounts broke onto a second line from ten lakh.
"""

from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.core.documents import counted, grouped, money


class Currency:
    def __init__(self, code, symbol):
        self.code, self.symbol = code, symbol


class MoneyOnPaperTests(SimpleTestCase):
    def test_rupees_are_grouped_in_lakhs_and_crores_with_no_sign(self):
        inr = Currency("INR", "₹")
        self.assertEqual(money(D("1764000"), inr), "17,64,000.00")
        self.assertEqual(money(D("17957520"), inr), "1,79,57,520.00")
        self.assertEqual(money(D("999.5"), inr), "999.50")
        self.assertEqual(money(D("-2081520"), inr), "-20,81,520.00")
        self.assertNotIn("₹", money(D("1"), inr))

    def test_nothing_less_than_a_paisa_prints_as_minus_nothing(self):
        self.assertEqual(grouped(D("-0.001")), "0.00")

    def test_another_currency_keeps_its_sign_and_the_western_grouping(self):
        self.assertEqual(money(D("1764000"), Currency("USD", "$")), "$1,764,000.00")
        # A sign the letterhead font cannot draw is its code instead.
        self.assertEqual(money(D("1500"), Currency("AED", "د.إ")), "AED 1,500.00")

    def test_a_whole_quantity_prints_whole(self):
        self.assertEqual(counted(D("1800000.0000")), "18,00,000")
        self.assertEqual(counted(D("487.3096")), "487.3096")
        self.assertEqual(counted(D("12.50")), "12.5")
