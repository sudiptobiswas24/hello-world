"""
Tax deducted at source: the sections, and the arithmetic of what one
bill owes under one.

Two shapes of threshold, and they are not the same rule twice:

- On the excess (194Q, goods bought): nothing until the year's purchases
  from a seller pass the threshold, then tax on what they pass it by.
- On the whole (194C contractors, 194J professionals, 194I rent): nothing
  until one bill passes the single limit or the year passes the annual
  one, and then on everything in the year not yet taxed, earlier bills
  included. The catch-up is the rule, not a correction.

Tax is a whole number of rupees (section 288B), rounded half up. The base
is the taxable value, GST left out, as the CBDT reads it when the bill
shows GST separately.

The rates and thresholds are the plant's to keep: they change with every
budget, and a rate written here would be wrong by the next one.
"""

import datetime
import re
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel

PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def rupees(amount):
    return Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def financial_year(day):
    """The April-to-March year a day falls in, as (first day, last day)."""
    start = datetime.date(day.year if day.month >= 4 else day.year - 1, 4, 1)
    return start, datetime.date(start.year + 1, 3, 31)


def validate_pan(value):
    pan = (value or "").strip().upper()
    if not PAN.match(pan):
        raise ValidationError(f"{value!r} is not a PAN: five letters, four digits, a letter.")
    return pan


class ThresholdMode(models.TextChoices):
    EXCESS = "excess", "On what the year passes the threshold by (194Q)"
    WHOLE = "whole", "On the whole year once a limit is passed (194C, 194J)"


class TdsSection(AuditModel):
    code = models.CharField(max_length=16, unique=True, help_text="194Q, 194C, 194J")
    name = models.CharField(max_length=128)
    rate_percent = models.DecimalField(
        max_digits=7, decimal_places=4, help_text="Deducted from a party whose PAN is on file.")
    no_pan_rate_percent = models.DecimalField(
        max_digits=7, decimal_places=4,
        help_text="Deducted from a party with no PAN on file (section 206AA).")
    mode = models.CharField(max_length=8, choices=ThresholdMode.choices, default=ThresholdMode.WHOLE)
    single_threshold = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="One bill above this is taxed. Empty for none.")
    annual_threshold = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="The year's bills from one party above this are taxed. Empty for none.")
    payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where tax the company deducts is owed until a challan pays it over.")
    receivable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where tax a customer deducted waits to be claimed against Form 26AS.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(rate_percent__gte=0) & Q(no_pan_rate_percent__gte=0),
                                   name="tds_rates_not_negative"),
            models.CheckConstraint(
                check=~Q(mode="excess") | Q(annual_threshold__isnull=False),
                name="tds_excess_has_an_annual_threshold"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    def owed(self, rate, this, year_before, untaxed_before):
        """
        (base, tax) on a bill of `this`, with `year_before` already billed
        by the party this year and `untaxed_before` of it never taxed.
        """
        this, year_before = Decimal(this), Decimal(year_before)
        if self.mode == ThresholdMode.EXCESS:
            limit = self.annual_threshold
            base = max(Decimal("0"), year_before + this - limit) - max(Decimal("0"), year_before - limit)
        else:
            crossed = (
                (self.single_threshold is None and self.annual_threshold is None)
                or (self.single_threshold is not None and this > self.single_threshold)
                or (self.annual_threshold is not None and year_before + this > self.annual_threshold)
            )
            base = Decimal(untaxed_before) + this if crossed else Decimal("0")
        return base, rupees(base * Decimal(rate) / 100)


def deduction_terms(party, section=None):
    """
    (section, rate, PAN) a party is deducted at: the section given, or the
    one on its profile; the rate on its lower-deduction certificate, its
    section's, or the no-PAN rate.
    """
    profile = getattr(party, "tax_profile", None)
    section = section or (profile.tds_section if profile else None)
    if section is None:
        raise ValidationError(f"{party} has no TDS section on its tax profile; say which section.")
    if not section.is_active:
        raise ValidationError(f"{section} is no longer in use.")
    pan = profile.pan_on_file() if profile else ""
    if profile and profile.tds_rate_percent is not None and profile.tds_section_id == section.pk:
        return section, profile.tds_rate_percent, pan
    return section, (section.rate_percent if pan else section.no_pan_rate_percent), pan
