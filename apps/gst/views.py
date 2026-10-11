import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
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


class Gstr2bStatementViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin,
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


class EInvoiceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
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
    action_permission_map = {"record": "gst.change_einvoice"}

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


class EwayBillViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
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
