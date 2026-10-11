"""The shared document layout: the amount in words a tax invoice carries, and the letterhead."""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from .documents import in_words, render_document, rupees_in_words
from .models import Address, AddressType, Company, Currency, Party


class AmountInWordsTests(SimpleTestCase):
    def test_the_indian_way(self):
        self.assertEqual(in_words(0), "zero")
        self.assertEqual(in_words(21), "twenty-one")
        self.assertEqual(in_words(101), "one hundred one")
        self.assertEqual(in_words(1001), "one thousand one")
        self.assertEqual(in_words(100_000), "one lakh")
        self.assertEqual(in_words(1_230_000), "twelve lakh thirty thousand")
        self.assertEqual(in_words(10_000_000), "one crore")
        self.assertEqual(in_words(123_456_789),
                         "twelve crore thirty-four lakh fifty-six thousand seven hundred eighty-nine")

    def test_rupees_and_paise(self):
        self.assertEqual(rupees_in_words("12345.67"),
                         "Rupees twelve thousand three hundred forty-five and sixty-seven paise only")
        self.assertEqual(rupees_in_words(Decimal("1230000.50")), "Rupees twelve lakh thirty thousand and fifty paise only")
        self.assertEqual(rupees_in_words("100000"), "Rupees one lakh only")
        self.assertEqual(rupees_in_words("0.005"), "Rupees zero and one paise only")
        self.assertEqual(rupees_in_words("-5.25"), "Minus Rupees five and twenty-five paise only")


class LetterheadTests(TestCase):
    def test_a_company_with_an_address_and_a_gstin_still_renders(self):
        inr = Currency.objects.create(code="INR", name="Rupee", symbol="₹", is_base=True)
        plant = Party.objects.create(code="SELF", name="Deccan Polysacks")
        here = Address.objects.create(party=plant, address_type=AddressType.BILLING, line1="Plot 7, MIDC",
                                      city="Nanded", state="Maharashtra", postal_code="431603")
        Company.objects.create(name="Deccan Polysacks", base_currency=inr, address=here, tax_id="27AAAAA0000A1Z5")
        customer = Party.objects.create(code="C-1", name="Shree Cement")
        pdf = render_document(heading="Note", document=None, party=customer, address=None,
                              meta=[["Number", "N-1"]], totals=[["Total", Decimal("10.00")]], currency=inr,
                              table=(["What", "Amount"], [["A line", "10.00"]], [120, 54]))
        self.assertTrue(pdf.startswith(b"%PDF-"))
