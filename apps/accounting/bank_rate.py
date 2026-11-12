"""
The bank rate the Reserve Bank of India notifies, from the day each rate
applies: what the MSMED Act 2006 s.16 trebles for a micro or small
supplier paid late.

It is notified from time to time, so it is a dated row the plant keeps and
its accountant confirms, never a figure written in the code. Nothing is
worked at a rate until it is confirmed: the MSME list says "not confirmed"
in place of a guess. Nothing is ever posted at it (the interest is a
disclosure, s.22, and a disallowance, s.23), so a row can be corrected;
a corrected figure waits for the accountant again (accounting.dated_rates).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel

from .dated_rates import DatedRate


class BankRate(DatedRate, AuditModel):
    """The Reserve Bank's bank rate from a day, in percent a year; read only once the accountant confirms it."""

    applies_from = models.DateField(unique=True, help_text="The first day the Reserve Bank's notification sets "
                                                           "this rate for.")
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4, help_text="The bank rate, percent a year.")
    authority = models.CharField(max_length=128, blank=True,
                                 help_text="The Reserve Bank's notification or press release it comes from.")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    confirmed_on = models.DateField(null=True, blank=True, editable=False,
                                    help_text="When the accountant confirmed it. Until then no interest is "
                                              "worked at it.")

    # The dated-rate rules (accounting.dated_rates). Nothing is taken at a bank rate: it is read, never posted.
    FIGURES = ("applies_from", "rate_percent")

    class Meta:
        ordering = ["-applies_from"]
        permissions = [("confirm_bankrate", "Can confirm a bank rate as the Reserve Bank's")]
        constraints = [models.CheckConstraint(check=Q(rate_percent__gte=0), name="bank_rate_not_negative")]

    def __str__(self):
        return f"Bank rate {self.rate_percent.normalize():f}% from {self.applies_from}"

    def taken(self):
        return False

    def save(self, *args, **kwargs):
        # Asked here, not in clean(): a serializer runs neither clean() nor the constraint's words.
        if self.rate_percent is not None and self.rate_percent < 0:
            raise ValidationError({"rate_percent": ["A bank rate is not negative."]})
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.confirmed_on is not None:
            raise ValidationError(
                f"{self} is confirmed, and the MSME interest shown since was worked at it. Correct its figures "
                "(it then waits to be confirmed again), or change them first and remove it unconfirmed.")
        return super().delete(*args, **kwargs)


def bank_rates():
    """Every bank rate row, oldest first, as (applies_from, rate_percent, confirmed): read once for a list."""
    return [(row.applies_from, row.rate_percent, row.confirmed_on is not None)
            for row in BankRate.objects.order_by("applies_from", "pk")]
