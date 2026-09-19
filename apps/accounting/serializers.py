from rest_framework import serializers

from .models import (
    Account,
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
    class Meta:
        model = Account
        fields = ["id", "code", "name", "account_type", "parent", "currency", "is_active"]


class JournalLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = JournalLine
        fields = ["id", "entry", "account", "party", "debit", "credit", "description"]

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
            "lines",
        ]
        read_only_fields = ["posted", "posted_at"]


class TaxGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxGroup
        fields = ["id", "code", "name"]


class TaxSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tax
        fields = [
            "id", "code", "name", "group", "computation", "rate", "price_included",
            "include_base_amount", "sequence", "scope", "collected_account",
            "paid_account", "is_active",
        ]


class FiscalPositionTaxMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalPositionTaxMapping
        fields = ["id", "fiscal_position", "source_tax", "target_tax"]


class FiscalPositionSerializer(serializers.ModelSerializer):
    tax_mappings = FiscalPositionTaxMappingSerializer(many=True, read_only=True)

    class Meta:
        model = FiscalPosition
        fields = ["id", "code", "name", "country", "is_active", "tax_mappings"]


class PartyTaxProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyTaxProfile
        fields = ["id", "party", "fiscal_position", "tax_exempt", "exemption_reference"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id", "number", "party", "direction", "payment_date", "amount", "currency",
            "exchange_rate", "bank_account", "counterpart_account", "reference", "memo",
            "journal_entry", "posted", "posted_at",
        ]
        read_only_fields = ["number", "exchange_rate", "journal_entry", "posted", "posted_at"]
