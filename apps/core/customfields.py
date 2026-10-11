"""
Columns a keeper adds without a developer: a customer's credit rating,
an item's shelf, a bill's gate pass number.

A field is defined once for a kind of record (Settings → Custom fields)
and its values live in one JSON column on that record's own table,
`extra`. Nothing else reads them: no posting, no ledger, no stock
figure, no report, no printed paper, so a field added or filled wrong
can move no number and touch no other table. What is enforced is the
value's shape: a key that is not defined is refused, a number is a
number, a date a date, a choice one of the choices; "required" holds
for what the office types (the API), not for the system's own saves,
so a field made required today does not stop an import or a posting
that never knew it. A field is deactivated, never deleted, once it has
values; its key never changes.
"""

import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .audit import AuditableViewSetMixin
from .models import AuditModel, to_date

__all__ = ["CustomField", "CustomFieldViewSet", "EXTENSIBLE", "ExtensibleSerializerMixin", "check_extra"]

# The kinds of record that take custom fields, as the office names them.
EXTENSIBLE = {
    "core.party": "Customers and vendors",
    "inventory.item": "Items",
    "hr.employee": "Employees",
    "assets.fixedasset": "Fixed assets",
    "manufacturing.machine": "Machines",
    "sales.salesorder": "Sales orders",
    "sales.invoice": "Invoices and credit notes",
    "purchasing.purchaseorder": "Purchase orders",
    "purchasing.bill": "Bills and debit notes",
}
KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PLACES = Decimal("0.0001")


class FieldType(models.TextChoices):
    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    DATE = "date", "Date"
    YES_NO = "yes_no", "Yes or no"
    CHOICE = "choice", "One of a list"


class CustomField(AuditModel):
    """One column a keeper added to one kind of record."""

    kind = models.CharField(max_length=32, choices=sorted(EXTENSIBLE.items()))
    key = models.CharField(max_length=32, help_text="How the value is stored and sent: letters, digits and _ ; never changes.")
    label = models.CharField(max_length=64)
    field_type = models.CharField(max_length=8, choices=FieldType.choices, default=FieldType.TEXT)
    choices = models.TextField(blank=True, help_text="One choice a line, for a field that is one of a list.")
    required = models.BooleanField(default=False, help_text="Must be filled when the record is saved from the office.")
    hint = models.CharField(max_length=255, blank=True)
    position = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True, help_text="Off, the field is hidden and its values kept.")

    class Meta:
        ordering = ["kind", "position", "id"]
        constraints = [models.UniqueConstraint(fields=["kind", "key"], name="one_custom_field_per_key")]

    def __str__(self):
        return f"{EXTENSIBLE.get(self.kind, self.kind)}: {self.label}"

    def choice_list(self):
        return [line.strip() for line in self.choices.splitlines() if line.strip()]

    def save(self, *args, **kwargs):
        self.key = (self.key or "").strip().lower()
        if not KEY.match(self.key):
            raise ValidationError({"key": ["A key is a word of letters, digits and underscores, starting with a letter."]})
        if self.kind not in EXTENSIBLE:
            raise ValidationError({"kind": [f"Custom fields go on {', '.join(EXTENSIBLE.values())}."]})
        if self.field_type == FieldType.CHOICE and not self.choice_list():
            raise ValidationError({"choices": ["A field that is one of a list needs the list, one choice a line."]})
        if not self._state.adding:
            before = CustomField.objects.get(pk=self.pk)
            if (before.kind, before.key) != (self.kind, self.key):
                raise ValidationError({"key": ["The key is how every record stores the value; it cannot change. "
                                               "Deactivate this field and make another."]})
            if before.field_type != self.field_type:
                raise ValidationError({"field_type": ["The kind of value cannot change once records may hold one; "
                                                      "deactivate this field and make another."]})
        super().save(*args, **kwargs)


