"""
Any list, grouped: ?endpoint=/api/sales/invoices/&by=customer with the
list's own narrowing (posted=true, from, to, search) gives each group's
count and the sum of every figure the list shows. One endpoint over
every list rather than a report a screen: the list's view does the
reading — its permission, its scoping (a rep's own customers), its
filters, its serializer — and this groups the rows it would have paged.

The figures are the list's own, added up in Python: an invoice's total
is derived from its lines, not a column, so the database cannot sum it
and the screen's number is the one to add. A list too long to add up
at once says so and asks to be narrowed.

What may be grouped by is what the list narrows by (and its date, by
month); what is summed is every exact figure a row carries.
"""

import copy
from collections import OrderedDict
from decimal import Decimal

from django.http import QueryDict
from django.urls import Resolver404, resolve
from rest_framework.exceptions import ValidationError

from apps.core.api import _field_at

OWN = frozenset({"endpoint", "by"})
PAGING = frozenset({"page", "page_size", "ordering", "format"})
MONTH = ":month"
AT_MOST = 5000
ZERO = Decimal("0")


def list_view(request, endpoint, params):
    """The list's own view, set up as if the request had been to it, and what it would have listed."""
    try:
        match = resolve(endpoint)
    except Resolver404:
        raise ValidationError({"endpoint": [f"{endpoint!r} is not a list."]})
    cls, actions = getattr(match.func, "cls", None), getattr(match.func, "actions", None) or {}
    if cls is None or actions.get("get") != "list" or not hasattr(cls, "get_queryset"):
        raise ValidationError({"endpoint": [f"{endpoint!r} is not a list."]})
    view = cls(**getattr(match.func, "initkwargs", {}))
    view.action_map, view.action, view.format_kwarg = actions, "list", None
    view.args, view.kwargs = (), {}
    asked = copy.copy(request._request)
    asked.GET = QueryDict(mutable=True)
    for name, value in params.items():
        if name not in OWN and name not in PAGING:
            asked.GET[name] = value
    view.request = view.initialize_request(asked)
    view.headers = view.default_response_headers
    view.check_permissions(view.request)
    return view, view.filter_queryset(view.get_queryset())


def groupings(view, model):
    """[{key, label}]: what this list can be grouped by — what it narrows by, and its date by month."""
    by = []
    for name in getattr(view, "filter_fields", ()):
        if name.endswith("__isnull"):
            continue
        by.append({"key": name, "label": str(_field_at(model, name).verbose_name).capitalize()})
    date_field = getattr(view, "date_field", None)
    if date_field:
        by.append({"key": f"{date_field}{MONTH}",
                   "label": f"{str(_field_at(model, date_field).verbose_name).capitalize()}, by month"})
    return by


def _figure(value):
    """The exact figure a row's cell holds, or None where it is not one (text, a flag, an id)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str) and value and value.replace(".", "", 1).replace("-", "", 1).isdigit() and "." in value:
        return Decimal(value)
    return None


def _label(model, by, row):
    """What a row's group is called: the related record's name the row carries, the month, the choice's name."""
    if by.endswith(MONTH):
        day = str(row.get(by[:-len(MONTH)]) or "")
        return f"{day[:7]}" if len(day) >= 7 else "—"
    value = row.get(by)
    if value in (None, ""):
        return "—"
    if f"{by}_name" in row and row[f"{by}_name"]:
        return str(row[f"{by}_name"])
    field = _field_at(model, by)
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if field.get_internal_type() == "BooleanField":
        return "Yes" if value else "No"
    return str(value)


def summarise(view, queryset, by):
    """Each group's count and the sum of every figure its rows carry, labelled, largest count first."""
    model = queryset.model
    allowed = {entry["key"] for entry in groupings(view, model)}
    if by not in allowed:
        raise ValidationError({"by": [f"This list is grouped by {', '.join(sorted(allowed)) or 'nothing'}, not {by!r}."]})
    rows = queryset.count()
    if rows > AT_MOST:
        raise ValidationError({"by": [f"{rows:,} rows is more than this adds up at once ({AT_MOST:,}); narrow the list first."]})
    data = view.get_serializer(queryset, many=True).data
    key = by[:-len(MONTH)] if by.endswith(MONTH) else by
    groups, figures = OrderedDict(), OrderedDict()
    for row in data:
        value = str(row.get(key) or "")[:7] if by.endswith(MONTH) else row.get(key)
        group = groups.setdefault(value, {"key": "" if value is None else str(value), "label": _label(model, by, row),
                                          "count": 0, "sums": {}})
        group["count"] += 1
        for name, cell in row.items():
            figure = _figure(cell)
            if figure is None or name in ("id", key) or name.endswith("_id"):
                continue
            figures.setdefault(name, figure.as_tuple().exponent)
            group["sums"][name] = group["sums"].get(name, ZERO) + figure
    sums = [{"key": name, "label": name.replace("_", " ").capitalize(), "kind": "money" if exponent == -2 else "quantity"}
            for name, exponent in figures.items()]
    ordered = sorted(groups.values(), key=lambda group: (-group["count"], group["label"]))
    for group in ordered:
        for name in figures:
            group["sums"].setdefault(name, ZERO)
    # Related records the rows do not name (no <field>_name): one query for their names.
    if not by.endswith(MONTH):
        field = _field_at(model, by)
        if field.is_relation and all(group["label"] == group["key"] for group in ordered if group["key"]):
            named = {str(row.pk): str(row) for row in field.related_model._default_manager.filter(
                pk__in=[group["key"] for group in ordered if group["key"]])}
            for group in ordered:
                group["label"] = named.get(group["key"], group["label"])
    return {"by": groupings(view, model), "grouped_by": by, "sums": sums, "rows": ordered}
