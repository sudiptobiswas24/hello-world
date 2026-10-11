"""
The import tool from the office: the kinds there are, a blank file of
each, and a run of one file, checked first and kept on the second call.
Everything the command does, as the person signed in, so what is made is
stamped with who brought it in. One right covers it, held by the
controller: an import posts invoices, confirms orders and writes the
asset register, so it is not a clerk's.
"""

from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.api import flag
from apps.core.models import to_date
from apps.core.permissions import RequiredPermission

from .importer import KINDS, NEEDS, columns_of, run, template

WHAT = {
    "parties": "Customers and vendors, with GST details, credit limit and billing address",
    "employees": "Employees with department, manager, login and roles",
    "customer_reps": "Which rep each customer belongs to",
    "items": "Items with their unit, HSN and costing",
    "tape_specs": "Tape specifications; each builds its bill of materials",
    "fabric_specs": "Fabric specifications, on their tapes",
    "bag_specs": "Bag specifications, on their fabrics",
    "film_specs": "Film specifications",
    "liner_specs": "Liner specifications, on their films",
    "opening_stock": "Stock on hand: one posted adjustment a warehouse",
    "open_invoices": "Invoices still owed, one per old invoice",
    "open_bills": "Bills still owing, one per old bill",
    "opening_balances": "Every other balance, as one posted journal entry",
    "open_sales_orders": "Sales orders still to deliver, confirmed as they come in",
    "open_purchase_orders": "Purchase orders still to receive, confirmed as they come in",
    "fixed_assets": "The asset register, with what was already depreciated",
    "compensation": "Each employee's pay components",
    "price_lists": "Customer price lists and their prices",
    "vendor_prices": "What vendors charge for items",
    "shipment_history": "Months shipped from the old system, for the forecast",
}


class ImportViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "core.import_records"

    def list(self, request):
        """The kinds, in the order they are brought in, with their columns and what each needs beside its file."""
        return Response([{"kind": kind, "what": WHAT.get(kind, ""), "columns": columns_of(kind),
                          "needs": list(NEEDS.get(kind, ()))} for kind in KINDS])

    def _kind(self, request, source):
        kind = source.get("kind") or ""
        if kind not in KINDS:
            raise DRFValidationError({"kind": [f"{kind!r} is not one of {', '.join(KINDS)}."]})
        return kind

    @action(detail=False, methods=["get"])
    def template(self, request):
        """A blank file of ?kind=, its columns in the heading."""
        kind = self._kind(request, request.query_params)
        response = HttpResponse(template(kind), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{kind}.csv"'
        return response

    @action(detail=False, methods=["post"])
    def run(self, request):
        """
        One file ({"kind", "text"}), checked whole; kept only with
        commit=true and no problems. {"date", "against", "reason",
        "memo"} as the command's flags. Passwords an employees file made
        come back once, here, and nowhere else.
        """
        kind = self._kind(request, request.data)
        text = request.data.get("text") or ""
        if not text.strip():
            raise DRFValidationError({"text": ["Paste or upload the file, headings first."]})
        needs = NEEDS.get(kind, ())
        day = to_date(request.data.get("date") or None)
        if "date" in needs and day is None:
            raise DRFValidationError({"date": ["The day the opening position stands at."]})
        against = str(request.data.get("against") or "")
        if "against" in needs and not against:
            raise DRFValidationError({"against": ["The opening-balance account's code."]})
        commit = flag(request.data, "commit", False)
        report = run(kind, text, commit=commit, user=request.user, date=day, against=against,
                     reason=str(request.data.get("reason") or "OPENING"), memo=str(request.data.get("memo") or ""))
        return Response({
            "kind": kind, "rows": report.rows, "created": report.created, "committed": report.committed,
            "errors": [[row, column, message] for row, column, message in report.errors],
            "passwords": [[username, password] for username, password in report.passwords],
        })
