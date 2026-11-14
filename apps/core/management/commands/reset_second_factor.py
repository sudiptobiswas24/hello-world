"""
manage.py reset_second_factor <login>: the way back for a sole administrator
who lost the phone and the recovery codes (review F8), run on the server by
whoever runs the server. The factor and its codes go, every session of the
login ends, the login's history says so, and the next sign-in enrols a new
phone with the password asked again. RUNBOOK.md, "Users and what they may do".
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from apps.core import second_factor


class Command(BaseCommand):
    help = "Reset a login's second factor (a lost phone): it enrols again at its next sign-in."

    def add_arguments(self, parser):
        parser.add_argument("login", help="The login's user name.")

    def handle(self, *args, **options):
        user = User.objects.filter(username=options["login"]).first()
        if user is None:
            raise CommandError(f"No login named {options['login']!r}.")
        if not second_factor.reset(user, None, summary="Second factor reset on the server (manage.py "
                                                      "reset_second_factor): enrols again at the next sign-in"):
            raise CommandError(f"{user.get_username()} has no second factor to reset.")
        self.stdout.write(f"{user.get_username()}'s second factor is reset; it enrols again at its next sign-in.")
