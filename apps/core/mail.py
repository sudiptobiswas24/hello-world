"""Email on a server where nobody has set up a mail server."""

from django.core.exceptions import ValidationError
from django.core.mail.backends.base import BaseEmailBackend


class NotConfiguredBackend(BaseEmailBackend):
    """
    Refuses, in a sentence, rather than trying a mail server that is not
    there. Django's default reaches for one on this machine; in the
    container there is none, and the person who pressed "send" got a
    server error instead of being told what to ask for.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        raise ValidationError(
            "Email is not set up on this server, so nothing was sent. "
            "Whoever looks after the server sets EMAIL_HOST and the lines "
            "under it in .env (RUNBOOK.md, 'Email')."
        )


def recipient_for(party):
    """Where a party's documents go: its primary contact's address, else the party's own."""
    contact = party.primary_contact()
    if contact and contact.email:
        return contact.email
    return party.email or ""


def send_document(document, party, what, *, to=None, subject=None, body=None, user=None, pdf=None,
                  filename=None):
    """
    Mail a document's PDF to its party ("Delivery challan DN-7 from Deccan
    Polysacks"), to `to` or the party's address, and write it in the
    document's history with who it went to. Returns the address used.
    `pdf` is the bytes to attach when the document renders more than one
    paper (an order's proforma), under `filename`.
    """
    from .models import Company

    recipient = to or recipient_for(party)
    if not recipient:
        raise ValidationError(f"{party} has no email address on the party or its primary contact.")
    company = Company.get()
    number = getattr(document, "number", "") or ""
    return deliver(
        document, recipient,
        subject or " ".join(part for part in (what, number, "from", company.name) if part),
        body or (f"Dear {party.name},\n\nPlease find {what.lower()} {number} attached.\n\n"
                 f"Regards,\n{company.name}\n"),
        user=user, what=what,
        attachment=(filename or f"{number or what}.pdf", document.render_pdf() if pdf is None else pdf),
    )


def deliver(document, recipient, subject, body, *, user=None, what="Mail", attachment=None):
    """
    One mail, sent and written in the record's history with who it went
    to: a document's paper (`attachment` is (filename, pdf bytes)), or a
    note with none (a reply to a lead). Returns the address used.
    """
    from django.core.mail import EmailMessage

    from .history import EventKind, record

    subject = " ".join((subject or "").split())
    if not recipient:
        raise ValidationError("Say who it goes to: there is no email address.")
    if not subject:
        raise ValidationError({"subject": ["A mail has a subject."]})
    message = EmailMessage(subject=subject, body=body or "", to=[recipient])
    if attachment is not None:
        message.attach(attachment[0], attachment[1], "application/pdf")
    message.send()
    record(document, user, EventKind.MAIL, action="send", summary=f"{what} to {recipient}"[:255])
    return recipient
