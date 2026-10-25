"""
The scale bridge: weights the scale reported, not weights typed.

A station's "scale" weight was whatever number the station's screen
sent, and the screen could send any number. Where the scale has a
serial or network output, a small bridge beside it posts what the scale
reads — the weight, and whether it has settled — and a station marked
as bridged takes its weight from that and nothing else:

- **the latest reading**, which must have settled: a roll still being
  lowered onto the platform is not its weight;
- **fresh**, from the last minute: a reading older than that is the
  roll before, or nothing at all;
- **unused**: one reading weighs one roll or one doff. The next one on
  the platform makes a new reading; confirming twice on the same one is
  the same roll booked twice;
- **something on the scale**: an empty platform reads nought.

The bridge should post when the reading settles or changes, not every
tick of the scale; every post is kept, as the record of what the scale
said.

A weight typed at a bridged station is a typed weight — scale off line,
under calibration, the roll does not fit — and needs a supervisor's PIN
and a reason, exactly as at a station with no bridge.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import AuditModel

FRESH = datetime.timedelta(seconds=60)

TYPED_REASONS = [
    ("scale_offline", "Scale not connected"),
    ("calibration", "Scale under calibration"),
    ("does_not_fit", "Roll does not fit platform"),
    ("other", "Other"),
]


class ScaleReading(AuditModel):
    scale_code = models.CharField(max_length=32, db_index=True)
    gross_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                   help_text="What a gross frame (GS) read; blank for a net one.")
    net_kg = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        help_text="What a net frame (NT) read, the indicator tared on the core: not tared again (review F7).")
    key = models.CharField(
        max_length=96, null=True, blank=True, unique=True,
        help_text="The bridge's key for one settled weight: posted again because its answer was lost, it is "
                  "the same reading, answered as the first time (review F8).")
    stable = models.BooleanField()
    read_at = models.DateTimeField(help_text="When the server received it.")

    class Meta:
        ordering = ["-read_at", "-id"]

    def __str__(self):
        return f"{self.scale_code} {self.kg()} kg {self.weighed_as()} at {timezone.localtime(self.read_at):%H:%M:%S}"

    def kg(self):
        return self.net_kg if self.net_kg is not None else self.gross_kg

    def weighed_as(self):
        return "net" if self.net_kg is not None else "gross"

    def used_by(self):
        """The roll or doff this reading weighed, if any."""
        for name in ("fabric_roll", "tape_doff", "film_roll", "process_roll"):
            try:
                return getattr(self, name)
            except models.ObjectDoesNotExist:
                continue
        return None


def post_reading(scale_code, gross_kg, stable, at=None, net_kg=None, key=None):
    """
    Keep what a scale's bridge says: a gross weight or a net one (exactly one), and whether it has
    settled. A key posted before is the same reading: the first one is answered again, and no second
    row is made (review F8: a reading whose answer was lost was posted twice, a fresh one).
    """
    from django.db import IntegrityError, transaction

    code = (scale_code or "").strip()
    if not code:
        raise ValidationError("Say which scale this is.")
    if (gross_kg in (None, "")) == (net_kg in (None, "")):
        raise ValidationError("Send the weight once: gross_kg for a gross frame, net_kg for a net one.")
    try:
        weight = Decimal(str(gross_kg if net_kg in (None, "") else net_kg))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("A reading is a number of kilogrammes.")
    if not weight.is_finite():
        raise ValidationError("A reading is a number of kilogrammes.")
    if not isinstance(stable, bool):
        raise ValidationError("Say whether the reading has settled: true or false.")
    key = (str(key).strip() or None) if key is not None else None
    if key is not None:
        if len(key) > ScaleReading._meta.get_field("key").max_length:
            raise ValidationError("A reading's key is 96 characters or fewer.")
        kept = ScaleReading.objects.filter(key=key).first()
        if kept is not None:
            kept.kept_again = True
            return kept
    fields = {"gross_kg": weight} if net_kg in (None, "") else {"net_kg": weight}
    try:
        with transaction.atomic():
            return ScaleReading.objects.create(scale_code=code, stable=stable, key=key,
                                               read_at=at or timezone.now(), **fields)
    except IntegrityError:
        # The same key posted twice at once: the other one kept it.
        kept = ScaleReading.objects.filter(key=key).first() if key else None
        if kept is None:
            raise
        kept.kept_again = True
        return kept


def latest(station, at=None):
    at = at or timezone.now()
    return (ScaleReading.objects.select_for_update(no_key=True)
            .filter(scale_code=station.scale_code, read_at__lte=at).first())


def take_reading(station, at):
    """The reading a bridged station weighs with now, or why there is none."""
    reading = latest(station, at)
    scale = station.scale_code
    if reading is None:
        raise ValidationError(f"Scale {scale} has reported nothing. Check its bridge, or "
                              "type the weight with a supervisor's PIN.")
    if at - reading.read_at > FRESH:
        raise ValidationError(
            f"Scale {scale} last reported at {timezone.localtime(reading.read_at):%H:%M:%S}. "
            "Put the load on it again, or type the weight with a supervisor's PIN.")
    if not reading.stable:
        raise ValidationError(f"Scale {scale} has not settled. Wait for it.")
    if reading.kg() <= 0:
        raise ValidationError(f"Scale {scale} reads {reading.kg()} kg: nothing is on it.")
    used = reading.used_by()
    if used is not None:
        raise ValidationError(f"This reading already weighed {used.lot.code}. Take it off and put "
                              "the next one on.")
    return reading


def _kg(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("The gross weight is a number.")
    if not number.is_finite():
        raise ValidationError("The gross weight is a number.")
    return number


def gross_weight(station, gross_kg, source, at, tare_kg=None):
    """
    (gross, reading) for a weight the station calls a scale weight.

    A bridged station's comes from its scale; a typed figure sent with
    it must agree. Elsewhere it is the figure the screen sent. A net
    reading (the indicator tared on the core) is its weight plus the
    `tare_kg` the caller takes off again, so it is not tared twice
    (review F7); `tare_kg` may be a function, asked only then.
    """
    if source != "scale" or not station.scale_bridged:
        if gross_kg in (None, ""):
            raise ValidationError("Enter the gross weight.")
        return _kg(gross_kg), None
    reading = take_reading(station, at)
    if reading.net_kg is not None:
        if tare_kg is None:
            raise ValidationError(f"Scale {station.scale_code} sends net weights, and {station} weighs "
                                  "gross: set the indicator to gross, or type the weight.")
        tare = tare_kg() if callable(tare_kg) else tare_kg
        if gross_kg not in (None, "") and _kg(gross_kg) != reading.net_kg + tare:
            raise ValidationError(f"Scale {station.scale_code} reads {reading.net_kg} kg net, not {gross_kg} gross.")
        return reading.net_kg + tare, reading
    if gross_kg not in (None, "") and _kg(gross_kg) != reading.gross_kg:
        raise ValidationError(f"Scale {station.scale_code} reads {reading.gross_kg} kg, "
                              f"not {gross_kg}.")
    return reading.gross_kg, reading


def check_typed_weight(station, operator, supervisor, reason, note=""):
    """A weight not from the scale: somebody else approves it, and says why."""
    if supervisor is None:
        raise ValidationError("A typed weight needs a supervisor's PIN.")
    if supervisor.pk == operator.pk:
        raise ValidationError("A typed weight is approved by somebody else.")
    if not station.supervisors.filter(pk=supervisor.pk).exists():
        raise ValidationError(f"{supervisor} does not approve weights at {station}.")
    if not reason:
        raise ValidationError("Give the reason the scale was not used.")
    if reason not in dict(TYPED_REASONS):
        raise ValidationError(f"{reason!r} is not a reason the scale was not used.")
    if reason == "other" and not (note or "").strip():
        raise ValidationError("Say what the other reason was.")
