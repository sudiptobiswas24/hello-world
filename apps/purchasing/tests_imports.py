"""
Landed cost by weight (L23) and the bill of entry (L21), with the numbers worked by hand in
/home/user/work/calc_fix_imports/calc.py (A and D):

- USD 50,000 of polymer, two grades (H030SG 25,000 kg at 1.10, H110MA 18,000 kg at 1.25), billed at
  83.2500: the supplier's payable is 41,62,500.00 and stays there;
- the BE at the customs rate 84.1000, freight USD 1,500 actual, insurance unknown (1.125% of FOB,
  USD 562.50): assessable 43,78,456.25, by line 24,08,150.94 and 19,70,305.31; BCD 7.5% 1,80,611 and
  1,47,773; SWS 10% 18,061 and 14,777; IGST 18% 4,69,228 and 3,83,914;
- the duty and surcharge, 3,61,222, landed by value: 1,98,672.10 and 1,62,549.90;
- a transporter's 40,000.00 by weight on 24,960 kg weighed and 18,000 kg: 23,240.22 and 16,759.78
  (by value, as before, 22,000.00 and 18,000.00).
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.gst import GstReturnFiled
from apps.accounting.models import Account, AccountType, ChargeType, JournalLine, PartyTaxProfile
from apps.core.models import Company, Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.gate import GateVisit
from apps.inventory.models import Item, Warehouse

from .imports import BillOfEntry, CustomsValuationDefault, EntryStatus
from .models import Bill, BillLine, GoodsReceipt, GoodsReceiptLine, LandedCostApplication, PurchaseOrder, PurchaseOrderLine

D = Decimal
DAY = datetime.date(2026, 10, 5)


class ImportPlantTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=self.usd, rate=D("83.2500"), valid_from=datetime.date(2026, 9, 1))
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category="weight")
        self.nos = UnitOfMeasure.objects.create(code="nos", name="Numbers", category="count")
        self.grade_a = Item.objects.create(sku="PP-H030SG", name="PP raffia H030SG", uom=self.kg,
                                           weight_tolerance_percent=D("0.5"))
        self.grade_b = Item.objects.create(sku="PP-H110MA", name="PP H110MA", uom=self.kg)
        self.warehouse = Warehouse.objects.create(code="RM", name="Raw material")
        account = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)
        self.stock = account("1200", "Inventory", AccountType.ASSET)
        self.igst_in = account("1313", "IGST input", AccountType.ASSET)
        self.payable = account("2000", "Sundry creditors", AccountType.LIABILITY)
        self.grni = account("2150", "GRNI", AccountType.LIABILITY)
        self.duty_payable = account("2460", "Customs duty payable", AccountType.LIABILITY)
        self.cogs = account("5000", "Cost of goods sold", AccountType.EXPENSE)
        self.ppv = account("5150", "Purchase price variance", AccountType.EXPENSE)
        self.duty = account("5160", "Customs duty", AccountType.EXPENSE)
        self.scrap = account("5200", "Scrap", AccountType.EXPENSE)
        self.freight_in = account("5610", "Freight inward", AccountType.EXPENSE)
        self.fx_gain = account("4910", "Exchange gain", AccountType.INCOME)
        self.fx_loss = account("5910", "Exchange loss", AccountType.EXPENSE)
        Company.objects.create(name="Plant", base_currency=self.inr, default_inventory_account=self.stock,
                               default_cogs_account=self.cogs, grni_account=self.grni,
                               default_payable_account=self.payable, purchase_price_variance_account=self.ppv,
                               fx_gain_account=self.fx_gain, fx_loss_account=self.fx_loss)
        self.overseas = self.party("V-SABIC", "Gulf Polymers FZE", currency=self.usd)
        PartyTaxProfile.objects.create(party=self.overseas, gst_registration="overseas")
        self.customs = self.party("V-CUSTOMS", "Commissioner of Customs, Kolkata")
        self.transporter = self.party("V-SRW", "Sharma Roadways")
        self.freight = ChargeType.objects.create(code="FRT-IN", name="Freight inward", expense_account=self.freight_in,
                                                 capitalise_into_inventory=True)
        CustomsValuationDefault.objects.get_or_create(
            valid_from=datetime.date(2017, 9, 26),
            defaults={"freight_percent": D("20"), "insurance_percent": D("1.125"), "citation": "CVR 2007 r.10(2)"})

    def party(self, code, name, currency=None):
        party = Party.objects.create(code=code, name=name, default_currency=currency or self.inr)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        return party

    def client_as(self, role, name=None):
        user = User.objects.create_user(name or role.lower().replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (rows["d"] or D("0")) - (rows["c"] or D("0"))

    def weighing(self, gross, tare):
        from apps.quality.calibration import Instrument

        Instrument.objects.get_or_create(code="WB-1", defaults={"name": "Weighbridge", "interval_days": 365,
                                                               "is_weighbridge": True})
        typist = User.objects.filter(username="typist").first() or User.objects.create_user("typist")
        typist.user_permissions.add(Permission.objects.get(codename="type_weight"))
        visit = GateVisit.objects.create(vehicle_number=f"WB{GateVisit.objects.count():02d}AB1234",
                                         driver_name="Ramesh", purpose="inward_order")
        weighing = visit.start_weighing("WB-1")
        weighing.record("gross", "typed", gross, reason="power", by=typist)
        weighing.record("tare", "typed", tare, reason="power", by=typist)
        return weighing

    def import_order(self):
        """The USD 50,000 order: grade A received on a weighed truck (24,960 kg net), grade B not weighed."""
        order = PurchaseOrder.objects.create(vendor=self.overseas, order_date=datetime.date(2026, 9, 20),
                                             currency=self.usd)
        self.line_a = PurchaseOrderLine.objects.create(order=order, item=self.grade_a, uom=self.kg,
                                                       quantity=D("25000"), unit_price=D("1.10"))
        self.line_b = PurchaseOrderLine.objects.create(order=order, item=self.grade_b, uom=self.kg,
                                                       quantity=D("18000"), unit_price=D("1.25"))
        order.confirm()
        self.receipt_a = GoodsReceipt.objects.create(purchase_order=order, receipt_date=DAY,
                                                     weighing=self.weighing("39960", "15000"),
                                                     documented_weight_kg=D("25000"))
        GoodsReceiptLine.objects.create(receipt=self.receipt_a, order_line=self.line_a, warehouse=self.warehouse,
                                        quantity_received=D("25000"))
        self.receipt_a.post()
        self.receipt_b = GoodsReceipt.objects.create(purchase_order=order, receipt_date=DAY)
        GoodsReceiptLine.objects.create(receipt=self.receipt_b, order_line=self.line_b, warehouse=self.warehouse,
                                        quantity_received=D("18000"))
        self.receipt_b.post()
        self.bill = order.create_bill(self.payable, bill_date=DAY, reference="GPF/2026/0912")
        self.bill.post()
        return order

    def carrier_bill(self, amount="40000.00"):
        bill = Bill.objects.create(vendor=self.transporter, bill_date=DAY, payable_account=self.payable,
                                   reference="SRW/118")
        charge = BillLine.objects.create(bill=bill, charge=self.freight, quantity=D("1"), unit_price=D(amount),
                                         expense_account=self.freight_in)
        bill.post()
        return charge

    def goods(self):
        return [self.receipt_a.lines.get(), self.receipt_b.lines.get()]

    def write_off(self, item, kg, day=DAY):
        from apps.inventory.models import AdjustmentReason, StockAdjustment, StockAdjustmentLine

        reason = AdjustmentReason.objects.get_or_create(code="SCRAP", defaults={"name": "Scrapped",
                                                                                "account": self.scrap})[0]
        adjustment = StockAdjustment.objects.create(adjustment_date=day, warehouse=self.warehouse, reason=reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=item, uom=self.kg, quantity=-D(kg))
        adjustment.post()

    def stock_value(self):
        return self.grade_a.stock_value_at(self.warehouse) + self.grade_b.stock_value_at(self.warehouse)


class LandedByWeightTests(ImportPlantTestCase):
    def test_freight_lands_by_the_weighed_net_and_the_converted_quantity(self):
        self.import_order()
        charge = self.carrier_bill()
        applications = charge.allocate_landed_cost(self.goods(), on_date=DAY, basis="weight")
        self.assertEqual([a.amount for a in applications], [D("23240.22"), D("16759.78")])
        self.assertEqual([a.weight for a in applications], [D("24960"), D("18000")])
        self.assertEqual({a.basis for a in applications}, {"weight"})
        self.assertEqual(self.balance(self.freight_in), D("0"))
        self.assertEqual(self.balance(self.stock), self.stock_value())

    def test_value_stays_the_default(self):
        self.import_order()
        applications = self.carrier_bill().allocate_landed_cost(self.goods(), on_date=DAY)
        self.assertEqual([a.amount for a in applications], [D("22000.00"), D("18000.00")])
        self.assertEqual({a.basis for a in applications}, {"value"})

    def test_a_line_with_no_weight_refuses_rather_than_falling_back(self):
        self.import_order()
        bags = Item.objects.create(sku="BAG-1", name="Bags", uom=self.nos)
        order = PurchaseOrder.objects.create(vendor=self.transporter, order_date=DAY)
        line = PurchaseOrderLine.objects.create(order=order, item=bags, uom=self.nos, quantity=D("100"),
                                                unit_price=D("10"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=DAY)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                        quantity_received=D("100"))
        receipt.post()
        charge = self.carrier_bill()
        with self.assertRaisesMessage(ValidationError, f"{receipt.number} BAG-1"):
            charge.allocate_landed_cost([self.receipt_b.lines.get(), receipt.lines.get()], on_date=DAY, basis="weight")
        self.assertFalse(LandedCostApplication.objects.exists())

    def test_released_after_half_the_goods_were_used(self):
        """16,759.78 on 18,000 kg; 9,000 kg written off; the release takes 8,379.89 off the shelf and
        8,379.89 back out of the write-off's account."""
        self.import_order()
        charge = self.carrier_bill()
        _a, b = charge.allocate_landed_cost(self.goods(), on_date=DAY, basis="weight")
        self.write_off(self.grade_b, "9000")
        scrap_before, shelf_before = self.balance(self.scrap), self.grade_b.stock_value_at(self.warehouse)
        b.release(on_date=DAY)
        self.assertEqual(shelf_before - self.grade_b.stock_value_at(self.warehouse), D("8379.89"))
        self.assertEqual(scrap_before - self.balance(self.scrap), D("8379.89"))
        self.assertEqual(self.balance(self.freight_in), D("16759.78"))
        self.assertEqual(self.balance(self.stock), self.stock_value())


