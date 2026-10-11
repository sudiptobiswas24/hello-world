"""
The rest of the old system brought in at go-live: a tape, a fabric and
a bag specification in turn, each with its bill of materials built; the
order book, confirmed; a loom two years into a five-year life with
48,000 of its 120,000 already depreciated, which this system does not
depreciate again; a salary structure; a price list and a vendor's price.
"""

import datetime
from decimal import Decimal

from apps.accounting.models import Account, AccountType
from apps.assets.models import AssetCategory, FixedAsset
from apps.assets.tests import the_plants_day
from apps.core.models import Party, PartyRole, PartyRoleAssignment, UnitOfMeasure, UnitOfMeasureCategory
from apps.hr.models import Employee
from apps.hr.payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent
from apps.inventory.models import Item
from apps.manufacturing.woven import BagSpecification, FabricSpecification, TapeSpecification
from apps.purchasing.models import PurchaseOrder, VendorPrice
from apps.sales.models import PriceList, PriceListItem, SalesOrder

from .importer import run, template
from .tests import ImportTestCase


class SpecificationsTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogramme", category=UnitOfMeasureCategory.WEIGHT)
        self.pcs = UnitOfMeasure.objects.create(code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT)
        for sku, name, uom in (("GRAN", "PP granule", self.kg), ("FILL", "Filler", self.kg), ("TAPE-1", "Tape", self.kg),
                               ("FAB-1", "Fabric", self.kg), ("THREAD", "Thread", self.kg), ("BAG-1", "Sack 60 x 100", self.pcs)):
            Item.objects.create(sku=sku, name=name, uom=uom)

    TAPE = ("code,name,tape_item,denier,tape_width_mm,virgin_granule,filler_item,filler_percent\n"
            "T-1,Tape 1000 D,TAPE-1,1000,2.5,GRAN,FILL,8\n")
    FABRIC = ("code,fabric_item,warp_tape,ends_per_inch,picks_per_inch,lay_flat_width_cm,target_gsm,weave,shrink_percent\n"
              "F-1,FAB-1,T-1,10,10,60,87,tubular,0\n")
    BAG = ("code,bag_item,fabric,bag_width_cm,bag_length_cm,bottom_hem_cm,top_hem_cm,thread_item,thread_grams_per_bag,"
           "target_grams,weight_tolerance_percent,conversion_waste_percent\n"
           "B-1,BAG-1,F-1,60,100,3,2,THREAD,1.2,110,3,2.5\n")

    def test_the_template_is_the_models_own_fields(self):
        self.assertTrue(template("bag_specs").startswith("code,name,bag_item,fabric,bag_width_cm,bag_length_cm,"))
        self.assertNotIn("bom", template("fabric_specs"))

    def test_tape_then_fabric_then_bag_each_with_its_recipe(self):
        for kind, text in (("tape_specs", self.TAPE), ("fabric_specs", self.FABRIC), ("bag_specs", self.BAG)):
            report = run(kind, text, commit=True)
            self.assertTrue(report.committed, (kind, report.errors))
        tape = TapeSpecification.objects.get(code="T-1")
        self.assertEqual((tape.denier, tape.filler_percent, tape.bom.is_computed), (Decimal("1000"), Decimal("8.000"), True))
        self.assertEqual(sorted(tape.bom.components.values_list("item__sku", flat=True)), ["FILL", "GRAN"])
        fabric = FabricSpecification.objects.get(code="F-1")
        self.assertEqual((fabric.warp_tape, fabric.weave, fabric.bom.item.sku), (tape, "tubular", "FAB-1"))
        bag = BagSpecification.objects.get(code="B-1")
        self.assertEqual((bag.fabric, bag.bom.item.sku, bag.inspection_plan is not None), (fabric, "BAG-1", True))
        self.assertEqual(sorted(bag.bom.components.values_list("item__sku", flat=True)), ["FAB-1", "THREAD"])

    def test_a_dry_run_builds_nothing_and_every_problem_is_named(self):
        self.assertEqual(run("tape_specs", self.TAPE).committed, False)
        self.assertFalse(TapeSpecification.objects.exists())
        Item.objects.create(sku="TAPE-PCS", name="Tape counted", uom=self.pcs)
        bad = self.TAPE + ("T-2,No granule,TAPE-1,1000,2.5,NOPE,,\n"
                           "T-3,Counted tape,TAPE-PCS,1000,2.5,GRAN,,\n"
                           "T-1,Twice,TAPE-1,1000,2.5,GRAN,,\n"
                           ",No code,TAPE-1,1000,2.5,GRAN,,\n"
                           "T-4,Bad number,TAPE-1,thick,2.5,GRAN,,\n")
        report = run("tape_specs", bad, commit=True)
        self.assertEqual([(row, column) for row, column, _ in report.errors],
                         [(3, "virgin_granule"), (4, ""), (5, "code"), (6, "code"), (7, "denier")])
        self.assertIn("measured in", report.errors[1][2])
        self.assertFalse(report.committed)
        self.assertFalse(TapeSpecification.objects.exists())
        missing = run("fabric_specs", self.FABRIC, commit=True)
        self.assertEqual([(row, column) for row, column, _ in missing.errors], [(2, "warp_tape")])


class OrderBookTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.second = Item.objects.create(sku="SKU-2", name="Second item", uom=self.uom)

    def test_open_sales_orders_come_in_confirmed_one_per_reference(self):
        text = ("customer,reference,date,sku,quantity,unit_price,delivery_date,warehouse\n"
                f"C-1,OLD-SO-9,2026-09-20,{self.item.sku},1000,12.50,2026-10-15,WH1\n"
                "C-1,OLD-SO-9,2026-09-20,SKU-2,250,8,2026-10-20,WH1\n"
                f"C-1,OLD-SO-10,2026-09-25,{self.item.sku},100,12.50,,\n")
        self.assertEqual((run("open_sales_orders", text).committed, SalesOrder.objects.count()), (False, 0))
        report = run("open_sales_orders", text, commit=True)
        self.assertTrue(report.committed, report.errors)
        nine = SalesOrder.objects.get(reference="OLD-SO-9")
        self.assertEqual((nine.status, nine.lines.count(), nine.number.startswith("SO-")), ("confirmed", 2, True))
        self.assertEqual([(line.item.sku, line.quantity, line.unit_price, line.delivery_date) for line in nine.lines.order_by("pk")],
                         [(self.item.sku, Decimal("1000.0000"), Decimal("12.5000"), datetime.date(2026, 10, 15)),
                          ("SKU-2", Decimal("250.0000"), Decimal("8.0000"), datetime.date(2026, 10, 20))])
        self.assertEqual(SalesOrder.objects.get(reference="OLD-SO-10").status, "confirmed")
        again = run("open_sales_orders", text, commit=True)
        self.assertEqual([(row, column) for row, column, _ in again.errors], [(2, "reference"), (3, "reference"), (4, "reference")])

    def test_one_bad_line_keeps_no_order(self):
        text = ("customer,reference,date,sku,quantity,unit_price\n"
                f"C-1,OLD-SO-11,2026-09-20,{self.item.sku},10,5\n"
                "C-1,OLD-SO-11,2026-09-20,NO-SUCH,10,5\n"
                "C-9,OLD-SO-12,2026-09-20,SKU-2,0,5\n")
        report = run("open_sales_orders", text, commit=True)
        self.assertEqual([(row, column) for row, column, _ in report.errors], [(3, "sku"), (4, "customer")])
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_open_purchase_orders_mirror_it(self):
        text = ("vendor,reference,date,sku,quantity,unit_price,expected_date,warehouse\n"
                f"V-1,PO/OLD/41,2026-09-18,{self.item.sku},5000,91.25,2026-10-05,WH1\n")
        report = run("open_purchase_orders", text, commit=True)
        self.assertTrue(report.committed, report.errors)
        order = PurchaseOrder.objects.get(reference="PO/OLD/41")
        line = order.lines.get()
        self.assertEqual((order.status, order.vendor, line.quantity, line.expected_date, line.warehouse),
                         ("confirmed", self.vendor, Decimal("5000.0000"), datetime.date(2026, 10, 5), self.warehouse))


class AssetRegisterTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)  # noqa: E731
        self.category = AssetCategory.objects.create(
            code="PLANT", name="Plant and machinery", asset_account=acc("1500", "Plant", AccountType.ASSET),
            accumulated_account=acc("1590", "Accumulated depreciation", AccountType.ASSET),
            expense_account=acc("6100", "Depreciation", AccountType.EXPENSE),
            disposal_account=acc("6200", "Disposal", AccountType.EXPENSE), default_life_months=60)

    LOOM = ("name,category,acquisition_date,in_service_date,cost,salvage_value,life_months,depreciated_to_date\n"
            "Loom 7,PLANT,2024-10-01,2024-10-01,120000,0,60,48000\n")

    @the_plants_day(datetime.date(2026, 11, 5))
    def test_what_was_depreciated_is_carried_and_not_charged_again(self):
        report = run("fixed_assets", self.LOOM, commit=True, date=datetime.date(2026, 9, 30))
        self.assertTrue(report.committed, report.errors)
        loom = FixedAsset.objects.get(name="Loom 7")
        self.assertEqual((loom.status, loom.depreciated_before, loom.opening_depreciation, loom.accumulated(),
                          loom.remaining_to_depreciate(), loom.depreciation_entries.count()),
                         ("in_service", datetime.date(2026, 9, 30), Decimal("48000.00"), Decimal("48000.00"),
                          Decimal("72000.00"), 0))
        # Before the go-live date, as this system reads it, nothing had been charged here.
        self.assertEqual(loom.accumulated(as_of=datetime.date(2026, 9, 29)), Decimal("0"))
        # October is this system's first month: one charge of 2,000, and none for the 24 months before.
        made = loom.depreciate(through=datetime.date(2026, 10, 31))
        self.assertEqual([(entry.period_end, entry.amount) for entry in made], [(datetime.date(2026, 10, 31), Decimal("2000.00"))])
        self.assertEqual(loom.periods_due(datetime.date(2026, 12, 31)), [datetime.date(2026, 11, 30), datetime.date(2026, 12, 31)])

    def test_refusals(self):
        self.assertEqual([(row, column) for row, column, _ in run("fixed_assets", self.LOOM, commit=True).errors], [(2, "--date")])
        bad = ("name,category,acquisition_date,in_service_date,cost,salvage_value,life_months,depreciated_to_date\n"
               "Loom 8,PLANT,2024-10-01,,120000,0,60,130000\n"
               "Loom 9,PLANT,2026-10-05,,120000,0,60,0\n"
               "Loom 10,NOPE,2024-10-01,,120000,0,60,0\n")
        report = run("fixed_assets", bad, commit=True, date=datetime.date(2026, 9, 30))
        self.assertEqual([(row, column) for row, column, _ in report.errors],
                         [(2, "depreciated_to_date"), (3, "in_service_date"), (4, "category")])
        self.assertEqual(FixedAsset.objects.count(), 0)


class PayAndPricesTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        person = Party.objects.create(code="EMP-1", name="Asha")
        PartyRoleAssignment.objects.create(party=person, role=PartyRole.EMPLOYEE)
        self.asha = Employee.objects.create(party=person, employee_number="EMP-1", hire_date=datetime.date(2020, 1, 1))
        wages = Account.objects.create(code="6000", name="Wages", account_type=AccountType.EXPENSE)
        PayComponent.objects.create(code="BASIC", name="Basic", kind=ComponentKind.EARNING, basis=ComponentBasis.FIXED,
                                    expense_account=wages, sequence=10)
        self.vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def test_compensation(self):
        text = ("employee_number,component,amount,effective_from,effective_to,note\n"
                "EMP-1,BASIC,18000,2026-04-01,,From the April revision\n"
                "EMP-1,BASIC,18000,2026-04-01,,Twice\n"
                "EMP-2,BASIC,1,2026-04-01,,\n")
        report = run("compensation", text, commit=True)
        self.assertEqual([(row, column) for row, column, _ in report.errors], [(3, "component"), (4, "employee_number")])
        report = run("compensation", text.splitlines()[0] + "\n" + text.splitlines()[1] + "\n", commit=True)
        self.assertTrue(report.committed, report.errors)
        pay = EmployeeCompensation.objects.get(employee=self.asha)
        self.assertEqual((pay.component.code, pay.amount, pay.effective_from, pay.note),
                         ("BASIC", Decimal("18000.00"), datetime.date(2026, 4, 1), "From the April revision"))

    def test_price_lists_and_vendor_prices(self):
        text = ("price_list,price_list_name,currency,sku,min_quantity,unit_price\n"
                f"CEMENT,Cement buyers,USD,{self.item.sku},1,11.50\n"
                f"CEMENT,,,{self.item.sku},10000,11.00\n")
        report = run("price_lists", text, commit=True)
        self.assertTrue(report.committed, report.errors)
        cement = PriceList.objects.get(code="CEMENT")
        self.assertEqual((cement.name, cement.currency, PriceListItem.objects.filter(price_list=cement).count()),
                         ("Cement buyers", self.usd, 2))
        self.assertEqual([(row, column) for row, column, _ in run("price_lists", text, commit=True).errors], [(2, "sku"), (3, "sku")])
        vendors = ("vendor,sku,unit_price,currency,min_quantity,vendor_item_code,lead_time_days,valid_from,is_preferred\n"
                   f"V-1,{self.item.sku},91.25,,1000,PP-H110MA,7,2026-04-01,yes\n")
        report = run("vendor_prices", vendors, commit=True)
        self.assertTrue(report.committed, report.errors)
        price = VendorPrice.objects.get()
        self.assertEqual((price.vendor, price.unit_price, price.currency, price.min_quantity, price.vendor_item_code,
                          price.lead_time_days, price.valid_from, price.is_preferred),
                         (self.vendor, Decimal("91.25"), self.usd, Decimal("1000.0000"), "PP-H110MA", 7,
                          datetime.date(2026, 4, 1), True))
