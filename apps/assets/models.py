"""
Fixed assets.

Buying a machine is not buying stock and not incurring an expense. It is
exchanging cash for something that will be useful for years, and the
cost belongs on the balance sheet until those years consume it.

Without this a capital purchase had two bad homes: inventory, where it
would be valued as though it were for resale and relieved on a sale that
never comes, or an expense, which puts a decade of value into one
month's profit.

Depends on accounting and core only. Purchasing reaches in to capitalise
a bill line, the same direction as drop-ship: the dependent side holds
the pointer.
"""

import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry, JournalLine, round_money
from apps.core.models import AuditModel, DocumentSequence, Party, to_date


class DepreciationMethod(models.TextChoices):
    STRAIGHT_LINE = "straight_line", "Straight line"
    NONE = "none", "Not depreciated"


class AssetStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_SERVICE = "in_service", "In service"
    DISPOSED = "disposed", "Disposed"
    CANCELLED = "cancelled", "Cancelled"


class AssetCategory(AuditModel):
    """
    The accounts and default life a class of asset shares.

    Three accounts, not one: what the asset cost, what has been consumed
    of it, and this period's share of that consumption. Netting the
    second into the first would lose the original cost, which is the
    number every asset register is asked for.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    asset_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+",
        help_text="What the asset cost (an asset).",
    )
    accumulated_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+",
        help_text="Depreciation charged to date (a contra-asset).",
    )
    expense_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+",
        help_text="This period's depreciation (an expense).",
    )
    disposal_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Gain or loss on disposal. Without one, disposal is refused rather "
                  "than quietly written to depreciation.",
    )
    default_life_months = models.PositiveSmallIntegerField(default=60)
    method = models.CharField(
        max_length=16, choices=DepreciationMethod.choices,
        default=DepreciationMethod.STRAIGHT_LINE,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "asset categories"

    def __str__(self):
        return self.name


class FixedAsset(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        AssetCategory, on_delete=models.PROTECT, related_name="assets"
    )
    vendor = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="assets_sold"
    )
    bill_line = models.ForeignKey(
        "purchasing.BillLine", null=True, blank=True, on_delete=models.PROTECT,
        related_name="assets",
        help_text="The purchase this asset was capitalised from, when there was one.",
    )
    acquisition_date = models.DateField()
    in_service_date = models.DateField(
        null=True, blank=True,
        help_text="Depreciation starts here, not at acquisition — a machine in a crate "
                  "is not being consumed.",
    )
    cost = models.DecimalField(max_digits=18, decimal_places=2)
    salvage_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0"),
        help_text="What it will still be worth at the end of its life.",
    )
    life_months = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=16, choices=AssetStatus.choices, default=AssetStatus.DRAFT
    )
    disposed_on = models.DateField(null=True, blank=True, editable=False)
    disposal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    capitalisation_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="The entry that moved this asset's cost onto the asset "
                  "account, when it came from a bill — kept so that it can be "
                  "reversed rather than left behind.",
    )

    class Meta:
        ordering = ["-acquisition_date", "-id"]
        permissions = [("dispose_fixedasset", "Can dispose of fixed assets")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_asset_number"
            ),
            models.CheckConstraint(check=Q(cost__gt=0), name="asset_cost_positive"),
            models.CheckConstraint(
                check=Q(salvage_value__gte=0), name="asset_salvage_not_negative"
            ),
            # In the table as well as on save: at or above cost there is
            # nothing to depreciate, and the monthly charge comes out at
            # nothing or less.
            models.CheckConstraint(
                check=Q(salvage_value__lt=F("cost")),
                name="asset_salvage_below_cost",
            ),
            models.CheckConstraint(
                check=Q(in_service_date__isnull=True)
                | Q(in_service_date__gte=F("acquisition_date")),
                name="asset_in_service_after_acquisition",
            ),
        ]

    def __str__(self):
        return f"{self.number or f'FA-draft-{self.pk}'} {self.name}"

    # What every depreciation charge was worked out from. Once the asset
    # has left draft, changing any of them would leave the charges
    # already posted computed from figures the asset no longer has.
    FIXED_IN_SERVICE = (
        "category_id", "acquisition_date", "in_service_date", "cost",
        "salvage_value", "life_months",
    )

    def save(self, *args, **kwargs):
        # `clean()` is not called for an asset made in code — which is
        # every asset capitalised from a bill — or through the API, so
        # the question is asked here, where every one of them passes.
        self.clean()
        if self.pk:
            previous = FixedAsset.objects.filter(pk=self.pk).first()
            if previous is not None and previous.status != AssetStatus.DRAFT:
                changed = [
                    name for name in self.FIXED_IN_SERVICE
                    if getattr(previous, name) != getattr(self, name)
                ]
                if changed:
                    raise ValidationError(
                        f"{self} is {previous.get_status_display().lower()}; "
                        f"its {', '.join(n.replace('_id', '') for n in changed)} "
                        "can no longer change. Its depreciation was worked out "
                        "from them — dispose of it and register it again."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != AssetStatus.DRAFT or self.capitalisation_entry_id:
            raise ValidationError(
                f"{self} is on the books — in service, disposed of, or "
                "capitalised from a bill. Only a draft typed in by hand can "
                "be deleted; un-capitalise or dispose of the rest."
            )
        return super().delete(*args, **kwargs)

    def clean(self):
        self.acquisition_date = to_date(self.acquisition_date)
        self.in_service_date = to_date(self.in_service_date)
        if self.salvage_value is not None and self.cost is not None:
            if self.salvage_value >= self.cost:
                raise ValidationError(
                    "Salvage value must be below cost, or there is nothing to depreciate."
                )
        if self.in_service_date and self.acquisition_date:
            if self.in_service_date < self.acquisition_date:
                raise ValidationError("An asset cannot be in service before it was acquired.")

    def depreciable_base(self):
        return self.cost - self.salvage_value

    def monthly_charge(self):
        if self.category.method == DepreciationMethod.NONE or not self.life_months:
            return Decimal("0")
        return round_money(self.depreciable_base() / self.life_months)

    def accumulated(self, as_of=None):
        """Depreciation charged, through `as_of` when given."""
        entries = self.depreciation_entries.all()
        if as_of is not None:
            entries = entries.filter(period_end__lte=to_date(as_of))
        return sum((entry.amount for entry in entries), Decimal("0"))

    def net_book_value(self, as_of=None):
        return self.cost - self.accumulated(as_of)

    def remaining_to_depreciate(self):
        return max(self.depreciable_base() - self.accumulated(), Decimal("0"))

    @transaction.atomic
    def place_in_service(self, on_date=None):
        if self.status != AssetStatus.DRAFT:
            raise ValidationError(f"This asset is already {self.get_status_display().lower()}.")
        # Checked by `save()`, which every path through here reaches —
        # a date before the asset arrived is refused there.
        self.in_service_date = to_date(on_date) or self.in_service_date or self.acquisition_date
        if not self.number:
            self.number = DocumentSequence.next_for(
                "assets.fixed_asset", self.acquisition_date,
                name="Fixed Assets", prefix="FA-",
            )
        self.status = AssetStatus.IN_SERVICE
        self.save(update_fields=["number", "in_service_date", "status", "updated_at"])

    def periods_due(self, through):
        """
        Month ends between going into service and `through` that have not
        been charged yet.

        Month by month rather than a single catch-up figure, because each
        month is a period somebody closed, and a lump posted to the
        current one misstates every month it covers.
        """
        through = to_date(through)
        if self.status != AssetStatus.IN_SERVICE or not self.in_service_date:
            return []
        charged = {
            to_date(entry.period_end) for entry in self.depreciation_entries.all()
        }
        periods, cursor = [], to_date(self.in_service_date)
        while True:
            last = datetime.date(
                cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]
            )
            if last > through:
                break
            if last not in charged:
                periods.append(last)
            cursor = last + datetime.timedelta(days=1)
        return periods

    @transaction.atomic
    def depreciate(self, through=None):
        """Charge every month due up to `through`. Returns the entries made."""
        through = to_date(through) or timezone.now().date()
        if self.status != AssetStatus.IN_SERVICE:
            raise ValidationError("Only an asset in service is depreciated.")

        charge = self.monthly_charge()
        if charge <= 0:
            return []

        made = []
        for period_end in self.periods_due(through):
            remaining = self.remaining_to_depreciate()
            if remaining <= 0:
                break
            # The last month takes whatever is left rather than the full
            # charge, or the asset depreciates past its salvage value.
            amount = min(charge, remaining)
            memo = f"Depreciation {self.number} to {period_end:%b %Y}"
            entry = JournalEntry.objects.create(
                date=period_end, reference=self.number, memo=memo
            )
            JournalLine.objects.create(
                entry=entry, account=self.category.expense_account,
                debit=amount, description=memo[:255],
            )
            JournalLine.objects.create(
                entry=entry, account=self.category.accumulated_account,
                credit=amount, description=memo[:255],
            )
            entry.post()
            made.append(DepreciationEntry.objects.create(
                asset=self, period_end=period_end, amount=amount, journal_entry=entry
            ))
        return made

    @transaction.atomic
    def uncapitalise(self, on_date=None, memo=""):
        """
        Undo capitalising a bill line into this asset, before it has
        ever been used.

        The reverse of `BillLine.capitalise_as_asset`, written because a
        debit note on a capitalised line used to credit the account the
        capitalisation had already emptied — driving it below nothing
        while the asset stayed on the books at full cost. Only a draft:
        an asset that has been in service has been depreciated or could
        have been, and taking it off is a disposal.
        """
        if self.status != AssetStatus.DRAFT:
            raise ValidationError(
                f"{self} has been in service. Taking it off the books is a "
                "disposal, not an undo."
            )
        if self.capitalisation_entry_id is None:
            raise ValidationError(
                f"{self} was not capitalised from a bill, so there is nothing "
                "to undo — delete the draft instead."
            )
        on_date = to_date(on_date) or timezone.now().date()
        self.capitalisation_entry.create_reversal(
            entry_date=on_date, memo=memo or f"Un-capitalised {self}"
        )
        # Cancelled, not disposed: nothing was sold or scrapped, the
        # capitalisation simply never stood — and the register, which
        # leaves drafts out, must not start showing it as a disposal.
        self.status = AssetStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    @transaction.atomic
    def dispose(self, on_date=None, proceeds=Decimal("0"), memo=""):
        """
        Take the asset off the books at its remaining value.

        Every month due before the disposal is charged first. Without
        that, an asset sold in June with depreciation run to March took
        April's and May's charge into the loss on disposal: the total
        came out right and both lines of the profit and loss account came
        out wrong.

        **The proceeds are not banked here.** This entry takes the asset's
        cost and accumulated depreciation off and leaves its book value
        in the disposal account; the sale itself is invoiced to the
        buyer with the disposal account as its revenue account, and the
        two together leave the gain or loss there. `proceeds` shows the
        gain or loss on this entry's lines — it does not debit a bank or
        a receivable, and a disposal nobody invoices leaves the whole
        book value as a loss.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if self.status == AssetStatus.DISPOSED:
            raise ValidationError("This asset has already been disposed of.")
        if self.status != AssetStatus.IN_SERVICE:
            raise ValidationError("Only an asset in service can be disposed of.")
        month_before = on_date.replace(day=1) - datetime.timedelta(days=1)
        self.depreciate(through=month_before)

        account = self.category.disposal_account
        if account is None:
            raise ValidationError(
                f"{self.category} has no disposal account; a gain or loss has nowhere "
                "to go and would otherwise be hidden in depreciation."
            )

        proceeds = round_money(Decimal(proceeds))
        accumulated = self.accumulated()
        book_value = self.cost - accumulated
        result = proceeds - book_value  # positive is a gain

        memo = memo or f"Disposal of {self.number}"
        entry = JournalEntry.objects.create(date=on_date, reference=self.number, memo=memo)
        if accumulated:
            JournalLine.objects.create(
                entry=entry, account=self.category.accumulated_account,
                debit=accumulated, description=memo[:255],
            )
        JournalLine.objects.create(
            entry=entry, account=self.category.asset_account,
            credit=self.cost, description=memo[:255],
        )
        if proceeds:
            JournalLine.objects.create(
                entry=entry, account=account, debit=proceeds,
                description=f"{memo} — proceeds",
            )
        if result:
            JournalLine.objects.create(
                entry=entry, account=account,
                debit=-result if result < 0 else Decimal("0"),
                credit=result if result > 0 else Decimal("0"),
                description=f"{memo} — {'gain' if result > 0 else 'loss'}",
            )
        entry.post()

        self.status = AssetStatus.DISPOSED
        self.disposed_on = on_date
        self.disposal_entry = entry
        self.save(update_fields=["status", "disposed_on", "disposal_entry", "updated_at"])
        return entry


