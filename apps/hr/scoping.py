"""Which party a login is: its employee's, for the modules that ask core."""


def party_of_login(user):
    if not getattr(user, "is_authenticated", False):
        return None
    employee = getattr(user, "employee", None)
    return employee.party if employee is not None else None
