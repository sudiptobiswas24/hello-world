"""Figures on a printed page the way the documents print them (apps.core.documents), for HTML print pages."""

from django import template

from apps.core.documents import counted as _counted

register = template.Library()


@register.filter
def counted(value):
    """18,00,000 for whole sacks, 3,005.076 kg as it stands: the job card printed 40000 and 3005.076 (O413)."""
    if value in (None, ""):
        return ""
    return _counted(value)
