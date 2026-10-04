"""
How the API writes JSON.

Its own module, importing nothing of DRF's views: DRF reads
DEFAULT_RENDERER_CLASSES while it defines APIView, so a renderer in a
module that imports rest_framework.views is asked for before it exists.
"""

import decimal

from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder


class ExactEncoder(JSONEncoder):
    """
    Money and quantities as the exact decimal they are, in a string.

    DRF writes a Decimal that is not a serializer DecimalField (a report
    row, a computed figure) as a JSON float: 0.1 + 0.2 arrives as
    0.30000000000000004, and 70 views did it. A string is what the
    serializers already send for every stored figure, so a reader gets
    one kind of number everywhere. Never in exponent form: 1E+2 is 100.
    """

    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return format(obj, "f")
        return super().default(obj)


class ExactJSONRenderer(JSONRenderer):
    encoder_class = ExactEncoder
