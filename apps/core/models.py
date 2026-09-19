from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base giving every kernel/module model a consistent audit trail."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Currency(TimeStampedModel):
    code = models.CharField(max_length=3, unique=True, help_text="ISO 4217 code, e.g. USD")
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, blank=True)
    decimal_places = models.PositiveSmallIntegerField(default=2)

    class Meta:
        verbose_name_plural = "currencies"
        ordering = ["code"]

    def __str__(self):
        return self.code


class UnitOfMeasureCategory(models.TextChoices):
    COUNT = "count", "Count"
    WEIGHT = "weight", "Weight"
    VOLUME = "volume", "Volume"
    LENGTH = "length", "Length"
    TIME = "time", "Time"
    OTHER = "other", "Other"


class UnitOfMeasure(TimeStampedModel):
    code = models.CharField(max_length=16, unique=True, help_text="e.g. pcs, kg, L")
    name = models.CharField(max_length=64)
    category = models.CharField(
        max_length=16, choices=UnitOfMeasureCategory.choices, default=UnitOfMeasureCategory.COUNT
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Party(TimeStampedModel):
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
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "parties"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PartyRole(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    VENDOR = "vendor", "Vendor"
    EMPLOYEE = "employee", "Employee"
    OTHER = "other", "Other"


class PartyRoleAssignment(TimeStampedModel):
    party = models.ForeignKey(Party, related_name="role_assignments", on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=PartyRole.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["party", "role"], name="unique_party_role")
        ]

    def __str__(self):
        return f"{self.party} [{self.role}]"
