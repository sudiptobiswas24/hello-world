"""
A receipt weighed on the weighbridge, and a claim for what was short (L17), as the Purchase Manager.
Numbers worked in /home/user/work/calc_fix_gate/scenarios.py (A, B, C, D, F).
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.gate import GateVisit, Weighing
from apps.inventory.models import Item, ItemUnit, Warehouse
from apps.manufacturing.station_scale import post_reading

from .models import Bill, BillLine, BillPolicy, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine
from .weight_claims import ClaimStatus, WeightClaim

D = Decimal
DAY = datetime.date(2026, 10, 1)


class WeighedReceiptTestCase:
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category="weight")
        self.polymer = Item.objects.create(sku="PP-RAFFIA", name="Polypropylene raffia", uom=self.kg)
        self.warehouse = Warehouse.objects.create(code="RM", name="Raw material")
        self.vendor = Party.objects.create(code="RIL", name="Polymer Supplier")
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.transporter = Party.objects.create(code="SPEEDY", name="Speedy Roadways")
        account = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)
        self.payable = account("2000", "Payables", AccountType.LIABILITY)
        self.grni = account("2150", "Goods received not invoiced", AccountType.LIABILITY)
        self.stock = account("1200", "Inventory", AccountType.ASSET)
        self.cogs = account("5000", "Cost of sales", AccountType.EXPENSE)
        self.claims = account("1450", "Claims recoverable", AccountType.ASSET)
        Company.objects.create(name="Plant", default_inventory_account=self.stock, default_cogs_account=self.cogs,
                               grni_account=self.grni, default_payable_account=self.payable,
                               claims_recoverable_account=self.claims)
        self.manager = APIClient()
        user = User.objects.create_user("purchase_manager")
        user.groups.add(Group.objects.get(name="Purchase Manager"))
        self.manager.force_authenticate(user)
        self.typist = User.objects.create_user("typist")
        self.typist.user_permissions.add(Permission.objects.get(codename="type_weight"))

    def order(self, quantity="10000", price="95", item=None, uom=None):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY, bill_policy=BillPolicy.ORDERED)
        line = PurchaseOrderLine.objects.create(order=order, item=item or self.polymer, uom=uom or self.kg,
                                                quantity=D(quantity), unit_price=D(price))
        order.confirm()
        return line

    def weighing(self, gross="25860", tare="15920", purpose="inward_order", vehicle="MH12AB1234"):
        visit = GateVisit.objects.create(vehicle_number=vehicle, driver_name="Ramesh", purpose=purpose)
        weighing = visit.start_weighing("WB-1")
        weighing.record("gross", "typed", gross, reason="power", by=self.typist)
        weighing.record("tare", "typed", tare, reason="power", by=self.typist)
        return weighing

    def receipt(self, line, quantity, weighing, documented="10000", post=True):
        receipt = GoodsReceipt.objects.create(purchase_order=line.order, receipt_date=DAY, weighing=weighing,
                                              documented_weight_kg=D(documented) if documented else None)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                        quantity_received=D(quantity))
        if post:
            receipt.post()
        return receipt

    def bill(self, line, quantity="10000", price="95"):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, payable_account=self.payable,
                                   purchase_order=line.order)
        bill_line = BillLine.objects.create(bill=bill, order_line=line, item=line.item, description="Polymer",
                                            quantity=D(quantity), unit_price=D(price), expense_account=self.cogs)
        bill.post()
        return bill_line

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(d=Sum("debit"),
                                                                                         c=Sum("credit"))
        return (rows["d"] or D("0")) - (rows["c"] or D("0"))

    def raise_claim(self, receipt, status=201, **body):
        response = self.manager.post("/api/purchasing/weight-claims/", {"receipt": receipt.pk, **body},
                                     format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data


from django.test import TestCase  # noqa: E402


class WeighedReceiptTests(WeighedReceiptTestCase, TestCase):
    def test_the_receipt_compares_the_weighed_net_with_the_invoice_and_what_was_received(self):
        receipt = self.receipt(self.order(), "9940", self.weighing())
        compared = self.manager.get(f"/api/purchasing/goods-receipts/{receipt.pk}/").data["weight"]
        self.assertEqual((compared["net"], compared["documented"], compared["received"], compared["shortage"]),
                         (D("9940.000"), D("10000.000"), D("9940"), D("60.000")))
        self.assertEqual((compared["allowance"], compared["beyond_tolerance"], compared["typed"]),
                         (D("50.0000"), True, True))

    def test_more_received_than_the_truck_held_is_refused(self):
        receipt = self.receipt(self.order(), "10000", self.weighing(), post=False)
        with self.assertRaisesMessage(ValidationError, "9940 kg net; 10000 kg cannot be received on it"):
            receipt.post()
        self.assertFalse(GoodsReceipt.objects.get(pk=receipt.pk).posted)

    def test_a_weighing_of_a_vehicle_that_came_for_something_else_is_refused(self):
        receipt = self.receipt(self.order(), "9940", self.weighing(purpose="outward_delivery"), post=False)
        with self.assertRaisesMessage(ValidationError, "not with goods in"):
            receipt.post()

    def test_a_weighing_a_posted_receipt_names_is_neither_replaced_nor_its_visit_cancelled(self):
        weighing = self.weighing()
        self.receipt(self.order(), "9940", weighing)
        with self.assertRaisesMessage(ValidationError, "weighs by"):
            weighing.replace("tare wrong")
        with self.assertRaisesMessage(ValidationError, "named by"):
            GateVisit.objects.get(pk=weighing.visit_id).cancel("entered twice")
        self.assertIsNone(Weighing.objects.get(pk=weighing.pk).replaced_at)

    def test_a_replaced_or_cancelled_weighing_cannot_be_received_on(self):
        replaced = self.weighing()
        replaced.replace("tare typed wrong")
        receipt = self.receipt(self.order(), "9940", replaced, post=False)
        with self.assertRaisesMessage(ValidationError, "was replaced"):
            receipt.post()
        cancelled = self.weighing(vehicle="MH12AB9999")
        GateVisit.objects.get(pk=cancelled.visit_id).cancel("entered twice")
        receipt = self.receipt(self.order(), "9940", cancelled, post=False)
        with self.assertRaisesMessage(ValidationError, "was cancelled"):
            receipt.post()

    def test_in_the_items_stocking_unit_through_its_own_conversion(self):
        bag = UnitOfMeasure.objects.create(code="bag", name="Bag", category="count")
        sacks = Item.objects.create(sku="PP-BAG", name="Polymer in 25 kg bags", uom=bag)
        ItemUnit.objects.create(item=sacks, uom=self.kg, factor=D("0.04"))
        refused = self.receipt(self.order("400", "2375", sacks, bag), "400", self.weighing(), post=False)
        with self.assertRaisesMessage(ValidationError, "397.6 bag net; 400 bag cannot be received on it"):
            refused.post()
        receipt = self.receipt(self.order("400", "2375", sacks, bag), "397", self.weighing(vehicle="MH12AB9999"))
        claim = self.raise_claim(receipt, against="supplier")
        self.assertEqual((claim["quantity"], claim["value"]), ("2.4000", "5700.00"))


class WeightClaimTests(WeighedReceiptTestCase, TestCase):
    def setUp(self):
        super().setUp()
        self.line = self.order()
        self.received = self.receipt(self.line, "9940", self.weighing())

    def test_within_the_tolerance_is_no_claim(self):
        receipt = self.receipt(self.order(), "9960", self.weighing(gross="25880", vehicle="MH12AB9999"))
        refused = self.raise_claim(receipt, status=400, against="supplier")
        self.assertIn("short 40 kg, within the item's 0.5% (50 kg): no claim", str(refused))

    def test_against_the_supplier_settled_by_a_debit_note_on_their_bill(self):
        claim = self.raise_claim(self.received, against="supplier")
        self.assertEqual((claim["quantity"], claim["value"], claim["status"]), ("60.0000", "5700.00", "open"))
        refused = self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/settle/", {}, format="json")
        self.assertIn("bill the supplier's invoice first", str(refused.data))
        bill_line = self.bill(self.line)
        self.assertEqual(self.balance(self.grni), D("5700.00"), "billed 10,000 for 9,940 received")
        settled = self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/settle/",
                                    {"bill_line": bill_line.pk, "on_date": str(DAY)}, format="json")
        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertEqual(settled.data["status"], "settled")
        note = Bill.objects.get(pk=settled.data["debit_note"])
        self.assertEqual((note.debits_id, note.posted), (bill_line.bill_id, True))
        self.assertEqual(self.balance(self.grni), D("0.00"))
        self.assertEqual(self.balance(self.payable), D("-944300.00"))
        self.assertTrue(GoodsReceipt.objects.get(pk=self.received.pk).posted, "the receipt stands as posted")
        withdrawn = self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/withdraw/", {"reason": "x"},
                                      format="json")
        self.assertIn("the supplier holds that note", str(withdrawn.data))

    def test_against_the_transporter_settled_by_a_recovery_and_withdrawn_by_its_reversal(self):
        self.bill(self.line)
        claim = self.raise_claim(self.received, status=400, against="transporter")
        self.assertIn("Say which transporter", str(claim))
        claim = self.raise_claim(self.received, against="transporter", transporter=self.transporter.pk)
        settled = self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/settle/", {}, format="json")
        self.assertEqual(settled.status_code, 200, settled.data)
        self.assertEqual((self.balance(self.grni), self.balance(self.claims)), (D("0.00"), D("5700.00")))
        self.assertEqual(JournalLine.objects.get(account=self.claims, debit__gt=0).party, self.transporter)
        # The same shortage cannot be made good twice: by the supplier once the transporter has.
        self.assertEqual(self.raise_claim(self.received, status=400, against="supplier")[0],
                         "The shortage on " + self.received.number + " is claimed already.")
        withdrawn = self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/withdraw/",
                                      {"reason": "transporter disputed it, not pursued"}, format="json")
        self.assertEqual(withdrawn.data["status"], "withdrawn", withdrawn.data)
        self.assertEqual((self.balance(self.grni), self.balance(self.claims)), (D("5700.00"), D("0.00")))
        self.assertIsNotNone(withdrawn.data["recovery_reversal"])

    def test_part_of_the_shortage_then_the_rest_and_no_more(self):
        first = self.raise_claim(self.received, against="supplier", quantity="40")
        self.assertEqual(first["value"], "3800.00")
        refused = self.raise_claim(self.received, status=400, against="transporter",
                                   transporter=self.transporter.pk, quantity="21")
        self.assertIn("Only 20 kg of the shortage is left to claim", str(refused))
        rest = self.raise_claim(self.received, against="transporter", transporter=self.transporter.pk)
        self.assertEqual((rest["quantity"], rest["value"]), ("20.0000", "1900.00"))

    def test_an_open_claim_withdrawn_settles_nothing_and_frees_its_quantity(self):
        claim = self.raise_claim(self.received, against="supplier")
        self.assertEqual(self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/withdraw/", {},
                                           format="json").status_code, 400, "withdrawn with a reason")
        done = self.manager.post(f"/api/purchasing/weight-claims/{claim['id']}/withdraw/",
                                 {"reason": "supplier sent the 60 kg on the next truck"}, format="json")
        self.assertEqual((done.data["status"], done.data["recovery_entry"]), ("withdrawn", None))
        self.assertEqual(self.balance(self.claims), D("0"))
        self.assertEqual(self.raise_claim(self.received, against="supplier")["quantity"], "60.0000")
        self.assertIn(self.manager.delete(f"/api/purchasing/weight-claims/{claim['id']}/").status_code, (403, 405))
        with self.assertRaisesMessage(ValidationError, "never deleted"):
            WeightClaim.objects.get(pk=claim["id"]).delete()
        self.assertEqual(WeightClaim.objects.get(pk=claim["id"]).status, ClaimStatus.WITHDRAWN)
