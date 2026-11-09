from decimal import Decimal

from rest_framework import serializers

from .analytic import CostCentre
from .budgets import Budget, BudgetLine
from .recurring import RecurringJournal, RecurringJournalLine
from .models import (
    Account,
    AccountingPeriod,
    FiscalPosition,
    FiscalPositionTaxMapping,
    JournalEntry,
    JournalLine,
    Payment,
    PartyTaxProfile,
    Tax,
    TaxGroup,
)


class AccountSerializer(serializers.ModelSerializer):
    # What the account sits under, by name, for a page that shows it rather than offers a choice.
    parent_code = serializers.CharField(source="parent.code", read_only=True, default="")
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")

    class Meta:
        model = Account
        fields = ["id", "code", "name", "account_type", "parent", "parent_code", "parent_name", "currency",
                  "is_active", "holds_money"]


class CostCentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostCentre
        fields = ["id", "code", "name", "is_active", "note"]


class AccountingPeriodSerializer(serializers.ModelSerializer):
    closed_by_name = serializers.CharField(source="closed_by.username", read_only=True, default="")

    class Meta:
        model = AccountingPeriod
        fields = ["id", "name", "start_date", "end_date", "closed", "closed_at", "closed_by_name", "note"]
        # Closing is an action with its own permission, not a box to tick.
        read_only_fields = ["closed", "closed_at"]


class BudgetLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")

    class Meta:
        model = BudgetLine
        fields = ["id", "budget", "account", "account_code", "account_name", "cost_centre", "cost_centre_name", "amount"]


class BudgetSerializer(serializers.ModelSerializer):
    lines = BudgetLineSerializer(many=True, read_only=True)

    class Meta:
        model = Budget
        fields = ["id", "code", "name", "start_date", "end_date", "note", "is_active", "lines"]


class RecurringJournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    party_name = serializers.CharField(source="party.name", read_only=True, default="")
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")

    class Meta:
        model = RecurringJournalLine
        fields = ["id", "schedule", "account", "account_code", "account_name", "party", "party_name",
                  "cost_centre", "cost_centre_name", "debit", "credit", "description"]


class RecurringJournalSerializer(serializers.ModelSerializer):
    lines = RecurringJournalLineSerializer(many=True, read_only=True)

    class Meta:
        model = RecurringJournal
        fields = ["id", "code", "memo", "interval", "interval_count", "start_date", "end_date", "next_run_date",
                  "auto_post", "is_active", "lines"]
        # Advanced by each run; set by hand it would skip or repeat a period.
        read_only_fields = ["next_run_date"]


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    party_name = serializers.CharField(source="party.name", read_only=True, default="")
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")

    class Meta:
        model = JournalLine
        fields = ["id", "entry", "account", "account_code", "account_name", "party", "party_name",
                  "debit", "credit", "description", "cost_centre", "cost_centre_name"]

    def validate(self, attrs):
        debit = attrs.get("debit", getattr(self.instance, "debit", 0)) or 0
        credit = attrs.get("credit", getattr(self.instance, "credit", 0)) or 0
        if debit and credit:
            raise serializers.ValidationError("A journal line cannot have both a debit and a credit.")
        if not debit and not credit:
            raise serializers.ValidationError("A journal line must have either a debit or a credit.")
        return attrs


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True, read_only=True)
    posted_by = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "date",
            "reference",
            "memo",
            "posted",
            "posted_at",
            "reverses",
            "recurring_journal",
            "posted_by",
            "lines",
        ]
        # Posting, reversing and the schedule that takes an entry write these, never a person: the
        # second of two assignments here left both writable, so an entry made by hand could claim
        # to reverse a payment's and block its void.
        read_only_fields = ["posted", "posted_at", "reverses", "recurring_journal"]

    def get_posted_by(self, entry):
        """On one entry's page, the document that keeps it, where it is corrected; a list does not ask."""
        if not self.context.get("one"):
            return None
        document = entry.recorded_by()
        return None if document is None else f"{document._meta.verbose_name.capitalize()} {document}"


class TaxGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxGroup
        fields = ["id", "code", "name"]


class TaxSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True, default="")

    class Meta:
        model = Tax
        fields = [
            "id", "code", "name", "group", "computation", "rate", "price_included",
            "include_base_amount", "sequence", "scope", "collected_account",
            "paid_account", "is_active",
            "gst_head", "group_name", "reverse_charge", "reverse_charge_account",
            "reverse_charge_waiting_account",
        ]


class FiscalPositionTaxMappingSerializer(serializers.ModelSerializer):
    source_tax_name = serializers.CharField(source="source_tax.name", read_only=True)
    target_tax_name = serializers.CharField(source="target_tax.name", read_only=True, default="")

    class Meta:
        model = FiscalPositionTaxMapping
        fields = ["id", "fiscal_position", "source_tax", "target_tax", "source_tax_name", "target_tax_name"]


