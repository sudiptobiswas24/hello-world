from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
