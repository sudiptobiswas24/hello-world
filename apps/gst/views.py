import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Count

from apps.core.api import flag

from . import einvoice, ewaybill
from .gstr2b import Gstr2bStatement, keep, reconcile
from .serializers import EInvoiceSerializer, EwayBillSerializer, Gstr2bStatementSerializer

from .returns import gstr1, gstr1_json, gstr3b, month
from apps.core.audit import RowHeldViewSetMixin


class CanCompileReturns(BasePermission):
    """A return shows every customer's and vendor's figures for a month:
    its own permission, not whoever can read an invoice."""

    def has_permission(self, request, view):
        return request.user.has_perm("gst.compile_returns")


class ReturnView(APIView):
    """GET ?period=YYYY-MM. Compiled on each request from the posted
    documents; nothing is stored and nothing is filed."""

    permission_classes = [IsAuthenticated, CanCompileReturns]
    compile = None

    def get(self, request):
        period = request.query_params.get("period")
        if not period:
            raise DRFValidationError(["Give a period as YYYY-MM."])
        try:
            return Response(self.compile(*month(period)))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)


class Gstr1View(ReturnView):
    compile = staticmethod(gstr1)


class Gstr1JsonView(ReturnView):
    """The offline tool's shape — validate it there before uploading."""

    @staticmethod
    def compile(start, end):
        return gstr1_json(gstr1(start, end))


class Gstr3bView(ReturnView):
    compile = staticmethod(gstr3b)


class Itc04View(APIView):
    """GET ?start=YYYY-MM-DD&end=YYYY-MM-DD: the half-year or year asked for."""

    permission_classes = [IsAuthenticated, CanCompileReturns]

    def get(self, request):
        from .itc04 import itc04

        try:
            start = datetime.date.fromisoformat(request.query_params.get("start", ""))
            end = datetime.date.fromisoformat(request.query_params.get("end", ""))
        except ValueError:
            raise DRFValidationError(["Give start and end as YYYY-MM-DD."])
        try:
            return Response(itc04(start, end))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)


class Gstr2bStatementViewSet(RowHeldViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin,
                             viewsets.GenericViewSet):
    """
    The months' GSTR-2B files as kept. `upload` keeps one (POST text,
    period, replace); a kept month is deleted to be taken again. The
    returns' permission throughout: a 2B names every supplier's figures.
    """

    queryset = Gstr2bStatement.objects.select_related("created_by").annotate(line_count=Count("lines"))
    serializer_class = Gstr2bStatementSerializer
    permission_classes = [IsAuthenticated, CanCompileReturns]
    filter_fields = ["period"]
    ordering_fields = ["period"]

    @action(detail=False, methods=["post"])
    def upload(self, request):
        text = request.data.get("text") or ""
        if not text.strip():
            raise DRFValidationError({"text": ["Give the file's text."]})
        statement, skipped = keep(str(request.data.get("period") or ""), text, user=request.user,
                                  replace=flag(request.data, "replace", False))
        data = self.get_serializer(self.get_queryset().get(pk=statement.pk)).data
        data["skipped"] = skipped
        return Response(data, status=201)


class Gstr2bMatchView(APIView):
    """GET ?period=YYYY-MM: the month's 2B against the year's posted bills (gstr2b.reconcile)."""

    permission_classes = [IsAuthenticated, CanCompileReturns]

    def get(self, request):
        period = request.query_params.get("period")
        if not period:
            raise DRFValidationError(["Give a period as YYYY-MM."])
        try:
            return Response(reconcile(period))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)


def _moment(value, what):
    """An ISO date and time from the portal's answer; naive is read as local time."""
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise DRFValidationError([f"{what} is not a date and time (YYYY-MM-DDTHH:MM)."])
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


