# Entity Canonicalization

Entity extraction can produce several names for the same real-world person or organization, such as `President Museveni`, `Yoweri Museveni`, and `Museveni`.

The canonicalization layer resolves these variants before saving new `EntityMention` rows.

## How It Works

- Display names are cleaned for whitespace.
- Matching keys are normalized by lowercasing, removing punctuation, and removing conservative prefixes such as `President`, `Hon`, `Dr`, and `Gen` for people.
- Organization names normalize simple variants such as `Parliament of Uganda` and `Uganda Parliament`.
- Existing `Entity.aliases` are used when resolving a mention.
- New variants are added back to the canonical entity's aliases.
- `EntityMention.entity_name` keeps the extracted display text.
- `EntityMention.normalized_name` stores the canonical key used by trend, calendar, and top-article APIs.

## Backfill Existing Mentions

Preview changes first:

```bash
python manage.py canonicalize_entity_mentions --dry-run
```

Dry-run is read-only. It only reports merges that can already be resolved from existing canonical `Entity` rows and aliases.

Apply changes:

```bash
python manage.py canonicalize_entity_mentions
```

Limit to one type:

```bash
python manage.py canonicalize_entity_mentions --entity-type organization
```

## Manual Alias Curation

Some merges should stay editorial/manual, especially one-word people names that may be ambiguous. Add aliases in Django admin on the canonical `Entity` record, then rerun:

```bash
python manage.py canonicalize_entity_mentions
```

Example aliases for one entity:

```json
["Yoweri Museveni", "President Museveni", "Museveni"]
```
