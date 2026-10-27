"""
Tax depreciation by block of assets: a second schedule beside the books, never posted (g24).

The books keep straight line under the Companies Act (Schedule II). The Income-tax Act computes
depreciation on the written-down value of each block of assets, at the block's rate:
  - Income-tax Act, 2025 (from tax year 2026-27, 1 April 2026): section 33, deduction for
    depreciation; the percentage is prescribed by the rules (the Income-tax Rules, 2026); an asset
    acquired in the year and put to use for less than 180 days in it is allowed half. Section 74
    (the successor of the 1961 Act's section 50): where the money received for what is sold in a
    year exceeds the opening WDV and the year's additions, or where every asset of the block is sold
    in the year, the result is a capital gain (or loss) on short-term capital assets.
  - Income-tax Act, 1961 (years before 1 April 2026): section 32(1)(ii) and its second proviso (the
    180-day half rate), section 43(6) (written-down value of a block), section 2(11) (block of
    assets), section 50 (capital gains on depreciable assets); rates in Appendix I to the Income-tax
    Rules, 1962 (rule 5).
Neither the official text (incometaxindia.gov.in) nor the 2026 rules could be read from this machine;
the rates are loaded unconfirmed (`load_tax_depreciation_rates`) and nothing is filed until the
accountant confirms the rate a block is filed at (accounting.dated_rates). Additional depreciation
(1961 s.32(1)(iia); 2025 s.33, as reported) is not computed: it depends on the regime the company
pays under (not available under section 115BAA), which is the accountant's to say.

What is decided here, for the accountant to confirm:
  - An asset enters its block in the tax year it is put to use (in_service_date), at its cost.
  - Days used are counted from the day it was put to use to the year's last day, both included;
    fewer than 180 takes half the rate, 180 or more the full rate.
  - What a sale brings in reduces the block (`disposal_proceeds`, kept on the asset as it is disposed
    of); transfer expenses are not deducted (none are recorded).
  - Sales come off the full-rate part of the block first, and off the half-rate additions only for
    what is left.
  - Figures are kept to the paisa; the return rounds.

Deferred tax: a company under AS 22 (Accounting for Taxes on Income) or Ind AS 12 recognises deferred
tax on the timing difference between book and tax depreciation; the schedule shows both the year's
difference and the difference between the block's closing WDV and the book value of its assets, for
the accountant to work the deferred tax from.

A year's schedule, once filed, is kept as it was filed (mistake 4): its lines are the figures, and an
asset event dated in a filed year (in service, disposed of, reinstated, moved to another block) is
refused. Its reverse is a withdrawal for a revised return, refused once the next year is filed on it.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.dated_rates import DatedRate
from apps.accounting.models import round_money
from apps.core.models import AuditModel, lock_rows, to_date

HALF_RATE_BELOW_DAYS = 180


def tax_year_of(day):
    """The first day of the Indian tax year (1 April to 31 March) `day` falls in."""
    day = to_date(day)
    return datetime.date(day.year if day.month >= 4 else day.year - 1, 4, 1)


def year_end(year_start):
    return datetime.date(year_start.year + 1, 3, 31)


def days_used(put_to_use, year_start):
    """Days in the tax year from the day it was put to use to the year's last day, both counted."""
    return (year_end(year_start) - to_date(put_to_use)).days + 1


class TaxBlock(AuditModel):
    """A block of assets: assets of one class the law depreciates at one rate."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class TaxBlockRate(DatedRate, AuditModel):
    """The block's rate from the day the law sets it; confirmed by the accountant before a year is filed at it."""

    section = models.ForeignKey(TaxBlock, on_delete=models.PROTECT, related_name="rates")
    applies_from = models.DateField(help_text="The first day of the tax year the rate applies for.")
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4)
    authority = models.CharField(max_length=255, blank=True,
                                 help_text="Where the rate comes from: the Act, the section, the rule.")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    confirmed_on = models.DateField(null=True, blank=True, editable=False)

    FIGURES = ("section_id", "applies_from", "rate_percent")
    TAKEN_BY = "filed_lines"
    TAKEN_WORDS = "A tax depreciation schedule is filed"

    class Meta:
        ordering = ["section", "-applies_from"]
        permissions = [("confirm_taxblockrate", "Can confirm a tax depreciation rate as the law's")]
        constraints = [
            models.UniqueConstraint(fields=["section", "applies_from"], name="one_tax_block_rate_a_day"),
            models.CheckConstraint(check=Q(rate_percent__gte=0) & Q(rate_percent__lte=100),
                                   name="tax_block_rate_a_percentage"),
        ]

    def __str__(self):
        return f"{self.section.code} {self.rate_percent.normalize():f}% from {self.applies_from}"

    def taken(self):
        return self.filed_lines.filter(year__withdrawn_on__isnull=True).exists()