class DepreciationEntry(AuditModel):
    """One month's charge. A record, so a period is never charged twice."""

    asset = models.ForeignKey(
        FixedAsset, on_delete=models.PROTECT, related_name="depreciation_entries"
    )
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False
    )

    class Meta:
        ordering = ["period_end", "id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="depreciation_amount_positive"),
            models.UniqueConstraint(
                fields=["asset", "period_end"], name="one_charge_per_asset_and_period"
            ),
        ]

    def __str__(self):
        return f"{self.asset} {self.period_end:%b %Y} {self.amount}"


def asset_register(as_of=None, category=None):
    """
    Cost, depreciation and net book value per asset, as they stood on
    `as_of`.

    As they stood, which is the whole of the question: depreciation is
    counted through that date, and an asset bought after it is left out.
    This used to report today's depreciation under any date asked and
    list assets that did not exist yet, so a register "as at last March"
    was today's register with March written on it.
    """
    as_of = to_date(as_of) or timezone.now().date()
    assets = FixedAsset.objects.select_related("category").exclude(
        status__in=(AssetStatus.DRAFT, AssetStatus.CANCELLED)
    ).filter(acquisition_date__lte=as_of)
    if category is not None:
        assets = assets.filter(category=category)

    rows = []
    for asset in assets:
        if asset.disposed_on and asset.disposed_on <= as_of:
            continue
        rows.append({
            "asset": asset,
            "category": asset.category,
            "cost": asset.cost,
            "accumulated": asset.accumulated(as_of),
            "net_book_value": asset.net_book_value(as_of),
            "monthly_charge": asset.monthly_charge(),
        })
    return sorted(rows, key=lambda row: -row["net_book_value"])
