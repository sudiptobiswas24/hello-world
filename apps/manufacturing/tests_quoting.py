"""
Costing and quoting a sack. The figures were worked by an independent
script from the fixture chain before the code was written:

  Rates: virgin PP 102, regrind 60, CaCO3 28, masterbatch 165, thread
  160 a kg; tape 7, weaving 6, cutting 4 a kg, packing 0.15 a sack;
  overhead 8%, margin 12%.

  Per sack: fabric 110.23628 g net, 113.063 g with 2.5% cutting waste;
  tape 115.370 g with 2% loom waste; granules at 3% extrusion waste:
  virgin 89.2038 g, regrind 17.8408, CaCO3 9.5151, masterbatch 2.3788;
  thread 1.2308 g. Regrind recovered: 1.9786 (cutting) + 1.9613
  (loom) + 2.8545 (extrusion) = 6.7944 g.

  Material 11.025076, conversion 2.088220 (tape 0.115370 x 7, weaving
  and cutting 0.113063 x 6 and x 4, packing 0.15), credit 0.407665,
  overhead 1.016450: cost 13.722082, price 15.368731, quoted 15.37.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Tax
from apps.core.models import Currency, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.sales.models import Quotation, QuotationStatus

from .bom import BillOfMaterials, BomComponent
from .quoting import CostSheet, MaterialRate, QuotePolicy, StageRate, cost, quote
from .tests_woven import WovenTestCase, close

DAY = datetime.date(2026, 9, 1)


class QuotingTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        self.sack = self.bag()
        for item, rate in ((self.virgin, "102"), (self.regrind, "60"), (self.filler, "28"),
                           (self.colour, "165"), (self.thread, "160")):
            MaterialRate.objects.create(item=item, rate=Decimal(rate), valid_from=DAY)
        for stage, rate in (("tape", "7"), ("weaving", "6"), ("cutting", "4"),
                            ("packing", "0.15")):
            StageRate.objects.create(stage=stage, rate=Decimal(rate), valid_from=DAY)
        QuotePolicy.objects.create(overhead_percent=Decimal("8"), margin_percent=Decimal("12"),
                                   valid_from=DAY)

    def line(self, sheet, kind, item=None, stage=""):
        return sheet.lines.get(kind=kind, item=item, stage=stage)

    def refused(self, message, call, *args, **kwargs):
        with self.assertRaises(ValidationError) as caught:
            call(*args, **kwargs)
        self.assertIn(message, str(caught.exception))


class WorkedByHandTests(QuotingTestCase):
    def test_the_sack_costed_to_the_polymer(self):
        sheet = cost(self.sack, Decimal("50000"), DAY)
        for field, expected in (("material", "11.025076"), ("conversion", "2.088220"),
                                ("credit", "0.407665"), ("overhead", "1.016450"),
                                ("cost", "13.722082"), ("price", "15.368731")):
            self.assertTrue(close(getattr(sheet, field), expected, "0.00001"), field)
        self.assertEqual(sheet.quoted_price, Decimal("15.37"))
        self.assertEqual(sheet.order_value(), Decimal("768500.00"))
        # From what the sheet froze: 13.722082 over the sack's 111.4363 g
        self.assertTrue(close(sheet.per_kg(), "123.138349", "0.000001"))

    def test_each_granule_at_what_the_chain_asks_of_it(self):
        sheet = cost(self.sack, Decimal("1000"), DAY)
        # Kilogrammes a sack, to the six places a line keeps.
        for item, kg in ((self.virgin, "0.089204"), (self.regrind, "0.017841"),
                         (self.filler, "0.009515"), (self.colour, "0.002379"),
                         (self.thread, "0.001231")):
            self.assertEqual(self.line(sheet, "material", item).quantity, Decimal(kg), item.sku)
        self.assertEqual(self.line(sheet, "credit", self.regrind).quantity, Decimal("0.006794"))

    def test_each_stage_on_what_it_makes(self):
        sheet = cost(self.sack, Decimal("1000"), DAY)
        self.assertTrue(close(self.line(sheet, "conversion", stage="tape").quantity, "0.115370"))
        self.assertTrue(close(self.line(sheet, "conversion", stage="weaving").quantity, "0.113063"))
        self.assertTrue(close(self.line(sheet, "conversion", stage="cutting").quantity, "0.113063"))
        self.assertEqual(self.line(sheet, "conversion", stage="packing").quantity, Decimal("1"))
        self.assertFalse(sheet.lines.filter(stage__in=["lamination", "printing"]).exists())

    def test_a_laminated_printed_sack_adds_its_stages_on_the_roll(self):
        lam_pp = Item.objects.create(sku="LAM-PP", name="Lam PP", uom=self.kg)
        ldpe = Item.objects.create(sku="LDPE", name="LDPE", uom=self.kg)
        ink = Item.objects.create(sku="INK", name="Ink", uom=self.kg)
        for item, rate in ((lam_pp, "108"), (ldpe, "96"), (ink, "240")):
            MaterialRate.objects.create(item=item, rate=Decimal(rate), valid_from=DAY)
        for stage, rate in (("lamination", "4.5"), ("printing", "5")):
            StageRate.objects.create(stage=stage, rate=Decimal(rate), valid_from=DAY)
        sack = self.bag(code="LAM", is_laminated=True, lamination_gsm=Decimal("15"),
                        coating=[(lam_pp, 80), (ldpe, 20)], print_colours=2, ink_item=ink,
                        bag_item=Item.objects.create(sku="BAG-LAM", name="Lam", uom=self.pcs))
        sheet = cost(sack, Decimal("1000"), DAY)
        # roll = fabric 0.113063 + coat 0.01575 + 0.0039375
        self.assertTrue(close(self.line(sheet, "conversion", stage="lamination").amount, "0.597377"))
        self.assertTrue(close(self.line(sheet, "conversion", stage="printing").amount, "0.663752"))
        self.assertTrue(close(self.line(sheet, "conversion", stage="cutting").amount, "0.531001"))
        self.assertTrue(close(self.line(sheet, "material", lam_pp).amount
                              + self.line(sheet, "material", ldpe).amount, "2.079"))
        self.assertTrue(close(self.line(sheet, "material", ink).amount, "0.147692"))

    def test_a_margin_of_its_own(self):
        sheet = cost(self.sack, Decimal("1000"), DAY, margin_percent=Decimal("20"))
        self.assertTrue(close(sheet.price, "16.466498", "0.00001"))
        self.assertEqual(sheet.quoted_price, Decimal("16.47"))

    def test_the_last_receipt_is_shown_beside_the_rate(self):
        warehouse = Warehouse.objects.create(code="WH", name="Main")
        StockMovement.objects.create(item=self.virgin, warehouse=warehouse, uom=self.kg,
                                     movement_type=MovementType.RECEIPT, quantity=Decimal("100"),
                                     unit_cost=Decimal("99"), occurred_at=timezone.now())
        sheet = cost(self.sack, Decimal("1000"), DAY)
        self.assertEqual(self.line(sheet, "material", self.virgin).last_receipt_cost, Decimal("99"))
        self.assertIsNone(self.line(sheet, "material", self.filler).last_receipt_cost)


class RatesTests(QuotingTestCase):
    def test_the_rate_in_force_on_the_day(self):
        MaterialRate.objects.create(item=self.virgin, rate=Decimal("110"),
                                    valid_from=DAY + datetime.timedelta(days=1))
        before = cost(self.sack, Decimal("1000"), DAY)
        after = cost(self.sack, Decimal("1000"), DAY + datetime.timedelta(days=1))
        self.assertTrue(close(after.material - before.material, "0.713630", "0.00001"))

    def test_not_on_a_day_before_the_recipe_was_changed(self):
        fabric = self.sack.fabric
        type(fabric).objects.filter(pk=fabric.pk).update(
            created_at=fabric.created_at - datetime.timedelta(days=60))
        fabric.refresh_from_db()
        fabric.picks_per_inch = Decimal("10.5")
        fabric.save()
        self.refused("cannot be costed on that day", cost, self.sack, Decimal("1"), DAY)
        self.assertTrue(cost(self.sack, Decimal("1"), timezone.localdate()).material > 0)

    def test_every_missing_rate_is_named_at_once(self):
        MaterialRate.objects.filter(item__in=[self.filler, self.thread]).delete()
        StageRate.objects.filter(stage="cutting").delete()
        with self.assertRaises(ValidationError) as caught:
            cost(self.sack, Decimal("1000"), DAY)
        message = str(caught.exception)
        for missing in ("no rate for CACO3", "no rate for THREAD",
                        "no rate for cutting and stitching"):
            self.assertIn(missing, message)
        self.assertFalse(CostSheet.objects.exists())

    def test_before_any_policy(self):
        self.refused("no overhead and margin in force", cost, self.sack, Decimal("1"),
                     DAY - datetime.timedelta(days=1))

    def test_an_item_made_here_outside_the_chain_needs_a_rate(self):
        MaterialRate.objects.filter(item=self.thread).delete()
        bom = BillOfMaterials.objects.create(item=self.thread, quantity_produced=Decimal("1"),
                                             uom=self.kg)
        BomComponent.objects.create(bom=bom, item=self.virgin, quantity=Decimal("1"), uom=self.kg)
        self.refused("THREAD is made here outside this sack's chain", cost, self.sack,
                     Decimal("1"), DAY)

    def test_a_rate_is_superseded_not_edited(self):
        rate = MaterialRate.objects.get(item=self.virgin)
        rate.rate = Decimal("1")
        self.refused("Enter a new one", rate.save)
        self.refused("Supersede it", rate.delete)
        future = MaterialRate.objects.create(item=self.virgin, rate=Decimal("104"),
                                             valid_from=timezone.localdate() + datetime.timedelta(days=5))
        future.delete()
        self.assertFalse(MaterialRate.objects.filter(pk=future.pk).exists())

    def test_an_ended_or_inactive_specification(self):
        BagSpecificationModel = type(self.sack)
        BagSpecificationModel.objects.filter(pk=self.sack.pk).update(
            valid_to=DAY - datetime.timedelta(days=1))
        self.sack.refresh_from_db()
        self.refused("no longer in force", cost, self.sack, Decimal("1"), DAY)
        BagSpecificationModel.objects.filter(pk=self.sack.pk).update(valid_to=None, is_active=False)
        self.sack.refresh_from_db()
        self.refused("is not active", cost, self.sack, Decimal("1"), DAY)


class FrozenTests(QuotingTestCase):
    def test_a_later_rate_does_not_move_a_costed_sheet(self):
        sheet = cost(self.sack, Decimal("1000"), DAY)
        MaterialRate.objects.create(item=self.virgin, rate=Decimal("150"),
                                    valid_from=DAY + datetime.timedelta(days=1))
        sheet = CostSheet.objects.get(pk=sheet.pk)
        self.assertEqual(sheet.quoted_price, Decimal("15.37"))
        self.assertEqual(self.line(sheet, "material", self.virgin).rate, Decimal("102"))

    def test_nothing_edits_a_sheet_or_its_lines(self):
        sheet = cost(self.sack, Decimal("1000"), DAY)
        sheet.quantity = Decimal("5")
        self.refused("Cost it again", sheet.save)
        line = sheet.lines.first()
        self.refused("written when it is costed", line.save)


class QuoteTests(QuotingTestCase):
    def setUp(self):
        super().setUp()
        self.customer = Party.objects.create(code="C1", name="Safe Agri")
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.quotation = Quotation.objects.create(customer=self.customer, quotation_date=DAY)
        self.sheet = cost(self.sack, Decimal("50000"), DAY)

    def test_the_price_goes_on_the_draft(self):
        line = quote(self.sheet, self.quotation, [])
        self.assertEqual((line.item, line.quantity, line.unit_price),
                         (self.bag_item, Decimal("50000"), Decimal("15.37")))
        self.assertIn("B60X100: unlaminated", line.description)
        self.assertEqual(CostSheet.objects.get(pk=self.sheet.pk).quotation_line, line)

    def test_the_line_carries_the_taxes_named(self):
        gst = Tax.objects.create(code="GST5", name="GST 5%", rate=Decimal("5"))
        line = quote(self.sheet, self.quotation, [gst])
        self.assertEqual(list(line.taxes.all()), [gst])

    def test_not_on_a_quotation_in_another_currency(self):
        usd = Currency.objects.create(code="USD", name="US dollar", is_base=False)
        Quotation.objects.filter(pk=self.quotation.pk).update(currency=usd)
        self.quotation.refresh_from_db()
        self.refused("costed in the base currency", quote, self.sheet, self.quotation, [])
        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Quotation.objects.filter(pk=self.quotation.pk).update(currency=inr)
        self.quotation.refresh_from_db()
        self.assertEqual(quote(self.sheet, self.quotation, []).unit_price, Decimal("15.37"))

    def test_once(self):
        quote(self.sheet, self.quotation, [])
        self.refused("already quoted", quote, self.sheet, self.quotation, [])

    def test_only_on_a_draft(self):
        Quotation.objects.filter(pk=self.quotation.pk).update(status=QuotationStatus.SENT)
        self.quotation.refresh_from_db()
        self.refused("added to a draft", quote, self.sheet, self.quotation, [])

    def test_a_quoted_sheet_stays_while_its_line_does(self):
        line = quote(self.sheet, self.quotation, [])
        sheet = CostSheet.objects.get(pk=self.sheet.pk)
        self.refused("Remove the line first", sheet.delete)
        line.delete()
        sheet = CostSheet.objects.get(pk=self.sheet.pk)
        self.assertIsNone(sheet.quotation_line)
        quote(sheet, self.quotation, [])


class QuotingApiTests(QuotingTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("costing"))

    def test_costed_over_the_api(self):
        response = self.client.post("/api/manufacturing/cost-sheets/", {
            "specification": self.sack.pk, "quantity": "50000", "costed_on": str(DAY),
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["quoted_price"], "15.37")
        self.assertEqual(len(response.json()["lines"]), 10)
        url = f"/api/manufacturing/cost-sheets/{response.json()['id']}/"
        self.assertEqual(self.client.patch(url, {"quantity": "1"}, format="json").status_code, 405)

    def test_a_missing_rate_is_a_sentence(self):
        MaterialRate.objects.filter(item=self.filler).delete()
        response = self.client.post("/api/manufacturing/cost-sheets/", {
            "specification": self.sack.pk, "quantity": "1", "costed_on": str(DAY)}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no rate for CACO3", str(response.content))

    def test_rates_are_added_not_edited(self):
        rate = MaterialRate.objects.get(item=self.virgin)
        url = f"/api/manufacturing/material-rates/{rate.pk}/"
        self.assertEqual(self.client.put(url, {}, format="json").status_code, 405)
        self.assertEqual(self.client.patch(url, {}, format="json").status_code, 405)

    def test_quoting_needs_the_right_to_quote(self):
        user = User.objects.create_user("coster")
        user.user_permissions.add(*Permission.objects.filter(
            codename__in=["add_costsheet", "view_costsheet"]))
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        sheet = client.post("/api/manufacturing/cost-sheets/", {
            "specification": self.sack.pk, "quantity": "1", "costed_on": str(DAY)},
            format="json").json()
        customer = Party.objects.create(code="C2", name="Anmol")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        quotation = Quotation.objects.create(customer=customer, quotation_date=DAY)
        url = f"/api/manufacturing/cost-sheets/{sheet['id']}/quote/"
        body = {"quotation": quotation.pk, "taxes": []}
        self.assertEqual(client.post(url, body, format="json").status_code, 403)
        response = self.client.post(url, body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["unit_price"], "15.37")

    def test_taxes_must_be_stated(self):
        sheet = cost(self.sack, Decimal("1"), DAY)
        customer = Party.objects.create(code="C3", name="Viotonic")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        quotation = Quotation.objects.create(customer=customer, quotation_date=DAY)
        response = self.client.post(f"/api/manufacturing/cost-sheets/{sheet.pk}/quote/",
                                    {"quotation": quotation.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("taxes", response.json())
