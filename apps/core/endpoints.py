"""
Which kind of record each office address serves, read off the routers
themselves, and whether a login may read one record of a kind.

Reading a kind takes its view permission; reading one record of it takes
also that the record is among those its own screen shows the login: a
rep sees only their own customers' orders, and so only their history,
files, notes and follow-ups. Each module says who sees what in its
viewsets' get_queryset (apps/sales/scoping.py); this asks those same
querysets rather than keeping a second copy of the rule that would drift.
Core imports no module to do it: it walks the URL resolver.
"""

import re
from functools import lru_cache

from django.apps import apps
from django.http import HttpRequest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

DETAIL = re.compile(r"^\^?(?P<prefix>.*?)\(\?P<\w+>[^)]*\)/\$$")


@lru_cache(maxsize=1)
def served():
    """({"/api/sales/sales-orders/": "sales.salesorder"}, {"sales.salesorder": [viewset classes]})."""
    by_endpoint, by_model = {}, {}

    def walk(patterns, prefix):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                cls = getattr(pattern.callback, "cls", None)
                actions = getattr(pattern.callback, "actions", None) or {}
                queryset = getattr(cls, "queryset", None)
                found = DETAIL.match(str(pattern.pattern))
                if cls is None or queryset is None or actions.get("get") != "retrieve" or not found:
                    continue
                label = queryset.model._meta.label_lower
                by_endpoint[f"/{prefix}{found['prefix']}"] = label
                if cls not in by_model.setdefault(label, []):
                    by_model[label].append(cls)

    walk(get_resolver().url_patterns, "")
    return by_endpoint, by_model


def model_named(label):
    """The model an app.model label names, or None."""
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def may_read_kind(user, model):
    return user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}")


def may_read(user, model, pk):
    """
    Whether `user` may read record `pk` of `model`: the kind's view
    permission, and the record among those a screen serving the kind shows
    them. A kind no screen serves is limited by its permission alone.
    """
    if not may_read_kind(user, model):
        return False
    classes = served()[1].get(model._meta.label_lower, [])
    if not classes:
        return model._default_manager.filter(pk=pk).exists()
    asked = HttpRequest()
    asked.method = "GET"
    asked.user = user
    request = Request(asked)
    request.user = user
    for cls in classes:
        view = cls(request=request, args=(), kwargs={}, format_kwarg=None, action="retrieve")
        view.headers = {}
        if view.get_queryset().filter(pk=pk).exists():
            return True
    return False


class EndpointsView(APIView):
    """
    {"/api/sales/sales-orders/": "sales.salesorder", ...}: what a record
    screen knows of itself is its address; this names its kind, for its
    notes, follow-ups, files and history.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(served()[0])
