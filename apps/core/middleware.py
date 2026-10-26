"""Things held for the length of one request and no longer."""

from .history import acting_as, through_for
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


class Acting:
    """
    Who is acting for the edit log (apps/core/history.py) and the database's own log (apps/core/ledger_log.py):
    the request's signed-in person, read when a change is written (an API key or a session signs them in
    after this runs), and what it came through, from its path: the admin, the API, the pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with acting_as(through=through_for(request.path), request=request):
            return self.get_response(request)
