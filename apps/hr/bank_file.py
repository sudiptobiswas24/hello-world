"""
A pay run's bank payment file, and the one payment that settles it.

prepare(run): one row a payslip still owed, net pay to the account on the
employee's pay details, as a plain bulk-upload CSV (beneficiary name,
account number, IFSC, amount, narration) with the run's total. Refused
while anybody on it has no bank details, naming every one of them. Each row
keeps the account it was written with: the details can be corrected later,
and the file is what the bank was sent.

mark_paid(payment): the slips on the file are settled by one payment out
of exactly the file's total, against net pay payable. The payment voided,
every slip on it is owed again (Payslip.is_paid asks the payment).

return_transfer(row, receipt): the bank sent one transfer back. A receipt
of exactly that row, into net pay payable, unpays that slip alone; it is
paid again by a later file or a payment of its own. The receipt voided,
the transfer went through after all, and the slip reads paid again.
"""

import csv
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel, Company, DocumentSequence, lock_rows, serialised


class SalaryTransferFile(AuditModel):
    run = models.ForeignKey("hr.PayRun", on_delete=models.PROTECT, related_name="bank_files")
    number = models.CharField(max_length=32, unique=True)
    narration = models.CharField(max_length=30)
    total = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    text = models.TextField(editable=False, help_text="The file as it was written for the bank.")
    payment = models.OneToOneField(
        "accounting.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="salary_file",
        editable=False, help_text="The one payment out that paid every row.")
    paid_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-id"]
        constraints = [models.CheckConstraint(check=models.Q(total__gt=0), name="salary_file_total_positive")]

    def __str__(self):
        return self.number

    @classmethod
    @transaction.atomic
    def prepare(cls, run, **audit):
        """Write the run's file; `audit` is who wrote it (created_by, updated_by), from the API."""
        from .payroll import PayRunStatus

        lock_rows(run)
        if run.status != PayRunStatus.POSTED:
            raise ValidationError("Only a posted pay run is paid; this one is "
                                  f"{run.get_status_display().lower()}.")
        waiting = run.bank_files.filter(payment__isnull=True).first()
        if waiting is not None:
            raise ValidationError(f"{waiting} has been written for this run and not paid. Mark it paid, "
                                  "or delete it, before writing another.")
        from .payroll import SLIP_PAYMENT_READS

        slips = [slip for slip in run.payslips.select_related("employee__party").prefetch_related(
            "lines", *SLIP_PAYMENT_READS).order_by(
            "employee__employee_number") if not slip.is_paid() and slip.net() > 0]
        if not slips:
            raise ValidationError(f"Nobody on {run} is still owed their pay.")
        missing = [slip.employee.employee_number for slip in slips if not _bank_details(slip.employee)]
        if missing:
            raise ValidationError(f"No bank account on {', '.join(missing)}: the bank pays an account. "
                                  "Enter their bank details, or pay them by a payment of their own.")
        narration = f"Salary {run.period_end:%b %Y}"[:30]
        rows = []
        for slip in slips:
            details = slip.employee.pay_details
            rows.append(SalaryTransfer(payslip=slip, holder=details.account_holder,
                                       account_number=details.account_number, ifsc=details.ifsc,
                                       amount=slip.net()))
        total = sum((row.amount for row in rows), Decimal("0.00"))
        made = cls.objects.create(
            run=run, narration=narration, total=total, text=_csv(rows, narration, total), **audit,
            number=DocumentSequence.next_for("hr.salarytransferfile", run.pay_date, name="Salary bank files",
                                             prefix="SBF-"))
        for row in rows:
            row.file = made
            row.save()
        return made

    @serialised("payment")
    def mark_paid(self, payment):
        from apps.accounting.models import PaymentDirection

        from .payroll import _net_pay_account

        # One order for every path that pays a slip: the file, its run, the slips in key order, then
        # the payment. Payslip.pay holds the slip before its payment; holding the payment first, one
        # payment offered to both at once left each waiting on the other, and PostgreSQL ended one
        # of them as a deadlock instead of a refusal in words.
        rows = list(self.rows.select_related("payslip__employee").order_by("payslip_id"))
        slips = [row.payslip for row in rows]
        lock_rows(self.run)
        lock_rows(*slips)
        lock_rows(payment)
        if self.payment_id is not None:
            raise ValidationError(f"{self} has been paid, by {self.payment}.")
        if self.run.is_voided():
            raise ValidationError(f"{self.run} has been voided; it owes nobody.")
        if not payment.posted or payment.is_voided():
            raise ValidationError("That payment is not posted, or has been voided: nothing left the bank.")
        if payment.direction != PaymentDirection.DISBURSEMENT:
            raise ValidationError("Wages are settled by money paid out, not money received.")
        if payment.counterpart_account_id != _net_pay_account().pk:
            raise ValidationError(f"That payment was booked against {payment.counterpart_account}, not net pay "
                                  "payable, so it does not clear what the run owes.")
        if payment.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("Net pay is owed in the base currency.")
        if payment.amount != self.total:
            raise ValidationError(f"The payment is {payment.amount} and {self} totals {self.total}.")
        if payment.payslips.exists() or SalaryTransferFile.objects.filter(payment=payment).exists():
            raise ValidationError("That payment already settles other pay.")
        paid = [slip.employee.employee_number for slip in slips if slip.is_paid()]
        if paid:
            raise ValidationError(f"{', '.join(paid)} have been paid since {self} was written. Delete it and "
                                  "write it again.")
        for slip in slips:
            slip.payment = payment
            super(type(slip), slip).save(update_fields=["payment", "updated_at"])
        self.payment, self.paid_at = payment, timezone.now()
        super().save(update_fields=["payment", "paid_at", "updated_at"])
        return self

    @transaction.atomic
    def delete(self, *args, **kwargs):
        lock_rows(self, refresh=False)
        if SalaryTransferFile.objects.filter(pk=self.pk, payment__isnull=False).exists():
            raise ValidationError(f"{self} has been paid; a transfer sent back is returned, the file stays.")
        return super().delete(*args, **kwargs)