class BillOfEntryTests(ImportPlantTestCase):
    def entry(self, bcd="7.5", supersedes=None, be_number="4417823"):
        entry = BillOfEntry.objects.create(
            be_number=be_number, be_date=datetime.date(2026, 10, 6), port_code="INCCU1", currency=self.usd,
            customs_rate=D("84.1000"), freight=D("1500.00"), insurance=None, bcd_rate=D(bcd),
            customs_party=self.customs, payable_account=self.duty_payable, duty_account=self.duty,
            igst_account=self.igst_in, supersedes=supersedes)
        entry.clear_receipt(self.receipt_a, self.bill)
        entry.clear_receipt(self.receipt_b, self.bill)
        return entry

    def test_the_bill_of_entry_as_customs_assess_it(self):
        self.import_order()
        entry = self.entry()
        self.assertEqual(entry.insurance_taken, D("562.50"))
        self.assertIn("insurance 1.125% of FOB", entry.valuation_rule)
        lines = entry.lines_in_order()
        self.assertEqual([l.assessable_value for l in lines], [D("2408150.94"), D("1970305.31")])
        self.assertEqual([l.bcd for l in lines], [D("180611"), D("147773")])
        self.assertEqual([l.sws for l in lines], [D("18061"), D("14777")])
        self.assertEqual([l.igst for l in lines], [D("469228"), D("383914")])
        entry.post()
        self.assertEqual(entry.duty_bill.total(), D("1214364.00"))
        self.assertEqual(self.balance(self.duty_payable), D("-1214364.00"))
        self.assertEqual(self.balance(self.igst_in), D("853142.00"))
        landed = list(entry.landings.order_by("pk"))
        self.assertEqual([a.amount for a in landed], [D("198672.10"), D("162549.90")])
        self.assertEqual(self.balance(self.duty), D("0"))
        # The supplier's payable at its own bill's rate; the customs rate made no exchange difference.
        self.assertEqual(self.balance(self.payable), D("-4162500.00"))
        self.assertEqual(self.balance(self.fx_gain) + self.balance(self.fx_loss), D("0"))
        self.assertEqual(self.balance(self.stock), D("4162500.00") + D("361222.00"))
        self.assertEqual(self.balance(self.stock), self.stock_value())

    def test_voided_after_its_duty_landed_and_some_stock_was_used(self):
        self.import_order()
        entry = self.entry()
        entry.post()
        self.write_off(self.grade_a, "5000", day=datetime.date(2026, 10, 7))
        entry.void(on_date=datetime.date(2026, 10, 8), reason="Assessed in error")
        self.assertEqual(entry.status, EntryStatus.VOID)
        self.assertEqual(self.balance(self.duty_payable), D("0"))
        self.assertEqual(self.balance(self.igst_in), D("0"))
        # Dr 3,61,222 by the duty bill, Cr by the landing, Cr by the debit note, Dr by the release: nothing.
        self.assertEqual(self.balance(self.duty), D("0"))
        self.assertFalse(entry.landings.filter(released_entry__isnull=True).exists())
        self.assertEqual(self.balance(self.stock), self.stock_value())
        with self.assertRaisesMessage(ValidationError, "is void"):
            entry.void(reason="again")

    def test_voided_before_it_was_assessed_is_refused(self):
        self.import_order()
        entry = self.entry()
        entry.post()
        with self.assertRaisesMessage(ValidationError, "it was assessed on 2026-10-06"):
            entry.void(on_date=datetime.date(2026, 10, 5), reason="x")

    def test_superseded_by_a_reassessment(self):
        """Re-assessed at BCD 10%: 2,40,815 and 1,97,031; SWS 24,082 and 19,703; IGST 4,81,149 and 3,93,667."""
        self.import_order()
        old = self.entry()
        old.post()
        new = self.entry(bcd="10", supersedes=old)
        new.post()
        old.refresh_from_db()
        self.assertEqual(old.status, EntryStatus.SUPERSEDED)
        self.assertEqual([l.bcd for l in old.lines_in_order()], [D("180611"), D("147773")])
        self.assertEqual([l.bcd for l in new.lines_in_order()], [D("240815"), D("197031")])
        self.assertEqual(self.balance(self.duty_payable), -(D("481631") + D("874816")))
        self.assertEqual(self.balance(self.igst_in), D("874816.00"))
        self.assertEqual(self.balance(self.stock), D("4162500.00") + D("481631.00"))
        self.assertEqual(self.balance(self.stock), self.stock_value())
        with self.assertRaisesMessage(ValidationError, "re-assessment is a new bill of entry"):
            old.be_number = "1"
            old.save()

    def test_goods_cleared_once(self):
        self.import_order()
        self.entry().post()
        other = BillOfEntry.objects.create(
            be_number="4417999", be_date=datetime.date(2026, 10, 6), port_code="INCCU1", currency=self.usd,
            customs_rate=D("84.1000"), customs_party=self.customs, payable_account=self.duty_payable,
            duty_account=self.duty, igst_account=self.igst_in)
        with self.assertRaisesMessage(ValidationError, "is cleared on"):
            other.clear_receipt(self.receipt_a, self.bill)

    def test_not_voided_once_a_filed_return_claimed_its_credit(self):
        self.import_order()
        entry = self.entry()
        entry.post()
        GstReturnFiled.objects.create(return_name="gstr3b", period="2026-10", filed_on=datetime.date(2026, 10, 20))
        with self.assertRaisesMessage(ValidationError, "GSTR-3B 2026-10"):
            entry.void(on_date=datetime.date(2026, 10, 21), reason="late")

    def test_the_customs_broker_s_figures_are_confirmed_on_screen_and_a_posted_entry_keeps_what_it_took(self):
        """
        The defaults were loaded unconfirmed with no screen: nobody could confirm them (L21). The AP Manager
        confirms or corrects them; a bill of entry posted before keeps the figures and the rule it printed.
        """
        from apps.purchasing.imports import CustomsValuationDefault

        self.import_order()
        posted = self.entry()  # freight given, insurance left to the rule
        posted.post()
        posted.refresh_from_db()
        taken, rule = posted.insurance_taken, posted.valuation_rule
        self.assertIn("insurance 1.125% of FOB", rule)
        self.assertIn("unconfirmed", rule)
        standing = CustomsValuationDefault.on(posted.be_date)
        url = f"/api/purchasing/customs-valuation-defaults/{standing.pk}/"
        self.assertEqual(self.client_as("Purchase Manager").patch(url, {"confirmed": True},
                                                                  format="json").status_code, 403)
        self.assertEqual(self.client_as("Controller").get(url).status_code, 200)
        changed = self.client_as("AP Manager").patch(url, {"confirmed": True, "insurance_percent": "1"}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        standing.refresh_from_db()
        self.assertTrue(standing.confirmed)
        posted.refresh_from_db()
        self.assertEqual((posted.insurance_taken, posted.valuation_rule), (taken, rule))

    def test_through_the_api_as_the_people_who_do_it(self):
        self.import_order()
        body = {"be_number": "4417823", "be_date": "2026-10-06", "port_code": "INCCU1", "currency": self.usd.pk,
                "customs_rate": "84.1000", "freight": "1500.00", "bcd_rate": "7.5", "customs_party": self.customs.pk,
                "payable_account": self.duty_payable.pk, "duty_account": self.duty.pk, "igst_account": self.igst_in.pk}
        self.assertEqual(self.client_as("Purchase Manager").post("/api/purchasing/bills-of-entry/", body,
                                                                 format="json").status_code, 403)
        ap = self.client_as("AP Manager")
        made = ap.post("/api/purchasing/bills-of-entry/", body, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        pk = made.json()["id"]
        for receipt in (self.receipt_a, self.receipt_b):
            answer = ap.post(f"/api/purchasing/bills-of-entry/{pk}/clear_receipt/",
                             {"receipt": receipt.pk, "bill": self.bill.pk}, format="json")
            self.assertEqual(answer.status_code, 200, answer.content)
        posted = ap.post(f"/api/purchasing/bills-of-entry/{pk}/post/", {}, format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(posted.json()["igst_total"], "853142.00")
        self.assertEqual(posted.json()["duty_total"], "361222.00")
        controller = self.client_as("Controller")
        self.assertEqual(controller.get(f"/api/purchasing/bills-of-entry/{pk}/").status_code, 200)
        self.assertEqual(controller.post(f"/api/purchasing/bills-of-entry/{pk}/void/", {"reason": "x"},
                                         format="json").status_code, 403)
        voided = ap.post(f"/api/purchasing/bills-of-entry/{pk}/void/", {"reason": "Wrong port"}, format="json")
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertEqual(voided.json()["status"], "void")


class TheDutyBillIsCorrectedThroughItsEntryTests(ImportPlantTestCase):
    """
    Found by probing (2 November): a debit note raised on the duty bill itself took the duty payable to nothing while
    the bill of entry still stood posted, its 3,61,222.00 of duty on the goods and on the duty account for good.
    """

    entry = BillOfEntryTests.entry

    def test_a_debit_note_on_the_duty_bill_is_refused_and_the_entry_voids_it(self):
        self.import_order()
        posted = self.entry()
        posted.post()
        with self.assertRaisesMessage(ValidationError, "void the bill of entry"):
            posted.duty_bill.create_debit_note(memo="Refund?", on_date=posted.be_date)
        self.assertEqual(self.balance(self.duty_payable), D("-1214364.00"))
        posted.void(on_date=posted.be_date, reason="Wrong port")
        posted.refresh_from_db()
        self.assertEqual((posted.status, posted.landings.filter(released_entry__isnull=True).count()), ("void", 0))
        self.assertEqual((self.balance(self.duty_payable), self.balance(self.duty)), (D("0"), D("0")))
