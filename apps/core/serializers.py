from rest_framework import serializers

from .models import (
    Address,
    Company,
    Contact,
    Country,
    Currency,
    ExchangeRate,
    Party,
    PartyBankAccount,
    PartyRoleAssignment,
    PartyTag,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.core.customfields import ExtensibleSerializerMixin


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ["id", "code", "name"]


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["id", "code", "name", "symbol", "decimal_places", "is_base"]


class ExchangeRateSerializer(serializers.ModelSerializer):
    currency_code = serializers.CharField(source="currency.code", read_only=True)

    class Meta:
        model = ExchangeRate
        fields = ["id", "currency", "rate", "valid_from", "currency_code"]


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    base_unit_code = serializers.CharField(source="base_unit.code", read_only=True, default="")

    class Meta:
        model = UnitOfMeasure
        fields = ["id", "code", "name", "category", "base_unit", "conversion_factor", "base_unit_code"]


class AddressSerializer(serializers.ModelSerializer):
    one_line = serializers.CharField(read_only=True)

    class Meta:
        model = Address
        fields = [
            "id", "party", "address_type", "label", "line1", "line2", "city", "state",
            "postal_code", "country", "is_primary", "is_active", "one_line",
        ]


class ContactSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Contact
        fields = [
            "id", "party", "first_name", "last_name", "full_name", "job_title",
            "email", "phone", "mobile", "is_primary", "is_active",
        ]


class PartyBankAccountSerializer(serializers.ModelSerializer):
    currency_code = serializers.CharField(source="currency.code", read_only=True, default="")

    class Meta:
        model = PartyBankAccount
        fields = [
            "id", "party", "account_name", "bank_name", "account_number", "iban",
            "swift_bic", "ifsc", "currency", "is_primary", "is_active", "currency_code",
        ]


class PartyTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyTag
        fields = ["id", "name", "description"]


class PaymentTermsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTerms
        fields = [
            "id", "code", "name", "net_days", "discount_percent", "discount_days", "is_active",
        ]


class PartyRoleAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartyRoleAssignment
        fields = ["id", "party", "role"]


class PartySerializer(ExtensibleSerializerMixin, serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    addresses = AddressSerializer(many=True, read_only=True)
    contacts = ContactSerializer(many=True, read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")

    class Meta:
        model = Party
        fields = [
            "id", "code", "name", "legal_name", "email", "phone", "tax_id",
            "default_currency", "payment_terms", "tags", "is_active",
            "website", "cin", "iec", "parent", "parent_name", "notes",
            "roles", "addresses", "contacts", "extra",
        ]

    def get_roles(self, obj):
        return [row.role for row in obj.role_assignments.all()]


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "id", "name", "legal_name", "tax_id", "email", "phone", "website",
            "bank_name", "bank_account_number", "bank_ifsc",
            "base_currency", "address", "fiscal_year_start_month",
            "tax_rounding", "default_inventory_account", "default_cogs_account", "grni_account", "settlement_discount_account", "bad_debt_account", "default_revenue_account", "default_receivable_account", "default_payable_account", "default_bank_account", "default_purchase_expense_account", "fx_gain_account", "fx_loss_account", "vendor_prepayment_account", "settlement_discount_received_account", "purchase_price_variance_account", "purchase_price_tolerance_percent", "net_pay_account", "customer_deposit_account",
        ]
