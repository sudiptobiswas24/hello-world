from rest_framework import serializers

from .models import Account, JournalEntry, JournalLine


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
