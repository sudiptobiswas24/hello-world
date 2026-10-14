"""
Set up a new company's Indian books: a chart of accounts, the GST taxes
and tax groups for the current slabs, the intra- and inter-state fiscal
positions (and export under LUT), and the TDS and TCS accounts, with the
TCS section for scrap.

Everything it seeds is a default for the accountant to review, and is
listed as one (accounting.SeededDefault) until it is marked reviewed:
the slabs, the account names and codes, the TCS rate. The TCS rate is
seeded unconfirmed, so nothing is collected at it until the accountant
confirms it (TcsRate.confirm). It never overwrites what exists: an
account, a tax, a group, a position or a section whose code is taken is
left exactly as it is and reported as kept. Run again, it adds only what
is missing.

    python manage.py setup_indian_books [--gstin 19AAACB1234C1Z5]

The slabs are those from 22 September 2025 (0, 5, 18 and 40 per cent);
cess, which varies by good, is not seeded.
"""

import datetime
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
    TcsRate,
    TcsSection,
)

A, L, E, I, X = (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME,
                 AccountType.EXPENSE)
ACCOUNTS = [
    ("1100", "Trade receivables", A, False), ("1200", "Bank", A, True), ("1210", "Cash", A, True),
    ("1300", "Inventory", A, False),
    ("1311", "Input CGST", A, False), ("1312", "Input SGST", A, False), ("1313", "Input IGST", A, False),
    ("1320", "TDS receivable (deducted by customers)", A, False),
    ("1321", "TCS receivable (collected by suppliers)", A, False),
    ("2000", "Trade payables", L, False),
    ("2201", "Output CGST", L, False), ("2202", "Output SGST", L, False), ("2203", "Output IGST", L, False),
    ("2250", "TDS payable", L, False), ("2260", "TCS payable", L, False),
    ("3000", "Capital", E, False),
    ("4000", "Sales", I, False), ("4100", "Scrap sales", I, False),
    ("5000", "Purchases", X, False), ("5100", "Freight inward", X, False), ("5900", "Round off", X, False),
]
SLABS = [Decimal("0"), Decimal("5"), Decimal("18"), Decimal("40")]
# Seeded unconfirmed: the accountant confirms 1% or corrects it (O219).
TCS_SCRAP = {"code": "206C(1)", "name": "Scrap", "form_code": "6CE", "rate": Decimal("1"),
             "no_pan_rate": Decimal("5"), "from": datetime.date(2026, 4, 1),
             "authority": "s.206C(1) Income-tax Act 1961; s.394 Income-tax Act 2025 from 1 April 2026"}


def _plain(rate):
    return f"{rate.normalize():f}"


class Command(BaseCommand):
    help = "Set up a new company's Indian books, every figure a default for the accountant to review."

    def add_arguments(self, parser):
        parser.add_argument("--gstin", help="The company's GSTIN, for its GST settings if it has none yet.")

    def handle(self, *args, gstin=None, **options):
        self.made, self.kept = [], []
        with transaction.atomic():
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
            section = self.one(TcsSection, TCS_SCRAP["code"], name=TCS_SCRAP["name"],
                               form_code=TCS_SCRAP["form_code"], payable_account=accounts["2260"])
            if not section.rates.exists():
                rate = TcsRate.objects.create(section=section, applies_from=TCS_SCRAP["from"],
                                              rate_percent=TCS_SCRAP["rate"],
                                              no_pan_rate_percent=TCS_SCRAP["no_pan_rate"],
                                              authority=TCS_SCRAP["authority"])
                self.seeded(rate, f"TCS rate {rate} (unconfirmed: nothing is collected at it until confirmed)")
            if gstin:
                self.gst_settings(gstin, FiscalPosition.objects.get(code="INTER"))
        for line in self.made:
            self.stdout.write(f"seeded, to review: {line}")
        for line in self.kept:
            self.stdout.write(f"kept as it was: {line}")
        self.stdout.write(f"{len(self.made)} seeded for the accountant to review, {len(self.kept)} kept.")

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
        return self.one(Account, code, name=name, account_type=kind, holds_money=holds_money)

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