class TaxBlockOpening(AuditModel):
    """The block's written-down value at the start of a tax year, from the last return: where the schedule starts."""

    block = models.ForeignKey(TaxBlock, on_delete=models.PROTECT, related_name="openings")
    year_start = models.DateField(help_text="1 April of the tax year it opens.")
    written_down_value = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["block", "-year_start"]
        constraints = [
            models.UniqueConstraint(fields=["block", "year_start"], name="one_tax_opening_a_year"),
            models.CheckConstraint(check=Q(written_down_value__gte=0), name="tax_opening_not_negative"),
        ]

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.year_start = to_date(self.year_start)
        if self.year_start != tax_year_of(self.year_start) or self.year_start.month != 4 or self.year_start.day != 1:
            raise ValidationError({"year_start": ["A tax year opens on 1 April."]})
        refuse_in_filed_year(self.year_start, f"The opening of {self.block.code} is not changed")
        if self.pk:
            stored = TaxBlockOpening.objects.get(pk=self.pk)
            refuse_in_filed_year(stored.year_start, f"The opening of {stored.block.code} is not changed")
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        refuse_in_filed_year(self.year_start, f"The opening of {self.block.code} is not deleted")
        return super().delete(*args, **kwargs)


class TaxDepreciationYear(AuditModel):
    """A tax year's schedule as filed: its lines are what was filed."""

    year_start = models.DateField(editable=False)
    filed_on = models.DateField(editable=False)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+", editable=False)
    withdrawn_on = models.DateField(null=True, blank=True, editable=False)
    withdrawn_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    note = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-year_start", "-id"]
        permissions = [("file_taxdepreciationyear", "Can file and withdraw a tax depreciation schedule")]
        constraints = [models.UniqueConstraint(fields=["year_start"], condition=Q(withdrawn_on__isnull=True),
                                               name="one_filed_tax_schedule_a_year")]

    def __str__(self):
        return f"Tax depreciation {self.year_start.year}-{str(self.year_start.year + 1)[2:]}"

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is filed; withdraw it for a revised return instead.")

    @classmethod
    def standing(cls, year_start):
        return cls.objects.filter(year_start=year_start, withdrawn_on__isnull=True).first()

    @classmethod
    @transaction.atomic
    def file(cls, year_start, by=None, note=""):
        """Keep the year's schedule as it stands now. Refused before the year ends, with a rate unconfirmed,
        while an earlier year with figures is unfiled, or when it is filed already."""
        from apps.accounting.dated_rates import rate_standing

        year_start = tax_year_of(year_start)
        if year_end(year_start) >= timezone.localdate():
            raise ValidationError(f"The tax year to {year_end(year_start):%d-%m-%Y} has not ended; it is filed after.")
        # One at a time per year: the blocks are held while the year is worked out and kept.
        blocks = list(TaxBlock.objects.order_by("pk"))
        lock_rows(*blocks, refresh=False)
        if cls.standing(year_start) is not None:
            raise ValidationError(f"{cls.standing(year_start)} is already filed; withdraw it to file again.")
        earlier = tax_year_of(year_start - datetime.timedelta(days=1))
        # A block whose opening is entered for this year starts here: what came before is the old return's.
        opened_here = set(TaxBlockOpening.objects.filter(year_start=year_start).values_list("block_id", flat=True))
        if cls.standing(earlier) is None and any(row.has_figures() for row in schedule(earlier)
                                                 if row.block.pk not in opened_here):
            raise ValidationError(f"The tax year from {earlier:%d-%m-%Y} has figures and is not filed: file it first, "
                                  "since this year opens at its closing figures.")
        rows = [row for row in schedule(year_start) if row.has_figures()]
        if not rows:
            raise ValidationError(f"No block has figures for the tax year from {year_start:%d-%m-%Y}.")
        for row in rows:
            rate_standing(row.block, year_start, "Tax depreciation", "filed", "Fixed assets > Tax blocks")
        year = cls.objects.create(year_start=year_start, filed_on=timezone.localdate(), filed_by=by,
                                  note=note[:255], created_by=by, updated_by=by)
        TaxBlockLine.objects.bulk_create([TaxBlockLine(year=year, **row.kept()) for row in rows])
        return year

    @transaction.atomic
    def withdraw(self, by=None, note=""):
        """For a revised return: the year is no longer filed, and is worked out again. Refused once the next is filed."""
        lock_rows(self)
        if self.withdrawn_on is not None:
            raise ValidationError(f"{self} is already withdrawn.")
        later = TaxDepreciationYear.standing(datetime.date(self.year_start.year + 1, 4, 1))
        if later is not None:
            raise ValidationError(f"{later} is filed and opens at this year's closing figures; withdraw it first.")
        self.withdrawn_on, self.withdrawn_by = timezone.localdate(), by
        self.note = (note or self.note)[:255]
        super().save(update_fields=["withdrawn_on", "withdrawn_by", "note", "updated_at"])
        return self


