"""
g22 review of fix_stores: probes for O225, the scale bridge (deploy/scale_bridge), driven by recorded frames.
Each states a fact about what reaches the server: a weight posted once, none lost, none posted for what it is not.
"""
import io
import logging
import urllib.error
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from apps.manufacturing.tests_scale_bridge import Port, bridge_module

D = Decimal


class Feed:
    """Frames fed to a Bridge one by one; what `send` was asked to post."""

    def __init__(self, config=None, fail=None):
        self.module = bridge_module()
        self.sent = []
        self.fail = fail or (lambda payload, attempt: None)
        self.attempts = 0
        self.now = 0.0
        base = {"scale": "SC-1", "server": "https://erp.example", "pattern": "status_gross", "repeat": 3,
                "retry_seconds": 5}
        base.update(config or {})
        self.bridge = self.module.Bridge(base, open_port=None, send=self.send, sleep=lambda s: None,
                                         clock=lambda: self.now)

    def send(self, payload):
        self.attempts += 1
        # The server answers a key it has kept with the first answer, and keeps no second reading.
        if payload.get("key") and any(kept.get("key") == payload["key"] for kept in self.sent):
            return
        error = self.fail(payload, self.attempts)
        if error == "after":                     # the server took it, the answer was lost
            self.sent.append(payload)
            raise TimeoutError("answer lost")
        if error:
            raise error
        self.sent.append(payload)

    def frames(self, *lines, tick=0.2):
        for line in lines:
            self.now += tick
            self.bridge.handle(line)
        return [p["gross_kg"] for p in self.sent if "gross_kg" in p]


ST = lambda w, kind="GS", sign="+": f"ST,{kind},{sign}{w:>08}kg"   # noqa: E731
US = lambda w: f"US,GS,+{w:>08}kg"   # noqa: E731


