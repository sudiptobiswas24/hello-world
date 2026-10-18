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


def scoped(queryset, user, path=None, write=False):
    """
    `queryset` narrowed to rows whose party (at `path`) the login may see,
    or, with `write`, may write.

    Read: a row that names no party at `path` is nobody's to narrow by, and
    stays: filtered by `path__in` alone, the plant's own addresses, which
    belong to no party, dropped out of a rep's view (O130). Write: a login
    the scope limits reaches only its own parties' rows; a row naming no
    party is the plant's own, kept by the roles nothing limits. Read in
    sight, a rep changed the plant's address (O130 review F1).
    """
    limit = visible_parties(user)
    if limit is None:
        return queryset
    if path is None:
        return queryset.filter(limit).distinct()
    from django.db.models import Q

    from .models import Party

    theirs = Q(**{f"{path}__in": Party.objects.filter(limit)})
    return queryset.filter(theirs if write else theirs | Q(**{f"{path}__isnull": True}))


def refuse_write(user, party):
    """
    Why this login may not write a row that names `party`, or None: the one
    rule for every write of a party-scoped record, made, changed, moved or
    deleted. `party` None is the plant's own record (its address, a shared
    contact): written only by a login the scope does not limit.
    """
    limit = visible_parties(user)
    if party is None:
        if limit is None:
            return None
        return ("A record that names no party is the plant's own: it is kept by whoever keeps the plant's "
                "records, not from here. Name the party it belongs to.")
    from .models import Party

    if limit is not None and not Party.objects.filter(limit, pk=party.pk).exists():
        return "Not one of the parties you can see."
    return refuse_change(user, party)


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

