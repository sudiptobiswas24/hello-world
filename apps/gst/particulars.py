"""
What an e-invoice and an e-way bill both say about who and where.

The portal's schema has limits — a legal name of three to a hundred
characters, a six-digit PIN, a state as a GST code — and a payload that
breaks one is refused there, after somebody has driven to the gate. So
each is checked here and refused with the field named, rather than
truncated: a name cut at a hundred characters is a different name.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from apps.accounting.gst import STATES, GstSettings, state_code, validate_pincode

# Two-digit state codes the portal uses for places that are not a state.
OVERSEAS = "96"
OTHER_COUNTRY = "99"
UNREGISTERED = "URP"
EXPORT_PIN = 999999


def text(value, what, low, high):
    value = " ".join((value or "").split())
    if not low <= len(value) <= high:
        raise ValidationError(
            f"{what} is {len(value)} characters ({value or 'blank'}); the portal takes "
            f"{low} to {high}."
        )
    return value


def portal_date(value):
    return value.strftime("%d/%m/%Y")


def quantity(value, what):
    """The portal takes three decimals. More is refused rather than rounded:
    10.1235 kg reported as 10.124 is a quantity nobody shipped."""
    if value != value.quantize(Decimal("0.001")):
        raise ValidationError(f"{what} is {value}; the portal takes three decimals.")
    return float(value)


def three(value):
    return float(value.quantize(Decimal("0.001"), ROUND_HALF_UP))


def settings():
    found = GstSettings.active()
    if found is None:
        raise ValidationError("GST is not set up: there is no active registration.")
    return found


def place(address, what, registered_state=None, domestic=True):
    """
    An address as the portal wants it. Where the party is registered, the
    address must not contradict the registration; where it is not, the
    address is the only thing that says the state, so it must say one.
    """
    if address is None:
        raise ValidationError(f"{what} has no address on file.")
    found = state_code(address)
    if registered_state and found and found != registered_state:
        raise ValidationError(
            f"{what} is in {STATES[found]}, but the registration is in "
            f"{STATES[registered_state]}."
        )
    row = {
        "addr1": text(address.line1, f"{what}'s first line", 1, 100),
        "location": text(address.city, f"{what}'s town", 3, 50),
    }
    if address.line2.strip():
        row["addr2"] = text(address.line2, f"{what}'s second line", 3, 100)
    if domestic:
        row["pin"] = int(validate_pincode(address.postal_code, what))
        state = registered_state or found
        if state is None:
            raise ValidationError(
                f"{what} says its state is {address.state or 'nothing'}, which is no GST "
                "state. Give the state's name or its two-digit code."
            )
        row["state"] = state
    else:
        row["pin"] = EXPORT_PIN
        row["state"] = OVERSEAS
    return row


def seller():
    from apps.core.models import Company

    registration = settings()
    company = Company.objects.first()
    if company is None:
        raise ValidationError("There is no company profile.")
    return {
        "gstin": registration.gstin,
        "legal_name": text(company.legal_name or company.name, "The company's legal name", 3, 100),
        "trade_name": text(company.name, "The company's name", 3, 100),
    } | place(company.address, "The company", registered_state=registration.state)


def party_name(party):
    return text(party.legal_name or party.name, f"{party}'s legal name", 3, 100)
