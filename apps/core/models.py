import calendar
import contextvars
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


def prefetched(instance, relation):
    """Whether `relation` was prefetched on `instance`, so reading it costs nothing."""
    return relation in getattr(instance, "_prefetched_objects_cache", {})


# Per-request memo for Company.get(): None outside a request.
_COMPANY = contextvars.ContextVar("company_for_this_request", default=None)


def lock_rows(*instances, refresh=True):
    """
    Hold these rows until the transaction ends, in one fixed order, and
    re-read them.

    For a decision that reads two documents and writes a third: an
    allocation reads what is left on the payment and what is due on the
    invoice. Ordered by model and key, so two transactions that need the
    same rows take them in the same sequence instead of each holding
    one and waiting for the other.

    Re-read, because the object in hand was read before the lock: a
    material issue waiting on its work order while the order closed
    still held "released", and posted into a closed run.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("lock_rows() outside a transaction locks nothing.")
    for instance in sorted((i for i in instances if i is not None and i.pk is not None),
                           key=lambda i: (i._meta.label_lower, i.pk)):
        list(type(instance)._base_manager.select_for_update()
             .filter(pk=instance.pk).values_list("pk", flat=True))
        if refresh:
            instance.refresh_from_db()


def serialised(*state):
    """
    Make a method that changes a document's state one at a time per
    document.

    Every posting path reads the document's state, decides, and writes.
    Two people pressing Post on the same invoice at the same moment both
    read "not posted", both decide yes, and both post: receivables went
    to 2,000 for a 1,000 invoice, a payment voided twice took the bank
    below where it started, and a payslip was paid by two payments.
    No single-threaded test can see it; found on PostgreSQL with two
    threads held at the decision.

    This takes the row lock first, then re-reads `state` — the fields
    the method decides on — from what is committed, so the second caller
    waits for the first and then sees what it did. Only those fields are
    re-read: anything else the caller set and has not saved stays as the
    caller left it.
    """
    import functools

    def wrap(method):
        @functools.wraps(method)
        def inner(self, *args, **kwargs):
            with transaction.atomic():
                if self.pk is not None:
                    lock_rows(self, refresh=False)
                    if state:
                        self.refresh_from_db(fields=list(state))
                return method(self, *args, **kwargs)
        return inner
    return wrap


def to_date(value):
    """
    Normalise a date-ish value. Django allows assigning an ISO string to a
    DateField, so a model attribute can still be a string before any
    refresh — which breaks date arithmetic in posting logic.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        # The day it was at the plant, not in Greenwich: 01:00 in Kolkata
        # is still the previous day in UTC.
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    if not text:
        # What a form sends for a date box left empty: not given.
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        # Refused in words, not a server error: forty-two API actions read
        # a date through here, and a mistyped one was a 500 on each.
        raise ValidationError(f"{value!r} is not a date. Write it as 2026-03-31.") from None


class Country(TimeStampedModel):
    code = models.CharField(max_length=2, unique=True, help_text="ISO 3166-1 alpha-2, e.g. US")
    name = models.CharField(max_length=128)

    class Meta:
        verbose_name_plural = "countries"
        ordering = ["name"]

    def __str__(self):
        return self.name


# What is already booked in the base currency, said by the modules that
# book it (accounting registers its posted entries from its ready()): core
# keeps the currency and imports none of them.
_BOOKED_IN_BASE = []