class FiscalPositionSerializer(serializers.ModelSerializer):
    tax_mappings = FiscalPositionTaxMappingSerializer(many=True, read_only=True)

    class Meta:
        model = FiscalPosition
        fields = ["id", "code", "name", "country", "is_active", "tax_mappings"]


class PartyTaxProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyTaxProfile
        fields = ["id", "party", "fiscal_position", "tax_exempt", "exemption_reference",
            "gstin", "gst_state", "gst_registration",
            "pan", "tds_section", "tds_rate_percent", "tds_rate_reference", "msme_category", "udyam_number",
        ]


class PaymentSerializer(serializers.ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)
    # A payment whose entry was reversed, by void() or by hand: the
    # invoices it paid are owed again.
    voided = serializers.SerializerMethodField()

    # What is left to apply, worked out here rather than by a screen adding
    # up the allocations it happened to load (200 at most). Read through
    # the reverse relations, so accounting imports neither trading module.
    unallocated = serializers.SerializerMethodField()
    # Matched to a line of the bank's statement: the bank has seen it.
    reconciled = serializers.BooleanField(source="is_reconciled", read_only=True)

    def get_unallocated(self, obj):
        applied = sum((row.amount for row in obj.invoice_allocations.all()), Decimal("0")) + sum(
            (row.amount for row in obj.bill_allocations.all()), Decimal("0"))
        return str(obj.amount - applied)

    def get_voided(self, obj):
        if obj.voided_entry_id:
            return True
        return bool(obj.journal_entry_id) and len(obj.journal_entry.reversed_by.all()) > 0

    class Meta:
        model = Payment
        fields = [
            "id", "number", "party", "party_name", "direction", "payment_date", "amount", "currency",
            "exchange_rate", "bank_account", "bank_account_name", "counterpart_account", "reference", "memo",
            "journal_entry", "posted", "posted_at", "voided", "unallocated", "reconciled",
        ]
        read_only_fields = ["number", "exchange_rate", "journal_entry", "posted", "posted_at"]
        # Left out, the company's defaults (Payment.save).
        extra_kwargs = {"bank_account": {"required": False},
                        "counterpart_account": {"required": False}}


class MoneyLineSerializerMixin(serializers.Serializer):
    """What a taxed line comes to, for every trading document's lines."""

    gross_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    net_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    def create(self, validated_data):
        """A line that names no taxes takes its item's for its side (O395); one that names none on purpose, []."""
        if "taxes" in validated_data and hasattr(self.Meta.model, "given_taxes"):
            # Named, even as []: set as sent. Not named: the line takes its defaults as it is saved (O459).
            validated_data["given_taxes"] = validated_data.pop("taxes")
        return super().create(validated_data)


class TakesItsLinesMixin(serializers.Serializer):
    """
    A trading document made with its lines in one request (O401). The header and every line are written inside
    the one savepoint AuditableViewSetMixin.perform_create opens around serializer.save(), so a refusal on any line
    leaves nothing behind: no half-made draft for someone to find, or to post, after a person stopped between steps.

    Each line goes through the document's own line serializer, exactly as one added later through the line endpoint
    would: its validation, its default taxes when it names none (O459: `taxes` sent, even [], is taken as given), and
    the taxes_taken_under its document settled on as it was saved (O446). Adding a line needs the line's own add
    permission, as it does there.

    `new_lines` is asked only when the document is made; a saved one's lines are changed one by one.
    """

    line_serializer = None  # the document's line serializer
    line_parent = None  # the line's field naming its document

    new_lines = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)

    def get_field_names(self, declared_fields, info):
        names = list(super().get_field_names(declared_fields, info))
        return names if "new_lines" in names else [*names, "new_lines"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None and "new_lines" in attrs:
            raise serializers.ValidationError(
                {"new_lines": ["Lines are sent with a new document only; add one to a saved document on its own."]})
        return attrs

    def create(self, validated_data):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import PermissionDenied

        rows = validated_data.pop("new_lines", None) or []
        document = super().create(validated_data)
        if not rows:
            return document
        request = self.context.get("request")
        model = self.line_serializer.Meta.model
        permission = f"{model._meta.app_label}.add_{model._meta.model_name}"
        if request is not None and not request.user.has_perm(permission):
            raise PermissionDenied(f"You may not add {model._meta.verbose_name_plural}.")
        user = getattr(request, "user", None)
        stamp = {"created_by": user, "updated_by": user} if user is not None and user.is_authenticated else {}
        said, refused = [], False
        for row in rows:
            line = self.line_serializer(data={**row, self.line_parent: document.pk}, context=self.context)
            if not line.is_valid():
                said.append(line.errors)
                refused = True
                continue
            try:
                line.save(**stamp)
            except DjangoValidationError as error:
                said.append(error.message_dict if hasattr(error, "error_dict") else {"non_field_errors": error.messages})
                refused = True
                continue
            said.append({})
        if refused:
            # Raised inside the savepoint: the header and the lines already written go with it.
            raise serializers.ValidationError({"new_lines": said})
        return document
