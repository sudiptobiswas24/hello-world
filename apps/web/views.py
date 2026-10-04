"""
The office application: one page that the browser builds the screens on.

The page and everything it asks for come from this server, so the
session cookie that signed the person in is the one the API reads, and
there is nothing to configure across origins. The page itself holds no
data: every figure arrives through the API, which checks the person's
permissions on each request whatever the page chose to show them.
"""

from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie

# What the page may load, run and talk to: only this server. Styles set
# by the scripts go through the DOM, which a policy does not restrict;
# style attributes in markup would need 'unsafe-inline', and none are used.
POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

NOT_BUILT = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Not built</title><body style="font-family:system-ui;padding:40px">
<h1>The office application has not been built</h1>
<p>Run <code>npm ci &amp;&amp; npm run build</code> in <code>frontend/</code>,
or rebuild the server image, which does it.</p></body></html>"""


@ensure_csrf_cookie
@login_required
def shell(request, path=""):
    """
    The application's page, for any address under /app/: the browser
    decides which screen the rest of the address names.

    Never cached, so an update reaches everyone on their next load; the
    scripts it names carry a hash of their content and are cached for good.
    """
    found = finders.find("web/index.html")
    if not found:
        response = HttpResponse(NOT_BUILT, status=503)
    else:
        with open(found, "rb") as page:
            response = HttpResponse(page.read(), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Content-Security-Policy"] = POLICY
    response["Referrer-Policy"] = "same-origin"
    return response
