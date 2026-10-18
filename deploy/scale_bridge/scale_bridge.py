"""
The scale bridge: reads a weighing indicator on a serial (or USB-serial)
port and posts each settled weight to the ERP's scale-readings endpoint,
signed in as the bridge's own user, which may do nothing else (O225).

Indian indicators send continuous ASCII frames, several a second, such as
`ST,GS,+00123.4kg` (stable, gross) or `US,GS,+00121.9kg` (unstable). What a
frame looks like is configured, not coded: a pattern with named groups
`weight` and, optionally, `status`, `sign` and `unit`. Two are shipped
(PATTERNS); a site with another indicator writes its own in the config.

What is sent, and what never is:
- A weight is sent once it has settled: the indicator says stable (when
  its frames say so at all) and the same weight has come `repeat` frames
  running. An unstable frame, or a frame the pattern does not read, is
  never sent, and it starts the count again.
- A weight is sent once. The same weight held on the scale for an hour,
  or shaken and settled again, is one reading; it is sent again only
  after the scale has settled on something else (the empty scale's zero
  between one roll and the next).
- A dropped port is opened again, after `retry_seconds`, for ever: the
  bridge runs unattended. A refused or unanswered post is tried again
  while that weight still stands, and dropped once the scale reads
  something else: a stale weight posted late would be weighed as the
  next roll.

Standard library only, plus pyserial for the port itself. Run it as
`python3 scale_bridge.py /etc/scale-bridge/SC-1.json`; RUNBOOK.md says how
to install it on the station PC.
"""

import base64
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

log = logging.getLogger("scale_bridge")

# The two frame shapes shipped. Named groups: weight (required), status,
# sign, unit. `stable_status` lists the status words that mean settled;
# a pattern with no status group leaves stability to the repeat rule.
PATTERNS = {
    # "ST,GS,+00123.4kg", "US,NT,-00000.2kg", "OL,GS,+99999.9kg": status,
    # gross or net, signed weight, unit. Most bench and platform indicators
    # sold in India speak this, or can be set to.
    "status_gross": {
        "regex": r"^(?P<status>ST|US|OL),(?P<kind>GS|NT),(?P<sign>[+-])\s*(?P<weight>\d+(?:\.\d+)?)\s*(?P<unit>kg|g)\s*$",
        "stable_status": ["ST"],
    },
    # "  123.45 kg", "+0123.4", "=0123.4": a bare weight, sent over and over,
    # with no status; settled is the same weight `repeat` times running.
    "plain_weight": {
        "regex": r"^[=\s]*(?P<sign>[+-]?)\s*(?P<weight>\d+(?:\.\d+)?)\s*(?P<unit>kg|g)?\s*$",
        "stable_status": [],
    },
}

DEFAULTS = {
    "pattern": "status_gross",
    "repeat": 3,
    "baud": 9600,
    "retry_seconds": 5,
    "post_timeout_seconds": 10,
    "encoding": "ascii",
}


class FrameReader:
    """Reads one frame into a weight in kilogrammes, or None, by the configured pattern."""

    def __init__(self, pattern):
        if isinstance(pattern, str):
            if pattern not in PATTERNS:
                raise ValueError(f"No frame pattern {pattern!r}; shipped: {', '.join(PATTERNS)}.")
            pattern = PATTERNS[pattern]
        self.regex = re.compile(pattern["regex"])
        if "weight" not in self.regex.groupindex:
            raise ValueError("A frame pattern needs a group named 'weight'.")
        self.stable_status = set(pattern.get("stable_status") or ())
        self.has_status = "status" in self.regex.groupindex

    def read(self, frame):
        """(weight in kg, the indicator says stable) for a frame it reads; None for one it cannot."""
        match = self.regex.match(frame.strip("\r\n\x02\x03 "))
        if match is None:
            return None
        try:
            weight = Decimal(match.group("weight"))
        except InvalidOperation:
            return None
        groups = match.groupdict()
        if groups.get("sign") == "-":
            weight = -weight
        if groups.get("unit") == "g":
            weight = weight / 1000
        settled = (groups.get("status") in self.stable_status) if self.has_status else True
        # What the frame says it is: NT is net (tared on the core), anything else gross (review F7).
        return weight, settled, "net" if groups.get("kind") == "NT" else "gross"


class Settling:
    """
    The stability rule: the same weight `repeat` frames running, each one
    stable where the indicator says, and each weight once.
    """

    def __init__(self, repeat):
        if repeat < 1:
            raise ValueError("repeat is at least 1.")
        self.repeat = repeat
        self.last = None
        self.count = 0
        self.sent = None

    def offer(self, reading):
        """The weight to send now, or None."""
        if reading is None or not reading[1]:
            # Unreadable or moving: nothing is settling. What was sent stays
            # sent: someone leaning on the roll is not a second roll.
            self.last, self.count = None, 0
            return None
        weight = (reading[0], reading[2] if len(reading) > 2 else "gross")
        if weight == self.last:
            self.count += 1
        else:
            self.last, self.count = weight, 1
        if self.count >= self.repeat and weight != self.sent:
            return weight
        return None

    def delivered(self, weight):
        self.sent = weight

    def still(self, weight):
        """Whether `weight` is what the scale reads now, so a failed post is worth trying again."""
        return self.last == weight and self.count >= self.repeat


