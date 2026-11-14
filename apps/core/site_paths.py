"""
The one check for a same-site path a record keeps and a screen later renders as a link (O317): a follow-up's
link, a saved view's screen. Every such stored path asks `same_site_path`; the office application asks the same of
any it renders (frontend/src/lib/sitePath.ts), so a row stored before a rule is harmless too.
"""

from urllib.parse import unquote, urlsplit

SINGLE_DOT = {".", "%2e"}
DOUBLE_DOT = {"..", ".%2e", "%2e.", "%2e%2e"}


def resolved(path):
    """
    `path` with its dot segments resolved the way a browser's URL parser does it (WHATWG URL, path state): `.` and
    `%2e` drop out, `..` and its encoded spellings take the segment before them, and an empty segment stays. So
    `/.//host/x` is `//host/x` and `/x/..//host` is `//host`, which a browser follows to another site.
    """
    segments = path.split("/")[1:]
    kept = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        lowered = segment.lower()
        if lowered in DOUBLE_DOT:
            if kept:
                kept.pop()
            if last:
                kept.append("")
        elif lowered in SINGLE_DOT:
            if last:
                kept.append("")
        else:
            kept.append(segment)
    return "/" + "/".join(kept)


def same_site_path(text):
    """
    Whether `text` is a path on this site, and nothing a browser could read as another host. Each form of it is
    asked, as given and decoded up to three times over (`/%252f` is `/%2f` is `//`): it starts with one `/`; it holds
    no backslash and no control character, which a browser reads as a slash or drops (`/\\host` and `/<tab>/host` are
    both `//host`); parsed, it has no scheme and no host; and its path, its dot segments resolved as a browser
    resolves them, still starts with one `/` (`/.//host`, `/x/..//host`, `/%2e//host` are all `//host`).
    """
    if not isinstance(text, str):
        return False
    seen, forms = text, [text]
    for _ in range(3):
        seen = unquote(seen)
        forms.append(seen)
    for form in forms:
        if not form.startswith("/") or form.startswith("//"):
            return False
        if "\\" in form or any(ord(char) < 32 or ord(char) == 127 for char in form):
            return False
        parts = urlsplit(form)
        if parts.scheme or parts.netloc or resolved(parts.path).startswith("//"):
            return False
    return True
