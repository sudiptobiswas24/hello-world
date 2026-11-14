"""review_books3 probes: O349 standard-costed subcontract return nets to nothing in every shape."""
from decimal import Decimal as D
from .tests_review_stores2_sub import Awkward


class O349(Awkward):
    def test_zz_sweep(self):
        import io, contextlib
        bad = []
        cases = [(True, "standard", "3.3333", ["3", "3", "1"], None),
                 (True, "standard", "30.3333", ["2", "5"], None),
                 (True, "standard", "0.01", ["7"], None),
                 (True, "standard", "3.3333", ["7"], None),
                 (True, "standard", "5.55555", ["1", "1", "1", "1", "1", "1", "1"], None),
                 (True, "standard", "3.3333", {D("3"): ["1", "2"], D("4"): ["1", "3"]}, "3"),
                 (False, "standard", "3.3333", ["3", "3", "1"], None),
                 (False, "standard", "30.3333", ["2", "5"], None),
                 (True, "average", None, ["3", "3", "1"], None),
                 (True, "fifo", None, ["3", "3", "1"], None),
                 (True, "fifo", None, ["1"] * 7, None)]
        for legacy, method, std, parts, partial in cases:
            # each case in its own savepoint so fixtures are not reused: a fresh transaction per case
            from django.db import transaction
            sid = transaction.savepoint()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    delta = self.run_case(legacy, method, std, parts, partial_receipt=partial)
            except Exception as e:
                delta = ("EXC", repr(e)[:120])
            transaction.savepoint_rollback(sid)
            ok = all(x == 0 for x in delta) if delta[0] != "EXC" else False
            print(("PASS " if ok else "FAIL "), "legacy" if legacy else "recorded", method, std, parts, "delta(shelf,books,variance)=", delta)
            if not ok: bad.append(delta)
        self.assertFalse(bad)
