"""
Code 128 for roll labels, drawn as SVG on the server.

On the server because a loom shed's station has a plant network and not
always the internet: a barcode drawn by a script fetched from a CDN is a
label that prints blank the day the line goes down. Code set B only —
roll codes are upper-case letters, digits and hyphens — which a scanner
reads the same as any other Code 128.

The symbol table was checked against an independent implementation
entry by entry before it was trusted, and the tests decode what this
draws. A barcode that looks like one and does not scan is worse than a
printed code, because nobody types the code in any more.
"""

from xml.sax.saxutils import escape

# Bar and space widths, in modules, for symbol values 0 to 105, then the
# stop symbol with its terminating bar.
WIDTHS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
START_B = 104
STOP = 106
QUIET = 10


def values(text):
    """The symbol values for `text` in code set B, start and checksum included."""
    body = []
    for character in text:
        code = ord(character)
        if not 32 <= code <= 127:
            raise ValueError(f"{character!r} cannot be written in Code 128 set B.")
        body.append(code - 32)
    checksum = (START_B + sum(position * value for position, value in
                              enumerate(body, start=1))) % 103
    return [START_B, *body, checksum, STOP]


def modules(text):
    """The barcode as a string of 1 (bar) and 0 (space), one per module."""
    out = []
    for value in values(text):
        bar = True
        for width in WIDTHS[value]:
            out.append(("1" if bar else "0") * int(width))
            bar = not bar
    return "".join(out)


def runs(text):
    """
    Each bar as (first module, width in modules), counted from the left edge
    of the quiet zone, and the whole width in modules, quiet zones included.
    What every drawer draws: the SVG on a label, the PDF on a document.
    """
    bits = "0" * QUIET + modules(text) + "0" * QUIET
    bars, start = [], None
    for index, bit in enumerate(bits + "0"):
        if bit == "1" and start is None:
            start = index
        elif bit == "0" and start is not None:
            bars.append((start, index - start))
            start = None
    return bars, len(bits)


def svg(text, height=60, module=1):
    """
    An SVG of the barcode with its quiet zones, scaled by the viewer.

    Bars are drawn as runs rather than one rectangle per module, which
    keeps the file small and the edges crisp at any print size.
    """
    bars, span = runs(text)
    rects = [
        f'<rect x="{start * module}" y="0" width="{width * module}" height="{height}"/>'
        for start, width in bars
    ]
    width = span * module
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" aria-label="{escape(text)}" '
        f'shape-rendering="crispEdges"><g fill="#000">{"".join(rects)}</g></svg>'
    )


def drawing(text, height, module):
    """
    The same bars for a PDF, as a reportlab Drawing (a flowable), `height`
    and `module` in points. Printed documents drew their number with
    reportlab's own Code 128 beside this one (O436): two drawers, and a
    fault fixed in one stays in the other.
    """
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors

    bars, span = runs(text)
    picture = Drawing(span * module, height)
    for start, width in bars:
        picture.add(Rect(start * module, 0, width * module, height, fillColor=colors.black,
                         strokeColor=None, strokeWidth=0))
    return picture
