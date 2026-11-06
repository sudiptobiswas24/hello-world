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


def bars_on_the_page(pdf, height):
    """
    Every filled rectangle `height` points tall in the PDF's page streams, as
    (x, width), left to right: what a scanner sweeping across the number sees.
    """
    import base64
    import re
    import zlib

    found = []
    for stream in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = stream.group(1).strip()
        for inflate in (lambda data: data, zlib.decompress,
                        lambda data: zlib.decompress(base64.a85decode(data.strip(), adobe=True))):
            try:
                text = inflate(raw)
            except Exception:  # noqa: BLE001 - not this encoding: try the next
                continue
            if b" re" in text:
                break
        else:
            continue
        for x, _, width, tall in re.findall(rb"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) re", text):
            if abs(float(tall) - height) < 0.01:
                found.append((float(x), float(width)))
    return sorted(found)


def decoded(bits):
    """Code 128 read back from modules (1 bar, 0 space), checksum checked, as a scanner reads it."""
    from apps.core.barcode import START_B, STOP, WIDTHS

    bits = bits.strip("0")
    symbols = []
    while bits:
        size = 13 if len(bits) == 13 else 11
        chunk, bits = bits[:size], bits[size:]
        widths, run = [], 1
        for before, after in zip(chunk, chunk[1:]):
            if before == after:
                run += 1
            else:
                widths.append(run)
                run = 1
        widths.append(run)
        symbols.append([tuple(int(w) for w in entry) for entry in WIDTHS].index(tuple(widths)))
    start, *body, checksum, stop = symbols
    assert (start, stop) == (START_B, STOP), (start, stop)
    assert checksum == (START_B + sum(i * v for i, v in enumerate(body, start=1))) % 103, "checksum"
    return "".join(chr(value + 32) for value in body)


class TheDocumentNumberScansTests(TestCase):
    """
    O436: printed documents drew their number with reportlab's own Code 128, a
    second drawer beside apps/core/barcode.py. The bars on the page are now the
    one drawer's, and they read back as the number.
    """

    def test_the_bars_on_the_page_are_the_one_drawers_and_read_back_as_the_number(self):
        from reportlab.lib.units import mm

        from apps.core import barcode

        inr = Currency.objects.create(code="INR", name="Rupee", symbol="₹", is_base=True)
        Company.objects.create(name="Deccan Polysacks", base_currency=inr)
        customer = Party.objects.create(code="C-1", name="Shree Cement")
        number = "SO-2026-0042"
        pdf = render_document(heading="Sales order", document=None, party=customer, address=None, number=number,
                              meta=[["Number", number]], totals=[["Total", Decimal("10.00")]], currency=inr,
                              table=(["What", "Amount"], [["A line", "10.00"]], [120, 54]))
        module = 0.3 * mm
        bars = bars_on_the_page(pdf, 9 * mm)
        self.assertTrue(bars, "no bars on the page")
        # Rebuild the modules from the drawn rectangles, from the first bar's left edge.
        left = bars[0][0]
        width = round((bars[-1][0] + bars[-1][1] - left) / module)
        drawn = ["0"] * width
        for x, w in bars:
            first = round((x - left) / module)
            drawn[first:first + round(w / module)] = "1" * round(w / module)
        drawn = "".join(drawn)
        self.assertEqual(drawn, barcode.modules(number))
        self.assertEqual(decoded(drawn), number)
