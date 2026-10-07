"""
What went wrong on the server, kept where somebody can see it.

An unhandled exception used to be a line in the container's log and a
blank "something went wrong" to the person, who could not say which one
when they asked for help. Now each is a row with a short reference the
person is shown, the traceback the keeper needs, and room to say what
was done about it; whoever DJANGO_ADMINS names is mailed as well.

The middleware is the one place a request's exception is caught: Django
hands it a view's exception after the view's own transaction has rolled
back, so the row it writes survives, and a DRF view re-raises what its
own handler does not answer (a refusal, a missing record) so those never
reach here.
"""

import logging
import secrets
import sys
import traceback

from django.conf import settings
from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.db import models
from django.http import Http404, HttpResponseServerError, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.utils import timezone

logger = logging.getLogger(__name__)

# No 0, O, 1, I: read over the phone, these are not confused.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# Django answers these itself (a 404, a 403, a 400): not errors of ours.
ANSWERED_BY_DJANGO = (Http404, PermissionDenied, BadRequest, SuspiciousOperation, MultiPartParserError)
TOLD = ("Something went wrong on the server. It has been logged as {ref}; tell whoever keeps the "
        "system that reference.")


class ServerError(models.Model):
    """One unhandled exception, as the person saw it and as the keeper needs it."""

    ref = models.CharField(max_length=12, unique=True, editable=False)
    happened_at = models.DateTimeField(default=timezone.now, editable=False)
    path = models.CharField(max_length=255, blank=True, editable=False)
    method = models.CharField(max_length=8, blank=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+", editable=False)
    kind = models.CharField(max_length=128, editable=False, help_text="The exception's class.")
    message = models.TextField(blank=True, editable=False)
    traceback = models.TextField(blank=True, editable=False)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    note = models.CharField(max_length=255, blank=True, help_text="What was done about it.")

    class Meta:
        ordering = ["-happened_at", "-id"]
        verbose_name = "server error"

    def __str__(self):
        return f"{self.ref} {self.kind} at {self.path}"

    def resolve(self, by=None, note=""):
        self.resolved_at = timezone.now()
        self.resolved_by = by
        self.note = note
        self.save(update_fields=["resolved_at", "resolved_by", "note"])

    def reopen(self):
        self.resolved_at = None
        self.resolved_by = None
        self.save(update_fields=["resolved_at", "resolved_by"])


def new_ref():
    return "E-" + "".join(secrets.choice(ALPHABET) for _ in range(5))


def record(request, exc_info=None, log=True):
    """
    Keep the exception being handled as a ServerError and, with `log`,
    log it the way Django logs a 500 (so whoever DJANGO_ADMINS names is
    mailed, traceback and reference in the mail). Returns the row; its
    `ref` is empty when even recording it failed, which the log then
    says.
    """
    exc_info = exc_info or sys.exc_info()
    exc = exc_info[1]
    user = getattr(request, "user", None)
    row = ServerError(
        path=request.path[:255], method=request.method[:8],
        user=user if user is not None and user.is_authenticated else None,
        kind=type(exc).__name__, message=str(exc)[:2000],
        traceback="".join(traceback.format_exception(*exc_info))[-20000:],
    )
    for _ in range(5):
        row.ref = new_ref()
        try:
            row.save()
            break
        except Exception:  # noqa: BLE001 - the database may be what is broken
            if ServerError.objects.filter(ref=row.ref).exists():
                continue  # the one-in-33-million collision: another reference
            row.ref = ""
            logger.exception("Could not record a server error (%s %s)", request.method, request.path)
            break
    if log:
        logging.getLogger("django.request").error(
            "Internal Server Error: %s (%s)", request.path, row.ref or "not recorded",
            exc_info=exc_info, extra={"status_code": 500, "request": request},
        )
    return row


class ErrorCapture:
    """Records a view's unhandled exception and answers with its reference."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, ANSWERED_BY_DJANGO):
            return None
        # In DEBUG the exception goes on to Django, which logs it itself
        # and shows the developer's page; the row is still written.
        row = record(request, (type(exception), exception, exception.__traceback__), log=not settings.DEBUG)
        if settings.DEBUG:
            return None
        said = TOLD.format(ref=row.ref or "nothing, because recording it failed too")
        if request.path.startswith("/api/"):
            response = JsonResponse({"detail": said, "error_id": row.ref}, status=500)
        else:
            response = HttpResponseServerError(
                f"<!doctype html><title>Something went wrong</title><h1>Something went wrong</h1><p>{said}</p>",
                content_type="text/html",
            )
        # Django logs every 500 it hands back, without the traceback; this
        # one was logged with it above (the flag Django's own handler sets).
        response._has_been_logged = True
        return response
