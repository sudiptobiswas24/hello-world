"""
What the bank and the returns need of an employee and nobody else should
read: the account their pay goes to, their PAN and their date of birth.

A model of its own, with its own permissions, so reading an employee (as a
line manager or a storekeeper does) never shows them: only the roles that
run payroll hold hr.view_employeepaydetails. Shapes are checked in save(),
which every path reaches (Django calls no full_clean() for code): an IFSC is
four letters, a nought and six letters or digits; a PAN five letters, four
digits and a letter; an account number nine to eighteen digits.

A pay input: a run reads the date of birth for the pension share (EPS stops
the month after 58) and keeps it on the slip, so a change holds the person
first, as every pay input does (O20). The bank file reads the account as
it is written and keeps what it wrote on each row.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.models import AuditModel, documents_it_answers_to, to_date

from .models import Employee, hold_the_employee

IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
ACCOUNT = re.compile(r"^[0-9]{9,18}$")


class EmployeePayDetails(AuditModel):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="pay_details")
    account_holder = models.CharField(max_length=255, blank=True)
    account_number = models.CharField(max_length=18, blank=True)
    ifsc = models.CharField(max_length=11, blank=True, verbose_name="IFSC")
    pan = models.CharField(max_length=10, blank=True, verbose_name="PAN")
    date_of_birth = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "employee pay details"

    def __str__(self):
        return f"Pay details of {self.employee}"

    def locked_before_it(self):
        """The person, as save() holds them first (hold_the_employee, O20)."""
        return documents_it_answers_to(self, "employee")

    @transaction.atomic
    def delete(self, *args, **kwargs):
        hold_the_employee(*self.locked_before_it())
        return super().delete(*args, **kwargs)

    @transaction.atomic
    def save(self, *args, **kwargs):
        hold_the_employee(*self.locked_before_it())
        self.ifsc = self.ifsc.strip().upper()
        self.pan = self.pan.strip().upper()
        self.account_number = self.account_number.replace(" ", "")
        self.account_holder = self.account_holder.strip()
        problems = []
        if self.ifsc and not IFSC.match(self.ifsc):
            problems.append(f"{self.ifsc} is not an IFSC: four letters, a 0, then six letters or digits.")
        if self.pan and not PAN.match(self.pan):
            problems.append(f"{self.pan} is not a PAN: five letters, four digits, a letter.")
        if self.account_number and not ACCOUNT.match(self.account_number):
            problems.append("An account number is nine to eighteen digits.")
        if any((self.account_number, self.ifsc)) and not all((self.account_number, self.ifsc, self.account_holder)):
            problems.append("A bank account is its holder's name, its number and its IFSC, all three.")
        born = to_date(self.date_of_birth)
        if born is not None and self.employee.hire_date and born >= to_date(self.employee.hire_date):
            problems.append("Born on or after the day they were hired?")
        if problems:
            raise ValidationError(problems)
        super().save(*args, **kwargs)