class Bridge:
    def __init__(self, config, open_port, send, sleep=time.sleep, clock=time.monotonic, wall=time.time):
        self.config = {**DEFAULTS, **config}
        for name in ("scale", "server"):
            if not self.config.get(name):
                raise ValueError(f"The config names no {name}.")
        self.reader = FrameReader(self.config["pattern"])
        self.settling = Settling(int(self.config["repeat"]))
        self.open_port = open_port
        self.send = send
        self.sleep = sleep
        self.clock = clock
        self.wall = wall
        self.key_for, self.key, self.sequence, self.started = None, None, 0, wall()
        self.pending = None
        self.next_try = 0.0

    def payload(self, weight):
        """
        A gross frame as gross_kg, a net one as net_kg (review F7), with the key the server knows
        this settled weight by: posted again because its answer was lost, it is one reading (F8).
        """
        kg, kind = weight
        if self.key_for != weight:
            # The scale, this run of the bridge and the settled weight's number in it, and the weight:
            # unique without a clock that ticks between two rolls (review F8).
            self.key_for, self.sequence = weight, self.sequence + 1
            self.key = f"{self.config['scale']}:{self.started:.0f}:{self.sequence}:{kind}:{format(kg.normalize(), 'f')}"
        return {"scale": self.config["scale"], f"{kind}_kg": format(kg.normalize(), "f"), "stable": True,
                "key": self.key}

    def handle(self, frame):
        """One frame: send what it settles, and retry a weight not yet delivered while it stands."""
        weight = self.settling.offer(self.reader.read(frame))
        if self.pending is not None and not self.settling.still(self.pending):
            # The scale reads something else now: posted late, the old
            # weight would be weighed as whatever is on it.
            log.warning("Dropped %s kg: the scale moved before it was posted.", self.pending[0])
            self.pending = None
        if weight is None or (weight == self.pending and self.clock() < self.next_try):
            return None
        try:
            self.send(self.payload(weight))
        except Exception as error:  # noqa: BLE001 - logged and retried while it stands
            log.warning("Posting %s kg failed (%s); trying again while it stands.", weight[0], error)
            self.pending = weight
            self.next_try = self.clock() + float(self.config["retry_seconds"])
            return None
        self.settling.delivered(weight)
        self.pending = None
        log.info("Posted %s kg %s from %s.", weight[0], weight[1], self.config["scale"])
        return weight

    def frames(self, port):
        buffer = b""
        while True:
            chunk = port.read(64)
            if chunk is None:
                raise ConnectionError("the port was closed")
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer or b"\r" in buffer:
                cut = min(i for i in (buffer.find(b"\n"), buffer.find(b"\r")) if i >= 0)
                line, buffer = buffer[:cut], buffer[cut + 1:]
                if line.strip():
                    yield line.decode(self.config["encoding"], errors="replace")

    def run(self, stop=lambda: False):
        """Read, settle and send until `stop()`; a dropped port is opened again after a pause."""
        while not stop():
            try:
                port = self.open_port(self.config)
                try:
                    for frame in self.frames(port):
                        self.handle(frame)
                        if stop():
                            return
                finally:
                    close = getattr(port, "close", None)
                    if close is not None:
                        close()
            except (OSError, ConnectionError, StopIteration) as error:
                log.warning("Scale port lost (%s); opening it again in %ss.", error, self.config["retry_seconds"])
                # Nothing read across a gap counts towards settling.
                self.settling.offer(None)
                self.sleep(self.config["retry_seconds"])


def open_serial(config):
    import serial  # pyserial: the one thing beyond the standard library

    return serial.Serial(config["port"], baudrate=int(config["baud"]), timeout=1)


def poster(config):
    """POST a reading with the bridge's API key (an integration's, L12) over HTTPS; raises on anything but 201."""
    from urllib.parse import urlsplit

    server = urlsplit(config["server"])
    # The key goes with every post: in clear over http, anyone on the plant's network reads it
    # (review F9). Only the station PC itself may be spoken to without TLS.
    if server.scheme != "https" and not (server.scheme == "http" and server.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError(f"{config['server']} is not https: the bridge's key would cross the network in "
                         "clear. Use the ERP's https address (only localhost may be http).")
    url = config["server"].rstrip("/") + "/api/manufacturing/scale-readings/"
    with open(config["api_key_file"], encoding="utf-8") as kept:
        key = kept.read().strip()

    def send(payload):
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Api-Key {key}"})
        with urllib.request.urlopen(request, timeout=float(config["post_timeout_seconds"])) as answer:
            if answer.status != 201:
                raise urllib.error.HTTPError(url, answer.status, "not accepted", answer.headers, None)

    return send


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(argv) != 2:
        print("usage: scale_bridge.py CONFIG.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as kept:
        config = {**DEFAULTS, **json.load(kept)}
    Bridge(config, open_serial, poster(config)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
