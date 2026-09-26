"""
Date windows: from, to, and whether two of them meet.

Blank at either end means unbounded — from the beginning, or for ever.
Shared by everything that says "this applies over these dates" and
refuses two such things applying at once: a recipe and its successor,
an inspection plan and the one that replaces it. The comparison is
where an off-by-one hides, and one copy of it is one place to be
right.

Both ends are inclusive. A recipe valid to the thirty-first and its
successor valid from the first meet at nothing; one valid to the first
and one from the first overlap on the first.
"""


def covers(valid_from, valid_to, day):
    """Whether `day` falls inside the window."""
    if valid_from is not None and day < valid_from:
        return False
    if valid_to is not None and day > valid_to:
        return False
    return True


def overlaps(a_from, a_to, b_from, b_to):
    """Whether two windows share at least one day."""
    a_starts_before_b_ends = b_to is None or a_from is None or a_from <= b_to
    a_ends_after_b_starts = b_from is None or a_to is None or a_to >= b_from
    return a_starts_before_b_ends and a_ends_after_b_starts


def runs_backwards(valid_from, valid_to):
    return (
        valid_from is not None and valid_to is not None
        and valid_to < valid_from
    )


def label(valid_from, valid_to):
    return f"{valid_from or 'the beginning'} to {valid_to or 'open-ended'}"
