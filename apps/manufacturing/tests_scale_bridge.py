"""
The scale bridge (deploy/scale_bridge, O225), driven by recorded frames,
not a scale.

status_gross.txt is a roll put on (moving, then 106.8 kg settled five
frames), leaned on and settled again, taken off (0.0 settled), and the
next roll put on, which weighs 106.8 too. Sent: 106.8, 0, 106.8; the same
roll leaned on is not sent again, and no moving frame is ever sent.
plain_weight.txt is an indicator with no status word: 104.35 four times
is settled, a flicker to 104.40 and back is the same roll.
"""

import base64
import importlib.util
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

HERE = Path(settings.BASE_DIR) / "deploy" / "scale_bridge"


def bridge_module():
    spec = importlib.util.spec_from_file_location("scale_bridge", HERE / "scale_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Finished(Exception):
    pass


class Port:
    """A recorded serial port: its bytes a few at a time, then unplugged (OSError) or closed (None)."""

    def __init__(self, data, then="unplugged"):
        self.data, self.then, self.closed = data, then, False

    def read(self, size):
        if self.data:
            chunk, self.data = self.data[:7], self.data[7:]
            return chunk
        if self.then == "unplugged":
            raise OSError("device reports readiness to read but returned no data")
        return None

    def close(self):
        self.closed = True


def recorded(name):
    return (HERE / "frames" / name).read_bytes()


class BridgeCase(SimpleTestCase):
    def setUp(self):
        self.module = bridge_module()
        self.sent, self.slept = [], []

    def bridge(self, ports, pattern="status_gross", send=None, clock=None, **config):
        def open_port(_config):
            if not ports:
                raise Finished
            port = ports.pop(0)
            if isinstance(port, Exception):
                raise port
            return port

        return self.module.Bridge({"scale": "SC-1", "server": "https://erp.example", "pattern": pattern,
                                   "repeat": 3, **config},
                                  open_port, send or self.sent.append, sleep=self.slept.append,
                                  **({"clock": clock} if clock else {}))

    def weights(self):
        return [row["gross_kg"] for row in self.sent]

    def ran(self, bridge):
        with self.assertRaises(Finished):
            bridge.run()


class RecordedFramesTests(BridgeCase):
    def test_each_settled_weight_once(self):
        self.ran(self.bridge([Port(recorded("status_gross.txt"))]))
        self.assertEqual(self.weights(), ["106.8", "0", "106.8"])
        self.assertEqual(self.sent[0], {"scale": "SC-1", "gross_kg": "106.8", "stable": True})

    def test_an_unstable_frame_is_never_sent(self):
        moving = b"".join(f"US,GS,+00{w}kg\r\n".encode() for w in ("106.8",) * 10)
        self.ran(self.bridge([Port(moving + b"OL,GS,+99999.9kg\r\nrubbish\r\n")]))
        self.assertEqual(self.sent, [])

    def test_a_weight_not_yet_settled_is_not_sent(self):
        self.ran(self.bridge([Port(b"ST,GS,+00106.8kg\r\nST,GS,+00106.8kg\r\nUS,GS,+00106.8kg\r\n"
                                   b"ST,GS,+00106.8kg\r\nST,GS,+00106.8kg\r\n")]))
        self.assertEqual(self.sent, [])

    def test_an_indicator_with_no_status_settles_by_repeating(self):
        self.ran(self.bridge([Port(recorded("plain_weight.txt"))], pattern="plain_weight"))
        self.assertEqual(self.weights(), ["104.35"])

    def test_a_pattern_of_the_sites_own(self):
        own = {"regex": r"^W:(?P<weight>\d+\.\d) (?P<status>S|M)$", "stable_status": ["S"]}
        self.ran(self.bridge([Port(b"W:12.5 M\nW:12.5 S\nW:12.5 S\nW:12.5 S\n")], pattern=own))
        self.assertEqual(self.weights(), ["12.5"])

    def test_grammes_are_sent_as_kilogrammes(self):
        self.ran(self.bridge([Port(b"ST,NT,+01250.0g\r\n" * 3)]))
        self.assertEqual(self.weights(), ["1.25"])


class ADroppedConnectionTests(BridgeCase):
    def test_the_port_is_opened_again_and_nothing_is_sent_twice(self):
        frames = recorded("status_gross.txt")
        cut = frames.index(b"US,GS,+00106.9kg")
        ports = [OSError("could not open port /dev/ttyUSB0"), Port(frames[:cut]), Port(frames[cut:])]
        self.ran(self.bridge(ports, retry_seconds=5))
        self.assertEqual(self.weights(), ["106.8", "0", "106.8"])
        self.assertEqual(self.slept, [5, 5, 5])

    def test_settling_starts_again_across_the_gap(self):
        ports = [Port(b"ST,GS,+00106.8kg\r\n" * 2), Port(b"ST,GS,+00106.8kg\r\n")]
        self.ran(self.bridge(ports))
        self.assertEqual(self.sent, [])

    def test_a_closed_port_is_opened_again(self):
        ports = [Port(b"ST,GS,+00050.0kg\r\n" * 3, then="closed"), Port(b"ST,GS,+00050.0kg\r\n" * 3)]
        self.ran(self.bridge(ports))
        self.assertEqual(self.weights(), ["50"])


class APostThatFailsTests(BridgeCase):
    def test_tried_again_while_it_stands_and_sent_once(self):
        now, calls = [0.0], []

        def send(payload):
            calls.append(payload["gross_kg"])
            if len(calls) == 1:
                raise OSError("connection refused")
            self.sent.append(payload)

        bridge = self.bridge([], send=send, clock=lambda: now[0], retry_seconds=5)
        for frame in ["ST,GS,+00106.8kg"] * 3:
            bridge.handle(frame)
        bridge.handle("ST,GS,+00106.8kg")  # too soon to try again
        now[0] = 6.0
        bridge.handle("ST,GS,+00106.8kg")
        bridge.handle("ST,GS,+00106.8kg")
        self.assertEqual((calls, self.weights()), (["106.8", "106.8"], ["106.8"]))

    def test_dropped_once_the_scale_reads_something_else(self):
        def send(payload):
            raise OSError("connection refused")

        bridge = self.bridge([], send=send, clock=lambda: 100.0)
        for frame in ["ST,GS,+00106.8kg"] * 3 + ["US,GS,+00040.1kg"]:
            bridge.handle(frame)
        self.assertIsNone(bridge.pending)


class ToTheStationsEndpointTests(TestCase):
    """The frames, posted as the bridge's own user with its password, become the readings a station weighs."""

    def test_the_readings_it_posts(self):
        from .station_scale import ScaleReading

        user = User.objects.create_user("bridge-sc1", password="kept-on-the-station-pc")
        user.user_permissions.add(Permission.objects.get(codename="add_scalereading"))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(
            b"bridge-sc1:kept-on-the-station-pc").decode())

        def send(payload):
            answer = client.post("/api/manufacturing/scale-readings/", payload, format="json")
            if answer.status_code != 201:
                raise OSError(f"{answer.status_code} {answer.content[:200]}")

        module = bridge_module()
        ports = [Port(recorded("status_gross.txt"))]

        def open_port(_config):
            if not ports:
                raise Finished
            return ports.pop(0)

        with self.assertRaises(Finished):
            module.Bridge({"scale": "SC-1", "server": "https://erp.example"}, open_port, send,
                          sleep=lambda _s: None).run()
        self.assertEqual(list(ScaleReading.objects.order_by("pk").values_list("scale_code", "gross_kg", "stable")),
                         [("SC-1", Decimal("106.8"), True), ("SC-1", Decimal("0"), True),
                          ("SC-1", Decimal("106.8"), True)])
