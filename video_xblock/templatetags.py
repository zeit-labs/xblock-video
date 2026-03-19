"""
Video Xblock's templatetags.
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def trans(context, term):
    """
    Translate term using own xblock's translations.
    """
    i18n_service = context['i18n_service']
    return i18n_service.ugettext(term)


@register.filter
def neutral_decimal(value):
    """Ensure decimal numbers always use '.' regardless of locale."""
    if value is None:
        return ''
    return str(value).replace(',', '.')
