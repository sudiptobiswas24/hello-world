from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


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


class PartyRoleAssignment(AuditModel):
    party = models.ForeignKey(Party, related_name="role_assignments", on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=PartyRole.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["party", "role"], name="unique_party_role")
        ]

    def __str__(self):
        return f"{self.party} [{self.role}]"
