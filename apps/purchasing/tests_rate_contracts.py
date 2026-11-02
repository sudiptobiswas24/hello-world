"""
Polymer bought on circulars (L16), with the numbers worked in /home/user/work/calc_fix_imports/calc.py (B, C):
the circular at 100.0000 from 1 October, the contract at 1.50 off: 98.50 a kg; 18,000 kg received in three
lots (6,000, 7,000, 5,000); the maker revises 1 October to 102.5000 on the 20th: 2.50 a kg, 15,000.00,
17,500.00 and 12,500.00, 45,000.00 with CGST 4,050.00 and SGST 4,050.00.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.gst import GstSettings
from apps.accounting.models import Account, AccountType, FiscalPosition, PartyTaxProfile, Tax
from apps.sales.price_variation import PriceIndex, PriceIndexValue

from .models import BillPolicy, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine
from .rate_contracts import NoteStatus, PurchaseRateContract, RateContractLine, RateDifferenceNote
from .tests_imports import ImportPlantTestCase

D = Decimal
OCT = lambda day: datetime.date(2026, 10, day)  # noqa: E731


class RateContractTestCase(ImportPlantTestCase):
    def setUp(self):
        super().setUp()

        def tax(code, account_code):
            account = Account.objects.create(code=account_code, name=f"Input {code}", account_type=AccountType.ASSET)
            return Tax.objects.create(code=code, name=code, rate=D("9"), gst_head=code[:4].lower(),
                                      collected_account=account, paid_account=account)

        self.cgst, self.sgst = tax("CGST9", "1311"), tax("SGST9", "1312")
        GstSettings.objects.create(gstin="19AABCP1234E1ZT", state="19",
                                   interstate_position=FiscalPosition.objects.create(code="INTER", name="Inter"))
        self.supplier = self.party("V-HPL", "Haldia Polymer Traders")
        PartyTaxProfile.objects.create(party=self.supplier, gstin="19AACCH1180A1Z3")
        self.index = PriceIndex.objects.create(code="RIL-H030SG", name="RIL raffia H030SG circular")
        PriceIndexValue.objects.create(index=self.index, valid_from=OCT(1), value=D("100.0000"),
                                       published_on=OCT(1))
        self.contract = PurchaseRateContract.objects.create(
            vendor=self.supplier, index=self.index, discount_per_kg=D("1.50"), valid_from=OCT(1),
            valid_to=datetime.date(2026, 12, 31))
        RateContractLine.objects.create(contract=self.contract, item=self.grade_a, quantity_kg=D("50000"))

    def contract_order(self, kg="18000"):
        order = PurchaseOrder.objects.create(vendor=self.supplier, order_date=OCT(2), bill_policy=BillPolicy.RECEIVED)
        line = PurchaseOrderLine.objects.create(order=order, item=self.grade_a, uom=self.kg, quantity=D(kg),
                                                rate_contract=self.contract)
        line.taxes.set([self.cgst, self.sgst])
        order.confirm()
        return line

    def lot(self, line, kg, day):
        receipt = GoodsReceipt.objects.create(purchase_order=line.order, receipt_date=OCT(day))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                        quantity_received=D(kg))
        receipt.post()
        return receipt

    def three_lots(self):
        line = self.contract_order()
        lots = [self.lot(line, "6000", 5), self.lot(line, "7000", 10), self.lot(line, "5000", 15)]
        bill = line.order.create_bill(self.payable, bill_date=OCT(16), reference="HPT/26-27/0412")
        bill.post()
        return line, lots, bill

    def revise(self, value, day=20):
        PriceIndexValue.objects.create(index=self.index, valid_from=OCT(1), value=D(value), published_on=OCT(day))

    def note(self, reference="HPT/RD/0031", day=21):
        return RateDifferenceNote.objects.create(contract=self.contract, start=OCT(1), end=OCT(31),
                                                 note_date=OCT(day), supplier_reference=reference)


class RateContractTests(RateContractTestCase):
    def test_an_order_line_takes_the_index_on_its_date_less_the_discount(self):
        line = self.contract_order()
        self.assertEqual(line.unit_price, D("98.50"))
        self.assertEqual(line.contract_index_value, D("100.0000"))

    def test_an_order_beyond_the_contract_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "50000 kg of"):
            self.contract_order(kg="50001")

    def test_a_backdated_rise_is_a_supplementary_bill_landed_by_what_is_left(self):
        """Lot 1 of 6,000 kg: 1,000 returned to the supplier, 2,000 written off. Its 15,000.00 lands
        7,500.00 on the 3,000 kg left, 5,000.00 to the write-off and 2,500.00 stays in price variance;
        lot 2 17,500.00 and lot 3 12,500.00 on the shelf: 37,500.00 in stock."""
        # FIFO, so each lot is a layer and what is left of it is known: under the average, the shelf
        # holding 15,000 kg holds all 6,000 of lot 1 (inventory/costing.py goods_held), by design.
        self.grade_a.costing_method = "fifo"
        self.grade_a.save()
        line = self.contract_order()
        lot1 = self.lot(line, "6000", 5)
        lot1.create_return({lot1.lines.get(): D("1000")}, debit_bills=False, on_date=OCT(7))
        self.write_off(self.grade_a, "2000", day=OCT(8))  # only lot 1 on the shelf
        self.lot(line, "7000", 10)
        self.lot(line, "5000", 15)
        scrap_before = self.balance(self.scrap)
        line.order.create_bill(self.payable, bill_date=OCT(16), reference="HPT/26-27/0412").post()
        self.revise("102.5000")
        note = self.note()
        self.assertEqual([row.amount for row in note.lines.order_by("pk")], [D("15000.00"), D("17500.00"), D("12500.00")])
        self.assertEqual({row.per_kg for row in note.lines.all()}, {D("2.500000")})
        self.assertIsNone(note.debit_note)
        bill = note.bill
        self.assertFalse(bill.posted)
        self.assertEqual(bill.reference, "HPT/RD/0031")
        self.assertEqual(bill.subtotal(), D("45000.00"))
        self.assertEqual(bill.total(), D("53100.00"))
        stock_before, ppv_before = self.balance(self.stock), self.balance(self.ppv)
        bill.post()
        note.refresh_from_db()
        self.assertEqual(note.status, NoteStatus.LANDED)
        landed = list(note.landings.order_by("pk"))
        self.assertEqual([a.amount for a in landed], [D("15000.00"), D("17500.00"), D("12500.00")])
        self.assertEqual({a.basis for a in landed}, {"quantity"})
        self.assertEqual(self.balance(self.ppv) - ppv_before, D("2500.00"))
        self.assertEqual(self.balance(self.stock) - stock_before, D("37500.00"))
        self.assertEqual(self.balance(self.scrap) - scrap_before, D("5000.00"))
        self.assertEqual(self.balance(self.stock), self.grade_a.stock_value_at(self.warehouse))

    def test_a_fall_is_a_debit_note_against_the_latest_bill(self):
        """5,000 kg; revised down 1.20: a debit note of 6,000.00 and CGST, SGST 540.00 each."""
        line = self.contract_order(kg="5000")
        self.lot(line, "5000", 5)
        bill = line.order.create_bill(self.payable, bill_date=OCT(6), reference="HPT/26-27/0398")
        bill.post()
        self.revise("98.8000")
        note = self.note(reference="HPT/CN/0007")
        self.assertIsNone(note.bill)
        debit = note.debit_note
        self.assertEqual(debit.debits, bill)
        self.assertEqual(debit.supplier_note_number, "HPT/CN/0007")
        self.assertEqual(debit.total(), D("7080.00"))
        stock_before = self.balance(self.stock)
        debit.post()
        self.assertEqual(self.balance(self.stock) - stock_before, D("-6000.00"))
        self.assertEqual(self.balance(self.stock), self.grade_a.stock_value_at(self.warehouse))
        self.assertEqual(self.balance(self.ppv), D("0"))

    def test_a_receipt_is_varied_once(self):
        self.three_lots()
        self.revise("102.5000")
        self.note()
        with self.assertRaisesMessage(ValidationError, "is left to vary"):
            self.note(reference="HPT/RD/0032")

    def test_cancelling_a_draft_frees_its_receipts(self):
        self.three_lots()
        self.revise("102.5000")
        first = self.note()
        draft = first.bill
        first.cancel()
        self.assertFalse(type(draft).objects.filter(pk=draft.pk).exists())
        second = self.note(reference="HPT/RD/0032")
        self.assertEqual(second.total(), D("45000.00"))

    def test_nothing_moves_where_the_circular_stood(self):
        self.three_lots()
        with self.assertRaisesMessage(ValidationError, "stands where it was"):
            self.note()

    def test_through_the_api_as_the_people_who_do_it(self):
        self.three_lots()
        self.revise("102.5000")
        manager = self.client_as("Purchase Manager")
        body = {"contract": self.contract.pk, "start": "2026-10-01", "end": "2026-10-31", "note_date": "2026-10-21",
                "supplier_reference": "HPT/RD/0031"}
        self.assertEqual(self.client_as("Controller").post("/api/purchasing/rate-differences/", body,
                                                           format="json").status_code, 403)
        made = manager.post("/api/purchasing/rate-differences/", body, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["total"], "45000.00")
        lines = manager.get(f"/api/purchasing/rate-difference-lines/?note={made.json()['id']}")
        self.assertEqual(lines.status_code, 200)
        contract = manager.post("/api/purchasing/rate-contracts/", {
            "vendor": self.supplier.pk, "index": self.index.pk, "discount_per_kg": "1.25",
            "valid_from": "2027-01-01", "valid_to": "2027-03-31"}, format="json")
        self.assertEqual(contract.status_code, 201, contract.content)
        self.assertEqual(self.client_as("Controller", "controller2").get(
            "/api/purchasing/rate-contracts/").status_code, 200)
        self.assertEqual(self.client_as("AP Manager").post("/api/purchasing/rate-contracts/", {}, format="json")
                         .status_code, 403)


class ANotesDocumentSaysWhatTheNoteSaysTests(RateContractTestCase):
    """
    Found by probing (2 November): the note's draft bill cut from 45,000.00 to 15,000.00 still landed 45,000.00 on the
    goods and left -30,000.00 on the holding account for good. And a debit note on the posted supplementary bill would
    have taken the payable down with the price change still on the shelves.
    """

    def test_an_edited_bill_is_refused_at_posting_and_nothing_lands(self):
        self.three_lots()
        self.revise("102.5000")
        note = self.note()
        line = note.bill.lines.order_by("pk").first()
        line.unit_price = D("15000.00")
        line.save()
        with self.assertRaisesMessage(ValidationError, "a rate-difference note's document is not edited"):
            note.bill.post()
        note.bill.refresh_from_db()
        note.refresh_from_db()
        self.assertFalse(note.bill.posted)
        self.assertEqual((note.status, note.landings.count()), (NoteStatus.DRAFT, 0))

    def test_its_posted_bill_is_not_debited_but_answered_by_a_new_note(self):
        self.three_lots()
        self.revise("102.5000")
        note = self.note()
        note.bill.post()
        with self.assertRaisesMessage(ValidationError, "a fall in the circular is a new rate-difference note"):
            note.bill.create_debit_note(memo="Supplier's error")