class SalaryTransfer(AuditModel):
    """One row of the file: the account as it was written, and the receipt if the bank sent it back."""

    file = models.ForeignKey(SalaryTransferFile, on_delete=models.CASCADE, related_name="rows")
    payslip = models.ForeignKey("hr.Payslip", on_delete=models.PROTECT, related_name="transfers")
    holder = models.CharField(max_length=255)
    account_number = models.CharField(max_length=18)
    ifsc = models.CharField(max_length=11)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    returned_by = models.OneToOneField(
        "accounting.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="returned_transfer",
        editable=False, help_text="The receipt of the money the bank sent back.")

    class Meta:
        ordering = ["file", "id"]
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name="salary_transfer_positive")]

    def __str__(self):
        return f"{self.file} {self.holder} {self.amount}"

    def is_returned(self):
        return self.returned_by_id is not None and not self.returned_by.is_voided()

    @serialised("returned_by")
    def return_transfer(self, receipt):
        from apps.accounting.models import PaymentDirection

        from .payroll import _net_pay_account

        # The file, the slip, then the money: the order mark_paid and Payslip.pay take them in.
        slip = self.payslip
        lock_rows(self.file)
        lock_rows(slip)
        lock_rows(receipt)
        if self.file.payment_id is None:
            raise ValidationError(f"{self.file} has not been paid; nothing was sent to return.")
        if self.is_returned():
            raise ValidationError(f"{self} has already been returned, by {self.returned_by}.")
        if slip.payment_id != self.file.payment_id or not slip.is_paid():
            raise ValidationError(f"{slip.employee} is not paid by {self.file} now; there is nothing of it to return.")
        if not receipt.posted or receipt.is_voided():
            raise ValidationError("That receipt is not posted, or has been voided: nothing came back.")
        if receipt.direction != PaymentDirection.RECEIPT:
            raise ValidationError("A transfer sent back is money received.")
        if receipt.counterpart_account_id != _net_pay_account().pk:
            raise ValidationError(f"That receipt was booked against {receipt.counterpart_account}, not net pay "
                                  "payable, so the wages would not read as owed again.")
        if receipt.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("Net pay is owed in the base currency.")
        if receipt.amount != self.amount:
            raise ValidationError(f"The receipt is {receipt.amount} and the transfer {self.amount}.")
        if receipt.party_id != slip.employee.party_id:
            # Review RP9: a receipt in E3's name returned E2's transfer.
            raise ValidationError(f"That receipt is from {receipt.party}; the transfer that came back was "
                                  f"{slip.employee}'s.")
        if SalaryTransfer.objects.filter(returned_by=receipt).exclude(pk=self.pk).exists():
            raise ValidationError("That receipt already returns another transfer.")
        self.returned_by = receipt
        super().save(update_fields=["returned_by", "updated_at"])
        return self


def refuse_void_under_a_returned_transfer(payment, on_date):
    """
    A void follow-up (review RP2, RP3): net pay payable foots to the slips whatever is voided, in any order.

    A file's payment is not voided while a transfer on it stands returned: the receipt that brought the money
    back would stand against nobody, and net pay payable owed 32,276.00 for 23,825.50 of slips. Void the
    receipt first. A return's receipt voided says the transfer went through after all, so the slip is the
    file's again: refused while another payment since pays it (E2 was paid twice), and where one since was
    itself voided, the slip is put back on the file's payment, exactly what the return took from it.
    Each decides holding what it reads: the file, or the slip.
    """
    made = SalaryTransferFile.objects.filter(payment=payment).first()
    if made is not None:
        lock_rows(made)
        back = [row for row in made.rows.select_related("payslip__employee", "returned_by") if row.is_returned()]
        if back:
            raise ValidationError(
                f"This payment paid {made}, and "
                + ", ".join(f"{row.payslip.employee.employee_number}'s transfer came back by {row.returned_by}"
                            for row in back)
                + ". Void those receipts first: until then the money they brought back stands in the books.")
    row = SalaryTransfer.objects.filter(returned_by=payment).select_related("file", "payslip").first()
    if row is not None:
        slip = row.payslip
        lock_rows(slip)
        if slip.payment_id != row.file.payment_id:
            if slip.payment_id is not None and not slip.payment.is_voided():
                raise ValidationError(
                    f"{slip.employee} has been paid again since the transfer came back, by {slip.payment}. Void "
                    "that payment first: with this receipt voided as well they would be paid twice.")
            slip.payment_id = row.file.payment_id
            super(type(slip), slip).save(update_fields=["payment", "updated_at"])


def _bank_details(employee):
    details = getattr(employee, "pay_details", None)
    return details is not None and details.account_number and details.ifsc and details.account_holder


def _cell(text):
    """Text as data, never a spreadsheet formula (review RP10): the file is opened in a spreadsheet before upload."""
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(rows, narration, total):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["Beneficiary name", "Account number", "IFSC", "Amount", "Narration"])
    for row in rows:
        writer.writerow([_cell(row.holder), row.account_number, row.ifsc, f"{row.amount:.2f}", _cell(narration)])
    writer.writerow(["Total", "", "", f"{total:.2f}", f"{len(rows)} transfers"])
    return out.getvalue()
