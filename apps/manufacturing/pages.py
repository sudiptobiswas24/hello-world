"""
Pages for the shop floor: the loom exit station and its 8 AM report.

Server-rendered shells. The station page is a single screen that talks
to the station API; the report is rendered here from the same figures
the report API returns, so the printed page and the JSON cannot
disagree.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.decorators import login_required, permission_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .station import LoomStation
from .station_report import morning_report

ZERO = Decimal("0")


@login_required
@permission_required("manufacturing.weigh_at_station", raise_exception=True)
@ensure_csrf_cookie
def station_page(request, code):
    station = get_object_or_404(LoomStation, code=code, is_active=True)
    return render(request, "manufacturing/station.html", {"station": station})


@login_required
@permission_required("manufacturing.view_loomstation", raise_exception=True)
def report_page(request, code):
    station = get_object_or_404(LoomStation, code=code)
    value = request.GET.get("date")
    try:
        shift_date = (datetime.date.fromisoformat(value) if value
                      else timezone.localdate() - datetime.timedelta(days=1))
    except ValueError:
        raise Http404("date must be YYYY-MM-DD")
    report = morning_report(station, shift_date)
    from_weight = sum((row["from_weight"] for row in report["looms"]), ZERO)
    declared = sum((row["declared"] for row in report["looms"]), ZERO)
    overall = (
        ((declared - from_weight) / from_weight * 100).quantize(Decimal("0.01"))
        if from_weight else None
    )
    return render(request, "manufacturing/station_report.html", {
        "report": report,
        "station": station,
        "from_weight": from_weight,
        "declared": declared,
        "overall_percent": overall,
        "beyond": declared - from_weight,
        "over_looms": [row["loom"] for row in report["looms"] if row["over"]],
        "generated": timezone.localtime(),
    })
