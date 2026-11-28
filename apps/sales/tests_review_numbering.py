"""Review probe O257: a posting that failed leaves its number on the object in hand."""
from unittest import mock

from .models import Invoice
from .tests import SalesTestCase


class ReviewNumbering(SalesTestCase):
    def test_a_failed_post_retried_on_the_same_object_does_not_reuse_a_number_given_back(self):
        failing, other = self.make_invoice(), self.make_invoice()
        with mock.patch.object(Invoice, "_rate_for_posting", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                failing.post()
        # the number was given back with the rollback; the object in hand must not still carry it
        self.assertEqual(failing.number, "", "a rolled-back posting left its number on the object")
        other.post()
        try:
            failing.post()
        except Exception as exc:  # noqa
            self.fail(f"retry on the same object failed: {exc!r}")
        self.assertNotEqual(failing.number, other.number)

    def test_the_retry_on_the_same_object_after_another_took_the_number_gives_two_invoices_one_number(self):
        failing, other = self.make_invoice(), self.make_invoice()
        with mock.patch.object(Invoice, "_rate_for_posting", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                failing.post()
        other.post()
        failing.post()
        self.assertEqual(Invoice.objects.filter(number=other.number).count(), 1, (failing.number, other.number))
