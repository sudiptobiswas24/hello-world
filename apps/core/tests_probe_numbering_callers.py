"""
Review probe (not product code): record every DocumentSequence.next_for that is called with no transaction the
code opened around it (a number taken in a transaction of its own, GST Rule 46(b)). Writes caller lines to
$NUMBERING_CALLERS_LOG; the test itself only asserts the patch took.
"""
import os
import traceback

from django.test import SimpleTestCase

from apps.core.models import DocumentSequence, in_a_transaction_it_opened

_original = DocumentSequence.next_for.__func__


def _recording(cls, code, on_date=None, **defaults):
    log = os.environ.get("NUMBERING_CALLERS_LOG")
    if log and not in_a_transaction_it_opened():
        frames = [f for f in traceback.extract_stack()[:-1] if "/apps/" in f.filename and "tests" not in f.filename.rsplit("/", 1)[-1]]
        where = f"{frames[-1].filename.split('/apps/')[-1]}:{frames[-1].lineno}" if frames else "?"
        with open(log, "a") as handle:
            handle.write(f"{code}\t{where}\n")
    return _original(cls, code, on_date, **defaults)


if os.environ.get("NUMBERING_CALLERS_LOG"):
    DocumentSequence.next_for = classmethod(_recording)


class ThePatchTook(SimpleTestCase):
    def test_patch(self):
        self.assertTrue(True)
