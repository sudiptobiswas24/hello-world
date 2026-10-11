"""Things held for the length of one request and no longer."""

from .models import _COMPANY


class RequestMemo:
    """Gives Company.get() somewhere to keep its answer until the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _COMPANY.set({})
        try:
            return self.get_response(request)
        finally:
            _COMPANY.reset(token)
