"""
O318: one reader for a number from a request (apps.core.api.number), and
the audit check that names a Decimal made from a request around it.
"""
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from .api import ANY_SIGN, NOT_NEGATIVE, number


class ANumberFromARequestTests(SimpleTestCase):
    def refused(self, value, places=2, **options):
        with self.assertRaises(ValidationError) as caught:
            number({"x": value}, "x", places, **options)
        self.assertEqual(list(caught.exception.detail), ["x"])  # beside the field
        return str(caught.exception.detail["x"][0])

    def test_what_is_not_a_finite_number_is_refused_beside_the_field(self):
        for value in ("NaN", "Infinity", "-Infinity", "sNaN", "banana", True, [1], {"a": 1}):
            self.assertIn("is not a number", self.refused(value, sign=ANY_SIGN), value)

    def test_a_sign_the_field_does_not_take(self):
        self.assertEqual(self.refused("0"), "It must be above nothing.")
        self.assertEqual(self.refused("-5", sign=NOT_NEGATIVE), "It cannot be below nothing.")
        self.assertEqual(number({"x": "0"}, "x", 2, sign=NOT_NEGATIVE), Decimal("0"))
        self.assertEqual(number({"x": "-5"}, "x", 2, sign=ANY_SIGN), Decimal("-5"))

    def test_more_places_than_the_field_holds_or_too_large_a_figure(self):
        self.assertEqual(self.refused("1.005"), "It holds 2 places at most.")
        self.assertEqual(self.refused("1.5", places=0), "It is a whole number.")
        self.assertIn("too large", self.refused("1E+30"))
        self.assertEqual(number({"x": "1.50"}, "x", 2), Decimal("1.50"))
        self.assertEqual(str(number({"x": "1E+3"}, "x", 2)), "1000")

    def test_only_a_figure_as_a_screen_writes_it(self):
        """review_sechr F4: Decimal() also took 1_0, Arabic-Indic digits and a figure in spaces."""
        for value in ("1_0", "\u0661\u0662", " 7", "7 ", "7\n", "1,000", "0x10", "+-1", "1e", "."):
            self.assertIn("is not a number", self.refused(value, sign=ANY_SIGN), repr(value))
        self.assertEqual(number({"x": 7}, "x", 2), Decimal("7"))
        self.assertEqual(number({"x": 0.25}, "x", 2), Decimal("0.25"))
        self.assertEqual(number({"x": "-.5"}, "x", 2, sign=ANY_SIGN), Decimal("-0.5"))

    def test_the_cap_is_the_columns(self):
        """review_sechr F3: a journal line holds 18 digits, 2 of them places: 16 before the point, not 18."""
        from apps.accounting.models import JournalLine

        debit = JournalLine._meta.get_field("debit")
        self.assertEqual((debit.max_digits, debit.decimal_places), (18, 2))
        for big in ("99999999999999999.00", "10000000000000000"):  # 17 digits before the point
            with self.assertRaises(ValidationError) as caught:
                number({"x": big}, "x", field=debit)
            self.assertIn("16 digits before the point", str(caught.exception.detail["x"][0]))
        self.assertEqual(number({"x": "9999999999999999.99"}, "x", field=debit), Decimal("9999999999999999.99"))
        self.assertIn("too large", self.refused("1000", places=2, digits=5))  # a percentage column, 999.99 at most
        self.assertEqual(number({"x": "999.99"}, "x", 2, digits=5), Decimal("999.99"))

    def test_absent_is_the_default_or_refused(self):
        self.assertEqual(self.refused(None), "Give it as a number.")
        self.assertEqual(self.refused("  "), "Give it as a number.")
        self.assertIsNone(number({}, "x", 2, default=None))
        self.assertEqual(number({}, "x", 4, default="1"), Decimal("1"))


class TheAuditNamesADecimalMadeFromARequestTests(SimpleTestCase):
    def findings(self, text):
        from apps.core.management.commands.audit_invariants import Command

        return [detail.split(" ")[0] for _shape, detail in Command().numbers_read_around_the_reader(
            ["assets"], {"assets": {Path("apps/assets/views.py"): text}})]

    def test_a_planted_instance_is_reported_in_each_spelling(self):
        planted = {
            "def dispose(self, request):\n    return Decimal(str(request.data.get('proceeds') or '0'))\n": 2,
            "def when(self, request):\n    return Decimal(request.query_params['quantity'])\n": 2,
            "def figures(self, request):\n    for name in ('a', 'b'):\n        value = request.data.get(name)\n"
            "        x = Decimal(str(value))\n": 4,
            "def labour(self, request):\n    data = request.data\n    return Decimal(str(data.get('minutes')))\n": 3,
            "def rows(self, request):\n    return [Decimal(str(row.get('quantity'))) for row in "
            "request.data.get('made') or []]\n": 2,
            # review_sechr F2, as apps/web/api.py had it: the request's query handed to a helper.
            "def _margin(params, name, default):\n    try:\n        value = Decimal(params.get(name) or default)\n"
            "    except InvalidOperation:\n        raise ValidationError({name: ['A margin is a percentage.']})\n"
            "    return value\n\n\nclass BankStockStatementView(viewsets.ViewSet):\n"
            "    def list(self, request):\n        params = request.query_params\n"
            "        return Response(stock_statement(stock_margin=_margin(params, 'stock_margin', '25')))\n": 3,
        }
        for text, line in planted.items():
            self.assertEqual(self.findings(text), [f"assets/views.py:{line}"], text)

    def test_a_figure_computed_from_a_request_is_not_a_figure_read_from_it(self):
        for passes in (
                "def ledger(self, request):\n    start = request.query_params.get('from')\n"
                "    before = movement(start)\n    return Decimal(before['debit'])\n",
                "def days(request):\n    return Decimal(working_days(request.start_date, request.end_date))\n",
                "def dispose(self, request):\n    return number(request.data, 'proceeds', 2)\n"):
            self.assertEqual(self.findings(passes), [], passes)

    def test_the_code_as_it_stands_reads_every_number_with_the_reader(self):
        from apps.core.management.commands.audit_invariants import Command, source_of

        # Every app, the office's own reports (apps/web) included: the check read only OUR_APPS (review_sechr F2).
        every = sorted(path.name for path in Path("apps").iterdir() if (path / "__init__.py").exists())
        self.assertIn("web", every)
        self.assertEqual(Command().numbers_read_around_the_reader(every, {label: source_of(label) for label in every}),
                         [])
        self.assertEqual(sorted(Command.NUMBERS_READ_ON_PURPOSE), ["hr/payroll_views.py:calculate"])
