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


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ["id", "code", "name"]


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["id", "code", "name", "symbol", "decimal_places", "is_base"]


class ExchangeRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExchangeRate
        fields = ["id", "currency", "rate", "valid_from"]


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ["id", "code", "name", "category", "base_unit", "conversion_factor"]


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id", "party", "address_type", "label", "line1", "line2", "city", "state",
            "postal_code", "country", "is_primary", "is_active",
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
    class Meta:
        model = PartyBankAccount
        fields = [
            "id", "party", "account_name", "bank_name", "account_number", "iban",
            "swift_bic", "currency", "is_primary", "is_active",
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


class PartySerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    addresses = AddressSerializer(many=True, read_only=True)
    contacts = ContactSerializer(many=True, read_only=True)

    class Meta:
        model = Party
        fields = [
            "id", "code", "name", "legal_name", "email", "phone", "tax_id",
            "default_currency", "payment_terms", "tags", "is_active",
            "roles", "addresses", "contacts",
        ]

    def get_roles(self, obj):
        return list(obj.role_assignments.values_list("role", flat=True))


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "id", "name", "legal_name", "tax_id", "email", "phone", "website",
            "base_currency", "address", "fiscal_year_start_month",
        ]
