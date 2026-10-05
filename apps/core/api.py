"""
Turning a model's refusal into an answer the caller can read.

Every document in this codebase enforces its rules in `save()` — a
posted bill cannot be edited, a leaver cannot book next summer's
holiday, a movement must say which unit it counts in. Those raise
Django's ValidationError, which DRF does not know about, so an API
caller who broke a rule got a 500 and a stack trace instead of a 400
and the sentence the model wrote for them.

One handler rather than a try/except at every write, because the rules
live in save() and there is no list of the places that call it.
"""

import logging
import re
from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import (
    CheckConstraint,
    ProtectedError,
    Q,
    RestrictedError,
    UniqueConstraint,
)
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.models import to_date

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    """
    Map a model-level refusal onto the DRF error it always meant. A
    refusal about one field stays about that field, so a form can show it
    beside the box; the rest is a list of sentences.
    """
    if isinstance(exc, DjangoValidationError):
        if hasattr(exc, "error_dict"):
            exc = DRFValidationError({
                ("non_field_errors" if field == "__all__" else field): messages
                for field, messages in exc.message_dict.items()
            })
        else:
            exc = DRFValidationError(exc.messages if hasattr(exc, "messages") else [str(exc)])
    elif isinstance(exc, (ProtectedError, RestrictedError)):
        # Deleting what other records still point at: a customer with
        # invoices, a warehouse with stock movements.
        users = sorted({f"{type(row)._meta.verbose_name_plural}" for row in exc.protected_objects
                        } if isinstance(exc, ProtectedError) else {
                        f"{type(row)._meta.verbose_name_plural}" for row in exc.restricted_objects})
        exc = DRFValidationError({"non_field_errors": [
            f"Still used by {', '.join(users) or 'other records'}, so it cannot be deleted. "
            "Archive it instead, where it can be archived."]})
    elif isinstance(exc, IntegrityError):
        # A rule the database holds that nothing in Python asked first: a
        # serializer never runs check constraints. Refused in words, beside
        # the field where the rule is about one, rather than a 500. Logged,
        # because a posting of our own breaking one is a bug to find.
        logger.warning("The database refused a write: %s", exc)
        exc = DRFValidationError(refused_by_database(exc))
    return drf_exception_handler(exc, context)


COMPARISONS = {"gt": "more than", "gte": "at least", "lt": "less than", "lte": "at most"}


def _constraint(name):
    for model in django_apps.get_models():
        for constraint in model._meta.constraints:
            if constraint.name == name:
                return model, constraint
    return None, None


def _label(model, field_name):
    try:
        return str(model._meta.get_field(field_name).verbose_name)
    except FieldDoesNotExist:
        return field_name.replace("_", " ")


def refused_by_database(error):
    """{field: [sentence]} for a constraint the database enforced."""
    text = str(error)
    # PostgreSQL: Key (code)=(C-1) already exists. SQLite: UNIQUE
    # constraint failed: core_party.code.
    unique = re.search(r"Key \((\w+)(?:, [^)]*)?\)=", text) or re.search(
        r"UNIQUE constraint failed: \w+\.(\w+)", text)
    if unique:
        field = unique.group(1)
        return {field: [f"Another record already has this {field.replace('_', ' ')}."]}
    named = re.search(r'constraint(?: failed)?:? "?(\w+)"?', text)
    model, constraint = _constraint(named.group(1)) if named else (None, None)
    if isinstance(constraint, CheckConstraint):
        check = getattr(constraint, "condition", None) or getattr(constraint, "check", None)
        if isinstance(check, Q) and not check.negated and len(check.children) == 1 \
                and isinstance(check.children[0], tuple):
            path, value = check.children[0]
            field, _, lookup = path.partition("__")
            if lookup in COMPARISONS and isinstance(value, (int, Decimal)):
                label = _label(model, field)
                return {field: [f"{label[:1].upper()}{label[1:]} must be {COMPARISONS[lookup]} {value}."]}
    if isinstance(constraint, UniqueConstraint):
        return {"non_field_errors": ["Another record already has these details."]}
    rule = named.group(1).replace("_", " ") if named else "a rule of the database"
    return {"non_field_errors": [f"Refused: that breaks {rule}."]}


def quantities_by_line(requested, lines, document):
    """
    {"<line id>": "3"} from a request, as {line: Decimal}: part of a
    document to credit, debit or send back. None when nothing was asked,
    which means all of it.

    One reading for every correction that can be partial. Purchasing's
    debit note and goods return took none at all while the sales mirrors
    did, so a screen offering part sent back the whole.
    """
    from decimal import Decimal, InvalidOperation

    if not requested:
        return None
    if not isinstance(requested, dict):
        raise DRFValidationError({"quantities": ['Give them by line: {"<line id>": "3"}.']})
    by_id = {str(line.pk): line for line in lines}
    chosen = {}
    for line_id, quantity in requested.items():
        line = by_id.get(str(line_id))
        if line is None:
            raise DRFValidationError({"quantities": [f"Line {line_id} is not on this {document}."]})
        try:
            value = Decimal(str(quantity).strip())
        except (InvalidOperation, ValueError):
            value = None
        if value is None or not value.is_finite():
            raise DRFValidationError({"quantities": [f"{quantity!r} is not a quantity."]})
        chosen[line] = value
    return chosen


TRUE = {"true", "1", "yes", "on"}
FALSE = {"false", "0", "no", "off"}


