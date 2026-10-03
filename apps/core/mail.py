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
