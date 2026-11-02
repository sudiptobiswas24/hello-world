"""
Set up a new company's Indian books: a chart of accounts, the GST taxes
and tax groups for the current slabs, the intra- and inter-state fiscal
positions (and export under LUT), and the TDS and TCS accounts, with the
TCS section for scrap. It adds the countries of ISO 3166-1 too, which
nothing else does (O403); setup_stores then seeds the stock adjustment
reasons against the two stock accounts made here.

Everything it seeds is a default for the accountant to review, and is
listed as one (accounting.SeededDefault) until it is marked reviewed:
the slabs, the account names and codes, the TCS rate. The TCS rate is
seeded unconfirmed, so nothing is collected at it until the accountant
confirms it (TcsRate.confirm). It never overwrites what exists: an
account, a tax, a group, a position or a section whose code is taken is
left exactly as it is and reported as kept. Run again, it adds only what
is missing.

    python manage.py setup_indian_books [--gstin 19AAACB1234C1ZC]

The slabs are those from 22 September 2025 (0, 5, 18 and 40 per cent);
cess, which varies by good, is not seeded.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    SeededDefault,
    Tax,
    TaxGroup,
)
from apps.accounting.statutory_load import Loader, load_tcs

WAITING = "1319"
A, L, E, I, X = (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME,
                 AccountType.EXPENSE)
ACCOUNTS = [
    ("1100", "Trade receivables", A, False), ("1200", "Bank", A, True), ("1210", "Cash", A, True),
    ("1300", "Inventory", A, False),
    ("1311", "Input CGST", A, False), ("1312", "Input SGST", A, False), ("1313", "Input IGST", A, False),
    ("1320", "TDS receivable (deducted by customers)", A, False),
    ("1321", "TCS receivable (collected by suppliers)", A, False),
    # Where the credit of a reverse-charge tax waits until the tax is paid in cash and its document stands
    # (gst.reverse_charge, review_rcm F2): a bill of such a tax is refused without it.
    (WAITING, "Reverse-charge credit awaiting payment", A, False),
    ("2000", "Trade payables", L, False),
    ("2201", "Output CGST", L, False), ("2202", "Output SGST", L, False), ("2203", "Output IGST", L, False),
    ("2250", "TDS payable", L, False), ("2260", "TCS payable", L, False),
    ("3000", "Capital", E, False),
    # What the opening position posts against (docs/IMPORT.md), and the OPENING stock reason setup_stores seeds.
    ("3900", "Opening balances", E, False),
    ("4000", "Sales", I, False), ("4100", "Scrap sales", I, False),
    # The other sides of a stock adjustment, for setup_stores' reasons (O403).
    ("4200", "Stock found on count", I, False), ("5300", "Stock written off", X, False),
    ("5000", "Purchases", X, False), ("5100", "Freight inward", X, False), ("5900", "Round off", X, False),
    # What the application itself books to, which a fresh install had no account for (seen on a first
    # installation, 2 November): every one of these is a company default, wired below.
    ("1360", "Claims recoverable from transporters", A, False), ("1410", "Advances to suppliers", A, False),
    ("2150", "Goods received not invoiced", L, False), ("2300", "Advances from customers", L, False),
    ("2400", "Salaries payable", L, False),
    ("4310", "Discount received", I, False), ("4900", "Exchange gain", I, False),
    ("5010", "Cost of goods sold", X, False), ("5150", "Purchase price variance", X, False),
    ("5200", "Bad debts", X, False), ("5310", "Discount allowed", X, False), ("5910", "Exchange loss", X, False),
]
# The company's defaults, each from the account seeded under its code, and the words an account already there under
# that code must carry to be wired (a same-type account named for something else is not: a liability 2150 named
# Rent payable is not where goods received and not billed wait).
DEFAULTS = [
    ("default_receivable_account", "1100", ("receivable", "debtor")),
    ("default_payable_account", "2000", ("payable", "creditor")),
    ("default_bank_account", "1200", ("bank",)),
    ("default_revenue_account", "4000", ("sale", "revenue", "turnover")),
    ("default_inventory_account", "1300", ("inventor", "stock")),
    ("default_cogs_account", "5010", ("cost of goods", "cost of sales", "cogs")),
    ("grni_account", "2150", ("not invoiced", "not billed", "grni")),
    ("default_purchase_expense_account", "5000", ("purchase",)),
    ("customer_deposit_account", "2300", ("advance", "deposit")),
    ("vendor_prepayment_account", "1410", ("advance", "prepa")),
    ("fx_gain_account", "4900", ("exchange", "forex", "fx")),
    ("fx_loss_account", "5910", ("exchange", "forex", "fx")),
    ("settlement_discount_account", "5310", ("discount",)),
    ("settlement_discount_received_account", "4310", ("discount",)),
    ("bad_debt_account", "5200", ("bad debt", "doubtful", "written off")),
    ("purchase_price_variance_account", "5150", ("variance",)),
    ("net_pay_account", "2400", ("salar", "wage", "net pay")),
    ("claims_recoverable_account", "1360", ("claim",)),
]
# The plant's own currency, and those its imports and exports are most often priced in (ISO 4217).
CURRENCIES = [("INR", "Indian rupee", "\u20b9"), ("USD", "US dollar", "$"), ("EUR", "Euro", "\u20ac"),
              ("GBP", "Pound sterling", "\u00a3"), ("CNY", "Chinese yuan", "\u00a5"), ("JPY", "Japanese yen", "\u00a5"),
              ("AED", "UAE dirham", "AED"), ("SGD", "Singapore dollar", "S$")]
APRIL = 4
# Standard credit terms (code, name, days). Forty-five is the most a micro or small supplier may be made to wait
# (MSMED Act 2006 s.15).
TERMS = [("IMMEDIATE", "Immediate", 0), ("N15", "Net 15 days", 15), ("N30", "Net 30 days", 30),
         ("N45", "Net 45 days (the MSME limit)", 45), ("N60", "Net 60 days", 60)]
SLABS = [Decimal("0"), Decimal("5"), Decimal("18"), Decimal("40")]
# The goods TCS is collected on and its rates, named by the law of each day (206C(1) of the 1961 Act until 31 March
# 2026, 394(1) of the 2025 Act from 1 April 2026), are rows of statutory/tcs_rates.csv, loaded by the loader
# setup_statutory shares (accounting.statutory_load), unconfirmed: nothing is collected at either until the
# accountant confirms it.
# What each account a tax or section is wired into must be, by type and by what its name says (review F12).
PURPOSES = {"4200": ("found",), "5300": ("written off", "write-off", "write off"),
            "1311": ("cgst",), "1312": ("sgst",), "1313": ("igst",), "2201": ("cgst",), "2202": ("sgst",),
            "2203": ("igst",), "2250": ("tds",), "2260": ("tcs",)}


def _plain(rate):
    return f"{rate.normalize():f}"


class Command(BaseCommand):
    help = "Set up a new company's Indian books, every figure a default for the accountant to review."

    def add_arguments(self, parser):
        parser.add_argument("--gstin", help="The company's GSTIN, for its GST settings if it has none yet.")

    def handle(self, *args, gstin=None, **options):
        self.made, self.kept, self.collisions = [], [], []
        from apps.core.models import numbering_first

        with transaction.atomic():
            # The company's numbering row first (O257): its default bank is held as it is saved, below, after the
            # taxes, and the numbering row is the first lock of a transaction that takes it.
            numbering_first()
            accounts = {code: self.account(code, name, kind, money) for code, name, kind, money in ACCOUNTS}
            intra, inter = {}, {}
            for slab in SLABS:
                half = slab / 2
                group = self.one(TaxGroup, f"GST{_plain(slab)}", name=f"GST {_plain(slab)}% (CGST + SGST)")
                igroup = self.one(TaxGroup, f"IGST{_plain(slab)}", name=f"IGST {_plain(slab)}%")
                intra[slab] = [
                    self.tax(f"CGST{_plain(half)}", f"CGST {_plain(half)}%", half, "cgst", group, accounts["2201"],
                             accounts["1311"]),
                    self.tax(f"SGST{_plain(half)}", f"SGST {_plain(half)}%", half, "sgst", group, accounts["2202"],
                             accounts["1312"]),
                ]
                inter[slab] = self.tax(f"IGST{_plain(slab)}", f"IGST {_plain(slab)}%", slab, "igst", igroup,
                                       accounts["2203"], accounts["1313"])
            self.one(FiscalPosition, "INTRA", name="Within the state: CGST and SGST as charged")
            positions = {
                "INTER": ("Across a state line: CGST and SGST to IGST at the whole rate", inter),
                "EXPORT-LUT": ("Export or SEZ under bond or LUT: no IGST", {slab: inter[SLABS[0]] for slab in SLABS}),
            }
            for code, (name, targets) in positions.items():
                position = self.one(FiscalPosition, code, name=name)
                for slab in SLABS:
                    cgst, sgst = intra[slab]
                    self.mapping(position, cgst, targets[slab])
                    self.mapping(position, sgst, None)
            loader = Loader()
            load_tcs(loader, payable_account=accounts["2260"])
            self.made += loader.made
            self.kept += loader.kept
            # Every reverse-charge tax waits its credit in that account unless it names one already (F2 cutover).
            for tax in Tax.objects.filter(reverse_charge=True, reverse_charge_waiting_account__isnull=True):
                Tax.objects.filter(pk=tax.pk).update(reverse_charge_waiting_account=accounts[WAITING])
                self.made.append(f"tax {tax.code}: reverse-charge credit waits in {WAITING}")
            self.company(accounts)
            from apps.core.models import PaymentTerms

            for code, name, days in TERMS:
                self.one(PaymentTerms, code, name=name, net_days=days)
            if gstin:
                self.gst_settings(gstin, FiscalPosition.objects.get(code="INTER"))
            # The countries of ISO 3166-1 (O403): an export customer or overseas vendor had none to be given.
            from apps.core.countries import seed_countries

            countries_made, countries_kept = seed_countries()
        for line in self.made:
            self.stdout.write(f"seeded, to review: {line}")
        for line in self.kept:
            self.stdout.write(f"kept as it was: {line}")
        for line in self.collisions:
            self.stdout.write(f"collision, for the accountant: {line}")
        self.stdout.write(f"{countries_made} countries added, {countries_kept} already there.")
        self.stdout.write(f"{len(self.made)} seeded for the accountant to review, {len(self.kept)} kept.")
        self.stdout.write("Then: python manage.py setup_stores, for the stock adjustment reasons.")

    def company(self, accounts):
        """
        The company made ready to post, as an Indian plant keeps its books: rupees its base currency, its year from
        April, and every default account the application books to wired from the chart above. A fresh install had
        none of these (2 November): no currency to choose, the year from January, and seventeen accounts to pick by
        hand before the first invoice could post. Only what is unset is set; the year is moved only while nothing is
        posted, since every period and report already posted reads it.
        """
        from apps.accounting.models import JournalEntry
        from apps.core.models import Company, Currency

        currencies = {code: self.one(Currency, code, name=name, symbol=symbol) for code, name, symbol in CURRENCIES}
        company = Company.get()
        did = []
        if company.base_currency_id is None:
            base = Currency.objects.filter(is_base=True).first()
            if base is None:
                base = currencies["INR"]
                base.is_base = True
                base.save(update_fields=["is_base", "updated_at"])
            company.base_currency = base
            did.append(f"base currency {base.code}")
        else:
            self.kept.append(f"company base currency {company.base_currency.code}")
        if company.fiscal_year_start_month != APRIL:
            month = company.get_fiscal_year_start_month_display()
            if JournalEntry.objects.exists():
                self.kept.append(f"company year from {month}: entries are posted, so it is not moved")
            else:
                company.fiscal_year_start_month = APRIL
                did.append(f"year from April (was {month})")
        for field, code, words in DEFAULTS:
            if getattr(company, f"{field}_id") is not None:
                self.kept.append(f"company {field.replace('_', ' ')}")
                continue
            account = accounts[code]
            made_now = any(line.startswith(f"account {account.code} ") for line in self.made)
            if made_now or any(word in account.name.lower() for word in words):
                setattr(company, field, account)
                did.append(f"{field.replace('_', ' ')} {account.code}")
            else:
                self.collisions.append(
                    f"company {field.replace('_', ' ')}: account {account.code} is {account.name!r}, not what it is "
                    "for; left unset, choose it on Settings > Company")
        if did:
            company.save()
            label = f"company {company.name}: " + "; ".join(did)
            if SeededDefault.objects.filter(model=company._meta.label_lower, object_id=company.pk).exists():
                self.made.append(label)
            else:
                self.seeded(company, label)

    def seeded(self, instance, label):
        SeededDefault.objects.create(model=instance._meta.label_lower, object_id=instance.pk, label=label[:255])
        self.made.append(label)

    def one(self, model, code, **fields):
        found = model.objects.filter(code=code).first()
        if found is not None:
            self.kept.append(f"{model._meta.verbose_name} {code}")
            return found
        made = model.objects.create(code=code, **fields)
        self.seeded(made, f"{model._meta.verbose_name} {code} {fields.get('name', '')}".strip())
        return made

    def account(self, code, name, kind, holds_money):
        """
        The account by `code`, made if free. Taken by one of another type or purpose, it is left as it is,
        reported for the accountant, and nothing is wired into it: the account is seeded under a free code
        beside it instead. CGST never collects into the plant's Rent payable (review F12).
        """
        found = Account.objects.filter(code=code).first()
        if found is None:
            return self.one(Account, code, name=name, account_type=kind, holds_money=holds_money)
        words = PURPOSES.get(code, ())
        if found.account_type == kind and (not words or any(word in found.name.lower() for word in words)):
            self.kept.append(f"account {code}")
            return found
        spare = next(f"{code}-IN{n or ''}" for n in range(100)
                     if not Account.objects.filter(code=f"{code}-IN{n or ''}").exists())
        self.collisions.append(
            f"account {code} is {found.name!r} ({found.get_account_type_display().lower()}), not {name}: left as it "
            f"is and nothing wired into it; {name} seeded as {spare}")
        return self.one(Account, spare, name=name, account_type=kind, holds_money=holds_money)

    def tax(self, code, name, rate, head, group, collected, paid):
        return self.one(Tax, code, name=name, rate=rate, gst_head=head, group=group, collected_account=collected,
                        paid_account=paid)

    def mapping(self, position, source, target):
        if FiscalPositionTaxMapping.objects.filter(fiscal_position=position, source_tax=source).exists():
            return
        mapping = FiscalPositionTaxMapping.objects.create(fiscal_position=position, source_tax=source,
                                                          target_tax=target)
        self.seeded(mapping, f"{position.code}: {mapping}")

    def gst_settings(self, gstin, inter):
        from apps.accounting.gst import GstSettings

        if GstSettings.objects.exists():
            self.kept.append("GST settings")
            return
        settings = GstSettings.objects.create(gstin=gstin, interstate_position=inter)
        self.seeded(settings, f"GST settings for {settings.gstin}")
