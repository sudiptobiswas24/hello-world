"""
A rate the law sets, from the day it applies: TCS (accounting.tcs.TcsRate)
and TDS (accounting.tds.TdsRate) keep theirs the one way, here, so the two
cannot drift.

Each rate is a row with the day it applies from, the rate on a party with a
PAN on file and on one without, and the accountant's confirmation. Nothing
is taken at a rate until it is confirmed: a figure seeded by a setup command
or typed by anyone else waits, and the document meanwhile is refused in
words rather than taxed at a guess. A rate something was taken at is a
fact: changing its figures is refused, and a new rate is a new row from the
day it applies. What a document took is kept on it, with the row, as the
rate stood (O233: TDS rates were a table of today's figures).
"""

from django.core.exceptions import ValidationError
from django.utils import timezone


class DatedRate:
    """
    The rules of a dated rate row. The model has `section` (its rates as
    `section.rates`), `applies_from`, `rate_percent`, `no_pan_rate_percent`,
    `confirmed_by` and `confirmed_on`, and names:
      FIGURES, the fields a confirmation vouches for;
      TAKEN_BY, the related name of what was taken at the rate;
      TAKEN_WORDS, those documents in words ("Invoices collected").
    """

    FIGURES = ("section_id", "applies_from", "rate_percent", "no_pan_rate_percent")
    TAKEN_BY = ""
    TAKEN_WORDS = ""

    def taken(self):
        return getattr(self, self.TAKEN_BY).exists()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = type(self).objects.get(pk=self.pk)
            changed = any(getattr(before, name) != getattr(self, name) for name in self.FIGURES)
            if changed and self.taken():
                raise ValidationError(f"{self.TAKEN_WORDS} at this rate; a new rate is a new row from the day "
                                      "it applies.")
            if changed:
                # A changed figure is a new figure: the accountant confirms it again.
                self.confirmed_by, self.confirmed_on = None, None
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.taken():
            raise ValidationError(f"{self.TAKEN_WORDS} at this rate; it stays.")
        return super().delete(*args, **kwargs)

    def confirm(self, user):
        """The accountant's word that the rate is the law's: from now it is taken at."""
        self.confirmed_by, self.confirmed_on = user, timezone.localdate()
        super().save(update_fields=["confirmed_by", "confirmed_on", "updated_at"])

    def percent_for(self, pan):
        """The rate on a party with `pan` on file, or the no-PAN rate."""
        return self.rate_percent if pan else self.no_pan_rate_percent


def rate_standing(section, day, tax, taken, screen):
    """
    The rate row of `section` that applies on `day`, confirmed by the
    accountant; refused in words otherwise. `tax` is "TCS" or "TDS", `taken`
    what is done at it ("collected", "deducted"), `screen` where it is kept.
    """
    # The words name the tax, its section and the screen it is confirmed on: a supply the law taxes at source
    # is never posted taking nothing (g22, integrator's decision), and the person refused must know where to go.
    rate = section.rates.filter(applies_from__lte=day).order_by("-applies_from", "-pk").first()
    if rate is None:
        raise ValidationError(f"{tax} under {section.code} has no rate from {day:%d %b %Y} or before, so this "
                              f"cannot be posted: the accountant enters the rate the law sets and the day it "
                              f"applies from, under Settings > {screen}.")
    if rate.confirmed_on is None:
        raise ValidationError(
            f"{tax} under {section.code} at {rate.rate_percent.normalize():f}% from {rate.applies_from:%d %b %Y} "
            f"is not confirmed by the accountant, so this cannot be posted and nothing is {taken} at it yet: "
            f"confirm it, or correct it, under Settings > {screen}.")
    return rate
