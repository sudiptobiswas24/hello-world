"""A payslip on paper, from core's document layout: what was earned, what was taken, what was paid."""

from reportlab.lib.units import mm

from apps.core.documents import render_document, rupees_in_words

from .payroll import ComponentKind


def payslip_pdf(slip):
    run, employee = slip.run, slip.employee
    paid_days, ncp = slip.kept_days()
    meta = [["Employee", employee.employee_number], ["Pay run", run.number or str(run)],
            ["Period", f"{run.period_start:%d %b %Y} to {run.period_end:%d %b %Y}"],
            ["Pay date", f"{run.pay_date:%d %b %Y}"], ["Days paid", f"{paid_days:f}"],
            ["Days not paid", f"{ncp:f}"]]
    if employee.job_title:
        meta.insert(1, ["Job", employee.job_title])
    if employee.uan:
        meta.append(["UAN", employee.uan])
    if employee.esi_number:
        meta.append(["ESI number", employee.esi_number])
    rows = []
    for line in slip.lines.all():
        earned = line.amount if line.kind == ComponentKind.EARNING else ""
        taken = line.amount if line.kind == ComponentKind.DEDUCTION else ""
        if line.kind == ComponentKind.EMPLOYER_COST:
            continue
        rows.append([line.description, "" if line.quantity is None else f"{line.quantity.normalize():f}",
                     f"{earned:,.2f}" if earned != "" else "", f"{taken:,.2f}" if taken != "" else ""])
    totals = [["Earnings", slip.gross()], ["Deductions", slip.deductions()], ["Net pay", slip.net()]]
    employer = [line for line in slip.lines.all() if line.kind == ComponentKind.EMPLOYER_COST]
    note = f"Net pay: {rupees_in_words(slip.net())}."
    if employer:
        note += " Paid by the employer besides: " + ", ".join(
            f"{line.description} {line.amount:,.2f}" for line in employer) + "."
    return render_document(
        heading="Payslip", document=slip, party=employee.party, address=None, meta=meta, totals=totals,
        party_label="EMPLOYEE", note=note, number=f"{run.number}-{employee.employee_number}" if run.number else "",
        table=(["Item", "Quantity", "Earned", "Deducted"], rows, [86 * mm, 24 * mm, 32 * mm, 32 * mm]))
