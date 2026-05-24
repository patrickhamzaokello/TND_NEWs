import re

from django.db.models import Q

from .models import Entity


PERSON_PREFIXES = {
    "president",
    "vice president",
    "gen",
    "general",
    "maj gen",
    "lt gen",
    "brig",
    "capt",
    "hon",
    "rt hon",
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "minister",
}

ORG_PREFIXES = {"the"}
ORG_SUFFIXES = {
    "ltd",
    "limited",
    "inc",
    "plc",
    "llc",
    "corp",
    "corporation",
    "company",
}


def clean_entity_display_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


def normalize_entity_name(name: str, entity_type: str | None = None) -> str:
    value = clean_entity_display_name(name).casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"[’']", "", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    if entity_type == "person":
        value = _strip_prefixes(value, PERSON_PREFIXES)
    elif entity_type == "organization":
        value = _strip_prefixes(value, ORG_PREFIXES)
        value = _strip_suffixes(value, ORG_SUFFIXES)

    return value


def entity_alias_keys(name: str, entity_type: str | None = None) -> set[str]:
    normalized = normalize_entity_name(name, entity_type)
    aliases = {normalized} if normalized else set()

    if entity_type == "organization" and normalized:
        words = normalized.split()
        if len(words) == 3 and words[1] == "of":
            aliases.add(f"{words[2]} {words[0]}")
        if len(words) == 2:
            aliases.add(f"{words[1]} of {words[0]}")

    return {alias for alias in aliases if alias}


def resolve_canonical_entity(name: str, entity_type: str, create: bool = True, update_aliases: bool = True):
    display_name = clean_entity_display_name(name)
    aliases = entity_alias_keys(display_name, entity_type)
    if not display_name or not aliases:
        return None

    queryset = Entity.objects.filter(entity_type=entity_type)
    query = Q(normalized_name__in=aliases) | Q(name__iexact=display_name)
    for alias in aliases:
        query |= Q(aliases__contains=[alias])
    entity = queryset.filter(query).order_by("id").first()

    if not entity:
        if not create:
            return None
        normalized_name = sorted(aliases, key=len, reverse=True)[0]
        entity = Entity.objects.create(
            name=display_name,
            normalized_name=normalized_name,
            entity_type=entity_type,
            aliases=sorted(aliases | {display_name}),
        )
        return entity

    if not update_aliases:
        return entity

    existing_aliases = {
        clean_entity_display_name(str(alias))
        for alias in (entity.aliases or [])
        if clean_entity_display_name(str(alias))
    }
    new_aliases = existing_aliases | aliases | {display_name}
    if new_aliases != existing_aliases:
        entity.aliases = sorted(new_aliases)
        entity.save(update_fields=["aliases", "updated_at"])
    return entity


def _strip_prefixes(value: str, prefixes: set[str]) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in sorted(prefixes, key=len, reverse=True):
            pattern = rf"^{re.escape(prefix)}\s+"
            stripped = re.sub(pattern, "", value)
            if stripped != value:
                value = stripped
                changed = True
    return value.strip()


def _strip_suffixes(value: str, suffixes: set[str]) -> str:
    changed = True
    while changed:
        changed = False
        for suffix in sorted(suffixes, key=len, reverse=True):
            pattern = rf"\s+{re.escape(suffix)}$"
            stripped = re.sub(pattern, "", value)
            if stripped != value:
                value = stripped
                changed = True
    return value.strip()
