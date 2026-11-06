from io import StringIO
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient
from apps.inventory.models import Item, ItemTemplate, StockMovement, Lot
from apps.web.tests_load_demo import load

class U(TestCase):
    def test_u(self):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())
        call_command("setup_roles", verbosity=0)
        item = Item.objects.filter(movements__isnull=False).first()
        lot = Lot.objects.first()
        tpl = ItemTemplate.objects.first()
        urls = ["/api/inventory/stock-reports/", "/api/inventory/stock-reports/valuation/", "/api/inventory/stock-reports/valuation/?group_by=item",
                "/api/inventory/stock-reports/slow-moving/?since=2026-01-01", "/api/inventory/stock-reports/slow_moving/?since=2026-01-01",
                "/api/inventory/stock-reports/negative/", "/api/inventory/stock-reports/reconciliation/",
                "/api/inventory/stock-reports/movements/?start=2026-01-01&end=2026-12-31",
                f"/api/inventory/items/{item.pk}/stock/", f"/api/inventory/items/{item.pk}/ledger/",
                f"/api/inventory/items/{item.pk}/", "/api/inventory/stock-movements/?ordering=value", "/api/inventory/stock-movements/?ordering=-unit_cost",
                "/api/inventory/stock-movements/?value__gt=1", "/api/inventory/stock-movements/?search=1",
                "/api/inventory/stock-movements/?format=csv", "/api/inventory/stock-movements/?format=xlsx", "/api/inventory/stock-movements/?export=csv",
                "/api/core/endpoints/", "/api/core/history/?model=stockmovement", "/api/core/history/",
                "/api/inventory/stock-adjustments/", "/api/inventory/stock-counts/", "/api/inventory/stock-transfers/",
                "/api/inventory/stock-reservations/", "/api/inventory/lots/",
                ]
        if lot: urls += [f"/api/inventory/lots/{lot.pk}/", f"/api/inventory/lots/{lot.pk}/trail/"]
        if tpl: urls += [f"/api/inventory/item-templates/{tpl.pk}/stock/"]
        urls += ["/api/purchasing/goods-receipts/", "/api/purchasing/goods-receipt-lines/", "/api/sales/deliveries/", "/api/sales/delivery-lines/"]
        for role in ("Warehouse Staff", "Quality Inspector", "Stores Manager"):
            u = User.objects.create_user(role.replace(" ", "_")); u.groups.add(Group.objects.get(name=role))
            c = APIClient(); c.force_authenticate(u)
            print("=====", role)
            for url in urls:
                r = c.get(url)
                body = r.content.decode()[:3000]
                import re
                hits = sorted(set(re.findall(r'"(\w*(?:cost|value|worth|price|amount)\w*)"', body)))
                print(r.status_code, url, hits[:8], r.headers.get("Content-Type","")[:20])
