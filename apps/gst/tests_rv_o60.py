from django.core.exceptions import ValidationError
from apps.core.models import Address, AddressType
from apps.gst import tests_einvoice as T
from apps.gst.tests import D, SEPTEMBER, DAY
from apps.gst.returns import gstr1
from apps.sales.models import Invoice, InvoiceLine
from apps.gst.einvoice import build
import datetime


def show(*a):
    print("RV", *a)


class O60(T.PlaceOfSupplyTests):
    def mk(self, customer, ship=None, bill=None, item=True):
        inv = Invoice.objects.create(customer=customer, invoice_date=DAY, receivable_account=self.ar, currency=self.inr,
                                     shipping_address=ship, billing_address=bill)
        line = InvoiceLine.objects.create(invoice=inv, item=self.sack, quantity=D("1"), unit_price=D("1000"), revenue_account=self.revenue)
        line.taxes.set(self.pair)
        return inv, line

    def test_rv_registered_single_ka_address_both(self):
        site = self.site(self.mh)
        inv, line = self.mk(self.mh, ship=site, bill=site)
        try:
            inv.post()
            show("S2 reg MH, bill=ship=KA own:", self.taxes(line), inv.place_of_supply)
        except ValidationError as e:
            show("S2 refused", e)

    def test_rv_credit_note_after_address_change(self):
        # no addresses on invoice; customer has default shipping in KA
        ka = self.site(self.b2c)
        inv, line = self.mk(self.b2c)
        inv.post()
        show("S1 invoice", self.taxes(line), inv.place_of_supply, "delivered_to", inv.delivered_to())
        # now customer's KA shipping address is deleted/changed
        Address.objects.filter(pk=ka.pk).update(state="27")
        cn = inv.create_credit_note()
        cl = cn.lines.get()
        show("S1 credit note", self.taxes(cl), cn.place_of_supply)
        (row,) = gstr1(*SEPTEMBER)["b2cs"] if len(gstr1(*SEPTEMBER)["b2cs"]) == 1 else (None,)
        show("S1 gstr1 b2cs", gstr1(*SEPTEMBER)["b2cs"])

    def test_rv_blank_state_own_address(self):
        a = Address.objects.create(party=self.b2c, address_type=AddressType.SHIPPING, line1="x", city="y", state="", postal_code="1")
        inv, line = self.mk(self.b2c, ship=a)
        try:
            inv.post(); show("S5 blank-state posted", inv.place_of_supply)
        except ValidationError as e:
            show("S5 refused:", e)

    def test_rv_no_addresses_at_all(self):
        p = self.party("NOADDR", gst_state="27", gst_registration="unregistered")
        inv, line = self.mk(p)
        try:
            inv.post(); show("S6 no addr posted", inv.place_of_supply, self.taxes(line))
        except Exception as e:
            show("S6 error", type(e), e)

    def test_rv_mixed_service(self):
        from apps.accounting.models import ChargeType
        inv, line = self.mk(self.b2c, ship=self.site(self.b2c))
        show("S3 mixed: only goods here")

    def test_rv_no_party_same_state(self):
        a = Address.objects.create(address_type=AddressType.SHIPPING, line1="Plot", city="Nashik", state="27", postal_code="1")
        inv, line = self.mk(self.b2c, ship=a)
        try:
            inv.post(); show("S4 no-party same state", inv.place_of_supply)
        except ValidationError as e:
            show("S4 refused", e)

    def test_rv_no_party_blank_state(self):
        a = Address.objects.create(address_type=AddressType.SHIPPING, line1="Plot", city="Hubballi", state="", postal_code="1")
        inv, line = self.mk(self.b2c, ship=a)
        try:
            inv.post(); show("S4b no-party blank state", inv.place_of_supply, self.taxes(line))
        except ValidationError as e:
            show("S4b refused", e)

    def test_rv_no_party_ka_registered(self):
        a = Address.objects.create(address_type=AddressType.SHIPPING, line1="Plot", city="Hubballi", state="29", postal_code="1")
        inv, line = self.mk(self.mh, ship=a)
        try:
            inv.post(); show("S4c registered, no-party KA", inv.place_of_supply, self.taxes(line))
        except ValidationError as e:
            show("S4c refused", e)

    def test_rv_cn_of_no_party_invoice_other_state(self):
        # invoice posted when ship-to belonged to no party but same state; then the address is edited to KA; credit note
        a = Address.objects.create(address_type=AddressType.SHIPPING, line1="Plot", city="Nashik", state="27", postal_code="1")
        inv, line = self.mk(self.b2c, ship=a)
        inv.post()
        Address.objects.filter(pk=a.pk).update(state="29")
        try:
            cn = inv.create_credit_note()
            show("S7 CN after ship-to edit", cn.place_of_supply, self.taxes(cn.lines.get()))
        except ValidationError as e:
            show("S7 CN refused", e)

    def test_rv_draft_api_blank_state(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        a = Address.objects.create(party=self.b2c, address_type=AddressType.SHIPPING, line1="x", city="y", state="", postal_code="1")
        inv, line = self.mk(self.b2c, ship=a)
        u = User.objects.create_superuser("rv", "rv@x.com", "p")
        c = APIClient(); c.force_authenticate(u)
        r = c.get(f"/api/sales/invoices/{inv.pk}/")
        show("S8 GET draft invoice", r.status_code, str(r.content[:200]))
        r = c.get("/api/sales/invoices/")
        show("S8 GET list", r.status_code, str(r.content[:200]))
        try:
            show("S8 total", inv.total())
        except Exception as e:
            show("S8 total raised", type(e).__name__, e)
