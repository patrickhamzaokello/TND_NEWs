"""
Template filters for story pages.

`tag_entities` wraps entity mentions in the synthesized story text with
clickable, colored links to the stories search — the same verbatim-matching
contract the mobile clients use.
"""

import urllib.parse

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def tag_entities(text, entities):
    """
    Wrap every occurrence of each entity name in `text` with a link to
    /stories/?q=<name>. Entities is the story's [{"name", "type"}] list.

    Longest names are replaced first so 'Muwanga Kivumbi' isn't split by a
    shorter 'Kivumbi' entry. Placeholder tokens prevent double-wrapping when
    one entity name is a substring of another's replacement HTML.
    """
    if not text or not entities:
        return escape(text or '')

    escaped = escape(text)

    # Sort longest first; build placeholder tokens
    valid = [
        e for e in entities
        if isinstance(e, dict) and e.get('name') and e.get('type')
    ]
    valid.sort(key=lambda e: len(e['name']), reverse=True)

    replacements = {}
    for i, ent in enumerate(valid):
        name_escaped = escape(ent['name'])
        if name_escaped not in escaped:
            continue
        token = f'\x00E{i}\x00'
        escaped = escaped.replace(name_escaped, token)
        query = urllib.parse.quote(ent['name'])
        replacements[token] = (
            f'<a class="entity entity-{ent["type"]}" '
            f'href="/stories/?q={query}">{name_escaped}</a>'
        )

    for token, html in replacements.items():
        escaped = escaped.replace(token, html)

    return mark_safe(escaped)
