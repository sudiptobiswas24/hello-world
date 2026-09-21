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

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.exceptions import ValidationError as DRFValidationError


def exception_handler(exc, context):
    """Map a model-level refusal onto the DRF error it always meant."""
    if isinstance(exc, DjangoValidationError):
        exc = DRFValidationError(
            exc.messages if hasattr(exc, "messages") else [str(exc)]
        )
    return drf_exception_handler(exc, context)