def _one(field, value):
    """`value` as the field stores it, or a sentence why not."""
    if value is None or value == "":
        return None
    if field.field_type == FieldType.TEXT:
        text = str(value).strip()
        if len(text) > 255:
            raise ValueError("is longer than 255 characters.")
        return text
    if field.field_type == FieldType.NUMBER:
        try:
            number = Decimal(str(value).replace(",", "").strip())
        except InvalidOperation:
            raise ValueError(f"{value!r} is not a number.") from None
        if not number.is_finite():
            raise ValueError(f"{value!r} is not a number.")
        if number != number.quantize(PLACES):
            raise ValueError("has more than four decimal places.")
        # The shortest exact text: 30 not 3E+1, 12.5 not 12.5000.
        return format(number.quantize(PLACES).normalize(), "f")
    if field.field_type == FieldType.DATE:
        try:
            day = to_date(value)
        except (ValueError, ValidationError):
            raise ValueError(f"{value!r} is not a date; give it as YYYY-MM-DD.") from None
        if day is None:
            raise ValueError(f"{value!r} is not a date; give it as YYYY-MM-DD.")
        return day.isoformat()
    if field.field_type == FieldType.YES_NO:
        if isinstance(value, bool):
            return value
        said = str(value).strip().lower()
        if said in ("yes", "y", "true", "1"):
            return True
        if said in ("no", "n", "false", "0"):
            return False
        raise ValueError("is yes or no.")
    if field.field_type == FieldType.CHOICE:
        text = str(value).strip()
        if text not in field.choice_list():
            raise ValueError(f"is one of {', '.join(field.choice_list())}.")
        return text
    raise ValueError("has a kind nobody can read.")


def check_extra(kind, extra, *, from_office=False):
    """
    `extra` as the record keeps it: every key a defined field of `kind`,
    every value in its shape. With `from_office`, a required field that
    is empty is refused too. Problems are raised beside `extra.<key>`.
    """
    if extra in (None, ""):
        extra = {}
    if not isinstance(extra, dict):
        raise ValidationError({"extra": ["The extra fields are a set of named values."]})
    if not extra and not from_office:
        return {}  # nothing to check, and no query on the system's own saves (an import of thousands)
    fields = {field.key: field for field in CustomField.objects.filter(kind=kind)}
    kept, problems = {}, {}
    for key, value in extra.items():
        field = fields.get(key)
        if field is None:
            problems[f"extra.{key}"] = [f"{EXTENSIBLE.get(kind, kind)} have no field called {key}."]
            continue
        if not field.is_active:
            kept[key] = value  # hidden, kept as it was
            continue
        try:
            value = _one(field, value)
        except ValueError as error:
            problems[f"extra.{key}"] = [f"{field.label} {error}"]
            continue
        if value is not None:
            kept[key] = value
    if from_office:
        for key, field in fields.items():
            if (field.is_active and field.required and kept.get(key) in (None, "")
                    and f"extra.{key}" not in problems):
                problems[f"extra.{key}"] = [f"{field.label} is required."]
    if problems:
        raise ValidationError(problems)
    return kept


class ExtensibleSerializerMixin:
    """
    For a serializer of an Extensible model: `extra` is checked as the
    office typed it, required fields included, and a problem is answered
    beside `extra.<key>` so the screen shows it under the right box.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "extra" in attrs:
            try:
                attrs["extra"] = check_extra(self.Meta.model._meta.label_lower, attrs["extra"], from_office=True)
            except ValidationError as error:
                raise serializers.ValidationError(error.message_dict)
        return attrs


class CustomFieldSerializer(serializers.ModelSerializer):
    kind_label = serializers.SerializerMethodField()
    choice_list = serializers.SerializerMethodField()

    class Meta:
        model = CustomField
        fields = ["id", "kind", "kind_label", "key", "label", "field_type", "choices", "choice_list", "required",
                  "hint", "position", "is_active"]

    def get_kind_label(self, row):
        return EXTENSIBLE.get(row.kind, row.kind)

    def get_choice_list(self, row):
        return row.choice_list()


class CustomFieldViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    The keeper's fields. Everyone in the office reads them (a screen
    asks for its kind's); the keepers make and change them. Deleting
    one is refused once any record holds a value under its key.
    """

    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer
    filter_fields = ["kind", "is_active", "field_type"]
    search_fields = ["key", "label"]
    ordering_fields = ["kind", "position", "label"]

    @action(detail=False, methods=["get"])
    def kinds(self, request):
        return Response([{"kind": kind, "label": label} for kind, label in EXTENSIBLE.items()])

    def perform_destroy(self, instance):
        from django.apps import apps

        model = apps.get_model(instance.kind)
        if model.objects.filter(extra__has_key=instance.key).exists():
            raise serializers.ValidationError([f"Records hold a value under {instance.key}; deactivate the field "
                                               "instead, which hides it and keeps them."])
        super().perform_destroy(instance)
