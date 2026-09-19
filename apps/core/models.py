import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Abstract base giving every model a created/updated timestamp."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditModel(TimeStampedModel):
    """
    Abstract base for anything that needs a who-changed-it trail, not just a
    when-changed-it one. created_by/updated_by are set by the view/admin
    layer (see apps/core/audit.py), never inferred here.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


def to_date(value):
    """
    Normalise a date-ish value. Django allows assigning an ISO string to a
    DateField, so a model attribute can still be a string before any
    refresh — which breaks date arithmetic in posting logic.
    """
    if value is None or isinstance(value, datetime.datetime):
        return value.date() if value is not None else None
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


class Country(TimeStampedModel):
    code = models.CharField(max_length=2, unique=True, help_text="ISO 3166-1 alpha-2, e.g. US")
    name = models.CharField(max_length=128)

    class Meta:
        verbose_name_plural = "countries"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Currency(AuditModel):
    code = models.CharField(max_length=3, unique=True, help_text="ISO 4217 code, e.g. USD")
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, blank=True)
    decimal_places = models.PositiveSmallIntegerField(default=2)
    is_base = models.BooleanField(
        default=False,
        help_text="The company's single reporting/functional currency. At most one Currency may set this.",
    )

    class Meta:
        verbose_name_plural = "currencies"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_base"],
                condition=Q(is_base=True),
                name="unique_base_currency",
            )
        ]

    def __str__(self):
        return self.code

    def rate_on(self, on_date=None):
        """
        Units of the base currency that one unit of this currency buys on
        `on_date`, using the most recent rate effective on or before it.
        The base currency is always 1.
        """
        if self.is_base:
            return Decimal("1")
        on_date = on_date or timezone.now().date()
        rate = self.rates.filter(valid_from__lte=on_date).order_by("-valid_from").first()
        if rate is None:
            raise ValidationError(f"No exchange rate for {self.code} effective on {on_date}.")
        return rate.rate

    def to_base(self, amount, on_date=None):
        return amount * self.rate_on(on_date)

    def from_base(self, amount, on_date=None):
        return amount / self.rate_on(on_date)

    def convert_to(self, amount, target_currency, on_date=None):
        if self == target_currency:
            return amount
        return target_currency.from_base(self.to_base(amount, on_date), on_date)


class ExchangeRate(AuditModel):
    """
    Date-effective rate, quoted against the base currency. A rate applies
    from `valid_from` until superseded by a later one, so historical
    transactions keep converting at the rate that applied on their date.
    """

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    rate = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        help_text="Units of the base currency per 1 unit of this currency.",
    )
    valid_from = models.DateField()

    class Meta:
        ordering = ["currency", "-valid_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["currency", "valid_from"], name="unique_rate_per_currency_date"
            ),
            models.CheckConstraint(check=Q(rate__gt=0), name="exchange_rate_positive"),
        ]

    def __str__(self):
        return f"1 {self.currency} = {self.rate} (base) from {self.valid_from}"

    def clean(self):
        if self.currency_id and self.currency.is_base and self.rate != Decimal("1"):
            raise ValidationError("The base currency's rate against itself must be 1.")


class UnitOfMeasureCategory(models.TextChoices):
    COUNT = "count", "Count"
    WEIGHT = "weight", "Weight"
    VOLUME = "volume", "Volume"
    LENGTH = "length", "Length"
    TIME = "time", "Time"
    OTHER = "other", "Other"


class UnitOfMeasure(AuditModel):
    code = models.CharField(max_length=16, unique=True, help_text="e.g. pcs, kg, L")
    name = models.CharField(max_length=64)
    category = models.CharField(
        max_length=16, choices=UnitOfMeasureCategory.choices, default=UnitOfMeasureCategory.COUNT
    )
    base_unit = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="derived_units",
        help_text="Leave blank if this unit IS a base unit (e.g. 'each', 'kg').",
    )
    conversion_factor = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("1"),
        help_text="Quantity in this unit * conversion_factor = equivalent quantity in base_unit.",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def clean(self):
        if self.base_unit_id and self.base_unit_id == self.pk:
            raise ValidationError("A unit of measure cannot be its own base unit.")
        if self.base_unit_id and self.base_unit.category != self.category:
            raise ValidationError("base_unit must be in the same category as this unit.")

    def to_base_quantity(self, quantity):
        """Convert a quantity expressed in this unit into the base unit's quantity."""
        if not self.base_unit_id:
            return quantity
        return quantity * self.conversion_factor


