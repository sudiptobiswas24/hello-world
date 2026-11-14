"""
How many bags to pull from a lot, and how many may fail: acceptance
sampling by attributes (ISO 2859-1 / IS 2500 Part 1), single sampling,
normal inspection.

A buyer who writes "AQL 2.5, general inspection level II" into a
contract means: from a lot of 1,000 sacks pull 80, and accept the lot
if 5 or fewer are defective, reject it at 6. The sample depends on the
lot size, so a plan line that says so cannot carry a fixed sample size.

**Code letter** from the lot size and the inspection level; **sample
size** from the letter; **acceptance and rejection numbers** from the
letter and the AQL. The acceptance table runs in diagonals: moving one
letter down (a sample about 1.6 times larger) and one AQL step left (an
AQL about 1.6 times tighter) keeps the same numbers. Down each column
it reads: arrows down, 0/1, an arrow up, an arrow down, 1/2, 2/3, 3/4,
5/6, 7/8, 10/11, 14/15, 21/22, arrows up. An arrow means use the first
plan in that direction, sample size and all.

**When the sample is the lot**, every unit is inspected, against the
same acceptance number.

Only AQLs from 0.010 to 6.5 per cent nonconforming are offered: the
range the table's diagonal describes exactly, and the one sack buyers
write. Normal inspection only; switching to tightened or reduced is a
decision about a supplier's history, not about one lot.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

LEVELS = ["S-1", "S-2", "S-3", "S-4", "I", "II", "III"]
LETTERS = "ABCDEFGHJKLMNPQR"
SAMPLE = dict(zip(LETTERS, [2, 3, 5, 8, 13, 20, 32, 50, 80, 125, 200, 315, 500, 800,
                            1250, 2000]))
AQLS = [Decimal(value) for value in (
    "0.010", "0.015", "0.025", "0.040", "0.065", "0.10", "0.15", "0.25", "0.40", "0.65",
    "1.0", "1.5", "2.5", "4.0", "6.5")]

# Upper lot size of each band, and its letter for S-1, S-2, S-3, S-4, I, II, III.
_BANDS = [
    (8, "AAAAAAB"), (15, "AAAAABC"), (25, "AABBBCD"), (50, "ABBCCDE"), (90, "BBCCCEF"),
    (150, "BBCDDFG"), (280, "BCDEEGH"), (500, "BCDEFHJ"), (1200, "CCEFGJK"),
    (3200, "CDEGHKL"), (10000, "CDFGJLM"), (35000, "CDFHKMN"), (150000, "DEGJLNP"),
    (500000, "DEGJMPQ"), (None, "DEHKNQR"),
]

# Acceptance number by diagonal (letter index + AQL index); "up" and
# "down" are the table's arrows.
_DIAGONAL = {14: 0, 15: "up", 16: "down", 17: 1, 18: 2, 19: 3, 20: 5, 21: 7, 22: 10,
             23: 14, 24: 21}


def code_letter(lot_size, level="II"):
    if level not in LEVELS:
        raise ValidationError(f"Inspection level {level} is not one of {', '.join(LEVELS)}.")
    if lot_size < 2:
        raise ValidationError("A lot to be sampled has at least two units.")
    column = LEVELS.index(level)
    for upper, letters in _BANDS:
        if upper is None or lot_size <= upper:
            return letters[column]


def _aql_index(aql):
    try:
        aql = Decimal(str(aql))
    except InvalidOperation:
        raise ValidationError(f"AQL {aql} is not a number.")
    for index, known in enumerate(AQLS):
        if known == aql:
            return index
    raise ValidationError(f"AQL {aql} is not one of {', '.join(str(a) for a in AQLS)}.")


def _numbers(letter_index, aql_index):
    """(letter index, acceptance number) after following any arrows."""
    step = 0
    while True:
        diagonal = letter_index + aql_index
        found = _DIAGONAL.get(diagonal, "down" if diagonal < 14 else "up")
        if isinstance(found, int):
            return letter_index, found
        step = 1 if found == "down" else -1
        letter_index += step
        if not 0 <= letter_index < len(LETTERS):
            raise ValidationError("No sampling plan in the table answers that.")


def sampling_plan(lot_size, level, aql):
    """{letter, sample, accept, reject, whole_lot} for a lot at a level and AQL."""
    lot_size = int(lot_size)
    letter = code_letter(lot_size, level)
    index, accept = _numbers(LETTERS.index(letter), _aql_index(aql))
    sample = SAMPLE[LETTERS[index]]
    whole = sample >= lot_size
    return {"letter": LETTERS[index], "sample": lot_size if whole else sample,
            "accept": accept, "reject": accept + 1, "whole_lot": whole,
            "level": level, "aql": format(Decimal(str(aql)).normalize(), "f")}