class TaxBlockLine(AuditModel):
    """One block's figures in a filed year, as filed."""

    year = models.ForeignKey(TaxDepreciationYear, on_delete=models.PROTECT, related_name="lines", editable=False)
    block = models.ForeignKey(TaxBlock, on_delete=models.PROTECT, related_name="+", editable=False)
    rate = models.ForeignKey(TaxBlockRate, on_delete=models.PROTECT, related_name="filed_lines", editable=False)
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4, editable=False)
    opening = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    additions_full = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    additions_half = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    sales = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    depreciation = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    closing = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    capital_gain = models.DecimalField(max_digits=18, decimal_places=2, editable=False,
                                       help_text="Short-term capital gain (+) or loss (-), section 74 / 50.")
    ceased = models.BooleanField(default=False, editable=False)
    book_depreciation = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    book_value = models.DecimalField(max_digits=18, decimal_places=2, editable=False)

    class Meta:
        ordering = ["year", "block"]

    def save(self, *args, **kwargs):
        raise ValidationError("A filed schedule's lines are what was filed; withdraw the year instead.")


def refuse_in_filed_year(day, doing):
    """Refuse `doing` on `day` when the tax year it falls in is filed: the schedule kept would no longer hold."""
    if day is None:
        return
    filed = TaxDepreciationYear.standing(tax_year_of(day))
    if filed is not None:
        raise ValidationError(f"{doing} on {to_date(day):%d-%m-%Y}: {filed} is filed. Withdraw it for a revised "
                              "return first.")


@dataclass
class BlockYear:
    block: TaxBlock
    year_start: datetime.date
    rate: object = None
    opening: Decimal = Decimal("0")
    additions_full: Decimal = Decimal("0")
    additions_half: Decimal = Decimal("0")
    sales: Decimal = Decimal("0")
    depreciation: Decimal = Decimal("0")
    closing: Decimal = Decimal("0")
    capital_gain: Decimal = Decimal("0")
    ceased: bool = False
    book_depreciation: Decimal = Decimal("0")
    book_value: Decimal = Decimal("0")
    filed: bool = False
    assets: list = field(default_factory=list)

    def has_figures(self):
        return any((self.opening, self.additions_full, self.additions_half, self.sales, self.closing))

    @property
    def rate_percent(self):
        return self.rate.rate_percent if self.rate is not None else None

    def kept(self):
        return {name: getattr(self, name) for name in (
            "block", "rate", "opening", "additions_full", "additions_half", "sales", "depreciation", "closing",
            "capital_gain", "ceased", "book_depreciation", "book_value")} | {"rate_percent": self.rate.rate_percent}


