import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import (
    Address,
    AddressType,
    Company,
    Contact,
    Country,
    Currency,
    DocumentSequence,
    ExchangeRate,
    Party,
    PartyBankAccount,
    PaymentTerms,
)


class ExchangeRateTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.05"), valid_from=datetime.date(2026, 1, 1)
        )
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.10"), valid_from=datetime.date(2026, 6, 1)
        )

    def test_base_currency_rate_is_always_one(self):
        self.assertEqual(self.usd.rate_on(datetime.date(2026, 3, 1)), Decimal("1"))

    def test_uses_the_rate_effective_on_that_date(self):
        self.assertEqual(self.eur.rate_on(datetime.date(2026, 3, 1)), Decimal("1.05"))
        self.assertEqual(self.eur.rate_on(datetime.date(2026, 7, 1)), Decimal("1.10"))

    def test_rate_on_the_day_it_takes_effect_is_the_new_rate(self):
        self.assertEqual(self.eur.rate_on(datetime.date(2026, 6, 1)), Decimal("1.10"))

    def test_missing_rate_raises_rather_than_silently_assuming_one(self):
        with self.assertRaises(ValidationError):
            self.eur.rate_on(datetime.date(2025, 12, 31))

    def test_converting_to_base(self):
        self.assertEqual(
            self.eur.to_base(Decimal("100"), datetime.date(2026, 3, 1)), Decimal("105.00")
        )

    def test_converting_between_two_non_base_currencies(self):
        gbp = Currency.objects.create(code="GBP", name="Pound")
        ExchangeRate.objects.create(
            currency=gbp, rate=Decimal("1.25"), valid_from=datetime.date(2026, 1, 1)
        )
        # 100 EUR -> 105 USD -> 84 GBP
        result = self.eur.convert_to(Decimal("100"), gbp, datetime.date(2026, 3, 1))
        self.assertEqual(result.quantize(Decimal("0.01")), Decimal("84.00"))

    def test_same_currency_conversion_is_a_noop(self):
        self.assertEqual(self.eur.convert_to(Decimal("42"), self.eur), Decimal("42"))

    def test_one_rate_per_currency_per_date(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExchangeRate.objects.create(
                    currency=self.eur, rate=Decimal("9"), valid_from=datetime.date(2026, 1, 1)
                )

    def test_base_currency_rate_must_be_one(self):
        rate = ExchangeRate(currency=self.usd, rate=Decimal("2"), valid_from=datetime.date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            rate.full_clean()


class PaymentTermsTests(TestCase):
    def test_net_30_due_date(self):
        terms = PaymentTerms.objects.create(code="NET30", name="Net 30", net_days=30)
        self.assertEqual(
            terms.due_date(datetime.date(2026, 1, 1)), datetime.date(2026, 1, 31)
        )

    def test_early_settlement_discount(self):
        terms = PaymentTerms.objects.create(
            code="2-10-N30", name="2/10 Net 30", net_days=30,
            discount_percent=Decimal("2"), discount_days=10,
        )
        self.assertEqual(
            terms.discount_due_date(datetime.date(2026, 1, 1)), datetime.date(2026, 1, 11)
        )
        self.assertEqual(terms.discount_amount(Decimal("1000")), Decimal("20.00"))

    def test_no_discount_returns_zero_and_no_date(self):
        terms = PaymentTerms.objects.create(code="NET15", name="Net 15", net_days=15)
        self.assertIsNone(terms.discount_due_date(datetime.date(2026, 1, 1)))
        self.assertEqual(terms.discount_amount(Decimal("1000")), Decimal("0"))

    def test_discount_window_cannot_exceed_net_term(self):
        terms = PaymentTerms(
            code="BAD", name="Bad", net_days=10, discount_percent=Decimal("2"), discount_days=30
        )
        with self.assertRaises(ValidationError):
            terms.full_clean()

    def test_discount_percent_requires_a_window(self):
        terms = PaymentTerms(code="BAD2", name="Bad", net_days=30, discount_percent=Decimal("2"))
        with self.assertRaises(ValidationError):
            terms.full_clean()


class DocumentSequenceTests(TestCase):
    def setUp(self):
        self.sequence = DocumentSequence.objects.create(
            code="sales.invoice", name="Customer Invoices", prefix="INV-", padding=5
        )

    def test_formats_with_prefix_year_and_padding(self):
        value = self.sequence.next_value(datetime.date(2026, 3, 1))
        self.assertEqual(value, "INV-2026-00001")

    def test_numbers_increment_and_never_repeat(self):
        issued = [self.sequence.next_value(datetime.date(2026, 3, 1)) for _ in range(5)]
        self.assertEqual(issued[-1], "INV-2026-00005")
        self.assertEqual(len(set(issued)), 5)

    def test_resets_at_the_start_of_a_new_year(self):
        self.sequence.next_value(datetime.date(2026, 12, 31))
        self.sequence.next_value(datetime.date(2026, 12, 31))
        self.assertEqual(self.sequence.next_value(datetime.date(2027, 1, 1)), "INV-2027-00001")

    def test_no_reset_when_disabled(self):
        self.sequence.reset_yearly = False
        self.sequence.save()
        self.sequence.next_value(datetime.date(2026, 12, 31))
        self.assertEqual(self.sequence.next_value(datetime.date(2027, 1, 1)), "INV-2027-00002")

    def test_peek_does_not_consume_a_number(self):
        peeked = self.sequence.peek(datetime.date(2026, 3, 1))
        self.assertEqual(peeked, "INV-2026-00001")
        self.assertEqual(self.sequence.next_value(datetime.date(2026, 3, 1)), "INV-2026-00001")

    def test_year_can_be_omitted(self):
        sequence = DocumentSequence.objects.create(
            code="po", name="POs", prefix="PO-", padding=4, include_year=False
        )
        self.assertEqual(sequence.next_value(datetime.date(2026, 3, 1)), "PO-0001")


class AddressAndContactTests(TestCase):
    def setUp(self):
        self.party = Party.objects.create(code="C-1", name="Acme")
        self.country = Country.objects.create(code="US", name="United States")

    def make_address(self, address_type=AddressType.BILLING, primary=True, city="Springfield"):
        return Address.objects.create(
            party=self.party, address_type=address_type, line1="1 Main St",
            city=city, postal_code="12345", country=self.country, is_primary=primary,
        )

    def test_only_one_primary_address_per_type(self):
        self.make_address()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_address(city="Shelbyville")

    def test_primary_billing_and_shipping_can_coexist(self):
        self.make_address(AddressType.BILLING)
        self.make_address(AddressType.SHIPPING, city="Ogdenville")
        self.assertEqual(self.party.addresses.filter(is_primary=True).count(), 2)

    def test_shipping_falls_back_to_billing(self):
        billing = self.make_address(AddressType.BILLING)
        self.assertEqual(self.party.shipping_address(), billing)

    def test_shipping_address_preferred_when_present(self):
        self.make_address(AddressType.BILLING)
        shipping = self.make_address(AddressType.SHIPPING, city="Ogdenville")
        self.assertEqual(self.party.shipping_address(), shipping)

    def test_formatted_address_skips_empty_parts(self):
        address = self.make_address()
        self.assertEqual(address.formatted(), "1 Main St\nSpringfield 12345\nUnited States")

    def test_only_one_primary_contact_per_party(self):
        Contact.objects.create(party=self.party, first_name="Ada", is_primary=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Contact.objects.create(party=self.party, first_name="Grace", is_primary=True)

    def test_primary_contact_lookup(self):
        Contact.objects.create(party=self.party, first_name="Ada", last_name="Lovelace")
        grace = Contact.objects.create(
            party=self.party, first_name="Grace", last_name="Hopper", is_primary=True
        )
        self.assertEqual(self.party.primary_contact(), grace)
        self.assertEqual(grace.full_name(), "Grace Hopper")


class BankAccountTests(TestCase):
    def setUp(self):
        self.party = Party.objects.create(code="V-1", name="Supplier")

    def test_account_needs_a_number_or_iban(self):
        account = PartyBankAccount(party=self.party, account_name="Main")
        with self.assertRaises(ValidationError):
            account.full_clean()

    def test_iban_alone_is_enough(self):
        account = PartyBankAccount(
            party=self.party, account_name="Main", iban="DE89370400440532013000"
        )
        account.full_clean()  # should not raise

    def test_only_one_primary_account_per_party(self):
        PartyBankAccount.objects.create(
            party=self.party, account_name="Main", account_number="123", is_primary=True
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PartyBankAccount.objects.create(
                    party=self.party, account_name="Other", account_number="456", is_primary=True
                )


class CompanyTests(TestCase):
    def test_company_is_a_singleton(self):
        Company.objects.create(name="First")
        Company.objects.create(name="Second")
        self.assertEqual(Company.objects.count(), 1)
        self.assertEqual(Company.objects.get().name, "Second")

    def test_get_creates_a_default_profile_then_reuses_it(self):
        company = Company.get()
        self.assertEqual(Company.get().pk, company.pk)
        self.assertEqual(Company.objects.count(), 1)

    def test_second_company_preserves_the_original_audit_trail(self):
        first = Company.objects.create(name="First")
        original_created_at = first.created_at
        Company.objects.create(name="Second")
        company = Company.objects.get()
        self.assertEqual(company.name, "Second")
        self.assertEqual(company.created_at, original_created_at)

    def test_calendar_fiscal_year_bounds(self):
        company = Company.objects.create(name="Co", fiscal_year_start_month=1)
        start, end = company.fiscal_year_bounds(datetime.date(2026, 5, 15))
        self.assertEqual(start, datetime.date(2026, 1, 1))
        self.assertEqual(end, datetime.date(2026, 12, 31))

    def test_july_fiscal_year_bounds_before_and_after_the_start_month(self):
        company = Company.objects.create(name="Co", fiscal_year_start_month=7)

        start, end = company.fiscal_year_bounds(datetime.date(2026, 8, 1))
        self.assertEqual((start, end), (datetime.date(2026, 7, 1), datetime.date(2027, 6, 30)))

        start, end = company.fiscal_year_bounds(datetime.date(2026, 3, 1))
        self.assertEqual((start, end), (datetime.date(2025, 7, 1), datetime.date(2026, 6, 30)))
