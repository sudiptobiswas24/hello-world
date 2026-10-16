"""
Tax collected at source: the company collects it from a buyer on top of
the sale and pays it over by challan. The plant's case is scrap (section
206C(1) of the 1961 Act; section 394 of the Income-tax Act 2025 from
1 April 2026).

Everything the law sets is data with the day it applies from, never a
figure written here: which goods (an item names its section,
inventory.Item.tcs_section), the rate on a buyer with a PAN and without
one (TcsRate, by the day it applies from), whether the base takes in the
GST charged, and the buyer's declaration that the goods are for its own
manufacture and not for trading (Form 127 from April 2026; Form 27C before),
which exempts. A rate is collected at only once the accountant has
confirmed it: a figure seeded by a setup command, or typed by anyone else,
waits for that, and a scrap sale meanwhile is refused in words rather
than collected at a guess.

What one invoice collected is kept on it (sales.tcs.TcsCollection), as
the rate and declaration stood. The challan that pays it over is the TDS
challan's mechanism (challans.pay_over), shared so the two cannot drift.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel

from .dated_rates import DatedRate, rate_standing


class TcsSection(AuditModel):
    code = models.CharField(max_length=16, unique=True, help_text="206C(1), or 394 from April 2026")
    name = models.CharField(max_length=128, help_text="What it is collected on: scrap.")
    form_code = models.CharField(
        max_length=8, blank=True,
        help_text="The collection code the quarterly return (Form 143, was 27EQ) files it under.")
    base_includes_gst = models.BooleanField(
        default=True, help_text="Collected on the sale value with the GST charged on it, as the accountant reads "
                                "the section; off, on the taxable value alone.")
    payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where tax collected is owed until a challan pays it over.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "TCS section"

    def __str__(self):
        return f"{self.code} {self.name}"

    def save(self, *args, **kwargs):
        from .money import refuse_money_kept_as, settings_fields

        refuse_money_kept_as(self, *settings_fields(TcsSection))
        super().save(*args, **kwargs)

    def code_on(self, day):
        """The section the law names on `day` (TcsRate.section_code), whether or not its rate is confirmed."""
        rate = self.rates.filter(applies_from__lte=day).order_by("-applies_from", "-pk").first()
        return (rate.section_code if rate else "") or self.code

    def rate_on(self, day):
        """The rate row that applies on `day`, confirmed by the accountant; refused in words otherwise."""
        return rate_standing(self, day, "TCS", "collected", "TCS rates")


class TcsRate(DatedRate, AuditModel):
    section = models.ForeignKey(TcsSection, on_delete=models.PROTECT, related_name="rates")
    applies_from = models.DateField(help_text="The first day the law sets this rate for.")
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4,
                                       help_text="Collected from a buyer whose PAN is on file.")
    no_pan_rate_percent = models.DecimalField(
        max_digits=7, decimal_places=4,
        help_text="Collected from a buyer with no PAN on file (section 206CC: twice the rate or five per cent, "
                  "whichever is higher, as the law stands).")
    authority = models.CharField(max_length=128, blank=True,
                                 help_text="Where the rate comes from: the section, the notification.")
    section_code = models.CharField(
        max_length=16, blank=True,
        help_text="The section the law names from this day, as the invoice prints it: 206C(1) of the 1961 Act "
                  "until 31 March 2026, 394(1) of the 2025 Act from 1 April 2026. Empty: the section's code.")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    confirmed_on = models.DateField(null=True, blank=True, editable=False,
                                    help_text="When the accountant confirmed it. Until then nothing is "
                                              "collected at it.")

    class Meta:
        ordering = ["section", "-applies_from"]
        verbose_name = "TCS rate"
        permissions = [("confirm_tcsrate", "Can confirm a TCS rate as the law's")]
        constraints = [
            models.UniqueConstraint(fields=["section", "applies_from"], name="one_tcs_rate_per_section_and_day"),
            models.CheckConstraint(check=Q(rate_percent__gte=0) & Q(no_pan_rate_percent__gte=0),
                                   name="tcs_rates_not_negative"),
        ]

    def __str__(self):
        return f"{self.section.code} {self.rate_percent.normalize():f}% from {self.applies_from}"

    # The dated-rate rules, shared with TDS (accounting.dated_rates).
    FIGURES = ("section_id", "applies_from", "rate_percent", "no_pan_rate_percent", "section_code")
    TAKEN_BY = "collections"
    TAKEN_WORDS = "Invoices collected"


class TcsDeclaration(AuditModel):
    """The buyer's declaration (Form 127; Form 27C before April 2026) that exempts it from collection."""

    party = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="tcs_declarations")
    section = models.ForeignKey(TcsSection, on_delete=models.PROTECT, related_name="declarations")
    form = models.CharField(max_length=8, default="127", help_text="127, or 27C before April 2026.")
    pan = models.CharField(
        max_length=10, blank=True,
        help_text="The buyer's PAN as the declaration gives it. Without one it exempts nothing: the law has the "
                  "seller collect from a buyer whose declaration carries no PAN.")
    reference = models.CharField(max_length=64, blank=True, help_text="Its number or the buyer's reference.")
    received_on = models.DateField()
    valid_from = models.DateField()
    valid_to = models.DateField(help_text="Usually the financial year's last day.")

    class Meta:
        ordering = ["-valid_from", "-id"]
        verbose_name = "TCS declaration"
        constraints = [models.CheckConstraint(check=Q(valid_to__gte=models.F("valid_from")),
                                              name="tcs_declaration_ends_after_it_starts")]

    def __str__(self):
        return f"Form {self.form} from {self.party} for {self.section.code}"

    def save(self, *args, **kwargs):
        from .tds import validate_pan

        try:
            self.pan = validate_pan(self.pan)
        except ValidationError:
            raise ValidationError({"pan": [
                f"A Form {self.form} declaration exempts a buyer only with its PAN on it, and {self.pan or 'none'} "
                "is not one (five letters, four digits, a letter). Without it the plant collects, at the rate for "
                "a buyer with no PAN."]}) from None
        if not self._state.adding and self.collections.exists():
            before = TcsDeclaration.objects.get(pk=self.pk)
            if any(getattr(before, name) != getattr(self, name)
                   for name in ("party_id", "section_id", "valid_from", "received_on", "pan")):
                raise ValidationError("Invoices were exempted under this declaration as it stood; record a new one.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.collections.exists():
            raise ValidationError("Invoices were exempted under this declaration; it stays.")
        return super().delete(*args, **kwargs)

    @classmethod
    def covering(cls, party, section, day):
        # One recorded without a PAN (before the PAN was asked) is not honoured: it exempts nothing.
        return cls.objects.filter(party=party, section=section, valid_from__lte=day, valid_to__gte=day,
                                  received_on__lte=day).exclude(pan="").order_by("-valid_from", "-pk").first()


class SeededDefault(AuditModel):
    """
    Something a setup command made for the accountant to review (setup_indian_books): an account, a
    tax, a position, a rate. Listed until reviewed; what it names is the accountant's to keep or change.
    """

    model = models.CharField(max_length=100, help_text="app_label.model of what was seeded.")
    object_id = models.PositiveBigIntegerField()
    label = models.CharField(max_length=255)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    reviewed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["reviewed_on", "id"]
        verbose_name = "seeded default"
        constraints = [models.UniqueConstraint(fields=["model", "object_id"], name="one_seeded_default_per_record")]

    def __str__(self):
        return f"{self.label} ({'reviewed' if self.reviewed_on else 'to review'})"
