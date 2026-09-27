"""
ITC-04: what went out to job workers on challans, and what came back.

Half-yearly for a business over five crore, annual below it; the
period is given, not guessed. Compiled from issued challans and the
vendor's returns laid against them (manufacturing.jobwork), so it reads
what was recorded and nothing configured since.

Built: table 4 (sent) and table 5A (received back from the job worker
the goods were sent to), losses and wastes included. Not built: 5B and
5C, goods moved on from one job worker to another or supplied from the
job worker's premises, which this system has no record of.

The rate of tax is the GST the goods would bear, split into central and
state tax for a job worker in our own state and integrated tax for one
elsewhere. None is charged on a challan; the form asks for it anyway.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.gst import GstSettings
from apps.manufacturing.jobwork import JobWorkLine, JobWorkLoss, allocation

ZERO = Decimal("0")


def _uqc(line):
    uom = line.operation.work_order.uom
    code = getattr(uom, "gst_uqc", None)
    return code.code if code else "OTH"


def _rates(line, home_state):
    rate = line.tax_rate
    if line.challan.job_worker_state and line.challan.job_worker_state != home_state:
        return {"central": ZERO, "state": ZERO, "integrated": rate, "cess": ZERO}
    return {"central": rate / 2, "state": rate / 2, "integrated": ZERO, "cess": ZERO}


def _worker(challan):
    return {"gstin": challan.job_worker_gstin, "state": challan.job_worker_state,
            "name": challan.job_worker.name}


def itc04(start, end):
    settings = GstSettings.active()
    if settings is None:
        raise ValidationError("GST is not set up: there is no active registration.")
    if end < start:
        raise ValidationError("The period ends before it starts.")

    lines = list(
        JobWorkLine.objects.filter(challan__posted=True, challan__voided_at__isnull=True)
        .select_related("challan__job_worker", "operation__work_order__uom__gst_uqc")
        .order_by("challan__challan_date", "challan__number", "id")
    )
    sent, received, warnings = [], [], []
    by_operation = defaultdict(list)
    for line in lines:
        by_operation[line.operation_id].append(line)
        if start <= line.challan.challan_date <= end:
            sent.append({
                **_worker(line.challan),
                "challan": line.challan.number, "challan_date": line.challan.challan_date,
                "description": line.description, "hsn": line.hsn_code, "uqc": _uqc(line),
                "quantity": line.quantity, "taxable_value": line.value,
                "goods": "capital goods" if line.is_capital_goods else "inputs",
                "rates": _rates(line, settings.state),
            })

    for operation_lines in by_operation.values():
        operation = operation_lines[0].operation
        for row in allocation(operation).values():
            line = row["line"]
            for movement, quantity in row["events"]:
                if not start <= movement.movement_date <= end:
                    continue
                if quantity < 0:
                    warnings.append(
                        f"{movement.number}: {-quantity} of {line.description} went back "
                        f"to {line.challan.job_worker.name} for rework without a challan "
                        "of its own."
                    )
                    continue
                received.append({
                    **_worker(line.challan),
                    "job_worker_challan": movement.reference, "date": movement.movement_date,
                    "description": line.description, "uqc": _uqc(line),
                    "quantity": quantity, "original_challan": line.challan.number,
                    "original_challan_date": line.challan.challan_date,
                    "nature_of_job_work": operation.name,
                    "losses_uqc": _uqc(line), "losses_quantity": ZERO,
                })
    # A loss is only ever recorded against an issued challan, and a
    # challan with losses cannot be withdrawn, so every loss counts.
    for loss in JobWorkLoss.objects.filter(
        loss_date__gte=start, loss_date__lte=end,
    ).select_related("line__challan__job_worker", "line__operation__work_order__uom__gst_uqc"):
        line = loss.line
        received.append({
            **_worker(line.challan),
            "job_worker_challan": "", "date": loss.loss_date,
            "description": line.description, "uqc": _uqc(line), "quantity": ZERO,
            "original_challan": line.challan.number,
            "original_challan_date": line.challan.challan_date,
            "nature_of_job_work": line.operation.name,
            "losses_uqc": _uqc(line), "losses_quantity": loss.quantity,
        })
    received.sort(key=lambda row: (row["date"], row["original_challan"]))
    for row in sent + received:
        if row["uqc"] == "OTH":
            warnings.append(f"{row.get('challan') or row['original_challan']}: the run's unit "
                            "has no GST unit code.")
    return {
        "gstin": settings.gstin, "period": (start, end), "sent": sent,
        "received": received, "warnings": sorted(set(warnings)),
        "not_built": ["Table 5B: goods moved from one job worker to another",
                      "Table 5C: goods supplied from the job worker's premises",
                      "Components sent on a subcontract purchase order"],
    }
