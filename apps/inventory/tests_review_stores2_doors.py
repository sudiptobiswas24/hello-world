"""Review probe (review_stores2): doors for O324, O330, O326, O327."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.db import transaction
from django.test import Client
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, UnitOfMeasure
from apps.core.tests_roles import RolesTestCase

from .adjustments import AdjustmentReason, StockAdjustment, StockAdjustmentLine
from .models import Item, MovementType, StockMovement, Warehouse

D = Decimal


def app_code(src, **ns):
    """Run `src` as if from application code (a file in apps/, not a test)."""
    exec(compile(src, "/home/user/work/review_stores2/apps/inventory/shipping.py", "exec"), ns)
    return ns


class Doors(RolesTestCase):
    def setUp(self):
        super().setUp()
        usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.inventory = Account.objects.create(code="1200", name="Inventory", account_type=AccountType.ASSET)
        self.payable = Account.objects.create(code="2000", name="Payable", account_type=AccountType.LIABILITY)
        self.cogs = Account.objects.create(code="5000", name="COGS", account_type=AccountType.EXPENSE)
        self.eq = Account.objects.create(code="3000", name="Eq", account_type=AccountType.EQUITY)
        Company.objects.create(name="Co", base_currency=usd, default_inventory_account=self.inventory,
                               default_cogs_account=self.cogs)
        self.kg = UnitOfMeasure.objects.create(code="kg", name="kg")
        self.item = Item.objects.create(sku="T", name="Tape", uom=self.kg)
        self.store = Warehouse.objects.create(code="S", name="Store")
        StockMovement.objects.create(item=self.item, warehouse=self.store, movement_type=MovementType.RECEIPT,
                                     uom=self.kg, quantity=D("10"), unit_cost=D("5"), occurred_at=timezone.now())

    def reason(self, **kw):
        return AdjustmentReason.objects.create(code=kw.pop("code", "R"), name="r", account=kw.pop("account", self.cogs),
                                               direction="both", **kw)

    # ---- O324
    def test_o324_put_patch_roles(self):
        r = self.reason()
        sm, ctl = self.as_("Stores Manager"), self.as_("Controller")
        u = f"/api/inventory/adjustment-reasons/{r.pk}/"
        out = {}
        out["sm PUT same account"] = sm.put(u, {"code": "R", "name": "x", "account": self.cogs.pk, "direction": "both"}, format="json").status_code
        out["sm PUT other eq"] = sm.put(u, {"code": "R", "name": "x", "account": self.eq.pk, "direction": "both"}, format="json").status_code
        out["sm PATCH payable"] = sm.patch(u, {"account": self.payable.pk}, format="json").status_code
        out["sm PATCH account=null"] = sm.patch(u, {"account": None}, format="json").status_code
        out["ctl PATCH payable"] = ctl.patch(u, {"account": self.payable.pk}, format="json").status_code
        out["ctl PATCH eq"] = ctl.patch(u, {"account": self.eq.pk}, format="json").status_code
        out["sm PATCH name on eq"] = sm.patch(u, {"name": "zz"}, format="json").status_code
        # legacy bad
        AdjustmentReason.objects.filter(pk=r.pk).update(account=self.payable)
        out["sm PATCH name on legacy payable"] = sm.patch(u, {"name": "zz2"}, format="json").status_code
        out["sm PATCH direction legacy"] = sm.patch(u, {"direction": "increase"}, format="json").status_code
        out["sm PATCH account back to cogs (legacy)"] = sm.patch(u, {"account": self.cogs.pk}, format="json").status_code
        print("O324", out)

    def test_o324_admin_and_retype(self):
        r = self.reason()
        su = User.objects.create_superuser("root", "r@x.y", "pw")
        c = Client(); c.force_login(su)
        resp = c.post(f"/admin/inventory/adjustmentreason/{r.pk}/change/", {
            "code": "R", "name": "r", "account": self.payable.pk, "direction": "both", "is_active": "on"})
        print("O324 admin POST payable:", resp.status_code, AdjustmentReason.objects.get(pk=r.pk).account.code)
        # retype account after the reason exists
        r = AdjustmentReason.objects.get(pk=r.pk)
        Account.objects.filter(pk=r.account_id).update(account_type=AccountType.LIABILITY)
        adj = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=AdjustmentReason.objects.get(pk=r.pk))
        StockAdjustmentLine.objects.create(adjustment=adj, item=self.item, uom=self.kg, quantity=D("-2"))
        try:
            adj.post(); print("O324 retyped liability reason posted!!")
        except ValidationError as e:
            print("O324 retyped refused:", e.messages)

    def test_o324_other_users_of_reason(self):
        # a posted adjustment on a good reason, then reason account legacy-bad; void allowed?
        r = self.reason()
        adj = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
        StockAdjustmentLine.objects.create(adjustment=adj, item=self.item, uom=self.kg, quantity=D("-2"))
        adj.post()
        AdjustmentReason.objects.filter(pk=r.pk).update(account=self.payable)
        adj = StockAdjustment.objects.get(pk=adj.pk)
        adj.void()
        print("O324 void ok; payable credits:", sum((l.credit - l.debit for l in JournalLine.objects.filter(account=self.payable)), D(0)))
        # set_standard_cost with a legacy bad reason
        from .models import set_standard_cost
        it = Item.objects.create(sku="STD", name="s", uom=self.kg, costing_method="standard", standard_cost=D("2"))
        StockMovement.objects.create(item=it, warehouse=self.store, movement_type=MovementType.RECEIPT, uom=self.kg, quantity=D("5"), unit_cost=D("2"), occurred_at=timezone.now())
        r2 = AdjustmentReason.objects.get(pk=r.pk)
        try:
            set_standard_cost(it, D("3"), reason=r2)
            print("O324 std revaluation on payable reason posted; payable:", sum((l.credit - l.debit for l in JournalLine.objects.filter(account=self.payable)), D(0)))
        except ValidationError as e:
            print("O324 std reval refused", e.messages)
        # stock count post with bad reason
        from .adjustments import StockCount, StockCountLine
        sc = StockCount.objects.create(count_date=datetime.date(2026, 3, 2), warehouse=self.store, reason=r2)
        print("count fields ok", sc.pk)

    # ---- O330
    def test_o330(self):
        sm = self.as_("Stores Manager")
        u = f"/api/inventory/items/{self.item.pk}/"
        out = {}
        full = sm.get(u).json()
        full["costing_method"] = "fifo"
        out["PUT fifo"] = sm.put(u, full, format="json").status_code
        out["PATCH same"] = sm.patch(u, {"costing_method": "average"}, format="json").status_code
        out["PATCH standard"] = sm.patch(u, {"costing_method": "standard"}, format="json").status_code
        # code: save
        self.item.costing_method = "fifo"
        try:
            self.item.save(); out["code save"] = "ALLOWED"
        except ValidationError:
            out["code save"] = "refused"
        # queryset update
        Item.objects.filter(pk=self.item.pk).update(costing_method="fifo")
        out["update()"] = Item.objects.get(pk=self.item.pk).costing_method
        Item.objects.filter(pk=self.item.pk).update(costing_method="average")
        # admin
        su = User.objects.create_superuser("root", "r@x.y", "pw"); c = Client(); c.force_login(su)
        resp = c.get(f"/admin/inventory/item/{self.item.pk}/change/")
        out["admin get"] = resp.status_code
        # item with movement only elsewhere: new item no movements
        fresh = Item.objects.create(sku="F", name="f", uom=self.kg)
        out["fresh PATCH fifo"] = sm.patch(f"/api/inventory/items/{fresh.pk}/", {"costing_method": "fifo"}, format="json").status_code
        out["fresh PATCH specific"] = sm.patch(f"/api/inventory/items/{fresh.pk}/", {"costing_method": "specific"}, format="json").status_code
        # standard on fresh then set_standard_cost
        out["fresh PATCH standard"] = sm.patch(f"/api/inventory/items/{fresh.pk}/", {"costing_method": "standard"}, format="json").status_code
        # fresh with standard_cost=0 but after a value-only? n/a
        print("O330", out)

    def test_o330_template(self):
        from .variants import ItemTemplate
        t = ItemTemplate.objects.create(code="TT", name="tt", uom=self.kg, costing_method="average") if hasattr(ItemTemplate, "costing_method") else None
        if t is None:
            print("no template costing"); return
        v = Item.objects.create(sku="V1", name="v", uom=self.kg, template=t, variant_key="a=1")
        StockMovement.objects.create(item=v, warehouse=self.store, movement_type=MovementType.RECEIPT, uom=self.kg, quantity=D("5"), unit_cost=D("2"), occurred_at=timezone.now())
        ctl = self.as_("Stores Manager")
        r = ctl.patch(f"/api/inventory/item-templates/{t.pk}/", {"costing_method": "fifo"}, format="json")
        t.refresh_from_db()
        v.refresh_from_db()
        print("O330 template PATCH:", r.status_code, t.costing_method, "variant:", v.costing_method)

    # ---- O326 / O327
    def test_writers(self):
        out = {}
        now = "timezone.now()"
        pre = "from django.utils import timezone\nfrom apps.inventory.models import StockMovement, MovementType\nfrom decimal import Decimal as D\n"
        base = "item=item, warehouse=store, movement_type=MovementType.RECEIPT, uom=kg, quantity=D('3'), occurred_at=timezone.now()"
        spellings = {
            "create": f"StockMovement.objects.create({base})",
            "get_or_create": f"StockMovement.objects.get_or_create(item=item, warehouse=store, uom=kg, quantity=D('3'), defaults=dict(movement_type=MovementType.RECEIPT, occurred_at=timezone.now()))",
            "related create": f"item.movements.create({base})",
            "instance save": f"StockMovement({base}).save()",
            "kwargs": f"kw=dict({base}); StockMovement.objects.create(**kw)",
            "value None explicit": f"StockMovement.objects.create({base}, value=None)",
            "value_adjustment only": f"StockMovement.objects.create({base}, value_adjustment=D('5'))",
            "bulk_create": f"StockMovement.objects.bulk_create([StockMovement({base})])",
            "qty0 value0": "StockMovement.objects.create(item=item, warehouse=store, movement_type=MovementType.ADJUSTMENT, uom=kg, quantity=D('0'), occurred_at=timezone.now())",
            "unit_cost only, outbound": f"StockMovement.objects.create(item=item, warehouse=store, movement_type=MovementType.ISSUE, uom=kg, quantity=D('-1'), unit_cost=D('5'), occurred_at=timezone.now())",
            "sign: in, value<0": f"StockMovement.objects.create({base}, value=D('-5'))",
            "sign: out, value>0": f"StockMovement.objects.create(item=item, warehouse=store, movement_type=MovementType.ISSUE, uom=kg, quantity=D('-1'), value=D('5'), occurred_at=timezone.now())",
            "sign: in, value 0": f"StockMovement.objects.create({base}, value=D('0'))",
            "out value 0": f"StockMovement.objects.create(item=item, warehouse=store, movement_type=MovementType.ISSUE, uom=kg, quantity=D('-1'), value=D('0'), occurred_at=timezone.now())",
            "tiny out converts to 0 qty": f"StockMovement.objects.create(item=item, warehouse=store, movement_type=MovementType.ISSUE, uom=kg, quantity=D('-0.00001'), value=D('-5'), occurred_at=timezone.now())",
        }
        for name, code in spellings.items():
            n0 = StockMovement.objects.count()
            try:
                with transaction.atomic():
                    app_code(pre + code, item=self.item, store=self.store, kg=self.kg)
                out[name] = f"WRITTEN +{StockMovement.objects.count() - n0}"
            except ValidationError as e:
                out[name] = "refused"
            except Exception as e:
                out[name] = f"{type(e).__name__}: {str(e)[:60]}"
        for k, v in out.items():
            print("WRITER", k, "->", v)

    def test_admin_form(self):
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory
        from .admin import AdjustmentReasonAdmin
        ma = AdjustmentReasonAdmin(AdjustmentReason, dj_admin.site)
        su = User.objects.create_superuser("root", "r@x.y", "pw")
        req = RequestFactory().post("/"); req.user = su
        Form = ma.get_form(req)
        f = Form({"code": "ZZ", "name": "n", "account": self.payable.pk, "direction": "both", "is_active": "on"})
        print("ADMIN form valid for payable:", f.is_valid(), f.errors.as_json()[:200])
        if f.is_valid():
            try:
                ma.save_model(req, f.save(commit=False), f, False); print("ADMIN saved!!")
            except ValidationError as e:
                print("ADMIN save_model raises ValidationError (500 page):", e.messages[:1])


class StaleRead(Doors):
    def test_o323_stale_standard(self):
        """A write-up reads the item before it locks the shelf; a standard set in between is not seen."""
        from . import adjustments
        from .models import set_standard_cost
        std = Item.objects.create(sku="STD", name="s", uom=self.kg, costing_method="standard", standard_cost=D("3"))
        StockMovement.objects.create(item=std, warehouse=self.store, movement_type=MovementType.RECEIPT, uom=self.kg, quantity=D("5"), occurred_at=timezone.now())
        inc = Account.objects.create(code="4100", name="Found", account_type=AccountType.INCOME)
        r = AdjustmentReason.objects.create(code="F", name="found", account=inc, direction="both")
        adj = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
        StockAdjustmentLine.objects.create(adjustment=adj, item=std, uom=self.kg, quantity=D("2"), unit_cost=D("3"))
        real = adjustments.lock_positions
        def rival(*a, **k):
            adjustments.lock_positions = real
            set_standard_cost(Item.objects.get(pk=std.pk), D("4"), reason=r)
            return real(*a, **k)
        adjustments.lock_positions = rival
        try:
            StockAdjustment.objects.get(pk=adj.pk).post()
        finally:
            adjustments.lock_positions = real
        worth = std.valuation_at(self.store)
        print("O323 stale: shelf (qty, worth) =", worth, "7 x standard 4 =", D(7) * 4)


class FloorReadsAdjustments(Doors):
    def test_ws_reads_money_of_documents(self):
        from .models import set_standard_cost
        inc = Account.objects.create(code="4100", name="Found", account_type=AccountType.INCOME)
        r = AdjustmentReason.objects.create(code="F", name="found", account=inc, direction="both")
        adj = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
        StockAdjustmentLine.objects.create(adjustment=adj, item=self.item, uom=self.kg, quantity=D("2"), unit_cost=D("9"))
        StockAdjustmentLine.objects.create(adjustment=adj, item=self.item, uom=self.kg, quantity=D("0"), revaluation=D("7"))
        adj.post()
        from .adjustments import StockCount, StockCountLine
        sc = StockCount.objects.create(count_date=datetime.date(2026, 3, 2), warehouse=self.store, reason=r)
        for role in ("Warehouse Staff", "Quality Inspector", "Stores Manager"):
            c = self.as_(role)
            for url in ("/api/inventory/stock-adjustments/", f"/api/inventory/stock-adjustments/{adj.pk}/", "/api/inventory/stock-adjustment-lines/",
                        "/api/inventory/stock-counts/", "/api/inventory/stock-count-lines/", "/api/inventory/stock-transfers/", "/api/inventory/stock-transfer-lines/",
                        "/api/inventory/stock-reservations/", "/api/inventory/lots/"):
                resp = c.get(url)
                import re
                print("FLOOR", role, resp.status_code, url, sorted(set(re.findall(r'"(\w*(?:cost|value|worth|price|revaluation)\w*)"', resp.content.decode()))))


class Edge(Doors):
    def test_negative_reval_then_write_off(self):
        inc = Account.objects.create(code="4100", name="Found", account_type=AccountType.INCOME)
        r = AdjustmentReason.objects.create(code="F", name="found", account=inc, direction="both")
        def adj(qty=None, reval=None, cost=None, post=True):
            a = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
            StockAdjustmentLine.objects.create(adjustment=a, item=self.item, uom=self.kg, quantity=D(qty or 0), revaluation=D(reval) if reval is not None else None, unit_cost=D(cost) if cost else None)
            if post: a.post()
            return a
        out = {}
        for label, kw in (("reval -60 on 50", dict(reval="-60")), ("write off 4", dict(qty="-4")), ("write up 1 blank", dict(qty="1"))):
            try:
                with transaction.atomic():
                    adj(**kw)
                out[label] = "ok " + str(self.item.valuation_at(self.store))
            except ValidationError as e:
                out[label] = "refused " + str(e.messages)[:120]
        print("EDGE", out)

    def test_standard_empty_shelf_trap(self):
        std = Item.objects.create(sku="STD", name="s", uom=self.kg, costing_method="standard", standard_cost=D("3"))
        inc = Account.objects.create(code="4100", name="Found", account_type=AccountType.INCOME)
        r = AdjustmentReason.objects.create(code="F", name="found", account=inc, direction="both")
        out = {}
        for cost in (None, "5", "3"):
            a = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
            StockAdjustmentLine.objects.create(adjustment=a, item=std, uom=self.kg, quantity=D("2"), unit_cost=D(cost) if cost else None)
            try:
                with transaction.atomic():
                    a.post()
                out[cost] = "ok"
            except ValidationError as e:
                out[cost] = e.messages[0][:140]
        print("STD EMPTY", out)

    def test_o328_figures(self):
        # shelf 9,000 worth 30,000.00 (3.33333..), +3000 found blank
        it = Item.objects.create(sku="AVG", name="a", uom=self.kg)
        StockMovement.objects.create(item=it, warehouse=self.store, movement_type=MovementType.RECEIPT, uom=self.kg, quantity=D("9000"), value=D("30000.00"), occurred_at=timezone.now())
        inc = Account.objects.create(code="4100", name="Found", account_type=AccountType.INCOME)
        r = AdjustmentReason.objects.create(code="F", name="found", account=inc, direction="both")
        a = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
        StockAdjustmentLine.objects.create(adjustment=a, item=it, uom=self.kg, quantity=D("3000"))
        a.post()
        line = a.lines.get()
        print("O328", line.posted_value, line.unit_cost, it.valuation_at(self.store), it.average_cost_at(self.store))
        # tiny fraction quantity
        b = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store, reason=r)
        StockAdjustmentLine.objects.create(adjustment=b, item=it, uom=self.kg, quantity=D("0.0001"))
        b.post()
        print("O328 tiny", b.lines.get().posted_value, it.valuation_at(self.store))
        # ledger foot
        led = sum((l.debit - l.credit for l in JournalLine.objects.filter(account=self.inventory)), D(0))
        print("O328 ledger inventory", led, "shelf all", sum((i.valuation_at(self.store)[1] for i in Item.objects.all()), D(0)))


class History(Doors):
    def test_history_leaks(self):
        mv = StockMovement.objects.first()
        for role in ("Warehouse Staff", "Quality Inspector"):
            c = self.as_(role)
            r = c.get("/api/core/history/", {"model": "inventory.stockmovement", "id": mv.pk})
            print("HISTORY", role, r.status_code, r.content.decode()[:600])


class Summary(Doors):
    def test_summary_leaks(self):
        for role in ("Warehouse Staff", "Quality Inspector", "Stores Manager"):
            c = self.as_(role)
            r = c.get("/api/web/summary/", {"endpoint": "/api/inventory/stock-movements/", "by": "item"})
            print("SUMMARY", role, r.status_code, r.content.decode()[:420])


class Mirror(Doors):
    def test_item_inventory_account(self):
        fresh = Item.objects.create(sku="F", name="f", uom=self.kg)
        sm = self.as_("Stores Manager")
        resp = sm.patch(f"/api/inventory/items/{fresh.pk}/", {"inventory_account": self.payable.pk}, format="json")
        print("MIRROR item.inventory_account=payable:", resp.status_code)
        reason = AdjustmentReason.objects.create(code="SH", name="s", account=self.cogs, direction="both")
        adj = sm.post("/api/inventory/stock-adjustments/", {"adjustment_date": "2026-03-01", "warehouse": self.store.pk, "reason": reason.pk}, format="json").json()
        line = sm.post("/api/inventory/stock-adjustment-lines/", {"adjustment": adj["id"], "item": fresh.pk, "quantity": "4", "unit_cost": "12.5"}, format="json")
        posted = sm.post(f"/api/inventory/stock-adjustments/{adj['id']}/post/")
        debit = sum((l.debit - l.credit for l in JournalLine.objects.filter(account=self.payable)), D(0))
        print("MIRROR posted:", posted.status_code, "payables net debit with no vendor:", debit)