class PartyTag(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Party(AuditModel):
    """
    A single legal/physical entity the business deals with. One Party can hold
    multiple roles (customer, vendor, employee) via PartyRoleAssignment instead
    of being duplicated per module — the whole point of a shared kernel is that
    Accounting and Inventory refer to the same Party record, not their own copies.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    tax_id = models.CharField(max_length=64, blank=True)
    default_currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="parties"
    )
    payment_terms = models.ForeignKey(
        "PaymentTerms", null=True, blank=True, on_delete=models.PROTECT, related_name="parties"
    )
    tags = models.ManyToManyField(PartyTag, blank=True, related_name="parties")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "parties"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def primary_address(self, address_type=None):
        addresses = self.addresses.filter(is_active=True)
        if address_type:
            addresses = addresses.filter(address_type=address_type)
        return addresses.order_by("-is_primary").first()

    def billing_address(self):
        return self.primary_address(AddressType.BILLING)

    def shipping_address(self):
        """Falls back to the billing address, which is how most ERPs behave."""
        return self.primary_address(AddressType.SHIPPING) or self.billing_address()

    def primary_contact(self):
        return self.contacts.filter(is_active=True).order_by("-is_primary").first()


class PartyRole(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    VENDOR = "vendor", "Vendor"
    EMPLOYEE = "employee", "Employee"
    OTHER = "other", "Other"


class PartyRoleAssignment(AuditModel):
    party = models.ForeignKey(Party, related_name="role_assignments", on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=PartyRole.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["party", "role"], name="unique_party_role")
        ]

    def __str__(self):
        return f"{self.party} [{self.role}]"


class AddressType(models.TextChoices):
    BILLING = "billing", "Billing"
    SHIPPING = "shipping", "Shipping"
    OFFICE = "office", "Office"
    OTHER = "other", "Other"


class Address(AuditModel):
    """
    Structured address. Kept in the kernel rather than as free text on each
    model so Sales can bill one address and ship to another, and so
    Purchasing and HR reuse the same shape.
    """

    party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.CASCADE, related_name="addresses"
    )
    address_type = models.CharField(
        max_length=16, choices=AddressType.choices, default=AddressType.BILLING
    )
    label = models.CharField(max_length=64, blank=True)
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=128, blank=True, help_text="State, province or region.")
    postal_code = models.CharField(max_length=32, blank=True)
    country = models.ForeignKey(
        Country, null=True, blank=True, on_delete=models.PROTECT, related_name="addresses"
    )
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "addresses"
        ordering = ["party", "address_type", "-is_primary"]
        constraints = [
            models.UniqueConstraint(
                fields=["party", "address_type"],
                condition=Q(is_primary=True),
                name="one_primary_address_per_party_and_type",
            )
        ]

    def __str__(self):
        return self.one_line()

    def one_line(self):
        parts = [self.line1, self.line2, self.city, self.state, self.postal_code]
        if self.country_id:
            parts.append(self.country.name)
        return ", ".join(part for part in parts if part)

    def formatted(self):
        lines = [self.line1]
        if self.line2:
            lines.append(self.line2)
        city_line = " ".join(part for part in [self.city, self.state, self.postal_code] if part)
        if city_line:
            lines.append(city_line)
        if self.country_id:
            lines.append(self.country.name)
        return "\n".join(lines)


class Contact(AuditModel):
    """A person at a Party. The Party is the organisation; this is who you call."""

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="contacts")
    first_name = models.CharField(max_length=128)
    last_name = models.CharField(max_length=128, blank=True)
    job_title = models.CharField(max_length=128, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["party", "-is_primary", "last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["party"],
                condition=Q(is_primary=True),
                name="one_primary_contact_per_party",
            )
        ]

    def __str__(self):
        return f"{self.full_name()} ({self.party.name})"

    def full_name(self):
        return " ".join(part for part in [self.first_name, self.last_name] if part)


class PartyBankAccount(AuditModel):
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="bank_accounts")
    account_name = models.CharField(max_length=255)
    bank_name = models.CharField(max_length=255, blank=True)
    account_number = models.CharField(max_length=64, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    swift_bic = models.CharField(max_length=11, blank=True)
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["party", "-is_primary"]
        constraints = [
            models.UniqueConstraint(
                fields=["party"],
                condition=Q(is_primary=True),
                name="one_primary_bank_account_per_party",
            )
        ]

    def __str__(self):
        return f"{self.account_name} ({self.party.name})"

    def clean(self):
        if not self.account_number and not self.iban:
            raise ValidationError("Provide either an account number or an IBAN.")


class PaymentTerms(AuditModel):
    """
    When payment is due, and any early-settlement discount. Shared by
    Sales (customer invoices) and Purchasing (vendor bills) because the
    arithmetic is identical on both sides.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    net_days = models.PositiveSmallIntegerField(
        default=30, help_text="Days from document date until the full amount is due."
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Early-settlement discount, e.g. 2.00 for the '2' in 2/10 net 30.",
    )
    discount_days = models.PositiveSmallIntegerField(
        default=0, help_text="Days within which the discount applies, e.g. the '10' in 2/10 net 30."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "payment terms"
        ordering = ["code"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.discount_percent and not self.discount_days:
            raise ValidationError("A discount percentage needs a discount window in days.")
        if self.discount_days and self.discount_days > self.net_days:
            raise ValidationError("The discount window cannot be longer than the net term.")

    def due_date(self, from_date):
        return from_date + datetime.timedelta(days=self.net_days)

    def discount_due_date(self, from_date):
        if not self.discount_days:
            return None
        return from_date + datetime.timedelta(days=self.discount_days)

    def discount_amount(self, amount):
        if not self.discount_percent:
            return Decimal("0")
        return (amount * self.discount_percent / Decimal("100")).quantize(Decimal("0.01"))


class DocumentSequence(AuditModel):
    """
    Generates human-facing document numbers (INV-2026-00001) instead of
    leaking database primary keys onto paperwork. Numbers are handed out
    under a row lock so two concurrent posts can't take the same one.

    Note: the lock relies on SELECT ... FOR UPDATE, which is a no-op on
    SQLite. Run PostgreSQL if concurrent numbering matters.
    """

    code = models.CharField(max_length=64, unique=True, help_text="e.g. sales.invoice")
    name = models.CharField(max_length=128)
    prefix = models.CharField(max_length=16, blank=True, help_text="e.g. INV-")
    suffix = models.CharField(max_length=16, blank=True)
    padding = models.PositiveSmallIntegerField(default=5)
    next_number = models.PositiveIntegerField(default=1)
    include_year = models.BooleanField(default=True)
    reset_yearly = models.BooleanField(default=True)
    current_year = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} (next: {self.peek()})"

    def _format(self, number, year):
        middle = f"{year}-" if self.include_year else ""
        return f"{self.prefix}{middle}{str(number).zfill(self.padding)}{self.suffix}"

    @classmethod
    def next_for(cls, code, on_date=None, **defaults):
        """
        Issue the next number for `code`, creating the sequence on first use
        so posting a document never fails on missing configuration.
        """
        sequence, _ = cls.objects.get_or_create(
            code=code, defaults={"name": defaults.pop("name", code), **defaults}
        )
        return sequence.next_value(on_date)

    def peek(self, on_date=None):
        """The number that would be issued next, without consuming it."""
        year = (to_date(on_date) or timezone.now().date()).year
        number = 1 if (self.reset_yearly and self.current_year != year) else self.next_number
        return self._format(number, year)

    def next_value(self, on_date=None):
        year = (to_date(on_date) or timezone.now().date()).year
        with transaction.atomic():
            sequence = DocumentSequence.objects.select_for_update().get(pk=self.pk)
            if sequence.reset_yearly and sequence.current_year != year:
                sequence.current_year = year
                sequence.next_number = 1
            number = sequence.next_number
            sequence.next_number = number + 1
            super(DocumentSequence, sequence).save(
                update_fields=["next_number", "current_year", "updated_at"]
            )
        self.refresh_from_db()
        return self._format(number, year)


class Company(AuditModel):
    """
    Single-company profile. Deliberately a singleton: multi-entity support
    would put a company FK on every model in the system, which is a much
    larger change and isn't built.
    """

    name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    tax_id = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    website = models.CharField(max_length=255, blank=True)
    base_currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1, choices=[(m, datetime.date(2000, m, 1).strftime("%B")) for m in range(1, 13)]
    )
    default_inventory_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Asset account for stock value when an item doesn't name its own.",
    )
    default_cogs_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Cost of goods sold account when an item doesn't name its own.",
    )
    grni_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Goods received not invoiced: the accrual between receiving stock and "
                  "being billed for it.",
    )
    settlement_discount_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where early-settlement discounts are written off (an expense).",
    )
    bad_debt_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Expense account for receivables judged uncollectable.",
    )

    class Meta:
        verbose_name_plural = "company"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        existing = Company.objects.first()
        if self._state.adding and existing is not None:
            # Stay a singleton: fold this into the existing row rather than
            # adding a second, keeping the original audit trail intact.
            self.pk = existing.pk
            self.created_at = existing.created_at
            self.created_by_id = existing.created_by_id
            kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create(name="My Company")

    def fiscal_year_bounds(self, on_date):
        """The fiscal year (start, end) containing `on_date`."""
        start_year = on_date.year if on_date.month >= self.fiscal_year_start_month else on_date.year - 1
        start = datetime.date(start_year, self.fiscal_year_start_month, 1)
        end = datetime.date(start_year + 1, self.fiscal_year_start_month, 1) - datetime.timedelta(days=1)
        return start, end