def flag(data, name, default):
    """
    A yes-or-no from a request, however it was said.

    bool("false") is True: a replacement posted from a form as
    credit_invoices=false credited the invoice anyway. A JSON boolean, or
    a word a form would send, or the default when absent; anything else is
    refused rather than guessed at.
    """
    value = data.get(name, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE:
        return True
    if text in FALSE:
        return False
    raise DRFValidationError(f"{name} must be true or false, not {value!r}.")


class HeaderPagination(PageNumberPagination):
    """
    Every list in pages, the body still a plain list.

    Nothing was paginated: with a year of a plant's data, the invoice,
    order and bill lists did not answer inside a minute and the journal
    entries came back as ten megabytes. Pages of 50 by default,
    `?page_size=` up to 500, `?page=` to move.

    The count and the neighbouring pages go in headers rather than an
    envelope, so a caller that read the list as a list still does:
    X-Total-Count, X-Page, X-Page-Size and a Link header with rel next
    and prev.
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500

    def paginate_queryset(self, queryset, request, view=None):
        # Pages of an unordered list can repeat or skip rows from one page
        # to the next; the key gives every list an order to page through.
        if hasattr(queryset, "ordered") and not queryset.ordered:
            queryset = queryset.order_by("pk")
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        links = []
        if self.get_next_link():
            links.append(f'<{self.get_next_link()}>; rel="next"')
        if self.get_previous_link():
            links.append(f'<{self.get_previous_link()}>; rel="prev"')
        headers = {
            "X-Total-Count": str(self.page.paginator.count),
            "X-Page": str(self.page.number),
            "X-Page-Size": str(self.get_page_size(self.request)),
        }
        if links:
            headers["Link"] = ", ".join(links)
        return Response(data, headers=headers)

    def get_paginated_response_schema(self, schema):
        return schema


def _field_at(model, path):
    """The field a lookup path like `entry__posted` ends on."""
    *hops, last = path.split("__")
    for hop in hops:
        model = model._meta.get_field(hop).related_model
    return model._meta.get_field(last)


class FieldFilter(BaseFilterBackend):
    """
    `?customer=12&status=confirmed&from=2026-04-01&to=2026-04-30` on a
    list, for the fields its view names and no others.

    A view lists `filter_fields` (matched exactly; a yes-or-no field read
    with `flag`; a name ending `__isnull` asks whether there is one) and a
    `date_field` that `from` and `to` bound, both ends included. A value the field cannot hold is a 400 naming it, not a
    500 from the database.
    """

    def filter_queryset(self, request, queryset, view):
        params = request.query_params
        try:
            for name in getattr(view, "filter_fields", ()):
                if name not in params:
                    continue
                value = params[name]
                if name.endswith("__isnull"):
                    # ?credits__isnull=false: those that have one.
                    _field_at(queryset.model, name.removesuffix("__isnull"))
                    queryset = queryset.filter(**{name: flag(params, name, None)})
                    continue
                field = _field_at(queryset.model, name)
                if field.get_internal_type() == "BooleanField":
                    value = flag(params, name, None)
                elif field.is_relation and value == "":
                    name, value = f"{name}__isnull", True
                queryset = queryset.filter(**{name: value})
            date_field = getattr(view, "date_field", None)
            if date_field:
                # A moment is bounded by the plant's day it fell on.
                if _field_at(queryset.model, date_field).get_internal_type() == "DateTimeField":
                    date_field = f"{date_field}__date"
                for param, lookup in (("from", "gte"), ("to", "lte")):
                    if params.get(param):
                        day = to_date(params[param])
                        queryset = queryset.filter(**{f"{date_field}__{lookup}": day})
        except (ValueError, TypeError, DjangoValidationError) as error:
            said = " ".join(getattr(error, "messages", None) or [str(error)])
            raise DRFValidationError(f"Cannot filter by that: {said}")
        return queryset


class Ordering(OrderingFilter):
    """
    `?ordering=-invoice_date,number`, over the fields the view names in
    `ordering_fields` only: DRF's default offers every serializer field,
    and sorting a year of journal lines by a column with no index is a
    way to make the list slow on purpose. The key breaks ties, so a page
    boundary falls in the same place every time it is asked.
    """

    def get_default_valid_fields(self, queryset, view, context=None):
        return []

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if ordering and ordering != self.get_default_ordering(view):
            return queryset.order_by(*ordering, "-pk")
        return queryset


class Search(SearchFilter):
    """
    `?search=acme 1153` on a list: every word must appear in one of the
    fields the view names in `search_fields`, in any case.

    DRF's own search ORs every field, across joined tables, in one
    condition that no index can serve, and PostgreSQL then walks the list
    newest first testing each row in the hope of filling a page: a search
    that matched nothing read all 98,765 journal entries of five years
    (147 ms) and all 25,000 invoices (34 ms). Here each field is asked on
    its own, from its own index (the trigram indexes on numbers and
    references, the key of a related party), and the list is narrowed to
    what they found.

    A word so common that more than COMMON rows carry it is left to that
    newest-first walk after all, which fills a page from it at once.
    """

    COMMON = 2000

    def filter_queryset(self, request, queryset, view):
        fields = getattr(view, "search_fields", None)
        terms = self.get_search_terms(request)
        if not fields or not terms:
            return queryset
        model = queryset.model
        for term in terms:
            found = set()
            for path in fields:
                found.update(model._default_manager.filter(
                    self._matches(model, path, term)
                ).order_by().values_list("pk", flat=True)[:self.COMMON + 1])
                if len(found) > self.COMMON:
                    break
            if len(found) > self.COMMON:
                matches = Q()
                for path in fields:
                    matches |= self._matches(model, path, term)
                queryset = queryset.filter(matches)
            else:
                queryset = queryset.filter(pk__in=found)
        return queryset

    def _matches(self, model, path, term):
        head, _, rest = path.partition("__")
        if not rest:
            return Q(**{f"{head}__icontains": term})
        related = model._meta.get_field(head).related_model
        return Q(**{f"{head}__in": related._default_manager.filter(
            self._matches(related, rest, term)).values("pk")})