def work_block_year(opening, additions_full, additions_half, sales, rate_percent, ceased):
    """(depreciation, closing, capital gain): the block's year, by the rule in the module's words."""
    rate = Decimal(rate_percent) / 100
    before = opening + additions_full + additions_half - sales
    if ceased:
        return Decimal("0"), Decimal("0"), sales - (opening + additions_full + additions_half)
    if before < 0:
        return Decimal("0"), Decimal("0"), -before
    full_base, half_base = opening + additions_full - sales, additions_half
    if full_base < 0:
        half_base, full_base = half_base + full_base, Decimal("0")
    depreciation = round_money(full_base * rate + half_base * rate / 2)
    return depreciation, before - depreciation, Decimal("0")


def _block_year(block, year_start, opening, assets):
    from .models import AssetStatus

    end = year_end(year_start)
    row = BlockYear(block=block, year_start=year_start, opening=opening)
    row.rate = block.rates.filter(applies_from__lte=year_start).order_by("-applies_from", "-pk").first()
    remaining = 0
    for asset in assets:
        used_from = to_date(asset.in_service_date)
        if used_from is None or used_from > end:
            continue
        disposed = to_date(asset.disposed_on) if asset.status == AssetStatus.DISPOSED else None
        if disposed is not None and disposed < year_start:
            continue
        if used_from >= year_start:
            row.assets.append(asset)
            if days_used(used_from, year_start) < HALF_RATE_BELOW_DAYS:
                row.additions_half += asset.cost
            else:
                row.additions_full += asset.cost
        if disposed is not None and disposed <= end:
            row.sales += asset.disposal_proceeds or Decimal("0")
            if asset not in row.assets:
                row.assets.append(asset)
        else:
            remaining += 1
            row.book_value += asset.net_book_value(end)
        row.book_depreciation += sum(
            (charge.amount for charge in asset.depreciation_entries.all()
             if charge.reversal_id is None and year_start <= to_date(charge.period_end) <= end), Decimal("0"))
    row.ceased = bool(row.sales) and remaining == 0
    rate = row.rate.rate_percent if row.rate is not None else Decimal("0")
    row.depreciation, row.closing, row.capital_gain = work_block_year(
        row.opening, row.additions_full, row.additions_half, row.sales, rate, row.ceased)
    return row


def schedule(year_start):
    """
    Each block's year from `year_start`: filed years as filed, others worked out from the block's latest
    opening (TaxBlockOpening) or its first asset, year by year, each opening at the year before's closing.
    """
    from .models import FixedAsset

    year_start = tax_year_of(year_start)
    rows = []
    for block in TaxBlock.objects.order_by("code"):
        assets = list(FixedAsset.objects.filter(tax_block=block, in_service_date__isnull=False)
                      .prefetch_related("depreciation_entries").order_by("in_service_date", "pk"))
        opening_row = block.openings.filter(year_start__lte=year_start).order_by("-year_start").first()
        first = opening_row.year_start if opening_row else (
            tax_year_of(assets[0].in_service_date) if assets else year_start)
        first = min(first, year_start)
        carried = opening_row.written_down_value if opening_row and opening_row.year_start == first else Decimal("0")
        start = first
        while True:
            filed = TaxBlockLine.objects.filter(year__year_start=start, year__withdrawn_on__isnull=True,
                                                block=block).select_related("rate").first()
            if filed is not None:
                row = BlockYear(block=block, year_start=start, rate=filed.rate, filed=True, **{
                    name: getattr(filed, name) for name in (
                        "opening", "additions_full", "additions_half", "sales", "depreciation", "closing",
                        "capital_gain", "ceased", "book_depreciation", "book_value")})
            else:
                row = _block_year(block, start, carried, assets)
            if start == year_start:
                rows.append(row)
                break
            carried = row.closing
            start = datetime.date(start.year + 1, 4, 1)
    return rows
