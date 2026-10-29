"""review_stat2 probes: O161/O162 (place of supply, ship-to edits, credit notes)."""
import datetime
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.core.models import Address, AddressType
from apps.gst import tests_einvoice as T
from apps.gst.tests import D, SEPTEMBER, DAY
from apps.gst.returns import gstr1
from apps.gst.einvoice import build
from apps.sales.models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine, Quotation, QuotationLine


def show(*a):
    print("RV2", *a)


class Place(T.EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.b2c = self.party("B2C", gst_state="27", gst_registration="unregistered")
        self.address(self.b2c, "Shop 9, Main Road", "Nashik", "27", "422002")
        self.onward = self.party("KA-ONWARD", gstin=T.gstin("29AABCO4444D1Z"))

    site = T.PlaceOfSupplyTests.site
    taxes = T.PlaceOfSupplyTests.taxes
    lorry = T.PlaceOfSupplyTests.lorry

    def make(self, customer, ship=None, bill=None, post=True):
        inv = Invoice.objects.create(customer=customer, invoice_date=DAY, receivable_account=self.ar, currency=self.inr,
                                     shipping_address=ship, billing_address=bill)
        line = InvoiceLine.objects.create(invoice=inv, item=self.sack, quantity=D("2"), unit_price=D("1000"),
                                          revenue_account=self.revenue)
        line.taxes.set(self.pair)
        if post:
            inv.post()
        return inv, line

    def seen(self, tag, inv, line):
        inv.refresh_from_db()
        out = {"place": inv.place_of_supply, "taxes": self.taxes(line)}
        try:
            (row,) = (gstr1(*SEPTEMBER)["b2b"] if inv.customer.tax_profile.gstin else gstr1(*SEPTEMBER)["b2cs"])
            out["gstr1_pos"] = row["place_of_supply"]
        except Exception as e:  # noqa
            out["gstr1"] = repr(e)[:80]
        try:
            out["einv_pos"] = build(inv)["BuyerDtls"]["Pos"]
        except Exception as e:  # noqa
            out["einv"] = repr(e)[:80]
        try:
            out["eway"] = self.lorry(inv)
        except Exception as e:  # noqa
            out["eway"] = repr(e)[:80]
        show(tag, out)
        return out

    # --- item 4: ship-to edited after the invoice posted -------------------------------------------------
    def test_edit_after_post_then_credit_note(self):
        for label, state in (("ka->mh 27", "27"), ("ka->blank", ""), ("ka->garbage", "zz")):
            Invoice.objects.all().delete() if False else None
        site = self.site(self.mh)
        inv, line = self.make(self.mh, ship=site, bill=site)
        self.seen("posted (KA own, both)", inv, line)
        site.state = "27"
        try:
            site.save()
            show("address .save() of a posted invoice's address: allowed")
        except ValidationError as e:
            show("address .save() refused:", str(e)[:60])
        Address.objects.filter(pk=site.pk).update(state="27")
        self.seen("after address edit to 27", inv, line)
        note = inv.create_credit_note(quantities={line: D("1")})
        note.refresh_from_db()
        nline = note.lines.get()
        show("credit note after edit to 27: place", note.place_of_supply, "taxes",
             sorted((r.tax.code, r.amount) for r in nline.recorded_taxes.all()) or "unposted?", "posted", note.posted)
        Address.objects.filter(pk=site.pk).update(state="")
        self.seen("after address edit to blank", inv, line)
        note2 = inv.create_credit_note(quantities={line: D("1")})
        show("2nd credit note after blank: place", note2.place_of_supply, "posted", note2.posted,
             sorted((r.tax.code, r.amount) for r in note2.lines.get().recorded_taxes.all()))

    def test_edit_after_post_unregistered_nobody_address(self):
        nobody = Address.objects.create(line1="Plot", city="Hubballi", state="29", postal_code="580001")
        # bill-to/ship-to of no party: refuse unless same as principal... use KA ship-to with buyer on record in 29
        buyer = self.party("B2C-KA", gst_state="29", gst_registration="unregistered")
        self.address(buyer, "S 1", "Hubballi", "29", "580001")
        inv, line = self.make(buyer, ship=nobody)
        self.seen("ship-to no party KA, buyer KA", inv, line)
        Address.objects.filter(pk=nobody.pk).update(state="27")
        self.seen("nobody addr edited to 27", inv, line)
        note = inv.create_credit_note()
        show("note place", note.place_of_supply, note.posted)

    # --- O162: refused as saved; reading never; the fix path -----------------------------------------------
    def test_bad_address_draft_reads_saves_posts(self):
        site = self.site(self.mh)
        inv, line = self.make(self.mh, ship=site, bill=site, post=False)
        Address.objects.filter(pk=site.pk).update(state="")
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient
        call_command("setup_roles", verbosity=0)
        u = User.objects.create_superuser("ctl", "c@x.com", "pw")
        c = APIClient()
        c.force_authenticate(u)
        r = c.get("/api/sales/invoices/")
        show("list", r.status_code)
        r = c.get(f"/api/sales/invoices/{inv.pk}/")
        show("detail", r.status_code)
        r = c.get(f"/api/sales/invoices/{inv.pk}/", {"format": "json"})
        # a PATCH of an unrelated field while the address is bad
        r = c.patch(f"/api/sales/invoices/{inv.pk}/", {"memo": "hello"}, format="json")
        show("PATCH memo while bad", r.status_code, str(r.content)[:160])
        # PATCH the address row itself in the API (fix) then the invoice
        r = c.patch(f"/api/core/addresses/{site.pk}/", {"state": "29"}, format="json")
        show("PATCH address fix", r.status_code)
        r = c.patch(f"/api/sales/invoices/{inv.pk}/", {"memo": "hello"}, format="json")
        show("PATCH memo after fix", r.status_code)
        # delete a line while the address is bad
        Address.objects.filter(pk=site.pk).update(state="")
        r = c.delete(f"/api/sales/invoice-lines/{line.pk}/")
        show("DELETE line while bad", r.status_code)
        # the e-invoice / lorry on a draft?
        # post a bad one over the API
        inv2, l2 = self.make(self.mh, ship=site, bill=site, post=False) if False else (None, None)

    def test_order_line_refused_but_saved(self):
        site = self.site(self.mh)
        Address.objects.filter(pk=site.pk).update(state="")
        order = SalesOrder.objects.create(customer=self.mh, order_date=DAY, currency=self.inr, shipping_address=site,
                                          billing_address=site)
        try:
            SalesOrderLine.objects.create(order=order, item=self.sack, uom=self.sack.uom, quantity=D("1"),
                                          unit_price=D("1000"), revenue_account=self.revenue)
            show("order line: no refusal")
        except ValidationError as e:
            show("order line refused:", str(e)[:70], "| rows left:", order.lines.count())
        q = Quotation.objects.create(customer=self.mh, quotation_date=DAY, currency=self.inr, shipping_address=site) if hasattr(Quotation, "quotation_date") else None
        if q is not None:
            try:
                QuotationLine.objects.create(quotation=q, item=self.sack, quantity=D("1"), unit_price=D("1000"))
                show("quotation line: no refusal")
            except ValidationError as e:
                show("quotation line refused:", str(e)[:70], "| rows left:", q.lines.count())

    def test_order_posted_status_then_address_edit(self):
        """A non-draft order whose address is later blanked: any save refused? (draft-only per the diff)"""
        site = self.site(self.mh)
        order = SalesOrder.objects.create(customer=self.mh, order_date=DAY, currency=self.inr, shipping_address=site,
                                          billing_address=site)
        line = SalesOrderLine.objects.create(order=order, item=self.sack, uom=self.sack.uom, quantity=D("1"),
                                             unit_price=D("1000"), revenue_account=self.revenue)
        line.taxes.set(self.pair)
        order.confirm()
        Address.objects.filter(pk=site.pk).update(state="")
        order.refresh_from_db()
        try:
            order.save()
            show("confirmed order save with bad address: ok")
        except ValidationError as e:
            show("confirmed order save refused", str(e)[:60])

    def test_quotation_any_status(self):
        site = self.site(self.mh)
        fields = [f.name for f in Quotation._meta.get_fields() if hasattr(f, "name")]
        show("quotation fields", [f for f in fields if "date" in f or "status" in f])

    def test_query_count_of_total(self):
        site = self.site(self.mh)
        inv, line = self.make(self.mh, ship=site, bill=site, post=False)
        inv = Invoice.objects.get(pk=inv.pk)
        with CaptureQueriesContext(connection) as ctx:
            inv.total()
        show("queries for total() on draft:", len(ctx))


class Place2(Place):
    """Second batch (the parent's tests run again; ignore them)."""

    def test_b_draft_bad_ship_only(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        from django.core.management import call_command
        site = self.site(self.mh)
        inv, line = self.make(self.mh, ship=site, post=False)
        Address.objects.filter(pk=site.pk).update(state="")
        u = User.objects.create_superuser("su", "s@x.com", "pw")
        c = APIClient()
        c.force_authenticate(u)
        show("B list", c.get("/api/sales/invoices/").status_code, "detail", c.get(f"/api/sales/invoices/{inv.pk}/").status_code)
        r = c.patch(f"/api/sales/invoices/{inv.pk}/", {"reference": "x"}, format="json")
        show("B PATCH ref while bad", r.status_code, str(r.content)[:140])
        r = c.delete(f"/api/sales/invoice-lines/{line.pk}/")
        show("B DELETE line while bad", r.status_code)
        r = c.post(f"/api/sales/invoices/{inv.pk}/post_invoice/")
        show("B post over API", r.status_code, str(r.content)[:140])
        r = c.patch(f"/api/core/addresses/{site.pk}/", {"state": "29"}, format="json")
        show("B PATCH address fix", r.status_code)

    def test_c_order_quotation_left_rows(self):
        site = self.site(self.mh)
        Address.objects.filter(pk=site.pk).update(state="")
        order = SalesOrder.objects.create(customer=self.mh, order_date=DAY, currency=self.inr, shipping_address=site)
        try:
            SalesOrderLine.objects.create(order=SalesOrder.objects.get(pk=order.pk), item=self.sack, uom=self.sack.uom, quantity=D("1"),
                                          unit_price=D("1000"), revenue_account=self.revenue)
            show("C order line: no refusal")
        except ValidationError as e:
            show("C order line refused:", str(e)[:50], "| rows left:", SalesOrderLine.objects.filter(order=order).count())
        q = Quotation.objects.create(customer=self.mh, quotation_date=DAY, currency=self.inr, shipping_address=site)
        show("C quotation shipping_address", q.shipping_address_id, "delivered_to", q.delivered_to())
        try:
            QuotationLine.objects.create(quotation=Quotation.objects.get(pk=q.pk), item=self.sack, quantity=D("1"), unit_price=D("1000"))
            show("C quotation line: no refusal")
        except ValidationError as e:
            show("C quotation line refused:", str(e)[:50], "| rows left:", q.lines.count())

    def test_d_default_address_edited_after_post(self):
        buyer = self.party("MH2", gstin=T.gstin("27AABCM2222B1Z"))
        ka = Address.objects.create(party=buyer, address_type=AddressType.SHIPPING, line1="KA plant", city="Hubballi",
                                    state="29", postal_code="580001", is_primary=True)
        inv, line = self.make(buyer, post=True)
        self.seen("D posted w/ default KA shipping", inv, line)
        ka.state = "27"
        ka.save()
        show("D default shipping address edited to 27 after posting (allowed)")
        self.seen("D after edit", inv, line)
        note = inv.create_credit_note(quantities={line: D("1")})
        show("D credit note place", note.place_of_supply, sorted((r.tax.code, r.amount) for r in note.lines.get().recorded_taxes.all()))
        ka.state = ""
        ka.save()
        note2 = inv.create_credit_note(quantities={line: D("1")})
        show("D credit note after blank default", note2.place_of_supply, note2.posted)
