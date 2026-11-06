"""review_books3 probes: O347 (admin path, company-field collision, old item) and O329 health check."""
from decimal import Decimal as D
from django.contrib.auth.models import User
from django.test import TestCase
from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Currency, UnitOfMeasure
from .models import Item


class O347(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.cogs = Account.objects.create(code="5000", name="COGS", account_type=AccountType.EXPENSE)
        self.ap = Account.objects.create(code="2000", name="AP", account_type=AccountType.LIABILITY)
        self.ppv = Account.objects.create(code="5900", name="PPV", account_type=AccountType.EXPENSE)
        self.company = Company.objects.create(name="Co", base_currency=self.usd, default_cogs_account=self.cogs,
                                              purchase_price_variance_account=self.ppv)
        self.su = User.objects.create_superuser("root", "r@x.com", "pw")

    def test_admin_change_form_with_a_liability_cogs_account(self):
        from django.contrib import admin
        from django.core.exceptions import ValidationError
        item = Item.objects.create(sku="A", name="A", uom=self.uom)
        ma = admin.site._registry[Item]
        from django.test import RequestFactory
        req = RequestFactory().get('/'); req.user = self.su
        Form = ma.get_form(req, item, change=True)
        from django.forms.models import model_to_dict
        data = {k: ("" if v is None else v) for k, v in model_to_dict(item).items()}
        data["cogs_account"] = self.ap.pk
        data = {k: v for k, v in data.items() if k in Form.base_fields}
        form = Form(data, instance=item)
        valid = form.is_valid()
        outcome = "form refuses" if not valid else "form valid"
        if valid:
            try:
                ma.save_model(req, item, form, True); outcome += "; saved"
            except ValidationError as e:
                outcome += "; save_model raised ValidationError (admin 500)"
        print("admin form cogs=liability:", outcome, "errors:", dict(form.errors))
        self.assertFalse(valid and "500" in outcome, outcome)

    def test_variance_account_is_also_a_legit_company_account_collision(self):
        # The company's own price-variance account (an expense account) is refused as an item's cost of sales.
        item = Item(sku="B", name="B", uom=self.uom, cogs_account=self.ppv)
        from django.core.exceptions import ValidationError
        try:
            item.save(); res = "accepted"
        except ValidationError:
            res = "refused"
        print("PASS" if res == "refused" else "FAIL", "variance account refused as item cogs:", res)
        self.assertEqual(res, "refused")

    def test_bulk_paths_skip_save(self):
        # bulk_create / queryset.update reach the database without the rule; the use-point check (cogs_account_for) is the net.
        from .valuation import cogs_account_for
        from django.core.exceptions import ValidationError
        Item.objects.bulk_create([Item(sku="C", name="C", uom=self.uom, cogs_account=self.ap)])
        item = Item.objects.get(sku="C")
        with self.assertRaises(ValidationError):
            cogs_account_for(item)
        print("PASS bulk-created item with a liability cogs account is refused at the use point")


from .tests_reports import ReportTestCase
from .reports import reconcile_to_ledger
from .models import Item as _Item
from .variants import ItemTemplate


class O329Health(ReportTestCase):
    def manual(self, account, amount):
        import datetime
        from apps.accounting.models import JournalEntry, JournalLine
        entry = JournalEntry.objects.create(date=datetime.date(2026, 3, 1), memo="Manual")
        amount = D(amount)
        JournalLine.objects.create(entry=entry, account=account, debit=max(amount, D("0")), credit=max(-amount, D("0")))
        other = Account.objects.get(code="2150")
        JournalLine.objects.create(entry=entry, account=other, debit=max(-amount, D("0")), credit=max(amount, D("0")))
        entry.post()

    def test_three_accounts_one_surplus_two_shortfalls(self):
        from apps.web.health import _stock
        third = Account.objects.create(code="1220", name="Inventory - tools", account_type=AccountType.ASSET)
        tool = _Item.objects.create(sku="T", name="Tool", uom=self.each, inventory_account=third)
        self.buy("100", "6"); self.buy("50", "4", item=self.spare); self.buy("10", "3", item=tool)
        self.manual(self.inventory, "0.03")        # books too much: stock short by 0.03
        self.manual(self.spares_stock, "-0.01")    # books too little: stock over by 0.01
        self.manual(third, "-0.01")                # books too little: stock over by 0.01
        r = reconcile_to_ledger()
        rows = {x["account"].code: x["difference"] for x in r["rows"]}
        want = {"1200": D("-0.03"), "1210": D("0.01"), "1220": D("0.01")}
        f = _stock(None)
        res = [("rows", rows, want), ("totals difference nets to", r["difference"], D("-0.01")), ("out_by", r["out_by"], D("0.05")),
               ("balanced", r["balanced"], False), ("health ok/count", (f["ok"], f["count"]), (False, 3))]
        for n, g, w in res: print("PASS" if g == w else "FAIL", n, g if g == w else f"got {g} want {w}")
        print("health detail:", f["detail"])
        self.assertTrue(all(g == w for _, g, w in res))
        self.assertIn("out by 0.05", f["detail"])

    def test_template_only_account_and_company_default_only_account(self):
        orphan = Account.objects.create(code="1299", name="Inventory - orphan", account_type=AccountType.ASSET)
        ItemTemplate.objects.create(code="TT", name="TT", uom=self.each, inventory_account=orphan)
        self.manual(orphan, "5.00")
        r = reconcile_to_ledger()
        rows = {x["account"].code: x["difference"] for x in r["rows"]}
        print("PASS" if rows.get("1299") == D("-5.00") else "FAIL", "account named only by a template is asked:", rows)
        self.assertEqual(rows.get("1299"), D("-5.00"))
        # as-of before the entry: the asked account at 0 stays out of the rows
        r2 = reconcile_to_ledger(__import__("datetime").date(2026, 2, 1))
        self.assertNotIn("1299", {x["account"].code for x in r2["rows"]})


class O347Company(O347):
    def test_company_saves_when_default_cogs_is_the_variance_account_already_in_use(self):
        from django.core.exceptions import ValidationError
        Company.objects.filter(pk=self.company.pk).update(default_cogs_account=self.ppv)  # state from before the rule
        company = Company.objects.get(pk=self.company.pk)
        company.name = "Renamed"
        try:
            company.save(); res = "saved"
        except ValidationError as e:
            res = "refused: " + str(e)[:110]
        print("INFO unrelated company edit when default COGS == price-variance account:", res)
        self.assertEqual(res, "saved")
