"""
Sunrise Cement's order made a job-work order: they send the granule and
pay 20 a kilogramme for the conversion.

  900 kg of tape goes back at 20: 18,000.00 of conversion billed. With
  about 1,000 kg of their granule in it at the 110 they declared, the
  goods going back are worth 128,000.00, stated on the e-way bill. A
  stated 15,000.00 is refused: less than the conversion alone.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.gst import GstSettings, gstin_check_character
from apps.accounting.models import Account, AccountType, FiscalPosition
from apps.core.models import Address, AddressType, Company
from apps.inventory.models import Item, Lot
from apps.planning.mrp import (
    job_work_left_out,
    plan,
    sales_demand,
    work_order_demand,
    work_order_supply,
)
from apps.sales.models import (
    Delivery,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    SalesOrder,
    SalesOrderLine,
    SuppliedItem,
)

from .tests_inward import InwardTestCase
from .tests_orders import TODAY


class JobWorkOrderTestCase(InwardTestCase):
    def setUp(self):
        super().setUp()
        self.their_order.is_job_work = True
        self.their_order.save()
        SuppliedItem.objects.create(order=self.their_order, item=self.virgin)
        self.their_order.confirm()

    def gst(self, sac="998898"):
        first = "27AABCD1234E1Z"
        return GstSettings.objects.create(
            gstin=first + gstin_check_character(first), job_work_sac=sac,
            interstate_position=FiscalPosition.objects.create(code="INTER", name="Inter"))

    def conversion_invoice(self, order_line=None):
        revenue = Account.objects.get_or_create(
            code="4100", defaults={"name": "Conversion", "account_type": AccountType.INCOME})[0]
        receivable = Account.objects.get_or_create(
            code="1100", defaults={"name": "Debtors", "account_type": AccountType.ASSET})[0]
        line = order_line or self.their_line
        invoice = Invoice.objects.create(customer=line.order.customer, invoice_date=TODAY,
                                         receivable_account=receivable, currency=self.usd,
                                         sales_order=line.order)
        InvoiceLine.objects.create(invoice=invoice, order_line=line, item=self.tape,
                                   quantity=Decimal("900"), unit_price=Decimal("20"),
                                   revenue_account=revenue)
        invoice.post()
        return invoice


class TheOrderTests(JobWorkOrderTestCase):
    def test_job_work_names_what_the_customer_sends(self):
        order = SalesOrder.objects.create(customer=self.sunrise, order_date=TODAY,
                                          currency=self.usd, is_job_work=True)
        SalesOrderLine.objects.create(order=order, item=self.tape, uom=self.kg,
                                      warehouse=self.plant, quantity=Decimal("10"),
                                      unit_price=Decimal("20"))
        with self.assertRaisesMessage(ValidationError, "names nothing the customer supplies"):
            order.confirm()

    def test_an_ordinary_sale_supplies_nothing(self):
        order = SalesOrder.objects.create(customer=self.sunrise, order_date=TODAY,
                                          currency=self.usd)
        with self.assertRaisesMessage(ValidationError, "is not job work"):
            SuppliedItem.objects.create(order=order, item=self.virgin)

    def test_settled_once_confirmed(self):
        with self.assertRaisesMessage(ValidationError, "what the customer supplies is settled"):
            SuppliedItem.objects.create(order=self.their_order, item=self.regrind)
        with self.assertRaisesMessage(ValidationError, "what the customer supplies is settled"):
            self.their_order.supplied_items.get().delete()
        self.their_order.is_job_work = False
        with self.assertRaisesMessage(ValidationError, "whether it is job work cannot change"):
            self.their_order.save()


class PlanningTests(JobWorkOrderTestCase):
    def test_what_they_send_is_not_the_companys_to_buy(self):
        rows = work_order_demand(self.virgin, self.plant, TODAY)
        self.assertNotIn(self.their_run.pk, [row.work_order.pk for row in rows if row.work_order])
        # What they do not send is still planned: the regrind on the same run.
        rows = work_order_demand(self.regrind, self.plant, TODAY)
        self.assertIn(self.their_run.pk, [row.work_order.pk for row in rows if row.work_order])

    def test_their_order_and_their_runs_output_are_nobody_elses(self):
        ordinary = self.sold("1000")
        self.sale.confirm()
        demanded = [row.sales_order_line.pk for row in sales_demand(self.tape, self.plant, TODAY)]
        self.assertEqual(demanded, [ordinary.pk])
        supplied = [row.document.pk for row in work_order_supply(self.tape, self.plant, TODAY)]
        self.assertNotIn(self.their_run.pk, supplied)

    def test_a_line_naming_no_warehouse_is_said_too(self):
        SalesOrderLine.objects.filter(pk=self.their_line.pk).update(warehouse=None)
        self.their_run.cancel()
        self.assertEqual([line.pk for line in job_work_left_out(self.tape, self.plant)],
                         [self.their_line.pk])

    def test_a_line_nobody_is_making_is_said(self):
        self.assertEqual(job_work_left_out(self.tape, self.plant), [])
        self.their_run.cancel()
        (line,) = job_work_left_out(self.tape, self.plant)
        self.assertEqual(line, self.their_line)
        run = plan(self.plant, planned_on=TODAY)
        self.assertIn(f"job-work order {self.their_order.number}", run.deferred_demand)


class ConversionInvoiceTests(JobWorkOrderTestCase):
    def test_billed_as_a_service(self):
        self.gst()
        invoice = self.conversion_invoice()
        self.assertEqual(invoice.lines.get().hsn_code, "998898")
        note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("100")})
        self.assertEqual(note.lines.get().hsn_code, "998898")

    def test_a_credit_note_keeps_the_code_its_invoice_was_billed_under(self):
        settings = self.gst()
        invoice = self.conversion_invoice()
        GstSettings.objects.filter(pk=settings.pk).update(job_work_sac="998899")
        note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("100")})
        self.assertEqual(note.lines.get().hsn_code, "998898")

    def test_not_without_the_code(self):
        self.gst(sac="")
        with self.assertRaisesMessage(ValidationError, "Set the SAC for conversion"):
            self.conversion_invoice()

    def test_the_code_is_a_services_code(self):
        with self.assertRaisesMessage(ValidationError, "is a goods code"):
            self.gst(sac="6305")

    def test_an_ordinary_sale_keeps_its_goods_code(self):
        self.gst()
        Item.objects.filter(pk=self.tape.pk).update(hsn_code="54041900")
        ordinary = self.sold("900")
        self.sale.confirm()
        self.assertEqual(self.conversion_invoice(ordinary).lines.get().hsn_code, "54041900")


class GoingBackTests(JobWorkOrderTestCase):
    def setUp(self):
        super().setUp()
        self.gst()
        Item.objects.filter(pk=self.tape.pk).update(hsn_code="54041900")
        plant = Address.objects.create(line1="Plot 12, MIDC Chakan", city="Pune",
                                       state="Maharashtra", postal_code="410501")
        if not Company.objects.exists():
            Company.objects.create(name="Deccan Polysacks")
        Company.objects.update(address=plant)
        Address.objects.create(party=self.sunrise, address_type=AddressType.SHIPPING,
                               line1="Cement Works Road", city="Solapur", state="Maharashtra",
                               postal_code="413001", is_primary=True)
        self.receive()
        self.draw(self.their_run, "1000")
        made = Lot.objects.create(item=self.tape, code="TAPE-SUN")
        self.produce(self.their_run, "900", lot=made).post()
        self.delivery = Delivery.objects.create(sales_order=self.their_order,
                                                delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=self.delivery, order_line=self.their_line,
                                    warehouse=self.plant, quantity_shipped=Decimal("900"),
                                    lot=made)
        self.delivery.post()

    def prepare(self, **extra):
        from apps.gst.ewaybill import prepare

        return prepare(self.delivery, vehicle_number="MH13AB1234", **extra)

    def test_the_floor_is_the_conversion_after_its_discount(self):
        # 900 x 20 less 10% = 16,200.00 billed, so 17,000.00 stands.
        SalesOrderLine.objects.filter(pk=self.their_line.pk).update(
            discount_percent=Decimal("10"))
        self.assertEqual(self.prepare(declared_value="17000").payload["totInvValue"], 17000.0)

    def test_a_value_that_is_no_value(self):
        with self.assertRaisesMessage(ValidationError, "is not an amount"):
            self.prepare(declared_value="lots")
        with self.assertRaisesMessage(ValidationError, "state what"):
            self.prepare(declared_value="0")

    def test_on_a_delivery_challan_at_the_stated_value(self):
        bill = self.prepare(declared_value="128000")
        payload = bill.payload
        self.assertEqual((payload["subSupplyType"], payload["docType"], payload["docNo"],
                          payload["toGstin"], payload["totInvValue"]),
                         ("6", "CHL", self.delivery.number, "URP", 128000.0))
        (item,) = payload["itemList"]
        self.assertEqual((item["hsnCode"], item["quantity"], item["taxableAmount"]),
                         (54041900, 900.0, 128000.0))
        self.assertTrue(bill.required)

    def test_the_value_is_stated_and_at_least_the_conversion(self):
        with self.assertRaisesMessage(ValidationError, "State the goods' value"):
            self.prepare()
        with self.assertRaisesMessage(ValidationError, "is 18000.00; they cannot be worth 15000"):
            self.prepare(declared_value="15000")

    def test_goods_coming_back_are_the_senders_to_cover(self):
        back = self.delivery.create_return()
        with self.assertRaisesMessage(ValidationError, "goods coming back"):
            self.prepare_for(back)

    def prepare_for(self, delivery):
        from apps.gst.ewaybill import prepare

        return prepare(delivery, vehicle_number="MH13AB1234", declared_value="128000")

    def test_a_sale_moves_on_its_invoice(self):
        from apps.gst.ewaybill import prepare

        ours = Lot.objects.create(item=self.tape, code="TAPE-OWN")
        self.stock(self.tape, "10", "50", lot=ours)
        ordinary = self.deliver(self.sunrise, ours, "10")
        with self.assertRaisesMessage(ValidationError, "is a sale"):
            prepare(ordinary, vehicle_number="MH13AB1234", declared_value="1000")