class EInvoiceViewSet(RowHeldViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    POST {invoice} builds the payload to register (again, until the portal
    has answered); record/ keeps the portal's answer, checked against it.
    Nothing is sent from here.
    """

    queryset = einvoice.EInvoice.objects.select_related("invoice")
    serializer_class = EInvoiceSerializer
    filter_fields = ["invoice"]
    search_fields = ["invoice__number", "irn", "ack_number"]
    # Recording the portal's cancellation posts a full credit note: whoever posts invoices does it,
    # not the officer who files the return and books nothing (review F8).
    action_permission_map = {"record": "gst.change_einvoice", "cancel": "gst.change_einvoice",
                             "record_cancellation": "sales.post_invoice"}

    def create(self, request):
        from apps.sales.models import Invoice

        invoice = get_object_or_404(Invoice, pk=request.data.get("invoice"))
        return Response(EInvoiceSerializer(einvoice.prepare(invoice)).data, status=201)

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        record = self.get_object()
        record.record(
            irn=request.data.get("irn"), ack_number=request.data.get("ack_number"),
            ack_date=_moment(request.data.get("ack_date"), "The acknowledgement date"),
            signed_qr=str(request.data.get("signed_qr", "")),
        )
        return Response(EInvoiceSerializer(record).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """{reason (1 duplicate, 2 data entry mistake, 3 order cancelled, 4 others), remarks}: the request to send."""
        record = self.get_object()
        record.cancellation(str(request.data.get("reason", "")), str(request.data.get("remarks", "")))
        return Response(EInvoiceSerializer(record).data)

    @action(detail=True, methods=["post"])
    def record_cancellation(self, request, pk=None):
        """
        The portal's answer to the cancellation, {irn, cancelled_at} (its Irn and CancelDate): the books
        reverse the invoice by a full credit note. Needs the right to post invoices and to read e-invoices.
        """
        if not request.user.has_perm("gst.view_einvoice"):
            raise PermissionDenied("Recording a cancellation needs the e-invoices as well as the right to post.")
        record = self.get_object()
        answered = str(request.data.get("irn") or "").strip().lower()
        if answered != record.irn:
            raise DRFValidationError({"irn": [
                f"The portal's answer names IRN {answered or 'none'}; this e-invoice's is {record.irn}. Give the "
                "IRN and the cancellation date the portal returned."]})
        record.record_cancellation(_moment(request.data.get("cancelled_at"), "The cancellation date"))
        return Response(EInvoiceSerializer(record).data)


class EwayBillViewSet(RowHeldViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    POST {invoice | challan | delivery (with declared_value), mode, distance_km, transporter_id,
    transporter_name, vehicle_number, vehicle_type, transport_doc_number,
    transport_doc_date} builds the payload; record/ keeps the number the
    portal gave; cancel/ within 24 hours of it.
    """

    queryset = ewaybill.EwayBill.objects.select_related("invoice", "challan")
    serializer_class = EwayBillSerializer
    filter_fields = ["invoice", "delivery", "challan"]
    search_fields = ["number", "vehicle_number", "invoice__number", "transporter_name"]
    action_permission_map = {"record": "gst.change_ewaybill", "cancel": "gst.change_ewaybill"}

    def create(self, request):
        from apps.manufacturing.jobwork import JobWorkChallan
        from apps.sales.models import Delivery, Invoice

        data = request.data
        named = [key for key in ("invoice", "challan", "delivery") if data.get(key)]
        if len(named) != 1:
            raise DRFValidationError(["Name an invoice, a challan or a delivery: one of them."])
        model = {"invoice": Invoice, "challan": JobWorkChallan, "delivery": Delivery}[named[0]]
        document = get_object_or_404(model, pk=data[named[0]])
        doc_date = data.get("transport_doc_date")
        if doc_date and parse_date(str(doc_date)) is None:
            raise DRFValidationError(["The transport document's date is YYYY-MM-DD."])
        bill = ewaybill.prepare(
            document,
            mode=str(data.get("mode", ewaybill.TransportMode.ROAD)),
            distance_km=data.get("distance_km", 0),
            transporter_id=data.get("transporter_id", ""),
            transporter_name=data.get("transporter_name", ""),
            vehicle_number=data.get("vehicle_number", ""),
            vehicle_type=data.get("vehicle_type", ewaybill.VehicleType.REGULAR),
            transport_doc_number=data.get("transport_doc_number", ""),
            transport_doc_date=parse_date(str(doc_date)) if doc_date else None,
            declared_value=data.get("declared_value"),
        )
        return Response(EwayBillSerializer(bill).data, status=201)

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        bill = self.get_object()
        bill.record(
            number=request.data.get("number"),
            generated_at=_moment(request.data.get("generated_at"), "The generation time"),
            valid_until=_moment(request.data.get("valid_until"), "The expiry"),
        )
        return Response(EwayBillSerializer(bill).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        bill = self.get_object()
        bill.cancel(str(request.data.get("reason", "")), str(request.data.get("remarks", "")))
        return Response(EwayBillSerializer(bill).data)


# -- Reverse charge: the self-invoice, the payment voucher, the tax paid in cash ------------------------

from apps.accounting.models import Account, Payment  # noqa: E402
from apps.core.api import record_or_404, required  # noqa: E402
from apps.purchasing.models import Bill  # noqa: E402

from . import reverse_charge  # noqa: E402
from .serializers import GstCashPaymentSerializer, PaymentVoucherSerializer, SelfInvoiceSerializer  # noqa: E402


class SelfInvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    """POST {bill, date} issues one (s.31(3)(f), Rule 47A); cancel/ {reason, date} takes it back."""

    queryset = reverse_charge.SelfInvoice.objects.select_related("bill")
    serializer_class = SelfInvoiceSerializer
    filter_fields = ["bill", "cancelled_on__isnull"]
    search_fields = ["number", "bill__number", "supplier_name"]
    date_field = "date"
    action_permission_map = {"cancel": "gst.change_selfinvoice", "awaiting": "gst.view_selfinvoice"}

    def create(self, request):
        bill = record_or_404(Bill, request.data.get("bill"), "bill")
        made = reverse_charge.SelfInvoice.issue(bill, on_date=request.data.get("date") or None)
        return Response(SelfInvoiceSerializer(made).data, status=201)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        record = self.get_object()
        record.cancel(str(request.data.get("reason", "")), on_date=request.data.get("date") or None)
        return Response(SelfInvoiceSerializer(record).data)

    @action(detail=False, methods=["get"])
    def awaiting(self, request):
        """The reverse-charge supplies from unregistered suppliers with no self-invoice standing."""
        return Response([{"bill": bill.pk, "bill_number": bill.number, "vendor": bill.vendor.name,
                          "received_on": received, "due_by": last}
                         for bill, received, last in reverse_charge.awaiting_self_invoice()])


class PaymentVoucherViewSet(viewsets.ReadOnlyModelViewSet):
    """POST {payment}: a voucher for each reverse-charge bill it settles (s.31(3)(g), Rule 52)."""

    queryset = reverse_charge.PaymentVoucher.objects.select_related("bill", "payment")
    serializer_class = PaymentVoucherSerializer
    filter_fields = ["payment", "bill", "cancelled_on__isnull"]
    search_fields = ["number", "bill__number", "payment__number"]
    date_field = "date"

    action_permission_map = {"awaiting": "gst.view_paymentvoucher"}

    @action(detail=False, methods=["get"])
    def awaiting(self, request):
        """Payments for a reverse-charge supply with no voucher standing."""
        return Response([{"payment": row.payment_id, "payment_number": row.payment.number, "bill": row.bill_id,
                          "bill_number": row.bill.number, "amount": row.amount}
                         for row in reverse_charge.awaiting_payment_voucher()])

    def create(self, request):
        payment = record_or_404(Payment, request.data.get("payment"), "payment")
        made = reverse_charge.PaymentVoucher.issue_for(payment)
        # The first made, as a record screen opens what it created; the rest are listed by number with it.
        data = PaymentVoucherSerializer(made[0]).data
        data["issued"] = [voucher.number for voucher in made]
        return Response(data, status=201)


class GstCashPaymentViewSet(viewsets.ReadOnlyModelViewSet):
    """pay/ {month, date, bank_account, cash_ledger_account, cpin}; void/ {date}; cash_ledger/ ?account=."""

    queryset = reverse_charge.GstCashPayment.objects.all()
    serializer_class = GstCashPaymentSerializer
    filter_fields = ["month", "voided_entry__isnull"]
    search_fields = ["cpin"]
    date_field = "date"
    # Voiding a challan reverses money through the bank: the bill poster's decision, as a TDS challan's (O128).
    action_permission_map = {"pay": "gst.add_gstcashpayment", "void": "purchasing.post_bill",
                             "cash_ledger": "gst.view_gstcashpayment"}

    @action(detail=False, methods=["post"])
    def pay(self, request):
        data = request.data
        bank = record_or_404(Account, data.get("bank_account"), "bank_account")
        ledger = record_or_404(Account, data.get("cash_ledger_account"), "cash_ledger_account")
        required(data, "month", "date", "cpin")
        payment = reverse_charge.GstCashPayment.pay(str(data["month"]), data["date"], bank, ledger, data["cpin"])
        return Response(self.get_serializer(payment).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        payment = self.get_object()
        payment.void(on_date=request.data.get("date") or None)
        return Response(self.get_serializer(payment).data)

    @action(detail=False, methods=["get"])
    def cash_ledger(self, request):
        """?account= for one; without it, every account a challan used as the cash ledger."""
        if not request.query_params.get("account"):
            return Response([dict(ledger, account=ledger["account"].pk) for ledger in
                             map(reverse_charge.cash_ledger, reverse_charge.cash_ledger_accounts())])
        account = record_or_404(Account, request.query_params.get("account"), "account")
        # Only the accounts this ledger is about: any other pk read the books of whatever account it named, the
        # payables control included, for a role that reads no account at all (review_rcm F1).
        if not reverse_charge.ledger_accounts().filter(pk=account.pk).exists():
            raise DRFValidationError({"account": ["Not a GST cash ledger or reverse-charge payable account."]})
        ledger = reverse_charge.cash_ledger(account)
        ledger["account"] = account.pk
        return Response(ledger)