def register_booked_in_base(ask):
    """ask() -> words naming what is booked ("A posted journal entry"), or ""."""
    if ask not in _BOOKED_IN_BASE:
        _BOOKED_IN_BASE.append(ask)


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

    def save(self, *args, **kwargs):
        was_base = (Currency.objects.filter(pk=self.pk).values_list("is_base", flat=True).first()
                    if self.pk else False)
        if bool(was_base) != self.is_base:
            if was_base and Company.objects.filter(base_currency=self).exists():
                raise ValidationError(
                    f"The company keeps its books in {self.code}. Point the company at another "
                    "base currency first."
                )
            booked = next((said for said in (ask() for ask in _BOOKED_IN_BASE) if said), "")
            if booked:
                raise ValidationError(
                    f"{booked} is already booked in the base currency; moving it would restate "
                    "every posted amount."
                )
        super().save(*args, **kwargs)

    def rate_on(self, on_date=None):
        """
        Units of the base currency that one unit of this currency buys on
        `on_date`, using the most recent rate effective on or before it.
        The base currency is always 1.
        """
        if self.is_base:
            return Decimal("1")
        on_date = on_date or timezone.localdate()
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
        self._check_rate()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_rate()
        super().save(*args, **kwargs)

    def _check_rate(self):
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
        constraints = [
            # A zero or negative factor makes every conversion through this
            # unit either a division by zero or a sign flip, and there is no
            # sensible reading of "one case is zero eaches".
            models.CheckConstraint(
                check=Q(conversion_factor__gt=0), name="uom_conversion_factor_positive"
            ),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        self._check_base_unit()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_base_unit()
        super().save(*args, **kwargs)

    def _check_base_unit(self):
        if self.base_unit_id and self.base_unit_id == self.pk:
            raise ValidationError("A unit of measure cannot be its own base unit.")
        if self.base_unit_id and self.base_unit.category != self.category:
            raise ValidationError("base_unit must be in the same category as this unit.")

    def to_base_quantity(self, quantity):
        """Convert a quantity expressed in this unit into the base unit's quantity."""
        if not self.base_unit_id:
            return quantity
        return quantity * self.conversion_factor

    def root(self):
        """
        The unit at the bottom of this one's chain — the one everything in
        its category is ultimately counted in.

        Two units are comparable exactly when they share a root, which is
        the only question anyone actually asks. Category alone is not
        enough: two unrelated weight chains are both weights and still
        have no factor between them.
        """
        unit = self
        seen = {self.pk}
        while unit.base_unit_id:
            if unit.base_unit_id in seen:
                raise ValidationError(
                    f"Unit {unit} is defined in terms of itself, so no quantity "
                    "in it can be converted."
                )
            seen.add(unit.base_unit_id)
            unit = unit.base_unit
        return unit

    def factor_to_root(self):
        """How many root units one of this unit is worth."""
        factor = Decimal("1")
        unit = self
        seen = {self.pk}
        while unit.base_unit_id:
            if unit.base_unit_id in seen:
                raise ValidationError(
                    f"Unit {unit} is defined in terms of itself, so no quantity "
                    "in it can be converted."
                )
            seen.add(unit.base_unit_id)
            factor *= unit.conversion_factor
            unit = unit.base_unit
        return factor

    def convert_to(self, quantity, target):
        """
        Restate a quantity in another unit of the same chain.

        Refuses rather than guesses when the two do not share a root. A
        widget counted in eaches cannot be ordered by the kilogram, and
        silently treating 10 kg as 10 eaches is how a stock ledger starts
        disagreeing with the shelf.
        """
        if target is None or target.pk == self.pk:
            return quantity
        mine, theirs = self.root(), target.root()
        if mine.pk != theirs.pk:
            raise ValidationError(
                f"Cannot convert {self} to {target}: they are not the same kind of "
                f"measure ({self} is counted in {mine}, {target} in {theirs})."
            )
        return quantity * self.factor_to_root() / target.factor_to_root()


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


def only_one(row, field, **scope):
    """
    Setting a yes-or-no that only one row may hold clears it on the
    others in the same scope: the primary address, the policy in force.

    Ticking it on the new row means the old one no longer holds it, and
    nobody should have to untick that first; leaving two holding it
    makes whichever reads "the" one pick by the order rows come in.
    Called from save() inside the save's own transaction, so a refused
    save clears nothing.
    """
    if not getattr(row, field):
        return
    type(row)._base_manager.filter(**{field: True}, **scope).exclude(pk=row.pk).update(
        **{field: False}, updated_at=timezone.now(), updated_by_id=row.updated_by_id)


def take_primary(row, **scope):
    """
    One primary per party (and kind of address). A scope with no party is
    no scope: the company's own address is nobody's rival, and the
    database does not hold party-less rows to one.
    """
    if None not in scope.values():
        only_one(row, "is_primary", **scope)


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

    # What a document prints of an address. Its label, whether it is the
    # primary one and whether it is still in use print nowhere.
    PRINTED = ("party_id", "address_type", "line1", "line2", "city", "state", "postal_code",
               "country_id")

    def __str__(self):
        return self.one_line()

    def save(self, *args, **kwargs):
        if self.pk:
            before = Address.objects.filter(pk=self.pk).values(*self.PRINTED).first()
            if before and any(before[name] != getattr(self, name) for name in self.PRINTED):
                printed_on = self.posted_documents()
                if printed_on:
                    raise ValidationError(
                        f"A posted {printed_on} prints this address, and every reprint would print "
                        "the new one. Add the new address and archive this one."
                    )
        with transaction.atomic():
            take_primary(self, party_id=self.party_id, address_type=self.address_type)
            super().save(*args, **kwargs)

    def posted_documents(self):
        """
        The kind of posted document that names this address, or "".

        Asked of whatever points at an address and can be posted, without
        importing it: core imports no module built on it.
        """
        for relation in self._meta.get_fields(include_hidden=True):
            if not (relation.one_to_many and relation.auto_created):
                continue
            model = relation.related_model
            if not any(field.name == "posted" for field in model._meta.get_fields()):
                continue
            if model._base_manager.filter(**{relation.field.name: self, "posted": True}).exists():
                return str(model._meta.verbose_name)
        return ""

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

    def save(self, *args, **kwargs):
        with transaction.atomic():
            take_primary(self, party_id=self.party_id)
            super().save(*args, **kwargs)

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
        self._check_number()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_number()
        with transaction.atomic():
            take_primary(self, party_id=self.party_id)
            super().save(*args, **kwargs)

    def _check_number(self):
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
        self._check_discount()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_discount()
        super().save(*args, **kwargs)

    def _check_discount(self):
        if self.discount_percent and not self.discount_days:
            raise ValidationError("A discount percentage needs a discount window in days.")
        if self.discount_days and self.discount_days > self.net_days:
            raise ValidationError("The discount window cannot be longer than the net term.")

    def check_percentages(self):
        """
        Installment percentages must come to exactly 100.

        Checked when the term is *used*, not as each line is saved: a term
        is built one line at a time and the first line of a 50/50 would
        fail a per-line check every time. The moment a wrong answer could
        do damage is when a schedule is produced from it.
        """
        lines = list(self.lines.all())
        if not lines:
            return
        total = sum((line.percent for line in lines), Decimal("0"))
        if total != Decimal("100"):
            raise ValidationError(
                f"The installments on '{self.code}' come to {total}%, not 100%."
            )

    def schedule(self, from_date, amount):
        """
        [(due_date, amount)] for this term — the whole point of modelling
        terms as lines rather than a single number.

        "50% on order, 50% on delivery" and "30/60/90" are ordinary B2B
        terms and could not be expressed at all when a term was one
        net_days. A term with no lines is the one-installment case, which
        keeps every simple term working unchanged.
        """
        amount = Decimal(amount)
        lines = list(self.lines.all())
        if not lines:
            return [(self.due_date(from_date), amount)]

        self.check_percentages()
        rows, allocated = [], Decimal("0")
        for index, line in enumerate(lines):
            is_last = index == len(lines) - 1
            share = (
                amount - allocated if is_last
                else (amount * line.percent / Decimal("100")).quantize(Decimal("0.01"))
            )
            allocated += share
            rows.append((line.due_date(from_date), share))
        return rows

    def due_date(self, from_date):
        """The last day any of this term falls due."""
        lines = list(self.lines.all())
        if lines:
            return max(line.due_date(from_date) for line in lines)
        return from_date + datetime.timedelta(days=self.net_days)

    def discount_due_date(self, from_date):
        if not self.discount_days:
            return None
        return from_date + datetime.timedelta(days=self.discount_days)

    def discount_amount(self, amount):
        if not self.discount_percent:
            return Decimal("0")
        return (amount * self.discount_percent / Decimal("100")).quantize(Decimal("0.01"))


class PaymentTermsLine(AuditModel):
    """
    One installment of a payment term: how much, and how long after the
    document date.
    """

    terms = models.ForeignKey(PaymentTerms, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveSmallIntegerField(default=10)
    percent = models.DecimalField(
        max_digits=6, decimal_places=3,
        help_text="Share of the document falling due here. All lines must total 100.",
    )
    days = models.PositiveSmallIntegerField(
        default=0, help_text="Days from the document date until this installment is due."
    )
    day_of_month = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Snap to this day of the resulting month — 31 means end of month, "
                  "which is how 'net 30 EOM' is actually written.",
    )

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.CheckConstraint(check=Q(percent__gt=0), name="installment_percent_positive"),
        ]

    def __str__(self):
        return f"{self.percent}% after {self.days} days"

    def due_date(self, from_date):
        due = to_date(from_date) + datetime.timedelta(days=self.days)
        if self.day_of_month:
            last = calendar.monthrange(due.year, due.month)[1]
            due = due.replace(day=min(self.day_of_month, last))
        return due


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

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} (next: {self.peek()})"

    def save(self, *args, **kwargs):
        # Each change refused here gives a number twice, and a document
        # number is unique: the second post fails, or worse, two papers
        # carry one number. A number too high is only a gap, so raising
        # it stays allowed; there is no lowering, even before first use,
        # because nothing records whether a number was issued.
        if self.reset_yearly and not self.include_year:
            raise ValidationError({"include_year": "A sequence that starts again each year has to show "
                                                   "the year, or January's first number repeats last year's."})
        if not self._state.adding:
            before = DocumentSequence.objects.get(pk=self.pk)
            counted = self.years.exists()
            if self.code != before.code:
                raise ValidationError({"code": "The code is what the documents ask for; under a new one "
                                               "they would start a fresh sequence at 1."})
            if self.next_number < before.next_number:
                raise ValidationError({"next_number": f"Numbers up to {before.next_number - 1} may already "
                                                      "be on paper; the next one cannot go back."})
            if counted and self.next_number != before.next_number:
                raise ValidationError({"next_number": "Each year keeps its own counter now, so this number "
                                                      "is no longer read."})
            if counted and before.reset_yearly and not self.reset_yearly:
                raise ValidationError({"reset_yearly": "This sequence has counted by year; one counter from "
                                                       "here would start below numbers already issued."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A sequence is not deleted: the next document would start it again at 1.")

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
        year = (to_date(on_date) or timezone.localdate()).year
        if not self.reset_yearly:
            return self._format(self.next_number, year)
        counter = self.years.filter(year=year).first()
        if counter is None:
            return self._format(1 if self.years.exists() else self.next_number, year)
        return self._format(counter.next_number, year)

    def next_value(self, on_date=None):
        """
        The next number for the document's own year.

        One counter per year, not one that resets whenever the year
        changes: that reset in both directions, so a late 2025 invoice
        entered after a 2026 one started 2025 again at 1, and posting
        failed on a number already used.
        """
        year = (to_date(on_date) or timezone.localdate()).year
        with transaction.atomic():
            sequence = DocumentSequence.objects.select_for_update().get(pk=self.pk)
            if sequence.reset_yearly:
                # Under the sequence's lock, so two first numbers of a year
                # cannot both create the year's counter.
                # The first counter a sequence ever has starts where the
                # sequence says: numbers taken over from an older system,
                # or a sequence switched to yearly mid-year, carry on from
                # next_number. Every later year starts at 1. Once there are
                # counters, each is changed on its own (the admin's years).
                first = not sequence.years.exists()
                counter, _ = DocumentSequenceYear.objects.get_or_create(
                    sequence=sequence, year=year,
                    defaults={"next_number": sequence.next_number if first else 1},
                )
                number = counter.next_number
                counter.next_number = number + 1
                counter.save(update_fields=["next_number"])
            else:
                number = sequence.next_number
                sequence.next_number = number + 1
                super(DocumentSequence, sequence).save(update_fields=["next_number", "updated_at"])
        self.refresh_from_db()
        return self._format(number, year)


class LoginFailure(models.Model):
    """One wrong password, kept long enough to count (apps.core.auth)."""

    username = models.CharField(max_length=150, db_index=True)
    address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-at"]

    def __str__(self):
        return f"{self.username} {self.at:%Y-%m-%d %H:%M}"


class DocumentSequenceYear(models.Model):
    """The next number of one sequence in one year."""

    sequence = models.ForeignKey(DocumentSequence, on_delete=models.CASCADE, related_name="years")
    year = models.PositiveIntegerField()
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["sequence", "year"]
        constraints = [
            models.UniqueConstraint(fields=["sequence", "year"], name="one_counter_per_year"),
        ]

    def __str__(self):
        return f"{self.sequence.code} {self.year}: next {self.next_number}"


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
    tax_rounding = models.CharField(
        max_length=16, default="line",
        choices=[("line", "Round tax per line"), ("document", "Round tax per document")],
        help_text="Some jurisdictions require tax computed on the document total per rate "
                  "rather than line by line. The two differ by pennies, which is enough to "
                  "fail a tax return's reconciliation.",
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
    default_revenue_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where a sale is credited when its line names no revenue account.",
    )
    default_receivable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="What customers owe is booked to this, when an invoice or a receipt names "
                  "no account of its own.",
    )
    default_payable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="What is owed to vendors is booked to this, when a bill or a payment out "
                  "names no account of its own.",
    )
    default_bank_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The bank a payment goes through when it names none.",
    )
    default_purchase_expense_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where a non-stocked purchase lands when its line names no account.",
    )
    fx_gain_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Realised exchange gains — when a foreign balance settles for more base "
                  "currency than it was booked at.",
    )
    fx_loss_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Realised exchange losses — when it settles for less.",
    )
    vendor_prepayment_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Asset account holding money paid to a vendor before the goods arrive.",
    )
    settlement_discount_received_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where early-settlement discounts taken from vendors are booked "
                  "(income, or a contra-expense).",
    )
    purchase_price_variance_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where the difference lands when a vendor bills a different price than "
                  "was agreed on the order (an expense; a credit means they billed less).",
    )
    purchase_price_tolerance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="How far above the agreed purchase price a bill may go before it is "
                  "refused. Zero means the vendor's price must match the order exactly.",
    )
    net_pay_account = models.ForeignKey(
        "accounting.Account",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="What payroll owes its staff between posting a pay run and paying "
                  "it. A control account: it is emptied by paying people, and a "
                  "balance that never clears is wages nobody has chased.",
    )
    customer_deposit_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Liability account holding money taken up front, before the goods are "
                  "delivered and the revenue is earned.",
    )

    class Meta:
        verbose_name_plural = "company"
        permissions = [
            ("import_records", "Can bring the old system's records in"),
            ("check_health", "Can read whether the books agree and the server is well"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        """
        The base currency is stated twice — here and by Currency.is_base —
        and only one of them was ever read.

        Nothing consulted this field, so setting it to EUR while
        Currency.is_base said USD left the company quietly reporting in
        USD with a settings page insisting otherwise. Two sources of truth
        for one fact is a defect whichever one wins; they now have to
        agree.
        """
        self._check_base_currency()

    def _check_base_currency(self):
        if self.base_currency_id and not self.base_currency.is_base:
            raise ValidationError(
                f"{self.base_currency} is not flagged as the base currency. Set "
                "is_base on it, or point the company at the one that is."
            )

    def save(self, *args, **kwargs):
        self._check_base_currency()
        existing = Company.objects.first()
        if self._state.adding and existing is not None:
            # Stay a singleton: fold this into the existing row rather than
            # adding a second, keeping the original audit trail intact.
            self.pk = existing.pk
            self.created_at = existing.created_at
            self.created_by_id = existing.created_by_id
            kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)
        held = _COMPANY.get()
        if held is not None:
            held.pop("company", None)

    @classmethod
    def get(cls):
        """
        The company, read once per request.

        Asked for per line of every document a report walks: the payment
        run asked 8,944 times for one answer. Held only for the request
        (apps.core.middleware), never across them: with several server
        processes a cache that outlived the request would keep serving a
        setting another process had already changed.
        """
        held = _COMPANY.get()
        if held is not None and "company" in held:
            return held["company"]
        company = cls.objects.first() or cls.objects.create(name="My Company")
        if held is not None:
            held["company"] = company
        return company

    def currency(self):
        """The currency this company reports in, from whichever field holds it."""
        if self.base_currency_id:
            return self.base_currency
        return Currency.objects.filter(is_base=True).first()

    def fiscal_year_bounds(self, on_date):
        """The fiscal year (start, end) containing `on_date`."""
        start_year = on_date.year if on_date.month >= self.fiscal_year_start_month else on_date.year - 1
        start = datetime.date(start_year, self.fiscal_year_start_month, 1)
        end = datetime.date(start_year + 1, self.fiscal_year_start_month, 1) - datetime.timedelta(days=1)
        return start, end


from .attachments import Attachment  # noqa: E402,F401
from .errors import ServerError  # noqa: E402,F401
from .history import RecordEvent  # noqa: E402,F401
from .licences import Licence  # noqa: E402,F401
