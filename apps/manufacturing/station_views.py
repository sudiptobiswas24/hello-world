"""
The loom exit station's API.

The device at the station is signed in as a Django user who may run a
station; the person at it is known by their PIN, held in that device's
session until they sign out or their twelve hours are up. Every roll
is recorded against the person, never against the device.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.html import escape
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from . import barcode
from .rolls import FabricRoll
from .shifts import Shift
from .station import (
    CoreType,
    LoomStation,
    LoomWaste,
    TapeCount,
    latest_tape_lot,
    record_roll,
    run_on,
    specification_for,
)
from .station_report import morning_report

SIGNED_IN_FOR = datetime.timedelta(hours=12)


class CanRunStation(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("manufacturing.weigh_at_station")


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


def _decimal(data, name):
    try:
        return Decimal(str(data.get(name)))
    except (InvalidOperation, TypeError, ValueError):
        raise DRFValidationError([f"{name} must be a number."])


def _exact(value):
    """Figures go over the wire as strings: a float turns 0.60 into 0.5999."""
    return None if value is None else str(value)


def _person(employee):
    return {"number": employee.employee_number, "name": employee.party.name}


class LoomStationViewSet(viewsets.GenericViewSet):
    queryset = LoomStation.objects.filter(is_active=True)
    lookup_field = "code"
    permission_classes = [IsAuthenticated, CanRunStation]

    # -- who is at the station -------------------------------------------

    def _key(self, station):
        return f"station:{station.code}"

    def _operator(self, request, station):
        from apps.hr.models import Employee

        held = request.session.get(self._key(station))
        if not held:
            return None
        since = datetime.datetime.fromisoformat(held["since"])
        if timezone.now() - since > SIGNED_IN_FOR:
            del request.session[self._key(station)]
            return None
        person = Employee.objects.select_related("party").filter(pk=held["employee"]).first()
        # Asked again on every request, as the PIN was at sign-in: a PIN
        # revoked or reissued, or a person who has left, ends the session
        # now rather than twelve hours later.
        if (person is None or person.pin_digest != held.get("pin")
                or not person.is_working_on(timezone.localdate())):
            del request.session[self._key(station)]
            return None
        return person

    def _require_operator(self, request, station):
        operator = self._operator(request, station)
        if operator is None:
            raise DRFValidationError(["Sign in with your PIN first."])
        return operator

    def retrieve(self, request, code=None):
        station = self.get_object()
        now = timezone.now()
        shift = Shift.covering(now)
        operator = self._operator(request, station)
        return Response({
            "code": station.code,
            "name": station.name,
            "scale": station.scale_code,
            "printer": station.printer_code,
            "tolerance_percent": _exact(station.metres_tolerance_percent),
            "shift": shift.code if shift else None,
            "shift_name": shift.name if shift else None,
            "shift_date": shift.shift_date_for(now) if shift else None,
            "operator": _person(operator) if operator else None,
            "locked": station.is_locked(now),
            "looms": [
                {"code": machine.code,
                 "contractor": machine.contractor.name if machine.contractor_id else None,
                 "contractor_code": machine.contractor.code if machine.contractor_id else None}
                for machine in station.machines.select_related("contractor").order_by("code")
            ],
            "cores": [
                {"code": core.code, "tare_kg": _exact(core.tare_kg)}
                for core in CoreType.objects.filter(is_active=True)
            ],
            "reasons": dict(FabricRoll._meta.get_field("override_reason").choices),
        })

    @action(detail=True, methods=["post"], url_path="sign-in")
    def sign_in(self, request, code=None):
        station = self.get_object()
        operator = _run(station.identify, request.data.get("pin"))
        request.session[self._key(station)] = {
            "employee": operator.pk, "since": timezone.now().isoformat(),
            "pin": operator.pin_digest,
        }
        return Response({"operator": _person(operator)})

    @action(detail=True, methods=["post"], url_path="sign-out")
    def sign_out(self, request, code=None):
        station = self.get_object()
        request.session.pop(self._key(station), None)
        return Response({"operator": None})

    # -- the loom and its roll -------------------------------------------

    @action(detail=True, methods=["get"], url_path=r"looms/(?P<machine>[^/]+)")
    def loom(self, request, code=None, machine=None):
        station = self.get_object()
        self._require_operator(request, station)
        loom = station.machines.filter(code=machine).select_related("contractor").first()
        if loom is None:
            raise DRFValidationError([f"{station} does not weigh for {machine}."])
        order = _run(run_on, loom)
        shift = Shift.covering(timezone.now())
        on_date = shift.shift_date_for(timezone.now()) if shift else timezone.now().date()
        spec = specification_for(order.item, on_date)
        tape = latest_tape_lot(order)
        previous = FabricRoll.objects.filter(
            machine=loom, entry__voided_at__isnull=True).select_related("lot").order_by(
            "-weighed_at", "-id").first()
        return Response({
            "loom": loom.code,
            "contractor": loom.contractor.name if loom.contractor_id else None,
            "run": order.number,
            "item": order.item.sku,
            "item_name": order.item.name,
            "specification": None if spec is None else {
                "code": spec.code,
                "gsm": _exact(spec.target_gsm or spec.gsm()),
                "lay_flat_width_cm": _exact(spec.lay_flat_width_cm),
                "weave": spec.weave,
            },
            "tape_batch": tape.code if tape else None,
            "previous_roll": None if previous is None else {
                "code": previous.lot.code, "weighed_at": previous.weighed_at,
            },
        })

    @action(detail=True, methods=["post"])
    def rolls(self, request, code=None):
        station = self.get_object()
        operator = self._require_operator(request, station)
        data = request.data
        loom = station.machines.filter(code=data.get("loom")).first()
        if loom is None:
            raise DRFValidationError([f"{station} does not weigh for {data.get('loom')}."])
        core = CoreType.objects.filter(code=data.get("core"), is_active=True).first()
        if core is None:
            raise DRFValidationError([f"No core type {data.get('core')}."])
        source = data.get("source", "scale")
        supervisor = None
        if source == "manual":
            supervisor = _run(station.identify, data.get("supervisor_pin"))
        # The weaver's own PIN, never a number typed for them: this is
        # what their piece work is paid on.
        weaver = _run(station.identify, data["weaver_pin"]) if data.get("weaver_pin") else None
        roll = _run(
            record_roll, station, operator, loom, _decimal(data, "gross_kg"), core,
            _decimal(data, "declared_m"), source=source, supervisor=supervisor,
            reason=data.get("reason", ""), note=data.get("note", ""), weaver=weaver,
        )
        return Response({
            "code": roll.lot.code,
            "woven_by": _person(weaver) if weaver else None,
            "net_kg": _exact(roll.net_weight_kg),
            "gross_kg": _exact(roll.gross_weight_kg()),
            "declared_m": _exact(roll.length_m),
            "metres_from_weight": _exact(roll.metres_from_weight),
            "variance_percent": _exact(roll.metres_variance_percent),
            "tolerance_percent": _exact(roll.metres_tolerance_percent),
            "is_exception": roll.is_metres_exception,
            "source": roll.weight_source,
            "approved_by": _person(roll.approved_by) if roll.approved_by_id else None,
            "label": f"label/{roll.lot.code}/",
        }, status=201)

    # -- the tape balance's own figures ------------------------------------

    def _contractor(self, station, code):
        machine = station.machines.filter(contractor__code=code).select_related(
            "contractor").first()
        if machine is None:
            raise DRFValidationError([f"No loom here is run by {code}."])
        return machine.contractor

    @action(detail=True, methods=["post"], url_path="tape-count")
    def tape_count(self, request, code=None):
        """
        Tape on a contractor's looms when a shift-day closed. The day is
        given, not guessed from the clock: the eight o'clock count closes
        yesterday, and at shift change the clock says today.
        """
        station = self.get_object()
        operator = self._require_operator(request, station)
        contractor = self._contractor(station, request.data.get("contractor"))
        try:
            shift_date = datetime.date.fromisoformat(str(request.data.get("shift_date")))
        except ValueError:
            raise DRFValidationError(["shift_date must be a date."])
        kg = _decimal(request.data, "kg")
        if kg < 0:
            raise DRFValidationError(["A count cannot be negative."])
        existing = TapeCount.objects.filter(station=station, contractor=contractor,
                                            shift_date=shift_date,
                                            voided_at__isnull=True).first()
        with transaction.atomic():
            if existing is not None:
                if not request.data.get("correct"):
                    raise DRFValidationError([
                        f"Tape for {contractor.name} on {shift_date} is already counted. "
                        "To correct it, count again with a supervisor's PIN."])
                supervisor = _run(station.identify, request.data.get("supervisor_pin"))
                _run(existing.void, supervisor, operator, station)
            TapeCount.objects.create(station=station, contractor=contractor,
                                     shift_date=shift_date, kg=kg, counted_by=operator,
                                     counted_at=timezone.now())
        return Response({"contractor": contractor.code, "shift_date": shift_date,
                         "kg": _exact(kg)}, status=201)

    @action(detail=True, methods=["post"])
    def waste(self, request, code=None):
        station = self.get_object()
        operator = self._require_operator(request, station)
        contractor = self._contractor(station, request.data.get("contractor"))
        kg = _decimal(request.data, "kg")
        if kg <= 0:
            raise DRFValidationError(["Weigh some waste."])
        now = timezone.now()
        shift = Shift.covering(now)
        if shift is None:
            raise DRFValidationError(["No shift is running."])
        waste = LoomWaste.objects.create(station=station, contractor=contractor,
                                         shift_date=shift.shift_date_for(now), kg=kg,
                                         weighed_by=operator, weighed_at=now)
        return Response({"id": waste.pk, "contractor": contractor.code, "kg": _exact(kg)},
                        status=201)

    @action(detail=True, methods=["post"], url_path=r"waste/(?P<waste>[0-9]+)/void")
    def void_waste(self, request, code=None, waste=None):
        """A weighing taken wrong, withdrawn with a supervisor's PIN."""
        station = self.get_object()
        operator = self._require_operator(request, station)
        row = get_object_or_404(LoomWaste, pk=waste, station=station)
        supervisor = _run(station.identify, request.data.get("supervisor_pin"))
        _run(row.void, supervisor, operator, station)
        return Response({"id": row.pk, "voided": True})

    @action(detail=True, methods=["post"])
    def bags(self, request, code=None):
        """A bundle counted off a machine, its sample weighed. Off weight needs a
        supervisor's PIN and a reason, or nothing is booked."""
        from .conversion import record_bags

        station = self.get_object()
        operator = self._require_operator(request, station)
        data = request.data
        machine = station.machines.filter(code=data.get("machine")).first()
        if machine is None:
            raise DRFValidationError([f"{station} does not count for {data.get('machine')}."])
        supervisor = (_run(station.identify, data["supervisor_pin"])
                      if data.get("supervisor_pin") else None)
        count = _run(record_bags, station, operator, machine, data.get("bags"),
                     data.get("sample_grams") or [], supervisor=supervisor,
                     reason=data.get("reason", ""))
        return Response({
            "id": count.pk, "batch": count.inspection.lot.code, "bags": count.bags,
            "target_grams": _exact(count.target_grams),
            "sample_mean_grams": _exact(count.sample_mean_grams), "passed": count.passed,
            "conceded_by": _person(count.supervisor) if count.supervisor_id else None,
            "inspection": count.inspection.number,
        }, status=201)

    @action(detail=True, methods=["post"], url_path=r"bags/(?P<count>[0-9]+)/void")
    def void_bags(self, request, code=None, count=None):
        """A count taken wrong, withdrawn with a supervisor's PIN and a reason."""
        from .conversion import BagCount, void_bags

        station = self.get_object()
        self._require_operator(request, station)
        row = get_object_or_404(BagCount, pk=count, station=station)
        supervisor = _run(station.identify, request.data.get("supervisor_pin"))
        _run(void_bags, row, supervisor, request.data.get("reason", ""))
        return Response({"id": row.pk, "voided": True})

    @action(detail=True, methods=["get"], url_path=r"label/(?P<roll>[^/]+)")
    def label(self, request, code=None, roll=None):
        """
        A 100 x 75 mm label: the barcode and the roll's code, as designed.

        Any roll, from any station: a label is the roll's own code, and a
        reprint at the next station when this one's printer is down is
        the whole point of being able to ask for one.
        """
        self.get_object()
        found = get_object_or_404(FabricRoll.objects.select_related("lot", "entry"), lot__code=roll)
        if found.entry is not None and found.entry.voided_at is not None:
            # A label is how a roll is picked and shipped; one for a roll
            # taken back off the books would put it back into the world.
            raise DRFValidationError([f"{roll} was voided; it has no label."])
        text = found.lot.code
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(text)}</title>
<style>
  @page {{ size: 100mm 75mm; margin: 0; }}
  html, body {{ margin: 0; background: #fff; }}
  .label {{ width: 100mm; height: 75mm; box-sizing: border-box; padding: 6mm 5mm 5mm;
           display: flex; flex-direction: column; justify-content: space-between;
           align-items: center; }}
  .bars {{ width: 88mm; height: 42mm; }}
  .bars svg {{ width: 100%; height: 100%; display: block; }}
  .code {{ font: 600 7mm/1 'IBM Plex Mono', ui-monospace, monospace; letter-spacing: .02em; }}
</style></head>
<body><div class="label"><div class="bars">{barcode.svg(text)}</div>
<div class="code">{escape(text)}</div></div></body></html>"""
        return HttpResponse(page, content_type="text/html; charset=utf-8")



class CanReadStationReports(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("manufacturing.view_loomstation")


def report_payload(report):
    """The morning report as JSON, figures as exact strings."""
    return {
        "station": report["station"].code,
        "shift_date": report["shift_date"],
        "rolls": report["rolls"],
        "by_shift": report["by_shift"],
        "from_scale": report["from_scale"],
        "manual": report["manual"],
        "manual_percent": _exact(report["manual_percent"]),
        "looms": [
            {"loom": row["loom"], "rolls": row["rolls"],
             "from_weight": _exact(row["from_weight"]), "declared": _exact(row["declared"]),
             "variance_percent": _exact(row["variance_percent"]),
             "tolerance_percent": _exact(row["tolerance"]), "over": row["over"]}
            for row in report["looms"]
        ],
        "overrides": [
            {**row, "net_kg": _exact(row["net_kg"])} for row in report["overrides"]
        ],
        "balances": [
            {"contractor": row["contractor"].code, "name": row["contractor"].name,
             "missing": row["missing"], "over": row["over"],
             **{key: _exact(row[key]) for key in (
                 "opening", "issued", "closing", "consumed", "rolls_kg", "waste",
                 "unaccounted", "unaccounted_percent")}}
            for row in report["balances"]
        ],
        "exceptions": report["exceptions"],
    }


class StationReportViewSet(viewsets.GenericViewSet):
    """The 8 AM report, for whoever manages the station rather than runs it."""

    queryset = LoomStation.objects.all()
    lookup_field = "code"
    permission_classes = [IsAuthenticated, CanReadStationReports]

    def retrieve(self, request, code=None):
        station = self.get_object()
        value = request.query_params.get("date")
        try:
            shift_date = (datetime.date.fromisoformat(value) if value
                          else timezone.localdate() - datetime.timedelta(days=1))
        except ValueError:
            raise DRFValidationError(["date must be YYYY-MM-DD."])
        return Response(report_payload(morning_report(station, shift_date)))

    @action(detail=True, methods=["get"])
    def bags(self, request, code=None):
        """Bags and off-weight bundles by machine and by operator, ?start=&end=."""
        from .conversion import summary

        station = self.get_object()
        try:
            start = datetime.date.fromisoformat(request.query_params.get("start", ""))
            end = datetime.date.fromisoformat(request.query_params.get("end", ""))
        except ValueError:
            raise DRFValidationError(["start and end must be YYYY-MM-DD."])
        found = summary(start, end, station=station)
        return Response({key: [row | {"mean_deviation_percent":
                                      _exact(row["mean_deviation_percent"])} for row in rows]
                         for key, rows in found.items()})