class StoresReviewScaleBridgeProbes(SimpleTestCase):
    def test_s1_an_unstable_frame_is_never_posted_and_restarts_the_count(self):
        feed = Feed()
        posted = feed.frames(*[US("0106.8")] * 6, ST("0106.8"), ST("0106.8"), US("0106.8"), ST("0106.8"), ST("0106.8"))
        self.assertEqual(posted, [])
        self.assertEqual(feed.frames(ST("0106.8")), ["106.8"])

    def test_s2_a_weight_held_is_one_reading_and_the_next_roll_of_the_same_weight_is_another(self):
        feed = Feed()
        feed.frames(*[ST("0106.8")] * 12)
        self.assertEqual([p["gross_kg"] for p in feed.sent], ["106.8"])
        feed.frames(*[ST("0000.0")] * 3, *[ST("0106.8")] * 4)
        self.assertEqual([p["gross_kg"] for p in feed.sent], ["106.8", "0", "106.8"])

    def test_s3_a_dropped_port_is_opened_again_and_nothing_across_the_gap_counts(self):
        module = bridge_module()
        ports = [Port(b"ST,GS,+0106.8kg\r\nST,GS,+0106.8kg\r\n", then="unplugged"),
                 Port(b"ST,GS,+0106.8kg\r\nST,GS,+0106.8kg\r\nST,GS,+0106.8kg\r\n", then="closed")]
        sent, slept = [], []
        opened = iter(ports)
        bridge = module.Bridge({"scale": "SC-1", "server": "https://x", "repeat": 3, "retry_seconds": 5},
                               open_port=lambda config: next(opened), send=sent.append, sleep=slept.append)
        bridge.run(stop=lambda: len(sent) > 0 or not slept and False)
        self.assertEqual(slept, [5])
        self.assertEqual([p["gross_kg"] for p in sent], ["106.8"], "2 frames before the drop + 3 after must not make 3")

    def test_s4_a_malformed_frame_breaks_the_run_of_equal_weights(self):
        feed = Feed()
        posted = feed.frames(ST("0106.8"), ST("0106.8"), "ST,GS,+01@6.8kg", ST("0106.8"))
        self.assertEqual(posted, [])

    def test_s5_a_weight_in_grams_is_posted_in_kilogrammes_and_another_unit_is_not_posted_at_all(self):
        feed = Feed()
        self.assertEqual(feed.frames(*["ST,GS,+00106800g"] * 3), ["106.8"])
        self.assertEqual(feed.frames(*["ST,GS,+00235.0lb"] * 5, *["ST,GS,+00235.0KG"] * 5), ["106.8"])

    def test_a_net_frame_is_posted_as_net_never_as_gross(self):
        # The roll's gross is what the station subtracts the core from. An indicator tared on the core
        # sends NT frames; posted as gross_kg, the core comes off twice.
        feed = Feed()
        posted = feed.frames(*[ST("0100.0", kind="NT")] * 4)
        self.assertEqual(posted, [], "an NT (net) frame was posted as the gross weight")
        # Review F7, the integrator's decision: posted as what it is, net, which the station does not tare again.
        self.assertEqual([p.get("net_kg") for p in feed.sent], ["100"])

    def test_a_reading_whose_answer_was_lost_is_one_reading(self):
        # The server took the reading (201) and the answer never came back: the retry makes a second
        # reading of the same roll, fresh and unused, so the station can weigh one roll twice.
        feed = Feed(fail=lambda payload, attempt: "after" if attempt == 1 else None)
        feed.frames(*[ST("0106.8")] * 3)
        feed.frames(*[ST("0106.8")] * 40, tick=0.2)     # 8 seconds on: the retry window opens
        self.assertEqual([p["gross_kg"] for p in feed.sent], ["106.8"], "the same roll posted twice")

    def test_s8_a_refused_post_is_retried_while_it_stands_and_dropped_when_the_scale_moves(self):
        refused = urllib.error.HTTPError("https://x", 503, "busy", {}, io.BytesIO())
        feed = Feed(fail=lambda payload, attempt: refused if attempt < 3 else None)
        feed.frames(*[ST("0106.8")] * 3)
        self.assertEqual(feed.attempts, 1)
        feed.frames(*[ST("0106.8")] * 30, tick=0.2)     # 6 s: one more try (fails), 
        feed.frames(*[ST("0106.8")] * 30, tick=0.2)
        self.assertEqual([p["gross_kg"] for p in feed.sent], ["106.8"])
        # a refused weight is dropped once the scale reads something else, and is not posted late
        feed2 = Feed(fail=lambda payload, attempt: refused)
        feed2.frames(*[ST("0106.8")] * 3)
        feed2.fail = lambda payload, attempt: None
        feed2.frames(*[ST("0000.0")] * 3)
        self.assertNotIn("106.8", [p["gross_kg"] for p in feed2.sent])

    def test_the_key_is_in_no_log_line_and_never_crosses_the_network_in_clear(self):
        """The bridge signs in with an API key now (L12): the same two facts, for the key."""
        module = bridge_module()
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".key", delete=False) as kept:
            kept.write("erp_deadbeef_s3cretKeyMaterial\n")
        os.chmod(kept.name, 0o600)
        try:
            config = {"server": "https://erp.example", "api_key_file": kept.name, "post_timeout_seconds": 1,
                      "scale": "SC-1"}
            send = module.poster(config)
            logs = io.StringIO()
            handler = logging.StreamHandler(logs)
            logging.getLogger("scale_bridge").addHandler(handler)
            logging.getLogger("scale_bridge").setLevel(logging.DEBUG)
            bridge = module.Bridge({**config, "pattern": "status_gross", "repeat": 1, "retry_seconds": 0},
                                   open_port=None, send=send, sleep=lambda s: None)
            with mock.patch("urllib.request.urlopen",
                            side_effect=urllib.error.URLError("Authorization: Api-Key failed")):
                bridge.handle("ST,GS,+0106.8kg")
            logging.getLogger("scale_bridge").removeHandler(handler)
            self.assertNotIn("s3cretKeyMaterial", logs.getvalue())
            with self.assertRaises(Exception, msg="a key sent to an http:// server (in clear)"):
                module.poster({**config, "server": "http://erp.example"})
        finally:
            os.unlink(kept.name)
