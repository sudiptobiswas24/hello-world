"""
Which parties a login may see, decided by the modules that know why.

Core holds parties and imports no module built on it, yet a sales rep is
limited to their own customers, and only sales knows whose customer a
party is, while only hr knows which party a login is. So each says what
it knows here, from its `ready()`:

- hr registers `register_user_party`: the party a login is (its employee);
- sales registers a scope: given a login, the parties it may see, whether
  it may make one in a role, what to do once one is made, and whether it
  may change one it can see.

A login nothing limits sees every party, which is everyone but a rep.
"""

_USER_PARTY = []
_SCOPES = []


def register_user_party(provider):
    """provider(user) -> Party or None."""
    if provider not in _USER_PARTY:
        _USER_PARTY.append(provider)


def party_of(user):
    """The party a login is, or None when no module can say."""
    for provider in _USER_PARTY:
        party = provider(user)
        if party is not None:
            return party
    return None


class PartyScope:
    """What a module limits. Each method's default limits nothing."""

    def visible(self, user):
        """A Q over Party, or None for no limit."""
        return None

    def refuse_create(self, user, role):
        """Why this login may not make a party in this role, or None."""
        return None

    def created(self, party, role, user):
        """A party was just made by this login (in the same transaction)."""

    def refuse_change(self, user, party):
        """Why this login may not change a party it can see, or None."""
        return None


def register_party_scope(scope):
    if all(type(existing) is not type(scope) for existing in _SCOPES):
        _SCOPES.append(scope)


def visible_parties(user):
    """A Q over Party for what this login may see, or None for everything."""
    limit = None
    for scope in _SCOPES:
        q = scope.visible(user)
        if q is not None:
            limit = q if limit is None else limit & q
    return limit


def scoped(queryset, user, path=None):
    """`queryset` narrowed to rows whose party (at `path`) the login may see."""
    limit = visible_parties(user)
    if limit is None:
        return queryset
    if path is None:
        return queryset.filter(limit).distinct()
    from .models import Party

    return queryset.filter(**{f"{path}__in": Party.objects.filter(limit)})


def refuse_create(user, role):
    for scope in _SCOPES:
        said = scope.refuse_create(user, role)
        if said:
            return said
    return None


def created(party, role, user):
    for scope in _SCOPES:
        scope.created(party, role, user)


def refuse_change(user, party):
    for scope in _SCOPES:
        said = scope.refuse_change(user, party)
        if said:
            return said
    return None

